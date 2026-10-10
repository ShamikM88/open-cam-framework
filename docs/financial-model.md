# Financial model and policy engine

> The sections before "Module reference" are written for readers; "Module reference" and what follows were moved from `CLAUDE.md` or the README, unchanged apart from the fixes in the [move ledger](move-ledger.md).

The figures a CAM reports, how the framework decides whether a covenant passes, and what a draft must contain, explained from first principles with worked numbers. Why it is built this way: [design decisions](decisions.md) D2, D4, D5 and D6.

## Terms and figures

This page explains the figures a CAM reports and how the framework decides what a draft must say about them. It
assumes no credit background. Figures are in whatever currency units you supply (the framework labels no currency);
the examples use the synthetic deal from [Workflows](workflows.md), in "synthetic units". The definitions here
describe `scripts/spreading_builder.py`, which is authoritative.

### Statement figures

Each is computed from the raw line items you supply for a period (`revenue`, `cost_of_sales`, and so on; the field
list is in the [command reference](commands.md#spread)). A missing line item counts as 0; it is never invented.

| Term | How it is computed | Plain meaning |
| :--- | :--- | :--- |
| Gross profit | revenue - cost of sales | What is left after the direct cost of what was sold |
| Operating profit (EBIT) | gross profit - admin expenses - depreciation - amortisation + other income | Profit from running the business, before interest and tax |
| EBITDA | operating profit + depreciation + amortisation | Operating profit before the non-cash charges; the usual measure of cash earnings |
| Profit before tax | operating profit - interest paid + interest received - exceptional costs | |
| Net profit | profit before tax - tax paid | |
| Free cash flow (FCF) | EBITDA - capex - tax paid - interest paid + interest received | Cash generated after investment, tax and net interest. Scheduled principal repayments are **not** deducted: they are financing, and DSCR already covers them |
| Current assets / liabilities | assets: cash + trade debtors + stock + other current assets. Liabilities: trade creditors + current debt + overdraft + other current liabilities | What is expected to turn into cash, or fall due, within a year |
| Total debt | current debt + overdraft + long-term debt + loan notes | Interest-bearing borrowing. Provisions and trade creditors are deliberately excluded |
| Total equity | share capital + retained profit | The owners' stake |
| Tangible net worth (TNW) | total equity - intangible assets | Net worth that does not rely on intangibles such as goodwill |

### Ratios

| Ratio (state key) | Formula | Reading |
| :--- | :--- | :--- |
| DSCR, debt service cover (`dscr`) | EBITDA / (interest paid + scheduled principal) | Times earnings cover the year's debt service. Above 1 means earnings cover it; a minimum covenant is typically set above 1 (here 1.25) |
| Gross leverage (`gross_leverage`) | total debt / EBITDA | Years of EBITDA needed to repay the debt; lower is stronger |
| Net debt / EBITDA (`net_debt_to_ebitda`) | (total debt - cash) / EBITDA | Leverage after counting cash. Negative when cash exceeds debt (net cash) |
| Gearing (`gearing`) | total debt / total equity | Debt relative to the owners' stake; stored as a fraction, so 0.87 reads as 87% |
| Current ratio (`current_ratio`) | current assets / current liabilities | Short-term liquidity; above 1 means current assets cover current liabilities |
| EBIT interest cover (`ebit_interest_cover`, alias `EBIT/Interest`) | operating profit / interest paid | Times profit covers interest |
| EBITDA interest cover (`ebitda_interest_cover`, alias `EBITDA/Interest`) | EBITDA / interest paid | The same on EBITDA |
| FCF conversion (`fcf_conversion_pct`) | FCF / EBITDA | The share of EBITDA that becomes free cash; stored as a fraction (0.35 is 35%) |
| Trade debtor days (`trade_debtor_days`) | trade debtors / revenue x 365 | How long customers take to pay |
| Stock days (`stock_days`) | stock / cost of sales x 365 | How long stock sits |
| Trade creditor days (`trade_creditor_days`) | trade creditors / cost of sales x 365 | How long the company takes to pay suppliers |
| Working capital cycle (`working_capital_cycle_days`) | debtor days + stock days - creditor days | Days of operating cash tied up |

## Worked example

`FY-Current` of the synthetic deal ([`financials_input.json`](examples/synthetic_co/financials_input.json)):
revenue 5000, cost of sales 3000, admin expenses 1000, depreciation 300, interest paid 150, scheduled principal 350,
capex 400, tax paid 100, cash 250, trade debtors 600, stock 400, trade creditors 450, other current liabilities 100,
current debt 350, long-term debt 1650, intangible assets 100, share capital 500, retained profit 1800.

- Gross profit = 5000 - 3000 = **2000**. Operating profit = 2000 - 1000 - 300 = **700**. EBITDA = 700 + 300 = **1000**.
- Profit before tax = 700 - 150 = 550; net profit = 550 - 100 = 450. FCF = 1000 - 400 - 100 - 150 = **350**.
- Total debt = 350 + 1650 = **2000**. Total equity = 500 + 1800 = 2300. TNW = 2300 - 100 = **2200**.
- Current assets = 250 + 600 + 400 = 1250; current liabilities = 450 + 350 + 100 = 900.
- **DSCR** = 1000 / (150 + 350) = **2.0**. **Gross leverage** = 2000 / 1000 = **2.0**.
  **Net debt / EBITDA** = (2000 - 250) / 1000 = **1.75**. **Current ratio** = 1250 / 900 = **1.3889**.
  **Gearing** = 2000 / 2300 = **0.8696**. **EBIT interest cover** = 700 / 150 = 4.6667. **FCF conversion** = 350 / 1000 = **0.35**.
- Debtor days = 600 / 5000 x 365 = **43.8**; creditor days = 450 / 3000 x 365 = **54.75**; stock days = 400 / 3000 x 365 =
  48.6667; working capital cycle = 43.8 + 48.6667 - 54.75 = **37.7167**.

`spreading_check.py` produces exactly these values, and the workbook's formulas produce them too.

## N/A and UNRESOLVABLE

A ratio is only meaningful when its **denominator is positive**. If the denominator is zero or negative the ratio is
**N/A**: `null` in `state.json`, the text `N/A` in the workbook. The denominators are EBITDA (gross leverage, net debt
/ EBITDA, FCF conversion, EBITDA interest cover), interest paid plus scheduled principal (DSCR), total equity
(gearing), interest paid (EBIT interest cover), current liabilities (current ratio), revenue (debtor days) and cost of
sales (stock and creditor days).

Why: dividing by a negative EBITDA gives a finite *negative* leverage, which sits below every maximum covenant
threshold, so a loss-making borrower would appear to pass "leverage must not exceed 3.5x". Zero is genuinely undefined
(no interest expense is infinite cover, not zero cover). Only the denominator is tested: a negative **numerator** over
a positive denominator is a real number and is kept (an EBITDA of -100 over debt service of 490 gives a DSCR of
-0.204, a real shortfall that fails a minimum-DSCR covenant; net cash gives a negative net debt / EBITDA).

A covenant on an N/A ratio is **UNRESOLVABLE**: it cannot be tested. That is neither a pass nor a fail, and it is the
same for a minimum and a maximum covenant. A covenant is also UNRESOLVABLE if its type is not `minimum`/`maximum`, its
threshold is missing or not a number, or its metric is not one of the computed ratios. Every covenant result carries
a `reason`, empty for a pass or fail and one sentence for UNRESOLVABLE, for example `gross leverage not meaningful:
EBITDA is negative`. If a stored ratio sits next to a recorded denominator that is zero or negative (an older
checkpoint, or an analyst-supplied ratio), the covenant is still UNRESOLVABLE and the stored number is ignored.

## Covenants

A covenant is a condition in the facility: a **minimum** (the ratio must be at least the threshold) or a **maximum**
(it must be at most the threshold), recorded as `{"metric": "dscr", "type": "minimum", "threshold": 1.25}`. Equality
passes. Results: `PASS`, `FAIL` or `UNRESOLVABLE`, with `actual` and a **headroom** as a fraction of the threshold:
for a minimum `(actual - threshold) / threshold`, for a maximum `(threshold - actual) / threshold`. In the example,
DSCR 2.0 against a minimum of 1.25 has headroom 0.6; gross leverage 2.0 against a maximum of 3.5 has 0.4286. A
threshold of exactly 0 has no percentage headroom (it is `null`), but the status is still decided by comparison.

Covenants are tested in three places, with three different consequences:

| Where | Which period | A FAIL or UNRESOLVABLE... |
| :--- | :--- | :--- |
| `covenant_results` | `FY-Current` | **rejects** the draft by code until the figures or the covenant record change |
| `downside_covenant_breaches` | each forward year, **only** where the base case passes and the stressed case fails or cannot be tested | **rejects** the draft unless it addresses the breach by id (`downside_breaches_acknowledged`) |
| `forward_covenant_results` | each projected forward year's own base case | **never rejects**; it is recorded, and the non-passing entries are shown to the model as plain data |

A forward year with no projection recorded is not tested, and the state carries no covenant tenor, so a covenant that
really ends earlier is still reported for later projected years.

## The forward base case

`FY+1`, `FY+2` and `FY+3` are management's own forecast: raw figures you supply, in the same shape as a historical
year, computed by the same formulas (or, in the analyst-supplied mode, the analyst's own figures recorded as given: see
[Analyst-supplied forecasts](#analyst-supplied-forecasts-and-their-downside)). Nothing is extrapolated. In the example, `FY+1` has EBITDA 550 and total debt
1650 (DSCR **1.1**, gross leverage **3.0**), and `FY+2` has EBITDA -100, so its gross leverage is N/A and its DSCR
is -0.204. Against the covenants (minimum DSCR 1.25, maximum leverage 3.5): `FY+1` DSCR fails, `FY+1` leverage
passes, `FY+2` DSCR fails and `FY+2` leverage is UNRESOLVABLE. All four are disclosed in `forward_covenant_results`
(ids `FORWARD-FY-1-DSCR`, `FORWARD-FY-1-GROSS-LEVERAGE`, `FORWARD-FY-2-DSCR`, `FORWARD-FY-2-GROSS-LEVERAGE`) without
rejecting the draft.

## The downside case

A downside case is a forward year re-run under three deterministic shocks, all optional (each defaults to no shock):

- `shocked_revenue = revenue x (1 - revenue_haircut_pct / 100)`
- `shocked_admin_expenses = admin_expenses x (1 + opex_increase_pct / 100)` (admin expenses only, never cost of sales)
- `shocked_interest_paid = interest_paid + total_debt x interest_rate_bump_bps / 10000`

Every other raw figure carries through unchanged. For the example's `FY+1` with a 5% revenue haircut and a 200 basis
point interest rise: revenue 4600 becomes 4370, interest 150 becomes 150 + 1650 x 200 / 10000 = 183, EBITDA falls from
550 to **320**, DSCR falls from 1.1 to **0.600** and gross leverage rises from 3.0 to **5.15625**, which breaches the
3.5 maximum. Because the base case passed, that is a downside breach (`DOWNSIDE-FY-1-GROSS-LEVERAGE`) and the draft
must address it. The `FY+1` DSCR already failed in the base case, so it is reported once, as a forward result, not
again as a downside breach.

## Analyst-supplied figures

By default every subtotal and ratio is recomputed from raw line items. An institution may instead supply its own
already-spread figures (its template may treat a line differently, for example depreciation inside cost of goods
sold). The framework then records them **exactly as given**, sets `financials_source` to `"analyst-supplied"`, and the
CAM must carry an explicit caveat that those figures were not independently recomputed. It never reconciles them to
its own schema. One limit: a stored ratio whose denominator is not recorded (an analyst-supplied DSCR, say) cannot be
cross-checked, so the N/A rule can only be applied where the denominator is on file.

## Analyst-supplied forecasts and their downside

`/project` has the same opt-in alternative as `/spread` (issue #124): when an institution's forecast convention does not
fit the framework's raw schema, the analyst supplies already-computed forward-year subtotals and ratios and
`scripts/supplied_forecast.py` records them **exactly as given** (`financials[FY+n]`, `ratios[FY+n]`, and an optional
raw breakdown in `analyst_supplied_financials`). They are never passed through the framework's formulas to produce a
recorded value. The default, framework-computed mode is unchanged.

**Labelling.** `forecast_source: "analyst-supplied"` says the forward years were supplied (absent means
framework-computed, which is every deal that existed before this mode), and the deal-wide `financials_source` is
`"analyst-supplied"` so the CAM must carry the caveat; the analyst's description of their forecast convention is added
to `financials_source_note`, which that caveat quotes. **One basis:** a deal whose forward years were computed from raw
lines, or whose historical years were computed by the framework (or have no recorded basis), is refused rather than
blended, because one deal-wide flag and one set of workbook raw inputs cannot describe two bases; and once an
analyst-supplied forecast is on file, the framework-computed path refuses to overwrite its forward years or relabel the
deal.

**The downside, decided conservatively.** The three stress shocks are defined on the framework's raw field names and an
interest-bearing-debt aggregate, so they cannot be assumed to mean anything for an analyst's own forecast. For each
forward year:

1. If the analyst supplied **their own stressed forecast** for it (subtotals and/or ratios plus a description of the
   scenario), that is recorded as `analyst-supplied` and is what covenants are tested against under stress.
2. Otherwise, if stress shocks were requested, they are applied **only if they genuinely meet their assumptions**: the
   year's raw lines were supplied; every line a requested shock acts on is present; every line the covenant ratios read
   is present (the calculation reads an omitted line as zero, and an omitted line is not a zero the analyst confirmed,
   so each is given, as an explicit `0` where it is nil); and the framework's formulas *reproduce the subtotals and
   ratios the analyst gave for that year* to two decimal places. Then the downside is derived by the existing
   calculation, labelled `framework-derived from reconciled analyst-supplied lines`, and holds only the figures every
   one of whose lines was supplied (no profit before tax unless exceptional costs were given, no total assets unless
   the fixed-asset lines were). A year that fails any of this is not stressed with the framework's shocks.
3. Otherwise, if a downside was wanted at all (shocks requested, or an own scenario given for another year), the year is
   recorded under `downside_case["unavailable"]` with the reason. The policy engine then treats any covenant that passes
   in that year's base case as **UNRESOLVABLE under stress**, exactly as it already does when a stress drives a ratio to
   N/A: a `downside_covenant_breaches` entry with `downside_status` UNRESOLVABLE, `downside_actual` `null` and a reason
   beginning `downside analysis unavailable for FY+n:`. The draft must address it (the existing "Undisclosed Downside
   Breach" rejection), so the absence of a downside is disclosed, never silent and never a pass.
4. If neither shocks nor a scenario were supplied, no downside was asked for and none is recorded, as for any deal.

`downside_case` keeps its `financials` and `ratios`, and for these deals also carries `basis` (which source each year's
downside came from), `unavailable` and the scenario's `description`. There is one `description` per case and it
describes every analyst-supplied year in it: supplying a scenario with a different description while earlier
analyst-supplied years are not restated is refused (nothing is written), and restating them all with the new
description is accepted. The headless `orchestrator.py` can neither recompute nor originate such a downside, so it
keeps the recorded stress assumptions and refuses a `--stress-assumptions` that differs from them (repeating them is a
no-op); change them by re-running `/project`'s analyst-supplied mode. In the workbook, supplied forward and downside
columns show the supplied values, unavailable downside columns are blank, and each is labelled
([outputs](outputs.md#the-spreading-workbook-xlsx)). A worked example is in
[Workflows](workflows.md#forward-years-supplied-by-the-analyst).

## Figures read from an image

Figures a session reads out of a screenshot, a photo or a scanned page go through `scripts/transcription_check.py`
before they are recorded (issue #132; the procedure is in [Workflows](workflows.md#figures-read-from-an-image)). The
rules it applies are described here.

**Signs: what the source writes versus what the framework is given.** The framework reads raw lines with fixed
semantics (`evaluate_financial_model()`): costs and cash outflows are entered as **positive** amounts and subtracted,
liabilities are entered as positive amounts, and a negative cost is a credit or reversal. A source often presents them
differently: costs or creditors in brackets, which is only presentation. So each figure is kept exactly as written
and a separate value is recorded:

- The staged file declares, per class of line, how the source writes an amount of that kind: `cost_sign` (cost of
  sales, admin expenses, depreciation, amortisation, interest paid, exceptional costs, tax), `outflow_sign` (scheduled
  principal, capex) and `liability_sign` (every liability line). `"positive"`: written as a positive number, so
  recorded as written, and a negative is a credit or reversal. `"negative"`: written in brackets or with a minus sign,
  so recorded with its sign reversed, and a positive is a credit or reversal. A declaration is required wherever a line
  of its class is transcribed; it is never inferred, and if the image does not make it plain the analyst is asked.
- Income, assets and equity lines are recorded as written. Subtotals and ratios are results with a sign of their own
  (a loss is `(120)`) and are recorded as written.
- The read-back shows both values, the interpretation of each line ("as written" or "sign reversed: ...") and each
  convention in words, and the digest covers the declarations, so changing one is a new confirmation. It lists every
  figure that will be recorded as negative so a genuine credit or deficit is seen, not assumed, and warns when every
  line of a class contradicts its declaration.
- The cross-foot uses the **recorded** values, so it checks what the framework will actually be given. If a mismatch
  would disappear were a class read with the opposite sign, it is reported as a doubt about the declaration
  (`matches if costs were read with the opposite sign`) and **cannot be acknowledged**: it blocks until the declaration
  or the figures are corrected. For example, revenue `1,200`, cost of sales `(700)` and a stated gross profit of `500`
  mismatch if costs are declared positive (`-700` is recorded, gross profit `1,900`) and match if they are declared
  negative (`700`).

**What is compared.** For every subtotal the source itself states (`gross_profit`, `operating_profit`, `ebitda`,
`profit_before_tax`, `net_profit`, `fcf`, `current_assets`, `current_liabilities`, `total_assets`, `total_liabilities`,
`total_equity`, `total_debt`, `tangible_net_worth`), the stated figure is compared with the same subtotal derived from
the recorded raw lines by `evaluate_financial_model()`, the framework's own formulas. Which lines feed a subtotal, and
with which sign, is discovered from that function (a line's weight is the subtotal's value when that line alone is
1), so the mapping is not written down a second time and cannot drift from the formulas. A test confirms the weights
reproduce the framework's subtotals exactly. In addition, when balance sheet lines are present, total assets less total
liabilities less total equity must come to zero (the `balance_sheet_balances` check).

**Rounding.** A presented statement is rounded, so its parts rarely add up exactly. The tolerance is the rounding the
figures as written allow: half a unit of the last written digit of the stated subtotal plus half a unit of the last
written digit of each line that feeds it, weighted by its sign. Lines shown to one decimal and a total shown to none:
`100.4 + 200.4 = 300.8` against a stated `301`, with two zero lines written as `0`, tolerates `0.5 x (0.1 + 0.1 + 1 +
1 + 1) = 1.6`. The same amounts written to two decimals tolerate only `0.025`. Digits count as written, which is why
the transcription keeps trailing zeros.

**When no check is made.** A check needs every feeding line to have been transcribed; an explicit `0` counts, an
omitted line is unknown, not zero (the framework otherwise reads an omitted line as 0, which is the wrong default for
a check). A check that cannot be made is reported as NOT ASSESSED with the missing lines, never as a pass. Ratios are
never cross-footed: the rounding of their inputs cannot be bounded, so any tolerance would be invented. If the image
states no subtotal and no balance sheet, the read-back says there is nothing to compare.

**A discrepancy.** It is shown with the stated figure, the sum of the lines, the difference and the tolerance, and
the commit is refused until it is resolved. Resolving means correcting the transcription (or the sign declaration)
where the image shows it is wrong, or, for a difference no sign reading explains, recording the analyst's reason in
their words as an acknowledgement for that period and check (a source can define a subtotal differently, for example
an equity line the raw schema has no field for). An acknowledgement is part of what the analyst confirms. Nothing is
ever amended on its own.

**By mode.** In framework-computed mode the stated subtotals are used only for the cross-foot; every recorded
subtotal and ratio is recomputed from the lines. In analyst-supplied mode the stated subtotals and ratios are recorded
exactly as given (a trailing `x` or `%` on a ratio is dropped and the number kept as written), any raw lines go to
`analyst_supplied_financials`, and the cross-foot still compares the stated subtotals with those lines when both are
present. Stated differences between an institution's subtotal and the framework's definition are exactly what an
acknowledgement is for in either mode.

**One basis per deal.** `financials_source` is a whole-deal flag, and a commit never changes it or mixes the two
modes in one deal. Before the image is copied or anything is written, an existing `financials_source` that differs
from the staged mode refuses the commit with one message. A state that records financial figures but no
`financials_source` (an older or hand-edited file) has an unknown basis and is refused the same way; it is not assumed
to be either mode. A deal with no figures yet, or one already in the same mode (a second screenshot for another
period), is accepted.

**Checked before anything is saved.** A refused commit leaves no copied image, manifest entry or state change, so everything about the deal that can be known in advance is checked first, with `state_manager`'s own logic rather than a second copy of it: the state is readable and each key this mode reads and rewrites has the right shape (in framework-computed mode that includes `spreading_check`'s keys, such as `stress_assumptions`); it is not recorded as a newer schema version than this checkout supports, which `write_state()` would refuse (a version that cannot be compared is refused too); `financials_transcriptions` is a list; `sources/manifest.json`, if present, is a readable list; and in framework-computed mode the computation itself, run without writing (`spreading_check.plan_fields()`, the same merge, formulas and downside case `compute()` uses), succeeds against what the deal already holds. What no preflight can know is a failure at write time: a full disk, a lock that times out, or another session changing the state in between.

**One state update.** The figures, the provenance record and the completed `spread` step are written together in a single state update, in both modes: in framework-computed mode the figures are planned with `spreading_check.plan_fields()` (the same merge, formulas and downside case `compute()` uses) and written with the record, never by a separate earlier write. The deal is looked at once more immediately before that update, and planned from that fresh read; if it can no longer take the transcription (another basis, a newer schema) the commit is refused. The image and its manifest entry, however, are saved **before** the state update, and the two cannot be committed atomically: if the update fails, or the deal changed in between, the verified image and its manifest entry remain with no figures, record or step recorded. A failed update (an `OSError` such as a lock timeout, or a `state_manager` error) is reported as one error line that names the saved file, says its `sources/manifest.json` entry remains and is not removed automatically, and tells the operator to inspect `state.json` before retrying; the original exception is kept as its cause. `state_manager` serialises writes but not a read-plan-write sequence, so another session changing the same keys in the instant between that last look and the write is not detected; the ordinary `spreading_check` path has the same limit.

**The image.** The read-back shows the SHA-256 fingerprint of the image file and the digest covers it, so replacing
the file after the read-back makes the confirmation stale. At commit the file is copied aside and the copy checked
against the confirmed fingerprint before it becomes the saved source, and the saved copy is checked again; the state
record keeps the fingerprint and never the image.

**Limits.** The check finds transcription slips that break a subtotal or the balance sheet. It cannot find an error
in a figure no subtotal depends on, two offsetting errors, a figure misread the same way in a line and in its total,
or a sign convention declared wrongly where the source states no subtotal to test it against (the notices and the
analyst's reading of the read-back are then all that stand between the declaration and the record). A source that
mixes conventions within one class of line cannot be expressed and has to be corrected with the analyst. It reads
commas as thousands separators and a point as the decimal mark, refuses anything else (`1,5`), and cannot tell `1,234`
in a European statement from one thousand two hundred thirty-four; the unit and the read-back exist for the analyst to
catch exactly that.

## What the policy engine decides

From the recorded structure alone (no model), `policy_state` holds:

- **Conditions precedent (CPs)**, things that must happen before drawdown, each with a stable `cp_id`. `KYC-AML` and
  `FACILITY-EXECUTION` are always required. A charge that is not exactly `"Perfected"` adds `SEC-PERFECT-<asset>`; one
  that does not rank exactly `"First"` adds `SEC-PRIORITY-<asset>`; each guarantee adds `GUARANTEE-<provider>`.
- **Conditions subsequent (CSs)**, ongoing obligations after drawdown, each with a `cs_id`: `MI-REPORTING` always,
  `CS-COVENANT-COMPLIANCE` when there are covenants, `CS-SEC-REPERFECT-<asset>` for each charge, and
  `CS-GUARANTEE-<provider>` for each guarantee.
- **Security gaps**: an uncharged asset, an unperfected charge, or a subordinate ranking.
- **Covenant results, downside breaches and forward covenant results** as above.

A draft must carry every required CP and CS, every downside breach, and a source for its narrative, state only
figures that match the computed ones, and cover the risk taxonomy; `policy_checks.py` checks all of that and the
reasons are listed in [Troubleshooting](troubleshooting.md#review-and-rejection). A passing check shows the draft is
consistent with the computed facts and complete against the deal's own structure. It does not show the narrative is
sound; that is the Risk Reviewer's and a person's job.

## Module reference

*The sections from here on are the module-level reference, moved from `CLAUDE.md` ([ledger](move-ledger.md)).*

## Policy engine

**`scripts/policy_engine.py`** — no `anthropic` dependency. `evaluate_deal_policy(state_dict)`
is the deterministic governance core: covenant PASS/FAIL/UNRESOLVABLE evaluation against
`covenants`, security perfection/ranking gap detection against `security_package`, Condition
Precedent (`cp_id`) and Condition Subsequent (`cs_id`) generation from the deal's own structure
(KYC/AML and facility-execution CPs always required; security-mapping/perfection/priority CPs
and guarantee CPs only when the underlying gap/guarantee actually exists), and downside covenant
breach detection (a covenant that PASSes in the base case but FAILs, or becomes UNRESOLVABLE, under stress).
Returns `policy_state`: `{"required_conditions_precedent", "required_conditions_subsequent",
"covenant_results", "security_gaps", "downside_covenant_breaches", "forward_covenant_results"}`. **A ratio with a zero or negative
denominator is N/A, and its covenant is UNRESOLVABLE (issue #169).** `spreading_builder` returns `None` for any
ratio whose denominator (EBITDA, total equity, interest paid, debt service, current liabilities, revenue, cost of
sales) is not positive -- before this, negative EBITDA or equity gave a finite negative leverage/gearing that sat
below every maximum threshold, so a loss-making borrower PASSED "leverage <= 3.5x". Only the denominator is tested
(a negative numerator over a positive denominator, e.g. DSCR below zero, is a real figure and a minimum-DSCR
covenant correctly FAILs on it). A covenant on an N/A ratio is UNRESOLVABLE for `minimum` and `maximum` alike --
never PASS, never a false FAIL -- and every `covenant_results` entry now has a `reason` (None when resolved; for
an N/A ratio one sentence naming the metric and denominator, e.g. "gross leverage not meaningful: EBITDA is
negative" / "gearing not defined: total equity is zero"; `spreading_builder.describe_undefined_ratio()`, which
only speaks about a RECORDED denominator: a subtotal that is absent or not a number, or an analyst-supplied
deal's missing `raw` block, gives the generic "has no computed value for this period", never "is zero"). **Defence
in depth:** if the period's recorded denominator is zero or negative, the covenant is UNRESOLVABLE even when a
number is stored next to it (analyst-supplied ratios are recorded as given, and a state checkpointed before this
fix may hold a negative leverage); the stored value is ignored and the reason says so. What it cannot catch: a
stored ratio whose denominator is not recorded in `financials` (e.g. an analyst-supplied DSCR, which has no `raw`
block to check against), and a state checkpointed before the fix whose stored negative ratio still reaches the
grounding figures (re-run `/spread` to refresh it; only the covenant status is protected). The
reason reaches the Maker in `covenant_results` and the code-enforced rejection text (UNRESOLVABLE has always been
a code-enforced reject reason). A covenant that PASSes in the base case but is FAIL or UNRESOLVABLE in a downside
year is a `downside_covenant_breaches` entry (new keys `downside_status` and `reason`), so a stress that wipes out
EBITDA is disclosed, not silently dropped (the code-enforced rejection text for one reads "cannot be tested in
FY+1 under stress (reason)", not "breaches threshold"). The exported workbook's ratio cells use the same rule
(`IF(denominator>0, ..., "N/A")`, not just `IFERROR`; the working capital cycle row wraps its sum in `IFERROR`
so an "N/A" day row gives "N/A", not `#VALUE!`), so it agrees with `state.json` cell for cell. The margin rows use the same guard but have no `state.json`
counterpart, and the collateral cover cells are `IFERROR`-only; neither is a covenant metric. **Forward years
(issue #176):** a covenant applies to every forward base-case year (`FY+1`..`FY+3`) that has a recorded,
non-empty ratio set; a year with nothing recorded is not evaluated, and state.json carries no covenant tenor, so
a covenant that really ends earlier is still reported for later projected years. `forward_covenant_results` is
the complete record -- one entry per year and covenant, PASS included -- each being `_evaluate_covenant()`'s
result (same #169 rules, `reason` for UNRESOLVABLE) plus `year` and a stable `forward_id`
(`FORWARD-FY-1-GROSS-LEVERAGE`, numeric suffix for two covenants on one metric). It is a separate list rather
than a year dimension on `covenant_results` because every consumer of that list (the reject reasons, the
orchestrator context, the eval contexts) assumes one FY-Current entry per covenant, and `policy_checks.py` turns
any FAIL/UNRESOLVABLE in it into a rejection. **Disclosure only:** no code-enforced reason reads
`forward_covenant_results`, so a forward-year FAIL or UNRESOLVABLE never rejects a deal by itself, and it cannot
go unrecorded. `orchestrator.py` shows the model only the non-PASS entries, as neutral data (no instruction; the
wording for the Maker and Reviewer is post-baseline prompt work); the full list is persisted with `policy_state`
in state.json. It never repeats `downside_covenant_breaches` (which still reports only base PASS -> stressed
non-PASS): a year already failing in the base case appears only in the forward list. This is
what both
`orchestrator.py` (headless) and `scripts/policy_check.py` (slash-command interface, below) call
to get the exact same code-enforced structural facts regardless of which interface a deal runs
through -- `agents/risk_reviewer_agent.md`'s own preamble treats `policy_state`'s presence in a
prompt as the signal that this deterministic layer is active for that run.

## Draft audit

**`scripts/policy_checks.py`** — no `anthropic` dependency. `parse_underwriter_output(draft_text)`
extracts the Underwriter's trailing structured JSON block (`cp_ids_included`,
`risk_categories_covered`, `reported_figures`, `sources`, `financials_source_disclosed`,
`credit_policy_considered`, etc. -- see `agents/underwriter_agent.md`'s Structured Output
guideline); `ground_truth_figures(financials, ratios, collateral, downside_case)` flattens the
deal's actual computed figures into one lookup dict; `check_draft_compliance(draft_text,
policy_state, ground_truth_figures_dict, financials_source=None, credit_policy_present=None)` is
the single source of truth for "why would this draft be code-enforced-REJECTED" -- covenant/
security/CP/CS completeness, risk-taxonomy coverage (`REQUIRED_RISK_TAXONOMY`), narrative-vs-
ground-truth figure mismatches, and the self-declared-field presence checks (analyst-supplied
disclosure, credit-policy consideration). Returns a plain list of human-readable reason strings,
empty if compliant -- callers (`orchestrator.py`'s `_apply_deterministic_policy_checks()`,
`.claude/commands/review.md`'s prose) decide what a non-empty list means for their own verdict.

## Combined policy check

**`scripts/policy_check.py`** — no `anthropic` dependency, same "importable or standalone" duality
as `deal_export.py`. `compute(company, proposal, draft_path=None)` wraps `policy_engine.py`/
`policy_checks.py` into one call: without `draft_path`, returns just `policy_state` (e.g. for
`/assemble` building its own drafting context, before a draft exists); with `draft_path`, also
runs the full `check_draft_compliance()` audit against that file's trailing structured JSON
block. This is what gives the slash-command interface (`/assemble`'s and `/review`'s own Bash
steps) the identical code-enforced governance `orchestrator.py`'s headless pipeline already has,
without either interface reimplementing the logic in prose:
```
python scripts/policy_check.py --company "Acme Corp" --proposal "Fleet Loan" --draft "deals/Acme Corp/Fleet Loan_draft.md"
```

## Spreading check

**`scripts/spreading_check.py`** — no `anthropic` dependency, same "importable or standalone"
duality as `policy_check.py`/`deal_export.py`. `compute(company, proposal,
multi_period_financials=None, stress_assumptions=None, update_financials_source=True)` wraps
`spreading_builder.py`'s `evaluate_financial_model()`/`evaluate_downside_case()`: merges
freshly-given raw periods into whatever this deal's `state.json` already has on file (never a
blind replace -- `/spread` and `/project` each supply only the periods they're responsible for,
in separate calls), recomputes `financials`/`ratios` for the affected periods, and -- when
stress assumptions (freshly given or already on file) and at least one forward period exist --
derives `downside_case` too. The merge is two levels deep for both inputs: a period's raw
figures are merged field-by-field into whatever that period already had on file (a correction to
just `revenue` doesn't discard the period's other already-recorded fields), and fresh
`stress_assumptions` are merged key-by-key the same way (re-confirming one shock doesn't drop
another already-confirmed one left unmentioned) -- caught in post-merge review of the PR that
introduced this script, where the first version replaced each wholesale instead. This is what
gives the slash-command interface (`/spread`'s and
`/project`'s own Bash steps) the identical code-enforced formula evaluation `orchestrator.py`'s
headless pipeline already has, instead of Claude recalculating the same subtotals/ratios by
hand in prose (see issue #98). Deliberately not used by `/spread`'s analyst-supplied mode, which
skips independent recomputation entirely (see issue #55):
```
python scripts/spreading_check.py --company "Acme Corp" --proposal "Fleet Loan" --financials "deals/Acme Corp/Fleet Loan_financials_input.json"
```
`update_financials_source` (CLI: `--no-update-financials-source` to disable) gates whether a
fresh computation stamps `financials_source: "framework-computed"` -- `financials_source` is a
whole-deal flag `/spread` owns the decision for, not per-period, so `/project`'s own call always
passes this flag: without it, `/project` supplying forward-year figures on a deal whose
historicals were recorded via `/spread`'s analyst-supplied mode would silently flip the deal's
flag back to `"framework-computed"`, dropping the Guideline 9 caveat requirement for figures
that were never actually independently recomputed.
