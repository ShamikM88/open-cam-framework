---
description: Calculate Gross/Net Exposure, RV Exposure, Collateral Coverage %, and Estimated Net Uncovered Risk (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [asset description] [valuation/invoice amount] [down payment %] [LGD grade & %] [RV %] [PD grade]"
disable-model-invocation: true
---

## Task: /collateral

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Arguments:** `--company "<Name>" --proposal "<Proposal name>"`, followed by whatever inputs
you have below (this deal identity is needed so results can be checkpointed to its state file).

$ARGUMENTS

## State: read

Glob `deals/<company>/<proposal>_*/state.json` (there should be at most one, regardless of
date). If found, read it and treat every figure already recorded there as the source of truth —
never re-derive a number from a summarized/compacted conversation when state.json already has
it. If none exists, this is the first step run for this deal.

**Primary inputs** (ask the user for whatever's missing and isn't already in state): Asset
Description, Valuation/Invoice Amount, Down Payment %, Loss Given Default (LGD) Grade & %,
Residual Value (RV) %, Probability of Default (PD) Grade.

Calculate: Gross/Net Exposure, RV Exposure, Collateral Coverage %, LGD %, and Estimated Net
Uncovered Risk. Show the formula and inputs used for each figure. PD and LGD grades are
user-supplied inputs — this framework has no bureau/rating-agency integration — so use exactly
what's given; never invent or adjust a grade yourself.

## State: write

Update this deal's state file with this step's results (merge with whatever you read above —
never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to the file you found.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known), `inputs.pd`, `inputs.lgd`
  (exactly as given — never adjusted), and `collateral` as a **flat list** (one entry per
  asset/facility — never nested by year), each entry an object with exactly these keys, matching
  `scripts/spreading_builder.py`'s collateral schema so `/assemble`'s export step populates the
  Collateral & Exposure sheet correctly:
  ```json
  [
    {
      "asset_class": "[Asset class]",
      "exposure": 0,
      "number_of_units": 0,
      "cap_value": 0,
      "non_recovery": 0,
      "costs": 0,
      "collateral_value": 0,
      "perfection_status": "[e.g. Registered / Pending / N/A]"
    }
  ]
  ```
  `exposure`/`collateral_value` are the Gross Exposure and Collateral Value you calculated above
  for that asset; `perfection_status` records whether the security interest is registered/
  perfected, pending, or not applicable — never leave it blank if you know the answer.
- Append `"collateral"` to `steps_completed` if it isn't already there.
