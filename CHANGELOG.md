# Changelog

## 2026-11-09

### Performance

- PyMuPDF's neural page-layout analysis is switched off in `pmr/__init__.py`. Reason: `pymupdf==2.0` turns it on wherever numpy is installed, which includes every fresh install (CI, the container, a reviewer's machine) but not the original development venv. The pipeline never uses its result.
  - Measured in the container: the client and quarter check fell from about 43 s to 0.5 s on one CPU. A full CPERS 4Q25 run fell from 23 s to 2 s on two CPUs. On the live B1 plan, creating a report had taken 44 to 88 s.
  - Facts, manifest and draft PDF are byte-identical with layout on and off.
  - `tests/test_reproducibility.py` fails if a PyMuPDF upgrade renames the hook.

### Added

- **New report** checks the client and quarter against the files before it creates anything. Reason: a report created as `PMR` `4Q26` from 4Q25 files built nothing but a blocker, and its Figures, Narrative and Chart values tabs stayed empty.
  - The check runs the pipeline's own discovery on a scratch copy, so it agrees with the run.
  - It refuses only a client that resolves to nothing or a missing flash. Other gaps, such as no outlook deck, still draft and show as issues.
  - The refusal lists what the files can report on, for example `CPERS (Cascadia Public Employees' Retirement System): 4Q25`, and keeps the typed client and quarter.
  - `discovery.inventory()` lists each client with a filled-in flash, its short code and its quarters.
- `/version` returns the commit the image was built from, and the deploy workflow waits until it matches. Reason: the workflow reported success about 3.5 minutes before the new container served traffic, because `/health` was answered by the old one.
- Slim Azure deployment in `infra/tofu/`: one App Service B1 runs the web app and the worker thread, with PostgreSQL, Blob Storage, Key Vault, Container Registry and Application Insights. Reason: a demo for a few reviewers does not justify the full design's $120 to $150 a month; this runs for about $36.
- Shared access code on the name sign-in (`PMR_ACCESS_CODE`, stored in Key Vault). Reason: demo visitors have no Entra ID accounts in the tenant, and the code keeps strangers from spending the Anthropic API credit.
- `.github/workflows/deploy.yml`: on push to `main`, runs the web tests, builds and pushes the image tagged with the commit hash, points the web app at it and waits for `/health`. Signs in with OpenID Connect, so GitHub stores no Azure secret.
- Anthropic API key option (`anthropic_api_key`, stored in Key Vault) in both OpenTofu configurations, as an alternative to Azure AI Foundry.

### Changed

- The full design moved to `infra/tofu-full/`. CI validates and tests both configurations.

### Fixed

- The app trusted `X-MS-CLIENT-PRINCIPAL-NAME` on any App Service host. App Service strips client-sent copies of that header only when its authentication is on, so without it anyone could sign in as any name by sending the header. Trust now follows `WEBSITE_AUTH_ENABLED` or an explicit `PMR_TRUST_EASY_AUTH`.

## 2026-11-08

### Added

- Drag and drop on **New report** and the **Inputs** tab. Preparers can drop files, folders or zips onto the upload box instead of using the pickers. Reason: dragging a drop folder from Finder is faster than browsing to it.
  - A dropped folder keeps its layout, minus the folder's own name, the same as the folder picker (`drop_1Q27/logs/x.xlsx` gives `logs/x.xlsx`).
  - Loose files keep their names. Dropped zips are unpacked as before.
  - Hidden files such as `.DS_Store` are left out in the browser. The server applies its usual type rules to the rest.
  - Each drop adds to what is already picked. **Clear** removes the dropped files.
  - The Inputs tab file picker now lists only supported types, like the one on New report.
- Favicon. Every page uses the logo (`src/pmr_web/static/logo.svg`) as its browser tab icon. Reason: tabs showed the browser's blank default icon.

### Changed

- Times show in the viewer's time zone instead of UTC. Reason: reviewers read times in their own zone, and UTC made approval and audit times easy to misread.
  - The server writes US Eastern time (EST or EDT) and the browser rewrites it in the device's zone, with the zone abbreviation (`2 Nov 2026, 09:08 EST`).
  - Eastern stays when the browser reports no zone or reports UTC, which privacy settings that hide the zone send.
  - Applies to the report list, report header, archive banner, approval record, run log, runs table and audit trail.
  - Storage is unchanged: the database, run logs and `review.json` still hold UTC.
  - Pins `tzdata` in `requirements-web.txt` so hosts without a system zone database can compute Eastern time.
- Report URLs name the report instead of showing a bare id: `/reports/cpers-4q25-draft-2026-11-08-2148-est-6` instead of `/reports/6`. Reason: a pasted or bookmarked link should say which client, quarter and draft it points to.
  - The slug holds the client code, quarter and last draft's build time, then the id. Before the first draft it holds the creation time (`created-...`).
  - The time is always US Eastern, because the server cannot know the viewer's zone.
  - Only the trailing id is read, so `/reports/6` and links from earlier drafts still open the report. The page then rewrites the address bar to the current slug.

## 2026-11-06

### Added

- Zip uploads. A zip in the drop folder is unpacked and each document inside it is read like any other input. Reason: preparers receive drop folders as zip files.
  - Web app: unpacks a zip on upload, from **New report** and from the **Inputs** tab, and logs an `archive_unpacked` audit event with the zip's SHA-256 and the counts of files saved and skipped.
  - CLI: unpacks into a temporary copy of the drop folder, so the folder itself is never changed, and prints the archives unpacked and the members skipped.
  - Members land under the archive's name (`drop.zip` gives `drop/...`).
  - Skips `__MACOSX` folders, hidden files, Excel lock files, `../` paths and nested zips.
  - Refuses archives over 2,000 files or 1 GiB unpacked, and corrupt archives.
  - Shared rules live in `src/pmr/archive.py`. See the README section "Zip archives".
