---
description: Capture forward-year (FY+1-FY+3) financials (computed from raw lines, or the analyst's own figures recorded as given), derive or record a downside case, and record covenants/guarantees (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [forward-year P&L/Balance Sheet figures, or the analyst's own forward-year subtotals/ratios] [stress assumptions or the analyst's own stressed forecast] [covenants] [guarantees]"
disable-model-invocation: true
---

## Task: /project

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Arguments:** `--company "<Name>" --proposal "<Proposal name>"`, followed by whatever inputs
you have below (this deal identity is needed so results can be checkpointed to its state file).

$ARGUMENTS

## State: read

Glob `deals/<company>/<proposal>_*/state.json` (there should be at most one, regardless of
date). If found, read it and treat every figure already recorded there as the source of truth —
never re-derive a number from a summarized/compacted conversation when state.json already has
it. If none exists, this is the first step run for this deal (unusual — `/spread`'s historical
figures are normally what a downside case is stress-tested against, but nothing here strictly
requires it to have run first).

This step covers four independent, separately-optional pieces — ask the user which of them
apply to this deal, and skip anything that doesn't (e.g. a deal with no covenants in its
facility agreement simply has none to record here):

1. **Forward-year (FY+1-FY+3) base-case financials.** 1-3 years of forecast/budget P&L and
   Balance Sheet figures, in the same shape and with the same raw line items `/spread` asks for — or, in
   the analyst-supplied mode (next section), the analyst's own already-computed subtotals and ratios.
