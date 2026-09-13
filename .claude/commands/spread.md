---
description: Extract and spread 3-5 years of P&L/Balance Sheet data into the core credit ratios (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [P&L and Balance Sheet figures, or a path to the source document]"
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

**Primary inputs** (ask the user for whatever's missing and isn't already in state): 3-5 years
of Profit & Loss and Balance Sheet data.

Extract and spread the line items, then calculate: Tangible Net Worth (TNW), EBITDA, Debt
Service Coverage Ratio (DSCR), EBIT/Interest, Gross Leverage, Gearing %, Current Ratio, and
Working Capital Days. Show which raw line items each ratio comes from so it can be checked, and
never estimate a figure that isn't in the source data provided.

## State: write

Update this deal's state file with this step's results (merge with whatever you read above —
never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to the file you found.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known), and a `financials` object
  keyed by period (`"FY-2"`, `"FY-1"`, `"FY-Current"` — whichever periods you were given).
  **Use this exact shape for each period** — it matches what
  `scripts/spreading_builder.py`'s `evaluate_financial_model()` produces, so `/assemble`'s export
  step can populate the spreading workbook's raw-input cells and re-verify your ratios
  regardless of whether this deal was run through this command or through the headless
  `orchestrator.py`:
  ```json
  {
    "raw": {
      "revenue": 0, "cost_of_sales": 0, "admin_expenses": 0, "depreciation": 0,
      "amortisation": 0, "other_income": 0, "interest_paid": 0, "interest_received": 0,
      "scheduled_principal": 0, "exceptional_costs": 0, "tax_paid": 0,
      "cash": 0, "trade_debtors": 0, "stock": 0, "other_current_assets": 0,
      "tangible_assets": 0, "intangible_assets": 0, "other_fixed_assets": 0,
      "trade_creditors": 0, "other_current_liabilities": 0, "overdraft": 0,
      "current_debt": 0, "long_term_debt": 0, "loan_notes": 0,
      "share_capital": 0, "retained_profit": 0
    },
    "gross_profit": 0, "operating_profit": 0, "ebitda": 0, "profit_before_tax": 0,
    "net_profit": 0, "current_assets": 0, "current_liabilities": 0, "total_assets": 0,
    "total_liabilities": 0, "total_equity": 0, "total_debt": 0, "tangible_net_worth": 0
  }
  ```
  Every key under `raw` is a source figure from the P&L/Balance Sheet you were given (`0` if not
  applicable — never invented). `scheduled_principal` is a memo line for DSCR only and must
  never be folded into `profit_before_tax`/`net_profit`. Every key alongside `raw` is a subtotal
  you calculate from those raw figures, using the labels above exactly.
- Also set a `ratios` object, keyed by the same periods, each holding: `dscr`, `gross_leverage`,
  `current_ratio`, `gearing`, `ebit_interest_cover`, `ebitda_interest_cover` — all calculated
  from the `financials` figures above (show your working in your response; store only the final
  numbers here).
- Append `"spread"` to `steps_completed` if it isn't already there.
