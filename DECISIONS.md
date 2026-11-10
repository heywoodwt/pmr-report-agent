# Decisions log

This program builds a client's quarterly portfolio management report (PMR) from a drop folder of source files. This log records the judgment calls made where the requirements ([docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)) are ambiguous, and the design choices behind them. [WRITEUP.md](WRITEUP.md) summarizes the main ones, [README.md](README.md) explains how to run the program, and [ARCHITECTURE.md](ARCHITECTURE.md) describes each module.

A **warning** lets the draft proceed and is shown to the reviewer. A **blocker** stops `--approve` until it is resolved (see README, "Warnings vs blockers").

## File discovery
- Files are identified by content, not filename: a flash is any workbook with a `FundingStatus(Agg)` sheet; its client and quarter come from the title rows. An IC log is any sheet with `Committee Action` and `Recommendation Category` headers.
- A flash that says "No data available" is skipped, even if the client and quarter match.
- The prior PMR is the latest populated PMR for the client from an earlier quarter. One-page stubs (`prior_pmr_2Q25.pdf` is "archived") are skipped. If the latest is not the immediately preceding quarter, the run proceeds with a warning.
- Bracketed endnote markers (`[n]`) are removed when a prior PMR is read. Next quarter's prior PMR will be a report this program generated, and earlier builds of it printed these markers. Without this, a limit reads as `<=40% per sector[105]` and the allowable allocation range is not found.
- `allocation_history.xlsx` has no client column. It is accepted, and its current-quarter NAV must match the flash total within USD 1MM, because the file states NAV in whole USD millions and rounding alone can differ by 0.5MM. A larger gap is a warning that the file may belong to another client.
- Zip archives in the drop folder are accepted and unpacked, although the requirements assume a plain `inputs/` folder, because preparers receive drop folders as zip files. The unpacking rules and limits are in README, [Zip archives](README.md#zip-archives).

## Client identity
- The short code is resolved from the prior PMR's definition `Full Name ("CODE")`, matched against client names that appear in populated flashes. No code-to-name table exists in the source.
- Client names are compared after removing a trailing " Portfolio" and normalizing whitespace and quotes, because the flash titles vary ("...System" vs "...System Portfolio").

## Flash parsing
- Sleeves are detected structurally: a label row with no numbers that reappears later with numbers (its subtotal). Every row between them is a fund. Sleeve names and fund counts are never assumed.
- Structural labels fixed by the file format (`Corvane Initiated Investments`, `Ex-US Portfolio`, sheet names and column headers) are constants. Client, benchmark, fund names and limits are always read from inputs.
- Blank cash-flow cells count as zero in sums (a blank Contribution means no contribution). Blank return cells stay blank (Tidewater has no quarterly TWR) and are never shown as 0.0%.
- Footing tolerance: USD 1.00 for dollar columns and 0.01 percentage points for percentage columns, because the flash rounds per-fund percentages to three decimal places.

## Benchmark
- The client's benchmark is the `AnnReturns(Agg)` row that the prior PMR names. The sheet also carries `NPI + 50 BPS`, which the prior PMR does not mention. Zero or several matches is a blocker.

## IC log and commitments
- Approvals and reversals are judged as of quarter-end. A withdrawal or lapse dated after quarter-end does not remove an approval from this quarter's totals. This mirrors REQ 1.6: an approval dated after quarter-end does not count.
- A reversal is paired with the most recent earlier, unreversed approval for the same canonical fund.
- An approval made and reversed inside the same quarter is neither a new commitment nor a note; the client was never told of it.
- An approval this quarter for a fund already in the flash is treated as funded and raises a warning.
- The client filter accepts the short code or the full name, since the log uses full names for some clients.

## Compliance
- Limits come from the prior PMR's Guideline/Limit/Actual/Status table. The leverage limits ("<=50% / 75%") are assigned to sleeves in the order the prior PMR's Actual column names them ("strategic / tactical").
- A guideline with no matching rule is a blocker, not silently dropped.
- A guideline breach is reported as found and raises a warning, so the reviewer sees it before sign-off. It is not a blocker: the report must be able to state a breach.
- The 1-year return objective uses the client portfolio row's 1-year TNET on `FundingStatus(Agg)` (5.457%), as REQ 1.9 requires. On `ReturnsMultiples(Agg)`, the `Corvane Initiated Investments` row shows 5.456% because it leaves out the `Realized Corvane Initiated Investments` row (USD 205,783 market value), which the portfolio row includes.

## Manager documents
- A document is matched to a fund when the fund's canonical name appears in the document text on word boundaries; filenames are never used. Canonical names come from the fixed rules in `entities.py` (punctuation, legal suffixes, Roman numerals, number words; see ARCHITECTURE.md). Ironwood's report names its fund only in the footer ("Ironwood Value-Add Fund 3, L.P."), which resolves through these rules.
- A near-miss or multiple match is flagged as ambiguous for the reviewer, never merged.
- Documents for funds with no role this quarter (Kestrel, Tidewater) are read, matched and skipped with that reason.
- A featured fund with no document (Meridian) is a draft-stage warning, not a blocker: the flash alone supplies its role and figures (REQ 1.15). The report says so in the prior PMR's own wording and adds no drivers.

## Report figures
- Since-inception IRR (9.3%) and equity multiple come from the `ReturnsMultiples(Agg)` `Corvane Initiated Investments` row, because the overview describes "the funded investments". The client portfolio row (10.2%, 1.45x) also includes realized investments. REQ 1.9 fixes only the 1-year return's source, so this row choice is open. The 3Q25 flash in the package has no data, so the prior report's choice cannot be confirmed from it.
- Facts that only the prior PMR states are carried forward with a locator to it: the mandate history sentence, the allowable allocation range, the benchmark description, the portfolio managers and the disclosures.
- The allocation exhibit runs up to the reporting quarter; rows for later quarters are left out.
- The reason for a withdrawal or lapse is quoted from the IC log (the text after the semicolon), never paraphrased.

## Rendering
- The house style (fonts, sizes, colors, table fills, rules, margins, footer, cover layout, chart palette) is measured from the prior PMR on every run, not typed into code. Section order, heading levels and page breaks come from its contents page and from where each heading sits.
- A prior PMR section that no builder maps to is a blocker (`section_unbuilt`), and the draft shows its heading with a placeholder. Adding a new kind of section needs code, which is safer than guessing at its content.
- The prior PMR was built with reportlab, including its charts, so the report uses reportlab too. Charts are vector drawings, so their labels are extractable text that the number check can read.
- The appendix re-tables the flash workbook in house style, as the prior PMR does, rather than embedding `flash_4Q25.pdf`. Blank flash cells print blank, not "$0".
- Separators use a colon where the prior PMR used an em dash (e.g. "Northgate Logistics Partners: $40.0M, approved October 2025"). This is a style preference and is easy to switch if the house style calls for the dash.
- PDFs are written in reportlab's invariant mode, so two runs on the same inputs give byte-identical files.
- Body text is justified, and emphasis (bold, italics, italic gray notes) follows the prior PMR's usage. ARCHITECTURE.md lists where each applies.
- Fund dollar figures print exactly, to the dollar ("$1,367,036"), not rounded to thousands as the prior PMR does. Exact figures reconcile to the flash cell by cell. WRITEUP.md §6 lists this as a deliberate departure.
- Page breaks follow the prior PMR. A page whose content runs up to 25% past one page is scaled down to fit it rather than spilling a few lines onto a mostly blank page, so text is never set below 80% of its measured size. Style is measured from the most common size of each text role, so a few scaled pages do not change the sizes the next quarter's run measures. Pages that end early because the next section starts a new page are kept, as in the prior PMR.

## Source citations
- The client PDF carries no citation markers and no Sources page, because the prior PMR has neither and markers next to figures ("12[3]") read as different numbers. Each figure's `file#Sheet!Cell` or `file#pN` locators are in `manifest.json` and in the Sources table on `review.html`.
- Citations come from the manifest's locators, not from `rag-citation`. That library infers sources from embedding similarity and named-entity matching, and it needs spaCy, sentence-transformers and PyTorch. The manifest already knows the exact cell for every number.
- A computed figure states its formula before its locators, e.g. "Sum: flash committed total + amounts of open IC approvals (Recommendation text)" with `...!C33`, `Log!G9` and `Log!G10`.

## Verification
- Every number printed in the PDF must appear in the manifest, every printed manifest figure must appear in the PDF, and every figure that cites a workbook cell is re-read from that cell. Any failure is a blocker.
- Limitation: the number check matches number strings, not positions, so a correct number printed in the wrong sentence would pass. The locator check and golden tests cover the values themselves.

## Model use
- Claude Opus 5.5 is the model requested, at high effort, for three jobs only: reading chart images, drafting manager commentary and strategy descriptions, and drafting the market update. Every figure, ranking, filter, compliance result and total from the workbooks, PDFs and IC logs is computed in code before the model runs, so the model cannot change one. The only numbers the model supplies are chart values read from images, and the reviewer confirms those (see Chart values).
- Opus 5.5 has no temperature setting. Repeatable prose comes from a response cache keyed by a hash of the model, prompt, source content and output schema. The cache for these inputs is committed (`llm_cache/`), so a re-run reproduces the same narrative with or without a key. A new period's inputs miss the cache and call the API.
- Server-side refusal fallback is enabled, so a false-positive safety decline is retried on Anthropic's recommended fallback model instead of failing. A fallback answer can therefore come from a model other than Opus 5.5; each cache file records the model that answered. Requests sent through Azure AI Foundry go without the fallback.
- The API key comes from `ANTHROPIC_API_KEY`, or from a git-ignored `.env` file for local use. With neither, the run completes and each narrative section without a cached answer shows a marked placeholder and raises a warning.
- A placeholder does not block approval. The reviewer decides whether a report with placeholder narrative can be finalized.

## Narrative checks
- Every drafted sentence must cite its evidence, and code checks it before it can be printed: quotes must appear verbatim in the cited document or slide; every number must appear in a cited quote, a cited chart value (including its series and category labels) or a cited report figure; every capitalized name must appear in the sources. Failing sentences are dropped and listed for the reviewer.
- The model may not compute: a sentence stating a spread, change or average that is not literally in its evidence is dropped. This is deliberately conservative.
- Two kinds of year are allowed without being printed in the evidence: the reporting period's own year ("the first quarter of 2026"), cited to the `FundingStatus(Agg)` title that states the period, and a year that a cited chart category stands for ("25Q4" supports "2025"). Any other year must be in the evidence. VERIFICATION.md records the effect on the 1Q26 and 4Q25 runs.
- A sentence's first word is exempt from the name check unless the next word is also capitalized, because grammar capitalizes it ("During", "Looking"). A single-word invented name at the start of a sentence could slip past this rule; the quote check and the reviewer page cover that case.
- The market update compares with the prior PMR's market update where a market indicator changed, and the change must be supported by a quote from it. In 4Q25 the office transaction cap rate rose to 7.7% from 6.3%, widening the gap the prior report said had narrowed.

## Chart values
- Values read from images have no source text, so each carries a slide/image locator and is shown on the reviewer page beside the image. Approval confirms them; a reviewer can correct a value in `review.json`, and the correction replaces the model's reading before the market update is drafted.
- Plausibility checks run in code: values within the printed axis range, and series on one chart sharing categories. A failure is a warning, not a blocker, because chart values are not required figures and all of them need confirmation. In 4Q25 the check flagged slide 10, where the population series has no bar for LA; the model reported no value rather than inventing one.

## Review and approval
- A reviewer can exclude a drafted sentence (`excluded_sentences`) or reword one (`edited_sentences`) in `review.json`. A rewording may not add a number or name that the sentence's evidence lacks, so an edit cannot introduce an unsourced figure.
- Approval is tied to content: it records a hash of everything the report says. Any later change to an input, figure, sentence or correction invalidates it, and finalization is refused until the new draft is approved.
- Approval is refused while any blocker is open. Finalization re-renders and re-verifies the PDF; a final PDF that fails verification is deleted, not shipped.
- Drafts carry "DRAFT FOR REVIEW" on every page; the final PDF does not.
- `review.json` is created once and never overwritten by a run, because it holds the reviewer's corrections and approval.

## Project layout
- Code lives in `src/pmr/`; the `inputs/` folder is never modified. It is read in place unless it contains a zip archive; then the CLI copies the folder to a temporary directory, unpacks the zips there, and reads the copy.
- openpyxl rather than polars for the workbooks: parsing locates cells by label one at a time and does not need a DataFrame.
- `python-pptx` reads the deck. REQ 1.21 restricts PDF libraries and model providers, not PowerPoint readers.
