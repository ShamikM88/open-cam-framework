---
description: Assemble the final CAM from the /triage /spread /commercial /collateral outputs, audit it via /review, and export .docx + .xlsx (see config/skills_registry.md).
argument-hint: --company "<Name>" --proposal "<Proposal name>" --type <deal_type> --pd <PD> --lgd <LGD>
allowed-tools: Bash(python scripts/deal_export.py *), Bash(python scripts/policy_check.py *), Bash(python scripts/state_manager.py *)
disable-model-invocation: true
---

## Task: /assemble

**Arguments:** `--company "<Name>" --proposal "<Proposal name>" --type <deal_type> --pd <PD> --lgd <LGD>`

$ARGUMENTS

## State: read

Glob `deals/<company>/<proposal>_*/state.json` (there should be at most one, regardless of
date). If found, read it first — it may already hold the outputs of `/triage`, `/spread`,
`/commercial`, and `/collateral` from earlier in this conversation, from a previous session, or
from after a context compaction. Treat it as the source of truth for any figure it has recorded;
never fall back to a summarized/compacted memory of a step when state.json already has that
step's result.

You're assembling the final Credit Assessment Memorandum from the outputs of `/triage`,
`/spread`, `/commercial`, and `/collateral` — from state.json if present, otherwise from earlier
in this conversation. If any of those steps' results aren't available from either source, tell
the user which ones are missing and stop rather than guessing their content.

1. **Resolve the template.** Read `templates/local/cam/<type>_cam.md` if it exists (a calibrated
   override — see `/calibrate`); otherwise read `templates/cam/<type>_cam.md` (the shipped
   default). If neither exists, this is a new deal type — note that, and you'll be establishing
   its structure with this draft.
2. **Hard-check step order.** Run:
   ```
   python scripts/state_manager.py --check-steps --company "<company>" --proposal "<proposal>" --required spread
   ```
   This is a code-level gate, not the prose reminder above — it reads this deal's actual
   `steps_completed` from `state.json` rather than trusting anything said earlier in this
   conversation. `spread` is the only hard requirement: the CAM's Financial Analysis section
   always needs the ratios `/spread` computes, so drafting without it would mean inventing
   figures. `/triage` and `/collateral` are deliberately **not** hard-required here — a deal can
   legitimately have no collateral (an unsecured facility) or have its triage-equivalent
   information supplied directly by the user in conversation without `/triage` ever having been
   run; the prose reminder above (state.json or this conversation) still applies to those. If
   this command's JSON output has `"ok": false`, **stop immediately** — do not draft anything —
   and tell the user exactly which step(s) in `missing_steps` still need to run (e.g. "run
   `/spread` first") rather than proceeding or guessing the missing figures.
3. **Get this deal's policy_state.** Run:
   ```
   python scripts/policy_check.py --company "<company>" --proposal "<proposal>"
   ```
   This reads `state.json`'s `ratios`/`collateral`/`covenants`/`security_package`/`guarantees`
   (whatever of those exist — none of them are required) and deterministically computes
   `required_conditions_precedent`, `covenant_results`, and `security_gaps`. If its `reasons`
   list is non-empty (a real covenant breach or security gap already exists in this deal's
   recorded structure), note that now — it's independent of anything you're about to draft.
4. **Draft the CAM.** Read `agents/underwriter_agent.md` and act according to that role for this
   step. Write the full CAM as Markdown, following the template's exact section structure,
   filling every placeholder with the grounded content already produced by the prior steps.
   `--pd`/`--lgd` above are user-supplied inputs — use them exactly as given; never invent or
   independently recompute a risk grade. Never state a fact or figure that wasn't grounded in one
   of the prior steps or a source the user supplied. Render every entry in policy_state's
   `required_conditions_precedent` (from step 3) as prose in the CAM's Conditions Precedent
   section, and end your response with the structured JSON block `agents/underwriter_agent.md`'s
   Structured Output guideline specifies (`cp_ids_included`, `risk_categories_covered`,
   `reported_figures`) — `/review` in step 6 checks the draft against this deterministically, so
   it must actually be present and accurate, not a formality.
5. Save that draft (Markdown *and* its trailing structured JSON block together, exactly as
   written) to `deals/<company>/<proposal>_draft.md` (create the folder if it doesn't exist).
6. **Run `/review --company "<company>" --proposal "<proposal>"`** against that draft. If the
   verdict is REJECTED, revise the draft per the notes (which may include code-enforced reasons
   from `policy_check.py` alongside the Reviewer's own qualitative feedback), **overwrite**
   `deals/<company>/<proposal>_draft.md` with the revised Markdown and structured JSON block
   (never leave the stale, rejected version on disk), and run `/review` again — repeat until
   APPROVED. `/review`'s own file-reuse step only skips a rewrite when the on-disk file already
   matches the draft being reviewed; a revision always changed it, so always overwrite here.
7. **Export it.** Once approved, run:
   ```
   python scripts/deal_export.py --company "<company>" --proposal "<proposal>" --type <type> --draft "deals/<company>/<proposal>_draft.md"
   ```
   This creates the dated output folder, exports `.docx` + `.xlsx`, and — only if this deal type
   had no template at all — auto-saves the draft's structure as a new override under
   `templates/local/cam/`.
8. Report the output folder to the user, then delete the temporary `<proposal>_draft.md` file —
   the real output now lives in the dated folder as a proper `.docx`/`.xlsx`.

## State: write

Update this deal's state file (merge with whatever you read above — never drop a field another
step already recorded): `deal_type`, `inputs.pd`/`inputs.lgd`, `draft_path` (the exported
`.docx` path from step 7), `policy_state` (step 3's computed output, verbatim — per CLAUDE.md's
state-management protocol, a code-enforced structural finding like a covenant breach or security
gap must exist as a disk artifact, not only in this conversation), and append `"assemble"` to
`steps_completed` if it isn't already there. `/review` already checkpoints `review_verdict`
itself. If `deal_export.py`'s output
directory (from step 7) uses a different date than the state file you read in step 1 — e.g. this
deal spanned multiple days — write the state file into `deal_export.py`'s actual output
directory instead, so `state.json` ends up next to the `.docx`/`.xlsx` it describes.
