# Write-up: CPERS 4Q25 PMR generator

Run instructions are in [README.md](README.md). File-by-file detail is in [ARCHITECTURE.md](ARCHITECTURE.md), and every judgment call is in [DECISIONS.md](DECISIONS.md).

## 1. What the model does and what code does

The rule: **code decides every fact, and the model only writes prose about facts that code has already fixed.**

| Job | Done by | Why |
|---|---|---|
| Find files, parse workbooks by label, foot totals | Code | A wrong number is the worst error a PMR can make. Parsing has one right answer, so code must find it. |
| Rank contributors, detractors and largest positions; pick featured funds | Code | Ranking is arithmetic on flash cells (REQ 1.3 to 1.5). |
| Compliance, commitments, IC-log approvals and reversals | Code | These rules are stated exactly in the requirements, and date logic is easy to get subtly wrong in prose. |
| House style: fonts, colors, table fills, section order | Code | Measured from the prior PMR's PDF on every run, so nothing is typed in. |
| Read values off the outlook deck's chart **images** | Model | No text source exists. Code can't read a bar chart without a vision model. |
| Fund commentary, strategy descriptions, market update | Model | Summarizing a manager letter in house voice is a language task. |

The model runs only after every figure from the source files is final, so it can't change one. The only numbers it supplies are chart values read from images, and the reviewer confirms those. It also must return each sentence with its evidence (a quoted line, a chart value ID or a report figure). Code then checks every sentence before it reaches the PDF:

- Every quote must appear word for word in the cited document or slide.
- Every number must appear in that evidence. The model may not compute, so a spread or change that isn't printed in a source gets the sentence dropped.
- Every capitalized name must appear in the sources.

A sentence that fails is dropped and listed for the reviewer. Nothing is rewritten silently. In 4Q25 every drafted sentence passed. In the live synthetic 1Q26 run the checks first dropped 5 accurate sentences that named a year not printed in their evidence. The checks now accept the reporting year and the years that cited chart labels stand for ("25Q4" supports "2025"), and 1 of the 5 is still dropped.

**Model:** Claude Opus 5.5 is requested for three jobs. If the API declines a request as unsafe, Anthropic's fallback model answers instead, and the cache records which model answered. A response cache keyed by a hash of model, prompt, sources and schema makes the prose repeatable. The 4Q25 cache is committed, so a re-run gives byte-identical PDFs with or without a key. A cold run made 13 calls and cost about USD 0.40 in 2 minutes. With no key and no cache hit, the run still completes: narrative sections show marked placeholders and raise a warning.

## 2. How I verify correctness and catch regressions

Each run checks itself in three ways. Any failure is a blocker.

1. **Footing.** Fund rows must sum to sleeve subtotals, and sleeves must sum to the portfolio total, within USD 1.00 or 0.01 percentage points.
2. **Manifest against PDF.** Every number printed in the PDF must exist in the manifest, and every manifest figure must be printed. The 4Q25 draft prints 608 numbers, and none lacks a source.
3. **Manifest against inputs.** Every figure that cites a workbook cell is re-read from that cell. In 4Q25 that is 464 cells, with 0 mismatches.

Regressions are caught by 113 tests that run without the API on Windows CI with Python 3.12:

