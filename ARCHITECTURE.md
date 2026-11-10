# Architecture

This document explains what each file does and how the files connect. The system has five
layers:

1. **Facts:** discovery, parsing, ranking, compliance and commitments, all deterministic.
2. **Narrative:** model-drafted prose and chart readings, accepted only when code can trace every claim.
3. **Report:** rendering the PDF in the prior PMR's house style from the facts and narrative alone.
4. **Verification:** checking the rendered PDF against its manifest, and the manifest against the inputs.
5. **Review:** a reviewer page, reviewer corrections, approval tied to content, and a finalization gate.

## How a run flows

A person runs `python -m pmr --client CPERS --quarter 4Q25`. The run passes through these
steps in order:

```
cli.py                      parse --client and --quarter, print the summary
  |
  v
pipeline.run                build facts, render, verify, write the outputs
  |
  +--> pipeline.build_facts
  |      +--> discovery.py    classify every file by content; pick this client's and quarter's files
  |      +--> flash.py        parse the flash by labels; run footing checks
  |      +--> prior_pmr.py    guideline table, short code, section text
  |      +--> style.py        measure the house style and section layout from the prior PMR
  |      +--> analytics.py    benchmark, attribution, featured funds, compliance
  |      +--> ic_log.py       new, open and reversed commitments
  |      +--> managers.py     match manager documents to funds by content
  |      +--> figures.py      every figure the report prints, with locators
  |
  +--> pipeline.build_narrative     (after every figure is fixed)
  |      +--> deck.py         slide text; chart values read from the deck's images
  |      +--> narrative.py    commentary, strategies, market update; source-check every sentence
  |      +--> llm.py          cached Opus 5.5 calls; cache-only when there is no key
  |
  +--> render.py            facts -> PDF; every number goes through the manifest
  |      +--> charts.py       vector charts in the house palette
  |      +--> manifest.py     number formats and the record of every printed number
  |
  +--> verify.py            PDF numbers vs manifest; manifest locators vs workbook cells
  |
  +--> review.py            reviewer page; review.json created once
  |
  v
output/<CODE>_<QUARTER>/
    PMR_<CODE>_<QUARTER>_draft.pdf   the draft, marked DRAFT FOR REVIEW on every page
    facts.json                       everything known, with locators and issues
    manifest.json                    every printed number and sentence, with its sources
    review.html                      what a reviewer must inspect before approving
    review.json                      the reviewer's corrections and approval
    PMR_<CODE>_<QUARTER>.pdf         the final report, written only by --finalize
```

The review steps use the same two required inputs:

```
python -m pmr --client CPERS --quarter 4Q25                          draft
  (edit review.json: correct a chart value, exclude a sentence; run the draft again)
python -m pmr --client CPERS --quarter 4Q25 --approve "Reviewer"     refused while blockers are open
python -m pmr --client CPERS --quarter 4Q25 --finalize               refused unless approved and unchanged
```

A locator names the exact source, for example `flash_4Q25.xlsx#CashActivity(Agg)!F11` or
`prior_pmr_3Q25.pdf#p10`. A computed value carries a formula and the locators of its inputs.

## Source files (`src/pmr/`)

### Entry points

| File | What it does | Calls |
|---|---|---|
| `__main__.py` | Lets the package run as `python -m pmr`. | `cli.main` |
| `cli.py` | Parses `--client` and `--quarter`, plus optional `--inputs`, `--out`, `--approve`, `--finalize` and `--no-llm` (cache only). Calls `pipeline.run` and prints a readable summary: files used and skipped, featured funds, commitments, compliance, footing, narrative, verification, issues. A draft run exits 0 even with blockers; a refused approval or finalization exits 1. | `pipeline.run`, `pipeline.finalize`, `review.approve` |
| `pipeline.py` | The orchestrator. `build_facts` runs every deterministic step. `build_narrative` drafts and checks the prose, applying reviewer corrections. `run` renders and verifies the draft and writes the outputs. `finalize` writes the final PDF through the review gate. | every module below |

### Shared building blocks

| File | What it does | Used by |
|---|---|---|
| `model.py` | Two shared types. `Fact` is a value plus where it came from. `Issue` is a reviewer-facing problem: a `warning` (safe to draft past) or a `blocker` (the report cannot be finalized). | all modules |
| `periods.py` | `Quarter` parses `4Q25` and `Fourth Quarter 2025`, and gives start and end dates, the previous quarter, and whether a date falls inside the quarter. | most modules |
| `xlsx.py` | Label-driven worksheet helpers: find a header row by its labels, map columns to names (including two-level headers such as `Quarter` over `NET`), read rows as `Fact`s. No fixed cell addresses. | flash, ic_log, discovery, pipeline |
| `entities.py` | Controlled entity resolution. `canonical` reduces a label to a key with fixed rules (punctuation, legal suffixes, Roman numerals, number words) and records which rules fired. `resolve` returns `exact`, `normalized`, `ambiguous` (flagged, never merged) or `none`. | ic_log, managers |

