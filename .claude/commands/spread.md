---
description: Extract and spread 3-5 years of P&L/Balance Sheet data into the core credit ratios, or record an analyst-supplied pre-spread snapshot as-is (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [P&L and Balance Sheet figures, or a path to the source document] [OR a pre-spread financials/ratios snapshot]"
disable-model-invocation: true
---

## Task: /spread

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Arguments:** `--company "<Name>" --proposal "<Proposal name>"`, followed by whatever inputs
you have below (this deal identity is needed so results can be checkpointed to its state file).

$ARGUMENTS

## State: read

Glob `deals/<company>/<proposal>_*/state.json` (there should be at most one, regardless of
date). If found, read it and treat every figure already recorded there as the source of truth —
never re-derive a number from a summarized/compacted conversation when state.json already has
it. If none exists, this is the first step run for this deal.

### Check for a persisted convention

Before asking which mode applies, check whether a spreading convention is already on file for
this borrower or this institution (see issue #58) -- neither is ever auto-applied without an
explicit response, but surfacing what's on file avoids re-deriving/re-asking from scratch every
time:
```
python scripts/conventions.py --enterprise --read
python scripts/conventions.py --company "<company>" --read
```
If either returns `"found": true`, tell the analyst what's on file (the confirmed date, the
mode, and the free-text note) before asking which mode applies to this deal -- e.g. "On file:
enterprise-wide default is analyst-supplied ('this institution always supplies its own pre-spread
figures'); no borrower-specific override for `<company>`. Apply this, or does this deal differ?"
Always require an explicit response before setting anything, even when both are absent or both
agree -- never auto-apply silently, and never merge or auto-pick between the two scopes yourself
if they conflict (that's the analyst's call, not something to infer -- mirrors #55's own explicit
"never infer an institution's proprietary accounting treatment" principle).

**Primary inputs** (ask the user for whatever's missing and isn't already in state): 3-5 years
of Profit & Loss and Balance Sheet data. **Or**, if this analyst already spreads against their
own company-approved template (organizations often treat certain line items differently than
this framework's own raw schema -- e.g. Depreciation charged within Cost of Goods Sold rather
than as its own line, common for asset-hire businesses), they may instead supply that template's
own already-computed figures and ratios directly -- see "Analyst-supplied pre-spread figures"
below. Ask which applies before assuming which mode this is.

### Default: spread from raw P&L/Balance Sheet data

Extract the raw line items for each period you have (`"FY-2"`, `"FY-1"`, `"FY-Current"` --
whichever you were given), using these exact field names (omit any field the source data doesn't
have -- it defaults to `0`, never invented): `revenue`, `cost_of_sales`, `admin_expenses`,
`depreciation`, `amortisation`, `other_income`, `interest_paid`, `interest_received`,
`scheduled_principal`, `capex`, `exceptional_costs`, `tax_paid`, `cash`, `trade_debtors`, `stock`,
`other_current_assets`, `tangible_assets`, `intangible_assets`, `other_fixed_assets`,
`trade_creditors`, `other_current_liabilities`, `overdraft`, `current_debt`, `long_term_debt`,
`loan_notes`, `other_long_term_liabilities`, `provisions`, `share_capital`, `retained_profit`.

Write them to a temporary JSON file, e.g. `deals/<company>/<proposal>_financials_input.json`:
```json
{"FY-Current": {"revenue": 0, "cost_of_sales": 0, "...": "..."}}
```
Then run:
```
python scripts/spreading_check.py --company "<company>" --proposal "<proposal>" \
    --financials "deals/<company>/<proposal>_financials_input.json"
```
This computes Tangible Net Worth (TNW), EBITDA, Debt Service Coverage Ratio (DSCR),
EBIT/Interest, Gross Leverage, Net Debt / EBITDA, Gearing %, Current Ratio, FCF Conversion %, and
Working Capital Days the same deterministic way `scripts/orchestrator.py`'s headless pipeline
does (see issue #98), and checkpoints `financials`/`ratios`/`multi_period_financials` straight to
this deal's `state.json` -- never recalculate or adjust any of these figures yourself, and report
exactly what the script's printed output (or a re-read of `state.json`) shows. Delete the
temporary input file afterward -- its content is already preserved in `state.json`'s
`multi_period_financials`.

### Alternative: analyst-supplied pre-spread figures

If the analyst supplies already-computed subtotals and ratios from their own template rather
than raw line items, **record them exactly as given -- never recompute, adjust, or reconcile
them against this framework's own raw schema.** Don't try to map their line items onto the raw
schema below if their own conventions differ (e.g. Depreciation embedded in Cost of Goods Sold);
trying to force a fit is how a real figure gets silently altered. If they also give you a raw
line-item breakdown, still record it (useful context, and it can still populate the exported
workbook's raw-input cells for whichever labels happen to match), but the subtotals/ratios you
write below always come from what they supplied, not from recalculating those raw lines
yourself. This deliberately skips the audit guarantee the default mode provides (every ratio
independently recomputed from raw inputs) in exchange for respecting the analyst's own
house-approved methodology -- see the `financials_source` flag below, which is what tells
`/assemble` to disclose this tradeoff in the CAM.

Once confirmed, also capture a short free-text description of the convention itself (e.g.
"Depreciation embedded in Cost of Goods Sold") and ask explicitly: **"Is this specific to
`<company>`, or an enterprise-wide convention that applies across every borrower?"** This decides
which store to persist it to in "State: write" below -- never guess or infer the scope.

## Source material

Whenever you're given a source document directly — the P&L/Balance Sheet document itself, or (in
the alternative mode) the analyst's own pre-spread template — save the actual document, not just
a citation to it, to this deal's `sources/` folder:
```
python scripts/source_manifest.py --company "<company>" --proposal "<proposal>" --step spread \
    --claim "<short description, e.g. 'FY2023-2025 P&L and Balance Sheet' or 'Analyst pre-spread template'>" \
    --file <path to the document>
```
This preserves the source of record for later audit (see issue #67) -- so the analyst can spot-
check a figure or re-run a step's numbers by hand against the exact document that was spread,
not just a description of it. Don't save incidental scratch/intermediate artifacts (e.g. an OCR
page render used only to extract a figure) -- only the source document itself.

## State: write

**Default mode:** `scripts/spreading_check.py` (run above) already checkpointed `financials`,
`ratios`, `multi_period_financials`, and `financials_source: "framework-computed"` straight to
this deal's `state.json` — nothing left to write for those fields. Just make sure `company`,
`proposal`, `date`, and `deal_type` (if known) are set (`write_state()` keeps `company`/
`proposal`/`date` in sync automatically on any write, including the script's own) and append
`"spread"` to `steps_completed` — see the bottom of this section.

**Analyst-supplied mode:** update this deal's state file with this step's results yourself
(merge with whatever you read above — never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to the file you found.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known), and a `financials` object
  keyed by period (`"FY-2"`, `"FY-1"`, `"FY-Current"` — whichever periods you were given), each
  holding exactly the subtotals the analyst's own template supplied — **use these exact field
  names**, since `ground_truth_figures()` reads this shape as this deal's ground truth for
  whatever the Underwriter later cites (see `agents/underwriter_agent.md`'s Guideline 5):
  ```json
  {
    "gross_profit": 0, "operating_profit": 0, "ebitda": 0, "profit_before_tax": 0,
    "net_profit": 0, "fcf": 0, "current_assets": 0, "current_liabilities": 0, "total_assets": 0,
    "total_liabilities": 0, "total_equity": 0, "total_debt": 0, "tangible_net_worth": 0
  }
  ```
  Omit any subtotal the analyst's own template doesn't separately break out — never back-derive
  one yourself from their other figures.
- If the analyst **also** gave you a raw line-item breakdown alongside their pre-spread figures
  (useful context, even though it's not required): set/update a separate top-level
  `analyst_supplied_financials` object, keyed by the same periods, each holding just the raw
  figures given — **not** nested inside `financials` above, and never back-derived from the
  analyst's own subtotals/ratios:
  ```json
  {
    "revenue": 0, "cost_of_sales": 0, "admin_expenses": 0, "depreciation": 0,
    "amortisation": 0, "other_income": 0, "interest_paid": 0, "interest_received": 0,
    "scheduled_principal": 0, "capex": 0, "exceptional_costs": 0, "tax_paid": 0,
    "cash": 0, "trade_debtors": 0, "stock": 0, "other_current_assets": 0,
    "tangible_assets": 0, "intangible_assets": 0, "other_fixed_assets": 0,
    "trade_creditors": 0, "other_current_liabilities": 0, "overdraft": 0,
    "current_debt": 0, "long_term_debt": 0, "loan_notes": 0,
    "share_capital": 0, "retained_profit": 0
  }
  ```
  `scripts/deal_export.py` reads this (rather than `financials`'s own raw figures, which this mode
  never populates) to fill the exported workbook's raw-input cells for whichever labels happen to
  match, whenever this deal's `financials_source` is `"analyst-supplied"` (see issue #121) — kept
  as its own separate store rather than nested alongside the framework-computed path's own raw
  figures, so the two can never be blended or mistaken for each other.
- Also set a `ratios` object, keyed by the same periods, each holding exactly the values the
  analyst gave you: `dscr`, `gross_leverage`, `net_debt_to_ebitda`, `current_ratio`, `gearing`,
  `ebit_interest_cover`, `ebitda_interest_cover`, `fcf_conversion_pct`.
- Set `financials_source` to `"analyst-supplied"` — the Underwriter reads this during
  `/assemble`'s drafting step to decide whether the CAM needs the analyst-supplied caveat (see
  `agents/underwriter_agent.md`'s Guideline 9). Applies to the whole deal, not per-period — if any
  period's figures were analyst-supplied, set it to `"analyst-supplied"` even if others in this
  same deal were framework-computed via the default mode above.
- Set a new `financials_source_note` field to the confirmed convention description, phrased
  ready for the CAM's caveat — e.g. "Depreciation embedded in Cost of Goods Sold, per prior
  confirmation for this borrower on 2026-01-15" (borrower-specific) or "Depreciation embedded in
  Cost of Goods Sold, per this institution's standing convention" (enterprise-wide) — see
  `agents/underwriter_agent.md`'s Guideline 9.
- Then persist/refresh the confirmed convention so future deals benefit automatically.
  Borrower-specific:
  ```
  python scripts/conventions.py --company "<company>" --write --financials-source analyst-supplied \
      --note "<the confirmed convention description>" --proposal "<proposal>"
  ```
  or, if the analyst said this is an enterprise-wide convention:
  ```
  python scripts/conventions.py --enterprise --write --financials-source analyst-supplied \
      --note "<the confirmed convention description>"
  ```
  Run this every time analyst-supplied mode is confirmed for this deal (new, reconfirmed
  unchanged, or corrected) — `conventions.py` always overwrites the current fields and appends to
  history, so there's no need to first check whether anything actually changed.
- Append `"spread"` to `steps_completed` if it isn't already there.
