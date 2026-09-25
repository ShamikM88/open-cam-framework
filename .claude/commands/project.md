---
description: Capture forward-year (FY+1-FY+3) financials, derive a stress-test downside case, and record covenants/guarantees (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [forward-year P&L/Balance Sheet figures] [stress assumptions] [covenants] [guarantees]"
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
   Balance Sheet figures, in the same shape and with the same raw line items `/spread` asks for.
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

## Forward-year financials/ratios: calculate exactly like `/spread`

For each forward year supplied, extract and spread the same raw line items `/spread` uses, then
calculate the same subtotals and ratios (Gross Profit, Operating Profit, EBITDA, Profit Before
Tax, Net Profit, FCF, Tangible Net Worth, DSCR, Gross Leverage, Net Debt / EBITDA, Current Ratio,
Gearing, EBIT/Interest, EBITDA/Interest, FCF Conversion %). These are directly-supplied forecast
figures (the user's own budget/forecast), never extrapolated or invented by you — there's nothing
forward-year-specific about the calculation itself, only about where the input numbers come from.

## Downside case: apply the stress assumptions deterministically

For each forward year that has both a base case (above) and at least one stress assumption
supplied, derive its shocked raw figures using exactly these three transformations — never a
different formula, never an extrapolation of your own:
- `shocked_revenue = base_revenue * (1 - revenue_haircut_pct / 100)`
- `shocked_admin_expenses = base_admin_expenses * (1 + opex_increase_pct / 100)`
- `shocked_interest_paid = base_interest_paid + (total_interest_bearing_debt * interest_rate_bump_bps / 10000)`

Every other raw field (the whole Balance Sheet, Tax, Capex, etc.) carries through **unchanged**
from that year's base case. Then recompute the same subtotals/ratios as above from the shocked
figures — the downside case is evaluated by the identical row-chain the base case uses, just fed
shocked inputs, never a separately hand-derived formula.

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

Update this deal's state file with this step's results (merge with whatever you read above —
never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to the file you found.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known).
- Merge each supplied forward year's raw figures into the existing `financials` object (created
  by `/spread` if it ran already) under its own period key (`"FY+1"`, `"FY+2"`, `"FY+3"`) — same
  `{"raw": {...}, ...subtotals}` shape `/spread` uses for historical periods. Merge the
  corresponding ratios into `ratios` the same way.
- Also set/update `multi_period_financials`, a **raw-figures-only** dict keyed by period
  (`{"FY+1": {...raw...}, ...}`, no subtotals) covering every forward year you were given a base
  case for — this is what lets a later re-run of this command recompute the downside case afresh
  without needing the base case retyped.
- If you derived a downside case: set/update `stress_assumptions` (the assumptions as given —
  omit a key entirely if that particular shock wasn't supplied, rather than writing `0`) and
  `downside_case`, shaped `{"financials": {period: {...}}, "ratios": {period: {...}}}` for
  exactly the forward years you actually stressed.
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