### Input readers

| File | Reads | What it produces |
|---|---|---|
| `discovery.py` | Every file in `inputs/` | A `Selection`: client name and code, the chosen flash, prior PMR, IC logs, allocation history and this quarter's manager documents, plus a used-or-skipped decision with a reason for every file. Files are classified by content, never by filename. |
| `flash.py` | The quarterly flash workbook | A `Flash` object: sleeves (found structurally), funds with their rows from three sheets, subtotals, totals, composition, performance summary, property and geographic mix. Runs the footing checks. |
| `prior_pmr.py` | The prior quarter's PMR | Page text, quarter, the client's short code, the compliance guideline table, and the text under any heading (`section_lines`, `find`) for facts carried forward. |
| `style.py` | The prior quarter's PMR, as a layout | A `Style`: fonts, sizes and colors for each text role, table header fill, stripe and grid, heading rule, margins, footer, cover spans, chart palette, and the section list with heading levels and page breaks. Measured on every run, so a format change in the prior report flows through. |
| `ic_log.py` | The firm-wide IC log(s) | New commitments this quarter (largest first), open approvals, and approvals withdrawn or lapsed this quarter, using the four filters in REQ 1.7. |
| `managers.py` | Manager reports and strategy one-pagers | Each document matched to the one fund whose canonical name it contains. Ambiguous or unmatched documents are flagged, not guessed; two documents for one fund is a blocker. |
| `deck.py` | The market outlook deck | Each slide's title and text with a slide locator, and every value in every chart image, read by the model, given an id and a slide/image locator, and checked for plausibility in code. |

### Computation and output

| File | What it does |
|---|---|
| `analytics.py` | Ranking and checks in code: dollar attribution, the featured funds and their roles, the benchmark, compliance against the prior PMR's limits, and the committed-or-approved totals. |
| `figures.py` | Gathers every figure the report prints into `facts["report"]`: portfolio, benchmark, sleeves, funds (with roles and matched documents), mix, facts carried from the prior PMR, and the appendix tables. The renderer reads only this. |
| `manifest.py` | Number formats (`usd`, `usd_m`, `pct`, `acct` and so on) and the `Manifest`. The renderer never writes a number directly: `fig` formats a sourced figure, `text` prints a sourced quote, `fixed` prints layout text such as page numbers. Each call is recorded with its locators. IC log figures cite the cell they come from: amounts cite the Recommendation cell their text is parsed from, dates cite the Meeting Date cell, and counts of approvals cite the rows by their IC # cell. The IC # is read as text and is never part of a sum. A fund count cites the name cell of every fund row it counted. |
| `charts.py` | The annualized TWR bars, allocation lines, property pie and geographic bars, drawn with `reportlab.graphics` like the prior PMR. Data labels and axis ticks are registered in the manifest. |
| `render.py` | Builds the PDF from `facts.json`. Walks the prior PMR's section list, maps each title to a builder by keyword (sleeves by their first word), and lays out the cover, contents, sections and appendix in the measured style. Narrative that needs the language-model step prints a visible placeholder. A section title with no builder gets a placeholder and a blocker. The PDF has no citation markers, as the prior PMR has none. Emphasis follows the prior PMR: bold for the two allocation measures in the overview, manager names, featured fund names and market figures in the market update (not the Portfolio's own figures); italics for the no-manager-report note; italic gray for notes. Appendix tables span the text width, and a header that fits on one line is not wrapped. `fit_pages` scales a page's content down when it runs up to 25% past one page, so it does not spill a few lines onto a mostly blank page. |
| `verify.py` | `check_pdf`: every number in the PDF is in the manifest, and every printed manifest figure is in the PDF. `check_locators`: every figure that cites a workbook cell is re-read from that cell and compared. |

### Narrative and review

| File | What it does |
|---|---|
| `llm.py` | The only place the model is called. Opus 5.5, high effort, JSON output matching a schema, server-side refusal fallback. Every answer is cached in `llm_cache/` by a hash of the full request, so identical inputs give identical prose. Reads the key from the environment or `.env` without setting it; with neither, runs cache-only. |
| `narrative.py` | Drafts manager commentary, strategy descriptions and the market update, then checks every sentence: quotes must be in the cited source, numbers must be in the cited evidence, names must be in the sources. Failing sentences are dropped and listed for the reviewer. |
| `review.py` | The reviewer page (`review.html`, including the source of every printed figure), the reviewer's file (`review.json`), reviewer edits (`edited_sentences`, applied only when they add no number or name), approval (refused while blockers are open; records a content hash) and the finalization gate (refused unless approved and unchanged since). |