- **Golden values** for 4Q25: featured funds and their roles, commitments, compliance statuses.
- **A perturbed flash** with every cell shifted and an extra fund added. It must still parse and foot, which proves nothing depends on cell positions.
- **A synthetic next quarter, CPERS 1Q26** (`tests/synthetic.py`, seed 42). It uses new cash flows, a fund wound down, a new fund, a compliance breach, a second IC log with traps, and **our own 4Q25 report as the prior PMR**. The generator works out the expected answers with its own arithmetic. It found three real bugs, all fixed: endnote markers broke the prior-PMR parser, a breach raised no warning, and one allocation sentence was wrong.
- **Determinism:** two runs on the same inputs must produce byte-identical PDFs (reportlab's invariant mode plus the cache).
- **Independent audit** (`tests/audit/audit_report.py`): re-reads the raw inputs without the program's parsers and checks the PDF's footing, roles, fund figures, overview figures and quotes. On 4Q25 it runs 151 checks with 0 failures. It found one real bug: a fund with a blank return cell printed "returned n/a net".

## 3. Where a human reviews, corrects and approves

Every draft writes `review.html`. It shows open issues, each chart image beside the values the model read from it, every model sentence with its evidence, and every dropped sentence with the reason. The reviewer acts through `review.json`. They can correct a chart value (the correction replaces the model's reading before the market update is drafted) or exclude a sentence, then run the draft again.

| | Warning | Blocker |
|---|---|---|
| Meaning | The report is correct, but a person should look | The report may be wrong or incomplete |
| Examples in 4Q25 | Meridian is featured but has no manager report; a chart has no bar for LA | (none) |
| Other examples | compliance breach, prior PMR not from the previous quarter, chart value outside its axis | footing failure, unsourced number, cell mismatch, unknown guideline, benchmark not found |
| Effect | Draft proceeds; reviewer sees it at the top of the page | `--approve` is refused until it is fixed |

Approval records a hash of everything the report says. If any input, figure, sentence or correction changes afterwards, approval is void and `--finalize` refuses. Finalizing re-renders and re-verifies the PDF, and a final PDF that fails verification is deleted. Drafts say "DRAFT FOR REVIEW" on every page; the final PDF doesn't.

## 4. Next quarter and format changes

**Next quarter, no code edits:** `python -m pmr --client CPERS --quarter 1Q26 --inputs <folder>`. Nothing in the code names a client, fund, file, row or count:

- Files are classified by **content**. A flash is any workbook with a `FundingStatus(Agg)` sheet, and its client and quarter come from its title rows. The prior PMR is the latest populated PMR for that client.
- Sleeves are found by **structure**: a label row with no numbers that reappears with numbers is a subtotal. Fund counts and positions are never assumed.
- The client code, benchmark, guideline limits and allowable range come from the prior PMR. A different client works the same way if its files follow the same conventions.
- Next quarter's prior PMR will be this program's own output, and the synthetic 1Q26 test runs exactly that case.

**Format changes:** the house style and the section layout are **measured** from the prior PMR on every run: fonts, sizes, colors, table fills, margins, heading levels, section order (from its contents page) and page breaks. If Corvane restyles a report, the next run follows the new prior PMR. A new section or a renamed guideline that code can't map raises a blocker (`section_unbuilt`, `guideline_unknown`) instead of being dropped silently. Adding a new kind of section needs code, and I chose that over guessing at content.

**Limits of this evidence.** I wrote the synthetic quarter and the perturbed flash alongside the pipeline, so they share its assumptions about what can change. `tests/test_stress.py` tries other changes: reversed columns, renamed files and shuffled sheets give the same report. A renamed sleeve stops with two blockers instead of a wrong report. An unseen layout outside what these tests cover could still fail, but it should fail as a blocker, not as a quietly wrong number, because every printed figure is re-checked against its source.

## 5. Brief notes

- **Architecture.** Five layers in order: facts, narrative, report, verification, review. Facts are fixed before the model runs, and the renderer prints only from facts and checked narrative. I chose reportlab because the prior PMR was built with it, so its charts and style can be matched, and its charts are vector, so the number check can read their labels.
- **Traceability.** `manifest.json` records every printed number and sentence with its exact locator (`flash_4Q25.xlsx#CashActivity(Agg)!F11`, `prior_pmr_3Q25.pdf#p10`), or a formula and the locators of its inputs. The client PDF carries no citation markers, matching the prior PMR. The reviewer page lists each printed figure with its source.
- **Ambiguity and entity resolution.** Fund names are normalized for case, punctuation, numerals and legal suffixes ("Fund III" = "Fund 3", ", L.P."). A manager report matches a fund only when the fund's name appears in its text, never its filename. An ambiguous match is flagged for the reviewer, not guessed. A featured fund with no report gets the prior PMR's own "not available" wording and no invented drivers. IC-log actions are judged as of quarter-end, so a withdrawal after quarter-end leaves the approval in this quarter's totals.
- **With more time,** I would: score the model's chart readings against a hand-labeled set and track accuracy, cost and latency per call; check numbers by position as well as by string, since a correct number in the wrong sentence passes today; and add a Word output.

## 6. Judgment calls and what I left out

- **Two IRRs.** The overview's since-inception IRR and multiple come from `Corvane Initiated Investments` (9.3%), since the overview describes "the funded investments". The client portfolio row (10.2%, 1.45x) also includes realized investments. The 3Q25 flash in the inputs is empty, so I can't confirm which one the prior report used.
- **1-year return:** `FundingStatus(Agg)` gives 5.457% and `ReturnsMultiples(Agg)` gives 5.456%. I use the first, per REQ 1.9. The gap comes from realized investments, not an error.
- **Exact dollars.** The prior PMR rounds fund dollar figures to thousands. I print them to the dollar ("$1,367,036") so each one reconciles to its flash cell. Switching to the prior PMR's rounding would need the number check to compare rounded values.
- **Separators.** The prior PMR uses em dashes in some labels. I print colons ("Northgate Logistics Partners: $40.0M"). Either is easy to switch if the house style calls for the dash.
- **Left out:** embedding `flash_4Q25.pdf` as the appendix. The prior PMR re-tables the flash in house style, so I do the same from the workbook. I also skipped OpenAI as a second provider, because one model behind a cache and source checks keeps the behavior easier to audit. The command line needs no container, because a pinned `requirements.txt` installs with one command on Windows; the Dockerfile packages only the web app.
