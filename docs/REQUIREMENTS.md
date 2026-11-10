# Requirements

The business rules this program implements, in brief. Code comments and docs cite them as `REQ 1.x`. [DECISIONS.md](../DECISIONS.md) records how each ambiguous rule was resolved.

## Domain terms

- **PMR (Performance Measurement Report):** the quarterly report an investment advisor sends each client about its real-estate portfolio.
- **Flash:** the quarterly data workbook. It is the source of truth for every portfolio number.
- **Sleeve:** a sub-portfolio. Every fund belongs to the Strategic or the Tactical sleeve, and the two roll up to the portfolio total.
- **IC log:** the firm-wide Investment Committee log of commitment decisions across all clients.
- **TWR / IRR:** time-weighted and internal rates of return. The flash already computes them; the program reads them, never derives them.
- **LTV (loan-to-value):** a fund's leverage.

## Rules

| REQ | Rule |
|---|---|
| 1.1 | Match the prior report's section structure, in order, from overview to the flash appendix. |
| 1.2 | Read the sleeve, portfolio-total and benchmark labels from the flash. |
| 1.3 | Rank funds by dollar contribution: income + appreciation - manager fees for the quarter. Capital flows are not performance. |
| 1.4 | Feature the two largest contributors, two largest detractors and two largest positions, by rank, never by a dollar threshold. If fewer than two funds lost money, say every other fund contributed positively. |
| 1.5 | Selection comes from the flash, not from which manager reports exist. A fund with several roles is written up once, with every role stated. |
| 1.6 | New commitments approved this quarter but not yet funded go in Recent Investment Activity, largest first, each with a 1 to 2 sentence strategy description. They count in the "committed or approved" totals and the position count. A previously reported approval that was withdrawn or lapsed gets one sentence. |
| 1.7 | Filter the IC log to this client, this quarter, the Commitment category and the `approved` action. The log is unsorted and split into one file per year. Unknown actions are flagged, not guessed. |
| 1.8 | Fund figures foot to sleeve totals and sleeves foot to the portfolio total. Nothing is estimated. |
| 1.9 | Reproduce the compliance table: largest sector share, leverage per sleeve (read pre-weighted, not averaged), fund-level Ex-US exposure (distinct from the look-through geographic figure), and 1-year net return versus the benchmark. |
| 1.10 | The market update condenses the outlook deck, including values read from its chart images. Market indices and the client's benchmark are different series and are never reconciled. |
| 1.11 | The appendix reproduces the flash. |
| 1.12 | The prior report is both the format template and the quarter-over-quarter baseline. |
| 1.13 | Software extracts every fact. No figure is typed into config, constants or prompts by hand. |
| 1.14 | Resolve name variants (legal suffixes, numerals, punctuation) with explicit, traceable, conservative rules. An ambiguous match goes to a reviewer, never merged silently. |
| 1.15 | Detect missing documents, keep drafting where the flash suffices, never invent narrative, and separate draft warnings from finalization blockers. |
| 1.16 | The LLM extracts and writes prose. Code computes, ranks, filters, reconciles and checks compliance. Every model claim from text is checked against its source; chart readings get a locator and human confirmation. |
| 1.17 | Every figure and factual claim carries a machine-readable source locator (file plus sheet and cell, page or slide) in a manifest. |
| 1.18 | Same inputs give the same figures, selection, roles, compliance and structure. A new period or client runs with no code changes. Nothing client- or quarter-specific lives in source code; tests may assert golden values. |
| 1.19 | The output matches the prior report's palette, typography and table styling. |
| 1.20 | Client code and quarter are the only inputs. The program finds its own files and skips files for other clients or quarters. |
| 1.21 | PDFs are read with docling, PyMuPDF or pdfplumber only. LLM calls go to the Anthropic or OpenAI API only. With no API key the run still completes, with model-written narrative marked unavailable. |
