---
description: Draft Company History, Management, Sector Dynamics, and Competitive Landscape (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [sector] [management bios] [customer/supplier notes] [business model description]"
disable-model-invocation: true
---

## Task: /commercial

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Arguments:** `--company "<Name>" --proposal "<Proposal name>"`, followed by whatever inputs
you have below (this deal identity is needed so results can be checkpointed to its state file).

$ARGUMENTS

## State: read

Glob `deals/<company>/<proposal>_*/state.json` (there should be at most one, regardless of
date). If found, read it and treat every fact already recorded there as the source of truth —
never re-derive something from a summarized/compacted conversation when state.json already has
it. If none exists, this is the first step run for this deal.

**Primary inputs** (ask the user for whatever's missing and isn't already in state): Sector
name, management bios, customer/supplier notes, business model description.

Draft: Company History, Executive Management overview, Parent/UBO Support analysis, Sector
Dynamics, Customer/Supplier Concentration, and Competitive Landscape. Cite the source for every
factual claim (company website, filings, a credible public source, or what the user told you
directly) — never invent a fact about the company, its management, or its market.

## State: write

Update this deal's state file with this step's results (merge with whatever you read above —
never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to the file you found.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known), and a `commercial` key
  holding the drafted sections above (or at least their key conclusions and sources).
- Append `"commercial"` to `steps_completed` if it isn't already there.
