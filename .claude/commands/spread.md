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
  holding the raw spread line items plus every calculated ratio above.
- Append `"spread"` to `steps_completed` if it isn't already there.
