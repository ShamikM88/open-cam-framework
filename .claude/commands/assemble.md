---
description: Assemble the final CAM from the /triage /spread /commercial /collateral outputs, audit it via /review, and export .docx + .xlsx (see config/skills_registry.md).
argument-hint: --company "<Name>" --proposal "<Proposal name>" --type <deal_type> --pd <PD> --lgd <LGD>
allowed-tools: Bash(python scripts/deal_export.py *)
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
2. **Draft the CAM.** Write the full CAM as Markdown, following that template's exact section
   structure, filling every placeholder with the grounded content already produced by the prior
   steps. `--pd`/`--lgd` above are user-supplied inputs — use them exactly as given; never invent
   or independently recompute a risk grade. Never state a fact or figure that wasn't grounded in
   one of the prior steps or a source the user supplied.
3. Save that draft to `deals/<company>/<proposal>_draft.md` (create the folder if it doesn't
   exist).
4. **Run `/review --company "<company>" --proposal "<proposal>"`** against that draft. If the
   verdict is REJECTED, revise the draft per the notes and run `/review` again — repeat until
   APPROVED.
5. **Export it.** Once approved, run:
   ```
   python scripts/deal_export.py --company "<company>" --proposal "<proposal>" --type <type> --draft "deals/<company>/<proposal>_draft.md"
   ```
   This creates the dated output folder, exports `.docx` + `.xlsx`, and — only if this deal type
   had no template at all — auto-saves the draft's structure as a new override under
   `templates/local/cam/`.
6. Report the output folder to the user, then delete the temporary `<proposal>_draft.md` file —
   the real output now lives in the dated folder as a proper `.docx`/`.xlsx`.

## State: write

Update this deal's state file (merge with whatever you read above — never drop a field another
step already recorded): `deal_type`, `inputs.pd`/`inputs.lgd`, `draft_path` (the exported
`.docx` path from step 5), and append `"assemble"` to `steps_completed` if it isn't already
there. `/review` already checkpoints `review_verdict` itself. If `deal_export.py`'s output
directory (from step 5) uses a different date than the state file you read in step 1 — e.g. this
deal spanned multiple days — write the state file into `deal_export.py`'s actual output
directory instead, so `state.json` ends up next to the `.docx`/`.xlsx` it describes.