### Web application (`src/pmr_web/`)

The browser front end and its Azure adapters. It depends on `pmr`; `pmr` knows nothing about it. See [`docs/AZURE.md`](docs/AZURE.md) for the screens and the cloud architecture.

| File | What it does |
|---|---|
| `config.py` | Settings from the environment: local (folder, SQLite, worker thread) or Azure (Blob Storage, PostgreSQL, separate worker). |
| `db.py` | SQLAlchemy models: reports, runs, uploaded-file hashes and the append-only audit trail. `claim_next_run` makes the runs table a safe work queue. |
| `storage.py` | One interface over a local folder and Azure Blob Storage. The worker copies a report's files to a temporary folder, runs the pipeline, and copies the outputs back. |
| `worker.py` | Claims queued runs and calls `pipeline.run` (and `pipeline.finalize`), reporting each stage and model answer to the run's row. Records input hashes and the content hash. Never writes `review.json` once it exists. |
| `evidence.py` | Renders a locator from the uploaded file: a workbook cell in context, a PDF page with quotes highlighted, or a slide. |
| `app.py` | Flask routes and screens: reports, new report, overview, figures, narrative, chart values, draft PDF, approval, inputs, audit trail. Reviewer actions write `review.json`; approval calls `review.approve` with the signed-in user. |
| `ui.py` | Page layout, theme and shared MonsterUI components, rendered to HTML for Flask. |
| `telemetry.py` | Application Insights through the Azure Monitor OpenTelemetry distro, when a connection string is set. |

## How the modules depend on each other

```
cli -> pipeline -> discovery -> flash, prior_pmr, ic_log, xlsx
                -> flash      -> xlsx, periods, model
                -> prior_pmr  -> periods, model
                -> style      -> prior_pmr (text helper), model
                -> ic_log     -> entities, xlsx, periods, model
                -> managers   -> entities, model
                -> analytics  -> flash, ic_log, prior_pmr, model
                -> figures    -> flash, style, managers, prior_pmr, model
                -> deck       -> llm, periods, model
                -> narrative  -> deck, llm, managers, manifest (number helper), model
                -> render     -> charts, manifest, style, periods, model
                -> verify     -> manifest, prior_pmr (text helper), model
                -> review     -> deck (for the chart images)
```

Dependencies point one way. The readers know nothing about each other. Only `llm.py` talks to
the model, and only `narrative.py` and `deck.py` use it. `render` never opens an input file: it
works from `facts.json` alone, so the report a reviewer signs off is exactly what the facts say.

## Tests (`tests/`)

| File | What it checks |
|---|---|
| `conftest.py` | Shared fixtures: the inputs path, a `facts` fixture (one `build_facts` run) and a `rendered` fixture (one full `run` into a temporary folder). |
| `test_periods.py` | Quarter parsing in every accepted form, date bounds, year rollover, bad input. |
| `test_entities.py` | Numeral, suffix and punctuation variants resolve with a recorded rule; Fund III and Fund IV are not merged; near misses are ambiguous. |
| `test_discovery.py` | The right files are selected; each decoy is skipped with the right reason; the full client name works; an unknown client is a blocker, not a crash. |
| `test_flash.py` | 12 funds in 2 sleeves and every footing check passes; blank returns stay blank; locators point at real cells; the perturbed-layout test. |
| `perturb.py` | Helper, not a test. Copies `inputs/`, shifts every cell in the flash down two rows, and adds a 13th fund sized to be the largest detractor, with totals updated so the books still foot. |
| `test_ic_log.py` | Golden 4Q25 commitments; synthetic cases for each filter, unsorted rows, reversals before and after quarter-end, same-quarter reversals, earlier-year approvals, unknown actions, missing amounts. |
| `test_analytics.py` | Golden featured funds, roles and compliance; the contribution formula; the fewer-than-two-detractors rule. |
| `test_style.py` | Golden style values measured from the prior PMR; the section list, levels and page breaks; JSON round trip. |
| `test_managers.py` | Documents match funds by content (Ironwood through its footer); renaming every file changes nothing; unmatched and two-fund documents are flagged. |
| `test_render.py` | Every printed number has a source and every citation re-reads correctly; sections appear in the prior PMR's order; key sentences are present; the PDF has no citation markers, prints exact dollars and uses the prior PMR's bold and italics; a computed figure carries its formula and inputs; a slight overflow is fitted to its page; a removed manifest entry and a wrong cell value are both caught; two runs give byte-identical PDFs; the perturbed inputs render and verify. |
| `test_reproducibility.py` | Two runs give identical facts; `src/` contains no fund name, client name or code, benchmark, input filename or guideline limit. |
| `test_llm.py` | Key lookup order (environment, then `.env`, without setting the environment); cache-only mode; cached answers are reused without a key. No live calls. |
| `test_deck.py` | The deck is found by its quarter; slide text and images load; chart values get ids and locators; out-of-range values and mismatched series are flagged; an unread chart is a warning. |
| `test_narrative.py` | Each source check, with a stubbed model: supported sentences kept with their page; a quote not in the document, an uncited number or an invented name are dropped; market sentences need cited evidence; a run with no key and no cache leaves marked placeholders; the committed cache supplies the 4Q25 narrative. |
| `test_review.py` | Finalization refused before approval and allowed after; the final PDF has no draft mark; a change after approval (a reviewer exclusion or an input change) blocks finalization; approval refused while blockers are open; a chart value correction replaces the model's reading; the reviewer page shows images and evidence. |
| `synthetic.py` | Helper, not a test. Builds a CPERS 1Q26 input folder from the 4Q25 package, with this program's 4Q25 report as the prior PMR, and returns the expected results from its own arithmetic. |
| `audit/audit_report.py` | Helper, not a test. The independent audit: re-reads the raw inputs with openpyxl, PyMuPDF and python-pptx, without importing `pmr`, and checks the PDF's footing, roles, fund and overview figures, and narrative quotes. |
| `test_audit.py` | The audit passes on the rendered 4Q25 report and fails when a quote is altered. |
| `test_stress.py` | Changes the synthetic quarter does not make: reversed flash columns and renamed files with shuffled sheets give the same featured funds and a verified report; a renamed sleeve stops with blockers. |
| `test_web.py` | The web workflow on the committed inputs: sign-in and CSRF are enforced; the first draft builds and every screen renders; the stored facts hash to the run's hash; the evidence viewer refuses paths outside the inputs; an edit that adds a number is refused; saved changes block approval until a new draft includes them; approval needs every review statement; approve then finalize writes the final PDF; a change after approval voids it. Skipped when the web requirements are not installed. |
| `test_synthetic.py` | The unseen-quarter run: files chosen by content and stale ones skipped; the books foot and every number is sourced; featured funds follow the new rankings; commitments follow the new IC log; the new leading sector is named and its breach flagged; narrative without a model is marked, not invented. |

