# PMR generator

Builds a client's quarterly Performance Measurement Report (PMR) as a PDF from a folder of source documents. You give it a client and a quarter. It finds the right files, computes every figure in code, drafts the narrative with Claude, verifies the PDF against its sources, and holds the report as a draft until a reviewer approves it.

## About this project

A real-estate investment advisor (here "Corvane Advisors") assembles a 10 to 15 page quarterly report for each institutional client by hand. This program automates it, matching the prior quarter's report in structure, tone, exhibits and numbers.

Everything in `inputs/` is synthetic. The firm, the client (Cascadia Public Employees' Retirement System, "CPERS"), every fund, every figure and every person named are fictional.

## Start here

1. [`WRITEUP.md`](WRITEUP.md): a 2-page summary of the approach and its trade-offs.
2. [`examples/PMR_CPERS_4Q25.pdf`](examples/PMR_CPERS_4Q25.pdf): the approved, final 4Q25 report this program produced.
3. [`VERIFICATION.md`](VERIFICATION.md): every check that confirms the report is correct.
4. This README: how to install, run, review and approve.
5. For detail when needed: [`ARCHITECTURE.md`](ARCHITECTURE.md) explains each module and [`DECISIONS.md`](DECISIONS.md) lists the judgment calls.
6. [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md): the business rules the program implements. Code comments cite them as `REQ 1.x`.
7. [`docs/AZURE.md`](docs/AZURE.md): the web application, its review screens and its Azure architecture.

## Requirements

- Python 3.12 or later. CI tests on Windows with Python 3.12.
- No system packages. PyMuPDF and reportlab ship as wheels.
- Optional: an Anthropic API key. The run works without one (see [Running without an API key](#running-without-an-api-key)).

## Install

Every dependency is pinned in `requirements.txt`, so install takes one command.

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks the activation script, run `Set-ExecutionPolicy -Scope Process Bypass` first, or use `.venv\Scripts\activate.bat` from Command Prompt.

macOS or Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run every command below in a shell where the venv is active. If another Python comes first on your PATH (for example a Conda base environment), `python -m pmr` fails with `No module named pmr`. To check, run `python -c "import sys; print(sys.executable)"`: it should print a path inside `.venv`. Without activating, call the venv's interpreter directly: `.venv\Scripts\python -m pmr ...` on Windows or `.venv/bin/python -m pmr ...` on macOS and Linux.

## Generate a report

```
python -m pmr --client CPERS --quarter 4Q25
```

Client and quarter are the only required inputs.

- `--client`: the short code (`CPERS`) or the client's full name. The program resolves the code to the name from the prior PMR, not from a table in the code.
- `--quarter`: the reporting quarter, written like `4Q25`.

Optional flags:

| Flag | Default | Purpose |
|---|---|---|
| `--inputs PATH` | `inputs` | The input drop folder. Zip archives in it are unpacked first; see [Zip archives](#zip-archives). |
| `--out PATH` | `output` | Where to write results. |
| `--no-llm` | off | Use cached model answers only and make no API calls. |
| `--cache PATH` | `llm_cache` | The model answer cache. Point it at an empty folder for a cold run. |
| `--approve NAME` | | Approve the current draft under a reviewer's name. |
| `--finalize` | | Write the final PDF if the approved draft is unchanged. |

To run against another period or client, point `--inputs` at its folder:

```
python -m pmr --client CPERS --quarter 1Q26 --inputs D:\pmr\inputs_1Q26
```

The installed `pmr` command works the same way: `pmr --client CPERS --quarter 4Q25`.

### What the run prints

The terminal summary lists:

- each file in the inputs folder, marked use or skip with the reason
- the featured funds and their roles
- new and reversed commitments
- compliance results
- footing checks
- how the narrative was obtained (cached, live or unavailable)
- verification counts
- every warning and blocker

For CPERS 4Q25 it prints, in part:

```
Footing: 79 checks, 0 failed
Narrative (claude-opus-5-5): 13 cached, 0 live, 0 unavailable, 0 failed
Report: output\CPERS_4Q25\PMR_CPERS_4Q25_draft.pdf (13 pages)
  608 printed numbers checked, 0 without a source
  464 workbook citations re-read, 0 mismatched
```

A draft run exits with code 0 even when it finds blockers. It still writes the draft, so a reviewer can see what is wrong.

### Output files

Everything goes to `output/<CODE>_<QUARTER>/`:

| File | Contents |
|---|---|
| `PMR_<CODE>_<QUARTER>_draft.pdf` | The report, marked "DRAFT FOR REVIEW" on every page. |
| `review.html` | The reviewer page: warnings, blockers, every narrative sentence with its evidence, each chart value beside its slide image, and the source of every printed figure. Opens in any browser. |
| `review.json` | Reviewer corrections and the approval record. A run creates it once and never overwrites it. |
| `manifest.json` | Every printed number and statement, with its source locator (file, sheet and cell, PDF page, or slide). |
| `facts.json` | All computed facts, file decisions, issues and verification results. |
| `PMR_<CODE>_<QUARTER>.pdf` | The final report, written only by `--finalize`. |

## Web application

The browser front end does everything the command line does: upload a drop folder, pick the client and quarter, watch the run, inspect every figure beside its source cell or page, edit or exclude narrative, correct chart values, approve and finalize.

```
python -m pip install -r requirements-web.txt
python -m pmr_web
```

Open http://127.0.0.1:5001, sign in with your name, and choose **New report**. Upload a folder, individual files or a zip of the drop folder, or tick "Start from the sample drop folder" to load `inputs/`. See [`docs/AZURE.md`](docs/AZURE.md) for the screens, the review rules and the Azure deployment.

## Review and approve

A report is final only after a person approves it.

1. Run the draft and open `review.html` in a browser.
2. Fix anything wrong in `review.json`, then run the draft again:
   - `chart_value_corrections`: replace a value the model read from a chart image.
   - `excluded_sentences`: drop a narrative sentence.
   - `edited_sentences`: reword a narrative sentence (`"drafted text": "new text"`). The rewording may not add a number or name that the drafted sentence and its evidence lack.
3. Approve:
   ```
   python -m pmr --client CPERS --quarter 4Q25 --approve "Your Name"
   ```
   Approval is refused while any blocker is open. It records a hash of everything the report says.
4. Finalize:
   ```
   python -m pmr --client CPERS --quarter 4Q25 --finalize
   ```
   Finalization is refused unless the draft is approved and unchanged since approval. A changed input, figure, sentence or correction voids the approval. The final PDF is re-verified before it is written, and the program deletes it if verification fails.

Warnings vs blockers:

- **Warning:** the reviewer should look, but the report can still ship. Examples: a featured fund with no manager report, a chart value that failed a plausibility check, a narrative sentence dropped for weak evidence.
- **Blocker:** the report cannot ship. Examples: a printed number with no source, a figure that does not match its workbook cell, a footing failure, a compliance limit with no matching rule.

## Running without an API key

The program reads the key from `ANTHROPIC_API_KEY`. If that is unset, it reads a local `.env` file with a line `ANTHROPIC_API_KEY=...`. It never modifies the process environment, and `.env` is in `.gitignore`.

With no key:

- The run completes end to end and exits normally.
- Figures, fund selection, commitments, compliance, footing, charts from the workbooks and the appendix are all complete. They come from code, not the model.
- Narrative comes from `llm_cache/`, the committed answers from earlier runs. Re-running CPERS 4Q25 without a key reproduces the same report.
- For new inputs, nothing is cached. Manager commentary, strategy descriptions, the market update and chart values read from the deck images appear as marked placeholders, and the review page lists them.

## Model use, runtime and cost

- **Model:** Claude Opus 5.5 (`claude-opus-5-5`) at high effort. It does three jobs: reading chart images in the outlook deck, drafting fund commentary and strategy descriptions, and drafting the market update. It never computes a number.
- **Calls:** one per deck chart, one per featured fund with a manager report, one per new commitment, and one for the market update. CPERS 4Q25 needs 13 calls.
- **Cached run:** about 8 seconds on a laptop. Two runs on the same inputs produce byte-identical PDFs.
- **Live run:** each new answer is written to the cache, so a second run on the same inputs is cached and free. The terminal prints the run's input and output tokens, API time and estimated cost at the Opus 5.5 list price ($4 input, $20 output per million tokens). `facts.json` records the same under `narrative.llm.usage`, and each cache file stores its own call's usage.
- **Measured cold run** for CPERS 4Q25 (13 live calls, run in sequence): 29,760 input and 13,798 output tokens, about USD 0.40, and 123 seconds of API time. The market update is the largest call (about 10,500 input and 5,800 output tokens, 46 seconds). The whole run takes about two minutes, well under the 15-minute budget.
- **Cold run** (no cached answers), leaving the committed cache untouched:
  ```
  python -m pmr --client CPERS --quarter 4Q25 --cache cold_cache --out output_cold
  ```

## Tests

```
python -m pytest
```

There are 140 tests, and none of them call the API. They cover:

- golden values for 4Q25
- a flash workbook with every cell address shifted and an extra fund added (`tests/perturb.py`), which must still parse and foot
- two runs giving identical output
- reversed columns, renamed files, shuffled sheets and a renamed sleeve (`tests/test_stress.py`)
- an unseen next quarter, CPERS 1Q26, generated from the 4Q25 inputs (`tests/synthetic.py`; see below)
- a scan of `src/` for hardcoded fund names, client names, the benchmark, input filenames and guideline limits
- narrative source checks
- the approval and finalization gate
- an independent audit of the PDF against the raw inputs (`tests/audit/audit_report.py`; see [VERIFICATION.md](VERIFICATION.md))

### Synthetic next quarter

`tests/synthetic.py` builds a CPERS 1Q26 input folder from the 4Q25 package, as a stand-in for an unseen future period. This program's own 4Q25 report serves as the prior PMR, as it would in production. Each change tests one thing:

| Change | What it tests |
|---|---|
| Every fund rolls forward with new cash flows | Rankings, roles and featured funds are recomputed, not remembered |
| One fund winds down; the open 4Q25 commitment funds and gets a new row | Cell addresses, fund counts and sleeve totals all move |
| Apartment leads at 41.2% | A new leading sector is named and the breach is flagged |
| A second IC log with two new commitments, one under the client's full name, and a withdrawal | Approvals and reversals by quarter-end, across two logs |
| IC log traps: another client's approval of the same fund, an approval dated after quarter-end, a deferred item | Filtering by client, date and action |
| Synthetic manager reports for some featured funds, one with a legal suffix | Matching by content; a featured fund with no report is flagged |
| The 4Q25 flash, deck and manager reports stay in the folder | Stale files are skipped |

The generator computes every expected result with its own arithmetic, and `tests/test_synthetic.py` checks the pipeline against them. To build the folder and run it by hand:

```
python -m pmr --client CPERS --quarter 4Q25 --approve "Your Name"
python -m pmr --client CPERS --quarter 4Q25 --finalize
python tests/synthetic.py output/CPERS_4Q25/PMR_CPERS_4Q25.pdf synthetic_inputs
python -m pmr --client CPERS --quarter 1Q26 --inputs synthetic_inputs
```

CI (`.github/workflows/ci.yml`) runs the tests on Windows with Python 3.12, then runs the report with empty API keys to show the no-key path completes.

## Inputs

The program reads the inputs folder in place and never modifies it. It identifies files by content, not by name, so it skips:

- a flash for another client
- a flash with no data
- an archived prior PMR stub

The program reads `.xlsx` and `.xlsm` workbooks, `.pdf` documents and `.pptx` decks. It lists any other file type as skipped.

This section is the full list of unpacking rules. `DECISIONS.md` records why zips are accepted.

### Zip archives

A zip in the drop folder is unpacked, and each document inside it is read like any other input. The CLI and the web app follow the same rules:

- **Where files go:** members land under the archive's name, so `drop.zip` containing `flash_4Q25.xlsx` yields `drop/flash_4Q25.xlsx`. A zip whose top folder matches its name (`drop.zip` containing `drop/`) is not nested twice.
- **What is skipped:** macOS `__MACOSX` folders, hidden files, Excel lock files (`~$`), paths that climb out of the archive (`../`) and zips inside the zip. In the web app, member types the upload form does not accept are also skipped.
- **Limits:** an archive with more than 2,000 files or more than 1 GiB unpacked is refused, as is a corrupt one.

The CLI never unpacks into the drop folder. When it finds a zip, it copies the folder to a temporary directory, unpacks there, runs the report on the copy, and deletes the copy at the end. The terminal prints how many archives it unpacked and lists every member it skipped:

```
Unpacked 1 zip archive(s) into a staged copy of C:\pmr\inputs
  [skip] drop.zip/drop/old.zip: a nested zip, or an archive that could not be unpacked
```

The web app unpacks a zip when you upload it, on **New report** or on the **Inputs** tab. The Inputs tab lists the unpacked documents, not the zip, and the audit trail records an `archive_unpacked` event with the zip's SHA-256 and the counts of files saved and skipped.
