---
description: Validate legal identity, UBO structure, and active charges; produce a Go/No-Go screening status (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [company registration number] [credit bureau summary] [charges/mortgages register notes]"
disable-model-invocation: true
---

## Task: /triage

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Arguments:** `--company "<Name>" --proposal "<Proposal name>"`, followed by whatever inputs
you have below (this deal identity is needed so results can be checkpointed to its state file).

$ARGUMENTS

## State: read

Glob `deals/<company>/<proposal>_*/state.json` (there should be at most one, regardless of
date). If found, read it and treat every figure already recorded there as the source of truth —
never re-derive a number from a summarized/compacted conversation when state.json already has
it. If none exists, this is the first step run for this deal.

**Primary inputs** (ask the user for whatever's missing and isn't already in state): Company
Registration Number, Credit Bureau Summary, Mortgages/Charges register.

Validate the company's legal identity, identify its Parent/UBO structure, verify any active
charges/mortgages against it, and output an initial **Go/No-Go** screening status with your
rationale. Ground every claim in a source the user has provided or a credible public record
(e.g. a companies registry) — never invent a registration detail, charge, or UBO relationship
you cannot source.

## State: write

Update this deal's state file with this step's results (merge with whatever you read above —
never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to the file you found.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known), a `triage` key holding your
  Go/No-Go verdict and rationale, and any bureau/registry figures under `inputs` (e.g.
  `inputs.experian_score`).
- Append `"triage"` to `steps_completed` if it isn't already there.
