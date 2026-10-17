---
description: Standalone research deliverable -- legal identity/Go-No-Go screen plus company/sector/competitive research, no financial spreading (see config/skills_registry.md).
argument-hint: "--company \"<Name>\" --proposal \"<Proposal name>\" [registration number, credit bureau summary, charges register] [sector, management bios, customer/supplier notes]"
allowed-tools: AskUserQuestion, Bash(python scripts/research_export.py *), Bash(python scripts/source_manifest.py *)
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

Glob `deals/<company>/<proposal>_*/state.json`. It lists at most 100 files,
newest-modified first, which is not the same as latest by date; if the list reaches 100 files (the tool also flags
truncation) it may be incomplete: say that the latest dated folder could not be established, and stop. Otherwise consider
only a folder named exactly `<proposal>_YYYY-MM-DD` (the proposal, an underscore, a four-digit year, two-digit month and
two-digit day); ignore every other folder, including another proposal that merely starts with the same words. Take the
latest of those by comparing the date text: that is the folder the scripts read too (`state_manager` applies the same
rule). Read its `state.json` and note the folder.
If found, treat every fact already recorded there as the source of truth — never re-derive something from a summarized/compacted conversation when state.json already has it.
If none exists, this is the first step run for this deal.

### Analyst-defined terms

If, at any point in this step, the analyst tells you what an abbreviation, house term or product name means (whether you asked or they volunteered it), write it to this deal's `state.json` at once as a new entry in an `analyst_terms` list (merge; never drop an existing entry): `{"term": "<as written>", "meaning": "<in the analyst's own words, not paraphrased>", "date": "<today>"}`. Do not wait for the end of the deal: a conversation can be compacted. If you do not know a term, ask the analyst; never guess its meaning.

## Legal identity & Go/No-Go screen

Same task as `/triage`. **Primary inputs** (ask the user for whatever's missing and isn't
already in state): Company Registration Number, Credit Bureau Summary, Mortgages/Charges
register.

Validate the company's legal identity, identify its Parent/UBO structure, verify any active
charges/mortgages against it, and output an initial **Go/No-Go** screening status with your
rationale. Ground every claim in a source the user has provided or a credible public record
(e.g. a companies registry) — never invent a registration detail, charge, or UBO relationship
you cannot source.

**For a borrower inside a group, read the primary documents, not only the registry summaries.** Open the full instrument for every outstanding charge: who the chargeholders are, what it secures and how far any negative pledge reaches. Read the creditors and debtors notes, the key accounting estimates and the going-concern note of the borrower's own accounts, and compare what the directors say about borrowing with what the notes show. Read the parent's subsidiary and guarantee notes for statutory parent guarantees and for which entity directly holds each subsidiary. Read the PSC history, including ceased PSCs, and look for overlap with the board and the company secretary. Save each document you rely on under the Source material step. Where a document cannot be opened, say so as a gap rather than writing around it. Do this for each year's accounts you can open, not only the latest: the controlling-party note can disclose a dated ownership change the PSC register has not caught up with, and a policy or loan note can change between years. Where the register and the accounts disagree, say so and make it an analyst follow-up.

## Company & sector research

Same task as `/commercial`. **Primary inputs** (ask the user for whatever's missing and isn't
already in state): Sector name, management bios, customer/supplier notes, business model
description.

Draft: Company History, Executive Management overview, Parent/UBO Support analysis, Sector
Dynamics, Customer/Supplier Concentration, and Competitive Landscape. Cite the source for every
factual claim (company website, filings, a credible public source, or what the user told you
directly) — never invent a fact about the company, its management, or its market.

**Parent/UBO Support analysis specifically:** follow `agents/underwriter_agent.md`'s Guideline 12
— trace the *full* ownership chain (not just the immediate parent), capture ownership
percentages where disclosed, and actively check for any recent ownership change or investment
rather than only reporting one if it happens to come up. Guideline 12 also covers the judgment
call on whether this deal needs the full Ultimate Parent template section or just a brief note.

## Source material

