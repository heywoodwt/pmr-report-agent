# What verifies the output is correct

The program checks its own output on every run. Tests and a person's approval sit on top of that. Figures below are from the CPERS 4Q25 run.

## 1. Checks inside every run

Any failure in steps 1 to 3 is a blocker. A blocker stops `--approve`.

1. **Footing.** Fund rows must sum to their sleeve subtotals, and sleeves must sum to the portfolio total. The tolerance is USD 1.00, or 0.01 percentage points for percentages. 4Q25 runs 79 checks with 0 failures.
2. **PDF against the manifest.** The verifier reads the finished PDF back and finds every number printed in it. Each number must have an entry in `manifest.json`, and each manifest figure must appear in the PDF. 4Q25 prints 608 numbers, and none is missing a source.
3. **Manifest against the inputs.** For every figure that cites a workbook cell (for example `flash_4Q25.xlsx#CashActivity(Agg)!F11`), the verifier opens the workbook and reads that cell again. 4Q25 has 464 citations with 0 mismatches.
4. **Narrative checks.** The model must return each sentence with its evidence, and code checks the evidence before the sentence can print:
   - each quote must appear word for word in the cited page or slide
   - each number must appear in that evidence (the model may not compute)
   - each capitalized name must appear in the sources
   - each year must be the reporting year or come from the cited source

   A sentence that fails is dropped and listed on the review page as a warning. Code never rewrites it.

   The year rule allows the reporting year and the years that cited chart labels stand for ("25Q4" supports "2025"). Before it, the live synthetic 1Q26 run dropped 5 sentences that named a year not printed in their evidence, including Harborview's main driver; 4 of the 5 now pass. In 4Q25 it lets the market update keep one more sentence (NPI cap rate 4.6% and 10-year Treasury 4.2% at 25Q4), checked against slide 5.

## 2. The human gate

- `review.html` shows each warning, each model sentence beside its evidence, each chart value beside its slide image, and the cells, pages or slides behind every printed figure.
- `--approve` records a hash of everything the report says. Any later change to an input, figure, sentence or correction voids the approval.
- `--finalize` renders and verifies the report again. If the final PDF fails, the program deletes it.

## 3. Tests and CI

There are 113 tests, and none of them calls the API:

- **Golden values:** the 4Q25 featured funds and their roles, commitments and compliance results.
- **Perturbed flash:** every cell address is shifted and an extra fund is added (`tests/perturb.py`). The workbook must still parse and foot.
- **Synthetic 1Q26:** `tests/synthetic.py` builds a next quarter and works out the expected answers with its own arithmetic. The pipeline must match them.
- **Stress cases:** reversed columns, renamed files and shuffled sheets give the same report; a renamed sleeve stops with blockers (`tests/test_stress.py`).
- **Determinism:** two runs on the same inputs must produce byte-identical PDFs.
- **Hardcoding scan:** `src/` must not name a fund, client, file or limit.
- **Independent audit:** the audit in section 4 must pass on the rendered report and must fail when a quote is altered (`tests/test_audit.py`).

On Windows with Python 3.12, `.github/workflows/ci.yml` runs these tests and then a run with no key. `.github/workflows/demo.yml` builds the report on two fresh Windows machines and checks that the two PDFs are byte-identical.

## 4. Independent audit

`tests/audit/audit_report.py` checks the finished PDF against the raw inputs. It does not import the program: it reads the flash with openpyxl, the manager reports and prior PMR with PyMuPDF, and the deck with python-pptx. It takes only the list of narrative sentences and their quotes from `manifest.json`; every value it compares comes from the inputs.

```
python -I tests/audit/audit_report.py --inputs inputs --pdf examples/PMR_CPERS_4Q25.pdf --manifest output/CPERS_4Q25/manifest.json
```

It checks:

- **Footing:** every money column of `CashActivity(Agg)`, fund rows to sleeve subtotals and sleeves to the portfolio row.
- **Roles:** the featured funds and their roles, re-ranked from the cash activity cells by REQ 1.3 to 1.5.
- **Fund figures:** each fund's quarterly net return, market value and LTV, and each featured fund's income, appreciation and contribution to the dollar.
- **Overview figures:** allocation share, NAV, NAV as a share of the plan and of the target, and the quarterly and one-year returns.
- **Narrative:** each of the 27 sentences prints in the PDF, and each of its 34 evidence quotes appears word for word on the page or slide it cites.

On the 4Q25 report it runs 151 checks with 0 failures. Its first run found one real bug: Tidewater Distressed Realty Fund has a blank quarterly return cell, and the report printed "returned n/a net". It now prints "reported no quarterly return in the flash".

The audit does not cover IC-log commitments, compliance results, charts or chart values read from images.

## 5. What nothing checks automatically

- **Chart values read from images.** No text source exists for them, so only the reviewer can confirm them against the slide images on the review page. The 6 estimated values in 4Q25 were checked by eye against slides 6 and 9.
- **Numbers in the wrong place.** The number check works on strings, not positions. A correct number in the wrong sentence would pass.
- **Meaning.** The checks confirm that each number and quote is real. They don't confirm that a sentence describes it fairly. The reviewer does that.