2. **Stress-test assumptions**, to derive a downside (stressed) case from (1) above:
   - `revenue_haircut_pct` — % reduction applied to revenue.
   - `opex_increase_pct` — % increase applied to Admin Expenses only (never Cost of Goods Sold —
     that's a different, unmodeled scenario).
   - `interest_rate_bump_bps` — basis-point increase applied to Interest Paid, computed as
     `total_interest_bearing_debt * bps / 10000` added to the base Interest Paid, where
     `total_interest_bearing_debt` is that forward year's own Current Portion - Debt + Overdraft
     / Revolving Debt + Long Term Debt + Loan Notes / Preference Shares.
   All three are optional and independently default to no shock (0) when not given — you don't
   need all three to derive a downside case, but you need at least (1) to have a forward year to
   stress in the first place.
3. **Covenants** — for each: the `metric` this covenant tests (must exactly match one of this
   deal's ratio keys: `dscr`, `gross_leverage`, `net_debt_to_ebitda`, `current_ratio`, `gearing`,
   `ebit_interest_cover`, `ebitda_interest_cover`, or `fcf_conversion_pct`), its `type`
   (`"minimum"` or `"maximum"`), and its numeric `threshold`.
4. **Guarantees** — for each: the `provider` (guarantor name), the `type` of guarantee (e.g.
   "Personal Guarantee", "Corporate Guarantee"), and the `amount` (numeric — omit it entirely for
   an unlimited/uncapped guarantee; never write `0`, which would understate it as a nil guarantee).

## Which mode: framework-computed (the default) or analyst-supplied

Ask which applies before assuming — never infer it from how the historicals were recorded or from the shape of the
figures, and never change the default:

- **Framework-computed (the default).** The forward years are raw line items (the same fields `/spread` uses); the
  framework recomputes every subtotal and ratio from them and can derive a downside case from the stress shocks. Use
  the sections "Forward-year financials/ratios, and the downside case: computed by script" and below, unchanged.
- **Analyst-supplied** (issue #124, mirroring `/spread`'s mode of the same name). Use it when the analyst's own
  forecast convention does not fit the framework's raw schema, so feeding it through the framework's formulas would
  silently misstate it, and the three stress shocks (which act on `revenue`, `admin_expenses`, `interest_paid` and a
  specific debt aggregate) cannot be assumed to mean anything for it. The analyst supplies the forward-year subtotals
  and ratios already computed; they are recorded exactly as given. Follow "Analyst-supplied forward years" below
  instead of the script section, and do **not** run `spreading_check.py` for these years.

Covenants and guarantees (pieces 3 and 4) are the same in either mode.

## Analyst-supplied forward years

Collect, for each forward year the analyst supplies (`"FY+1"`, `"FY+2"`, `"FY+3"`): the subtotals that the analyst's
own template supplies (use only these exact names: `gross_profit`, `operating_profit`, `ebitda`, `profit_before_tax`,
`net_profit`, `fcf`, `current_assets`, `current_liabilities`, `total_assets`, `total_liabilities`, `total_equity`,
`total_debt`, `tangible_net_worth`; omit any the template does not break out and never back-derive one), and the
ratios (`dscr`, `gross_leverage`, `net_debt_to_ebitda`, `current_ratio`, `gearing`, `ebit_interest_cover`,
`ebitda_interest_cover`, `fcf_conversion_pct`; a ratio the analyst reports as not applicable is `null`). Plain numbers
exactly as given: no strings, no units. A raw line-item breakdown for the year (the `/spread` field names) is optional
context and goes in `lines`; supply it if you have it, because it is what lets the framework's shocks be tested (below).
Raw lines alone are the default mode's input, not this one's.

Also ask for the analyst's **description of their forecast convention** (for example "Management budget; depreciation
within cost of sales"): it becomes `--note`, is added to `financials_source_note`, and is what the CAM's
analyst-supplied caveat quotes.

**Downside.** Ask the analyst which applies, and do not manufacture one:
1. **Their own stressed forecast.** Subtotals and/or ratios for the stressed years plus a `description` of the
   scenario. It is recorded as the analyst's data and is what covenants are tested against under stress.
2. **The framework's shocks** (`revenue_haircut_pct`, `opex_increase_pct`, `interest_rate_bump_bps`, in a stress
   file as in the default mode). They are applied to a year **only** if you supplied that year's raw lines, every line
   a requested shock acts on, and the framework's formulas reproduce the subtotals and ratios the analyst gave for that
   year (to two decimal places); otherwise that year gets no stressed case.
3. **Neither.** Then no downside analysis is recorded, and say so.

A year that was wanted but cannot be stressed is recorded as **unavailable**, with the reason, and the policy engine
treats a covenant that passes in that year's base case as UNRESOLVABLE under stress (the draft must address it). It is
never a silent absence and never a pass; do not describe it to the analyst as tested.

Write the supplied figures to a temporary JSON file, e.g. `deals/<company>/<proposal>_forecast_input.json`:
```json
{"forecast": {"FY+1": {"subtotals": {"ebitda": 0}, "ratios": {"dscr": 0}, "lines": {"revenue": 0}}},
 "downside": {"description": "...", "periods": {"FY+1": {"subtotals": {"ebitda": 0}, "ratios": {"dscr": 0}}}}}
```
(omit `lines` and `downside` if none), and a stress file only if the framework's shocks were asked for, then run:
```
python scripts/supplied_forecast.py --company "<company>" --proposal "<proposal>" \
    --forecast "deals/<company>/<proposal>_forecast_input.json" \
    [--stress-assumptions "deals/<company>/<proposal>_stress_input.json"] --note "<the analyst's convention>"
```
It records `financials`, `ratios`, the optional `analyst_supplied_financials`, `forecast_source:
"analyst-supplied"`, the deal-wide `financials_source: "analyst-supplied"`, the note and the downside treatment in one
state update, and prints a summary of what is analyst-supplied, framework-derived or unavailable; report that summary
to the analyst as printed. It refuses, with one `error:` line and nothing written, a malformed input, and a deal that
cannot take it: **one basis** applies, so a deal whose forward years were computed from raw lines, or whose historicals
were computed by the framework (or have no recorded basis), is refused rather than blended; tell the analyst and stop.
Once an analyst-supplied forecast is on file, the framework-computed path refuses to overwrite its forward years, and
a framework-computed `/spread` refuses to relabel the deal. Delete the temporary file(s) afterward.

If the supplied figures were read from an image, the same misreading risk as in `/spread` applies, and
`transcription_check.py` covers historical years only: show the analyst the figures back, period by period, and obtain
an explicit confirmation of exactly what you will record before running the command.

## Forward-year financials/ratios, and the downside case: computed by script, not by hand (default mode)

For each forward year supplied, extract the same raw line items `/spread` uses (`revenue`,
`cost_of_sales`, `admin_expenses`, `depreciation`, `amortisation`, `other_income`,
`interest_paid`, `interest_received`, `scheduled_principal`, `capex`, `exceptional_costs`,
`tax_paid`, and the full Balance Sheet set — see `/spread`'s own field list). These are
directly-supplied forecast figures (the user's own budget/forecast), never extrapolated or
invented by you.

Write them to a temporary JSON file, e.g. `deals/<company>/<proposal>_financials_input.json`,
keyed by whichever forward years you have (`"FY+1"`, `"FY+2"`, `"FY+3"`):
```json
{"FY+1": {"revenue": 0, "cost_of_sales": 0, "...": "..."}}
```
If stress assumptions were also supplied, write them to a second temporary file, e.g.
`deals/<company>/<proposal>_stress_input.json` (omit any key not supplied — each independently
defaults to no shock):
```json
{"revenue_haircut_pct": 0, "opex_increase_pct": 0, "interest_rate_bump_bps": 0}
```
Then run:
```
python scripts/spreading_check.py --company "<company>" --proposal "<proposal>" \
    --financials "deals/<company>/<proposal>_financials_input.json" \
    --stress-assumptions "deals/<company>/<proposal>_stress_input.json" \
    --no-update-financials-source
```
**Always pass `--no-update-financials-source` here** — `financials_source` is a whole-deal flag
`/spread` owns the decision for (see its own "State: write" section), not something this
command's forward-year figures should ever silently flip. Without it, this call would stamp
`"framework-computed"` even on a deal whose historicals were recorded via `/spread`'s
analyst-supplied mode, silently dropping the Guideline 9 caveat requirement for figures that were
never actually independently recomputed.

(omit `--stress-assumptions` entirely if none were supplied this run — it still recomputes the
downside case against the new base data using whatever stress assumptions `/spread`/`/project`
already confirmed earlier for this deal, if any). This computes the forward-year subtotals/
ratios and, when at least one stress assumption and a forward-year base case both exist, derives
the downside case by applying the three deterministic shocks below to that base case and
re-running the identical row-chain the base case uses — never a separately hand-derived formula,
never an extrapolation of your own (see issue #98):
- `shocked_revenue = base_revenue * (1 - revenue_haircut_pct / 100)`
- `shocked_admin_expenses = base_admin_expenses * (1 + opex_increase_pct / 100)`
- `shocked_interest_paid = base_interest_paid + (total_interest_bearing_debt * interest_rate_bump_bps / 10000)`

Every other raw field (the whole Balance Sheet, Tax, Capex, etc.) carries through **unchanged**
from that year's base case. The script checkpoints `financials`, `ratios`,
`multi_period_financials`, `stress_assumptions`, and `downside_case` straight to this deal's
`state.json` (merged with whatever `/spread` already wrote for historical periods — never
erased). Delete the temporary input file(s) afterward — their content is already preserved on
disk. Report exactly what the script's printed output (or a re-read of `state.json`) shows —
never recalculate or adjust any of these figures yourself.

## Source material

Whenever you're given a source document directly — a budget/forecast document, a facility
agreement (for covenant terms), a guarantee document — save the actual document, not just a
citation to it, to this deal's `sources/` folder:
```
python scripts/source_manifest.py --company "<company>" --proposal "<proposal>" --step project \
    --claim "<short description, e.g. 'FY+1-FY+3 budget' or 'facility agreement covenant schedule'>" \
    --file <path to the document>
```
This preserves the source of record for later audit (see issue #67) — so a forecast figure or
covenant threshold can be spot-checked against the exact document later. Don't save incidental
scratch/intermediate artifacts — only the source documents themselves.

## State: write

In the analyst-supplied mode, `scripts/supplied_forecast.py` (run above) already checkpointed
the forward-year figures, `forecast_source`, `financials_source`, the note and the downside treatment in one state
update — nothing left to write for those; only covenants, guarantees and the step below remain. In the default
mode, `scripts/spreading_check.py` (run above, if forward-year financials were supplied) already
checkpointed `financials`, `ratios`, `multi_period_financials`, `stress_assumptions`, and
`downside_case` straight to this deal's `state.json` — nothing left to write for those fields.
Update the rest of this step's results yourself (merge with whatever you read above — never drop
a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to the file you found.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known).
- If covenants were supplied: set/update `covenants` as a **flat list**, each entry exactly
  `{"metric": "...", "type": "minimum"|"maximum", "threshold": 0}` — matching
  `scripts/policy_engine.py`'s expected shape so `/assemble`'s code-enforced covenant check
  (`covenant_results`) picks them up correctly. Merge with, don't replace, any covenants already
  recorded from an earlier run of this command.
- If guarantees were supplied: set/update `guarantees` as a **flat list**, each entry
  `{"provider": "...", "type": "...", "amount": 0}` (omit `amount` entirely for an uncapped
  guarantee — never `0`). Add a stable `guarantee_id` to an entry only if the user gave one
  explicitly, or this guarantor would otherwise collide with another entry sharing the same
  `provider` name — `scripts/policy_engine.py` disambiguates automatically by provider name
  otherwise. Merge with, don't replace, any guarantees already recorded.
- Append `"project"` to `steps_completed` if it isn't already there.