Whenever you download a filing, fetch a web page or industry report, or the user hands you a
document directly — for either the legal-identity/Go-No-Go screen above or the company/sector
research above — save the actual material — not just its citation — to this deal's `sources/`
folder:
```
python scripts/source_manifest.py --company "<company>" --proposal "<proposal>" --step research \
    --claim "<short description of what this backs>" \
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
  Go/No-Go verdict and rationale (exactly the shape `/triage` would write), a `commercial` key
  holding the drafted sections above (exactly the shape `/commercial` would write), and any
  bureau/registry figures under `inputs` (e.g. `inputs.experian_score`).
- Append `"triage"` and `"commercial"` to `steps_completed` if not already there. This makes the
  deal indistinguishable from one that ran `/triage` and `/commercial` individually, so it can
  seamlessly continue into `/spread` → `/collateral` → `/project` → `/assemble` later if it turns
  out a full CAM is needed after all — nothing done here needs to be redone.

Then run:
```
python scripts/source_manifest.py --check-sources --company "<company>" --proposal "<proposal>"
```
If `missing_saved_sources` is `true`, this step cited sources above but never actually saved any
of the material behind them (see issue #72) — go back and save at least one via the Source
material step above before finishing, or tell the user explicitly that no source material was
saved for this step and why (e.g. every source was unsaveable) rather than silently moving on.

## Review and export the brief

Assemble a standalone Markdown document combining the Go/No-Go screen and the company/sector
research above into one readable brief (your own structure is fine — this isn't the CAM
template; a short header with company/proposal/date, then the Go/No-Go verdict and rationale,
then the company/sector sections reads naturally).

**Formatting for `scripts/docx_builder.py`'s export:** a single newline is a soft wrap, not a
paragraph break — consecutive plain lines join into one continuous paragraph in the exported
`.docx`, matching normal Markdown semantics (this is correct behavior, not a bug to work around).
This means a short header block (company/proposal/date/facility) or any other list of distinct
fields must use a bullet list (`- **Label:** value`) or genuinely blank-line-separated paragraphs
— never a bare run of consecutive `**Label:** value` lines, which will merge into one run-on
paragraph instead of rendering as separate lines.

**Mandatory final Sources section.** Before saving or reviewing the brief, make its final section a flat `## Sources` section. List every document the brief relies on, with its URL wherever one exists. Include a citation key defining each short-form reference used in the body (for example, `WRG note 14`). Keep the list flat: `scripts/docx_builder.py` does not render nested bullets as intended. A heading without entries is not enough.

