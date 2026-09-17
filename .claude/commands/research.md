---
description: Standalone research deliverable -- legal identity/Go-No-Go screen plus company/sector/competitive research, no financial spreading (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [registration number, credit bureau summary, charges register] [sector, management bios, customer/supplier notes]"
allowed-tools: Bash(python scripts/research_export.py *)
disable-model-invocation: true
---

## Task: /research

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

This is the standalone research path for a deal that doesn't (yet, or ever) need the full CAM
pipeline: a Go/No-Go legal screen plus company/sector/competitive research, in a few minutes,
with no financials/spreading required. Covers exactly the same ground as running `/triage` and
`/commercial` separately, combined into one step and one exported document.

**Arguments:** `--company "<Name>" --proposal "<Proposal name>"`, followed by whatever inputs
you have below (this deal identity is needed so results can be checkpointed to its state file).

$ARGUMENTS

## State: read

Glob `deals/<company>/<proposal>_*/state.json` (there should be at most one, regardless of
date). If found, read it and treat every fact already recorded there as the source of truth —
never re-derive something from a summarized/compacted conversation when state.json already has
it. If none exists, this is the first step run for this deal.

## Legal identity & Go/No-Go screen

Same task as `/triage`. **Primary inputs** (ask the user for whatever's missing and isn't
already in state): Company Registration Number, Credit Bureau Summary, Mortgages/Charges
register.

Validate the company's legal identity, identify its Parent/UBO structure, verify any active
charges/mortgages against it, and output an initial **Go/No-Go** screening status with your
rationale. Ground every claim in a source the user has provided or a credible public record
(e.g. a companies registry) — never invent a registration detail, charge, or UBO relationship
you cannot source.

## Company & sector research

Same task as `/commercial`. **Primary inputs** (ask the user for whatever's missing and isn't
already in state): Sector name, management bios, customer/supplier notes, business model
description.

Draft: Company History, Executive Management overview, Parent/UBO Support analysis, Sector
Dynamics, Customer/Supplier Concentration, and Competitive Landscape. Cite the source for every
factual claim (company website, filings, a credible public source, or what the user told you
directly) — never invent a fact about the company, its management, or its market.

## State: write

Update this deal's state file with this step's results (merge with whatever you read above —
never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to the file you found.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known), a `triage` key holding your
  Go/No-Go verdict and rationale (exactly the shape `/triage` would write), a `commercial` key
  holding the drafted sections above (exactly the shape `/commercial` would write), and any
  bureau/registry figures under `inputs` (e.g. `inputs.experian_score`).
- Append `"triage"` and `"commercial"` to `steps_completed` if not already there. This makes the
  deal indistinguishable from one that ran `/triage` and `/commercial` individually, so it can
  seamlessly continue into `/spread` → `/collateral` → `/project` → `/assemble` later if it turns
  out a full CAM is needed after all — nothing done here needs to be redone.

## Export the Research Brief

Assemble a standalone Markdown document combining the Go/No-Go screen and the company/sector
research above into one readable brief (your own structure is fine — this isn't the CAM
template; a short header with company/proposal/date, then the Go/No-Go verdict and rationale,
then the company/sector sections reads naturally).

Save it to `deals/<company>/<proposal>_brief.md` (create the folder if it doesn't exist yet),
then run:
```
python scripts/research_export.py --company "<company>" --proposal "<proposal>" --brief "deals/<company>/<proposal>_brief.md"
```
This creates the dated deal folder — reusing one that already exists for this company/proposal,
exactly like `/triage`/`/commercial` do — and exports the brief as
`<Company>_<Proposal>_Research_Brief.docx`. Report the output path to the user, then delete the
temporary `<proposal>_brief.md` file — the real output now lives in the dated folder.
