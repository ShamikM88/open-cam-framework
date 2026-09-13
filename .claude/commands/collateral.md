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
Residual Value (RV) %, Probability of Default (PD) Grade, and Ranking of the security interest
taken over the asset (e.g. First, Second — where you rank relative to any other chargeholder).

Calculate: Gross/Net Exposure, RV Exposure, Collateral Coverage %, LGD %, and Estimated Net
Uncovered Risk. Show the formula and inputs used for each figure. PD and LGD grades are
user-supplied inputs — this framework has no bureau/rating-agency integration — so use exactly
what's given; never invent or adjust a grade yourself.

Assign each asset a stable `asset_id` (e.g. `"AST-001"`, or reuse one already in state.json if
this asset already has one) — this is the join key `scripts/policy_engine.py` uses to cross-
reference this asset against its security/charge record, so it must stay the same across
re-runs of this command for the same asset.

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
      "asset_id": "AST-001",
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
- Also set/update `security_package` as a **flat list**, one entry per charge (an asset can
  carry more than one — e.g. a senior and a subordinate charge on the same `secures_asset_id` —
  give each its own entry rather than collapsing them), in this exact shape (this is what
  `scripts/policy_engine.py` cross-references against `collateral` above to flag an uncharged
  asset, an unperfected charge, or a subordinate ranking as a required Condition Precedent — see
  the `/assemble`/`/review` steps). Unlike `collateral.perfection_status` above, which is a
  narrative field, `security_package`'s `perfection_status` and `ranking` are compared against
  exact literal strings by code: use precisely `"Perfected"` when the charge is fully perfected
  (any other value — including "Registered" or "Pending" — is treated as unperfected and
  generates a Condition Precedent) and precisely `"First"` when this is the senior-most-ranking
  charge (any other value — including "1st" or "Senior" — is treated as subordinate):
  ```json
  [
    {
      "secures_asset_id": "AST-001",
      "perfection_status": "Perfected",
      "ranking": "First"
    }
  ]
  ```
- Append `"collateral"` to `steps_completed` if it isn't already there.