1. Save it to `deals/<company>/<proposal>_brief.md` (create the folder if it doesn't exist yet).
2. **Run `/review --company "<company>" --proposal "<proposal>" --research-brief --draft "deals/<company>/<proposal>_brief.md"`**
   against that saved brief (see issue #87 — this gives `/research` the same Checker audit `/assemble`
   already gets, scoped to what actually applies before any credit structuring has happened). `/review`
   reviews only the file on disk, never this conversation, and hands the audit to a fresh Risk Reviewer
   sub-agent that has not seen this conversation (issue #204). If
   the verdict is REJECTED, revise the brief per the notes, **overwrite**
   `deals/<company>/<proposal>_brief.md` with the revised Markdown (never leave the stale,
   rejected version on disk; `/review` does not write the brief, so a stale file would be reviewed
   again), and run `/review` again — a new Reviewer each time.

   **Review checkpoint.** Before starting any Reviewer after the first, count the consecutive `REJECTED` entries at the end of this deal's `review_trail` in `state.json` (never from memory of the conversation). After the third, and after every further `REJECTED`, do not start another Reviewer yet. Show the analyst the number of passes and a short list of the latest findings, then ask a structured question with exactly three options: **Run another review pass**; **Stop and export for manual review (unapproved, disclosed)**; **Stop without exporting**. Only an explicit choice of the second option exports an unapproved brief: silence, an ambiguous answer or "carry on" is not consent, and if the answer is unclear ask again. If they choose it, export with `--analyst-release-unapproved "<reason>"`, which adds a visible "Review status" paragraph and records the stop in `state.json`. The default is never to export an unapproved brief. If the analyst has set a lower maximum for this deal, checkpoint at that number instead, and record that maximum in `state.json` under `deal_structure_notes.review_cap` when they set it. After a manual-review release, carry on with the remaining export steps as for an approved export. If the analyst chose to stop without exporting, do not export, but still run the end-of-deal learnings step.

   If `/review` reports that it could not run or that no valid verdict came back, stop: that is neither an approval
   nor a rejection, so do not export; tell the user.

3. **Export it.** Once approved, run:
   ```
   python scripts/research_export.py --company "<company>" --proposal "<proposal>" --brief "deals/<company>/<proposal>_brief.md"
   ```
   This creates the dated deal folder — reusing one that already exists for this company/proposal,
   exactly like `/triage`/`/commercial` do — and exports the brief as
   `<Company>_<Proposal>_Research_Brief.docx`.
   The exporter refuses a missing or empty `Sources`/`References` section before it creates output. If the analyst explicitly authorises exporting without that section, in chat, and provides a reason, record that reason (never invent one) and add `--analyst-override-no-sources "<reason>"` to the export command above, leaving the brief path unchanged and using it for no other reason. The resulting `.docx` includes a final footnote naming the override date and reason. If the analyst explicitly authorises the omission but has not provided a reason, ask for the reason; do not suggest the override as an option. Silence, an ambiguous answer, or “just export it” is not explicit authorisation. Never pass the flag on your own initiative. If a populated sources section exists, do not pass the flag; it has no effect on a valid brief. The reason is disclosed in the document and is not written to `state.json`.

4. Report the output path to the user, then delete the temporary `<proposal>_brief.md` file — the
   real output now lives in the dated folder.
5. **Surface end-of-deal learnings (optional — only on explicit confirmation).** Read this deal's
   own `review_trail` (populated by the review loop above, if any REJECTED verdicts occurred).
   Unlike `/assemble`'s version of this step (see issue #86), a research brief's `review_trail`
   entries never mix in code-enforced deterministic reasons — `--research-brief` mode skips
   `/review`'s whole Code-enforced check section (see issue #87), so every REJECTED entry's
   `notes` here is already purely the Reviewer's own qualitative judgment; there's no deterministic
   portion to separate out first. Filter to REJECTED entries whose `notes` reflect a genuine,
   reusable insight about this borrower or this institution — as opposed to a narrow, one-off fix
   with no standing value (e.g. "add a citation for this one claim" isn't a reusable insight, but
   "this borrower's registered office recently changed and Companies House filings lag two weeks"
   is).

   Also read `analyst_terms` from this deal's `state.json`. List each entry in the same batch as
   the other candidates, and persist it on an explicit yes exactly like any other learning (same
   scope question, same files) in the form `**Term:** X — meaning.` If the analyst says a term is
   industry-standard rather than specific to their institution, say so and point to
   docs/glossary.md; never write to a tracked file. Only what is already in `analyst_terms` is a
   candidate; never infer a term from the conversation.

   If any candidates remain, surface them **as one batch**, not a prompt per item: *"This
   deal's review surfaced N item(s) that might be worth remembering for future deals:
   [list]. Persist any of these?"* For each one the analyst confirms, ask explicitly **"Is this
   specific to `<company>`, or does it apply across every borrower?"** (same question `/spread`'s
   own persisted-convention step asks) and append a dated entry to the matching file —
   `deals/<company>/_learnings.md` (borrower-specific) or `config/deal_learnings.md`
   (enterprise-wide) — creating it with a `# Deal Learnings` heading if it doesn't exist yet, e.g.:
   ```markdown
   ## 2026-01-15 — Acme Corp/Fleet Loan
   **Learning:** New CFO has a banking background; always cross-reference their prior institution
   in Executive Management commentary.
   ```
   Never write without an explicit yes, and never infer consent from the analyst simply moving on
   — see issue #58's "deterministic, analyst-confirmed, never silently assumed" principle. This is
   independent of "State: write" above, which already ran regardless of whether anything is
   persisted here.
