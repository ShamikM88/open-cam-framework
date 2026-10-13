---
description: Validate legal identity, UBO structure, and active charges; produce a Go/No-Go screening status (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [company registration number] [credit bureau summary] [charges/mortgages register notes]"
disable-model-invocation: true
---

## Task: /triage

**Before starting:** if this deal only needs company/sector research and a Go/No-Go screen --
not a full CAM -- use `/research` instead. It covers the same legal-identity/Go-No-Go ground as
this step plus `/commercial`'s company/sector research, exports a standalone research brief in a
few minutes, and writes the exact same state.json this step would, so the deal can still be
upgraded to a full CAM later without redoing anything.

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
If none exists, this is the first step run for this deal.

**Primary inputs** (ask the user for whatever's missing and isn't already in state): Company
Registration Number, Credit Bureau Summary, Mortgages/Charges register.

Validate the company's legal identity, identify its Parent/UBO structure, verify any active
charges/mortgages against it, and output an initial **Go/No-Go** screening status with your
rationale. Ground every claim in a source the user has provided or a credible public record
(e.g. a companies registry) — never invent a registration detail, charge, or UBO relationship
you cannot source.

## Source material

Whenever you download a filing (e.g. a companies registry page/PDF) or the user hands you a
document directly, save the actual material — not just its citation — to this deal's `sources/`
folder:
```
python scripts/source_manifest.py --company "<company>" --proposal "<proposal>" --step triage \
    --claim "<short description of what this backs, e.g. 'SC315671 legal identity and PSC filing'>" \
    --file <path to the downloaded/saved file> [--url <source URL, if any>]
```
This preserves the source of record for later audit (see issue #67) — a citation is only as
checkable as the document it points to, and a web page can change or disappear after the fact.
Don't save incidental scratch/intermediate artifacts (e.g. a page render used only to OCR a
figure) — only the source documents themselves.

## State: write

Update this deal's state file with this step's results (merge with whatever you read above —
never drop a field another step already recorded):
- If no state file existed, create `deals/<company>/<proposal>_<today's date>/state.json`;
  otherwise write back to that same file.
- Set/update: `company`, `proposal`, `date`, `deal_type` (if known), a `triage` key holding your
  Go/No-Go verdict and rationale, and any bureau/registry figures under `inputs` (e.g.
  `inputs.experian_score`).
- Append `"triage"` to `steps_completed` if it isn't already there.

Then run:
```
python scripts/source_manifest.py --check-sources --company "<company>" --proposal "<proposal>"
```
If `missing_saved_sources` is `true`, this step cited sources above but never actually saved any
of the material behind them (see issue #72) — go back and save at least one via the Source
material step above before finishing, or tell the user explicitly that no source material was
saved for this step and why (e.g. every source was unsaveable) rather than silently moving on.