Every test runs the model in cache-only mode, so the suite never calls the API.

Golden tests (this quarter's known values) live only in `tests/`. The pipeline itself never
expects a specific fund, count or ranking.

## Project files

| File | Purpose |
|---|---|
| `pyproject.toml` | Package metadata, the `pmr` command, and pytest settings. |
| `requirements.txt` | Pinned dependencies (openpyxl, PyMuPDF, reportlab, anthropic, python-pptx, lxml, pytest) plus an editable install, so one `pip install -r requirements.txt` is the whole setup. |
| `llm_cache/` | The model's answers for the 4Q25 inputs, one JSON file per request hash: 7 chart readings, 3 commentaries, 2 strategy descriptions, 1 market update. |
| `.github/workflows/ci.yml` | Runs the tests and a full run with no API keys on Windows and Python 3.12, a clean reviewer machine. |
| `.gitignore` | Excludes the virtual environment, caches, IDE files, `output/` and `.env`. |
| `DECISIONS.md` | Every judgment call made where the requirements are ambiguous, with the reason. Feeds the write-up. |
| `WRITEUP.md` | The 2-page summary of the approach. |
| `examples/` | The approved, final 4Q25 report this program produced. |
| `docs/REQUIREMENTS.md` | The business rules, numbered `REQ 1.x` as cited in code comments. |
| `README.md` | Install, run, review and no-key instructions. |

## Inputs (`inputs/`)

The drop folder as supplied. The code reads it in place and never modifies it.

| File | Role in the pipeline |
|---|---|
| `flash_4Q25.xlsx` | Source of truth for every portfolio figure, and the source of the appendix tables. |
| `flash_4Q25_NWPT.xlsx`, `flash_3Q25.xlsx` | Decoys: another client, and an earlier quarter with no data. Skipped. |
| `flash_4Q25.pdf` | A PDF rendering of the flash. Recognized and skipped: the appendix is rebuilt from the workbook. |
| `prior_pmr_3Q25.pdf` | Short code, guideline limits, benchmark name, carried-forward facts, the house style and the section layout. |
| `prior_pmr_2Q25.pdf` | Archived one-page stub. Skipped. |
| `ic_log_2025.xlsx` | Commitment approvals, withdrawals and lapses for all clients. |
| `allocation_history.xlsx` | Target allocation vs NAV series for the allocation exhibit; checked against the flash because it has no client column. |
| `manager_reports/*.pdf` | Matched to funds by content. Used for featured funds (commentary) and new commitments (strategy); skipped with a reason otherwise. |
| `market_outlook_4Q25.pptx` | Found by the quarter on its first slide. Its text and chart values feed the market update. |
