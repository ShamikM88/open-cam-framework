---
description: Calculate Gross/Net Exposure, RV Exposure, Collateral Coverage %, and Estimated Net Uncovered Risk (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [asset description] [valuation/invoice amount] [down payment %] [LGD grade & %] [RV %] [PD grade]"
disable-model-invocation: true
allowed-tools: Bash(python scripts/collateral_check.py *), Bash(python scripts/state_manager.py *), Bash(python scripts/source_manifest.py *)
---

## Task: /collateral

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Arguments:** `--company "<Name>" --proposal "<Proposal name>"`, followed by whatever inputs
you have below (this deal identity is needed so results can be checkpointed to its state file).

$ARGUMENTS

## State: read

Glob `deals/<company>/<proposal>_*/state.json`. It lists at most 100 files,
newest-modified first, which is not the same as latest by date; if the list reaches 100 files (the tool also flags
truncation) it may be incomplete: say that the latest dated folder could not be established, and stop. Otherwise consider
only a folder named exactly `<proposal>_YYYY-MM-DD` (the proposal, an underscore, a four-digit year, two-digit month and
two-digit day); ignore every other folder, including another proposal that merely starts with the same words. Take the
latest of those by comparing the date text: that is the folder the scripts read too (`state_manager` applies the same
rule). Read its `state.json` and note the folder.
If found, treat every figure already recorded there as the source of truth — never re-derive a number from a summarized/compacted conversation when state.json already has it.
**No matching record is a stop, not an empty record.** Run `python scripts/state_manager.py --find --company "<company>" --proposal "<proposal>"` first. If it reports `record_found: false`, stop and tell the analyst plainly: `No deal record exists for company "<company>" and proposal "<proposal>". Check the spelling and confirm the deal has been created before running this step.` Offer the near-miss candidates it returned, if any; do not invent others. Do not create a deal folder or state file. If it reports `company_folder_exists: false`, say the company folder is absent rather than offering near matches.

**Primary inputs** (ask the user for whatever's missing and isn't already in state): Asset
Description, Valuation/Invoice Amount, Down Payment %, Loss Given Default (LGD) Grade & %,
Residual Value (RV) %, Probability of Default (PD) Grade, Ranking of the security interest
taken over the asset (e.g. First, Second — where you rank relative to any other chargeholder),
and a credit bureau / reference-agency score (e.g. Experian) if one exists for this borrower.

**Deterministic collateral calculation (mandatory)**

Collect the required per-asset inputs and every non-asset mitigant, including an explicit
`eligible_for_netting` value for each mitigant. Each asset must have a stable, unique
`asset_id` matching `[A-Za-z0-9-]{1,32}`; refuse a missing, duplicate or non-matching ID.
Do not use `max` or `avg`; those asset IDs are reserved and refused because they collide
with the deal-level LGD keys. Record `lgd_pct` as the risk team's per-asset input; do not derive or estimate it. Do not
calculate, estimate, round, adjust or restate any collateral metric in prose.

Write all collected asset inputs and mitigants as one UTF-8 JSON payload outside the repository.
Run the deterministic calculator once, passing that complete payload either from a file or
from standard input:

```bash
python scripts/collateral_check.py --company "<company>" --proposal "<proposal>" --input-file "<payload.json>"
```

For standard input, use `-`:

```bash
python scripts/collateral_check.py --company "<company>" --proposal "<proposal>" --input-file - < "<payload.json>"
```

The calculator validates the complete payload before writing, then atomically records the
per-asset inputs and results in `collateral_analysis` and the separate mitigants list. It
refreshes the backward-compatible `collateral` projection with `exposure = total_exposure`,
preserves unrelated state, and marks the `collateral` step complete only after success.
The per-asset result echoes the recorded risk-team `lgd_pct` input; it is not a second
independent LGD calculation.

The deterministic report also supplies deal-level total exposure, total collateral value,
coverage, gross and net uncovered risk, any excess mitigant, `collateral_max_lgd_pct` and
`collateral_avg_lgd_pct`. Treat the calculator's JSON output as the sole numeric source of
truth. Use its unrounded values in the response; never calculate a substitute in prose. On a non-zero exit, stop, report the single `error:` line,
and do not mark the step complete or write partial results.

PD, LGD, and any bureau score are all user-supplied inputs — this framework has no bureau/rating-agency integration — so use
exactly what's given; never invent or adjust a grade or score yourself. Always ask for the
bureau score explicitly before this deal's CAM is finalized (the CAM template's Credit Bureau /
Legal Charges section — its section number varies by deal type/template) rather than defaulting
it to "N/A"/"Not provided" unasked — if the user confirms none is available or applicable, that's
a valid, citable answer in its own right.

Assign each asset a stable `asset_id` (e.g. `"AST-001"`, or reuse one already in state.json if
this asset already has one) — this is the join key `scripts/policy_engine.py` uses to cross-
reference this asset against its security/charge record, so it must stay the same across
re-runs of this command for the same asset. Include the stable ID in the calculator payload; do not write collateral results into `state.json` yourself.

## Source material

Whenever you're given a source document directly — a valuation report, an invoice, a charge
registration/security document — save the actual document, not just a citation to it, to this
deal's `sources/` folder:
```
python scripts/source_manifest.py --company "<company>" --proposal "<proposal>" --step collateral \
    --claim "<short description, e.g. 'AST-001 valuation report'>" --file <path to the document>
```
This preserves the source of record for later audit (see issue #67) — so a valuation or charge
detail can be spot-checked against the exact document later. Don't save incidental scratch/
intermediate artifacts — only the source documents themselves.

## State: write

Update this deal's state file with this step's results (merge with whatever you read above —
never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to that same file.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known), `inputs.pd`, `inputs.lgd`
  (exactly as given — never adjusted).
- Do not set or write `collateral` or `collateral_analysis`, and do not append `collateral` to `steps_completed` yourself. The deterministic calculator writes `collateral_analysis`, refreshes the backward-compatible `collateral` projection, and appends the `collateral` step atomically only after successful validation. On a non-zero exit, leave those state values unchanged.
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
