# Skills

The analyst aids that ship with the framework. A skill is a short, reusable procedure that a person starts by name in a
Claude Code session opened on this repository; it is not a step of the CAM pipeline. This page is the user-facing
reference. Why skills are shaped this way, what they may never become, and what is planned or deferred is in
[Skill design](skill-design.md); the single inventory of commands and skills is
[`config/skills_registry.md`](../config/skills_registry.md).

## What every skill here is

- **Typed by a person.** Each is marked `disable-model-invocation`, so the model never starts one on its own, and no
  command, agent prompt or `CLAUDE.md` line names one.
- **Read-only.** It runs in a forked subagent that cannot see your conversation and that has no write or edit tool. It
  reads the deal's `state.json` (the record, not the conversation) and runs only the read-only scripts below. It writes
  nothing: no `state.json` key, no `steps_completed` entry, no file.
- **An aid, not a step.** What it says is not part of the deal and is not provenance. It does not replace `/assemble`'s
  gates, the policy check or `/review`, and it never approves, rates or recommends.
- **Interactive only.** The headless pipeline (`orchestrator.py`, `calibrate.py`) cannot load a skill and has no
  equivalent, so using one never makes a deal that the headless pipeline could not reproduce.
- **Quote, do not compute.** Every number is copied from `state.json` or a script's output.

| Skill | Run | Use it to | Runs (all read-only) |
| :--- | :--- | :--- | :--- |
| `information-gaps` | `/information-gaps --company "<Name>" --proposal "<Proposal name>"` | Turn what is on disk into a prioritised request list: item, why it matters, where it should come from, priority, whether it blocks the next step | `state_manager.py --check-steps`, `source_manifest.py --check-sources`, `policy_check.py` (no `--draft`) |
| `evidence-discipline` | `/evidence-discipline --company "<Name>" --proposal "<Proposal name>" [claims]` | Audit the deal's evidence: saved source, cited but not saved, analyst-supplied, read from an image, or unsupported | `source_manifest.py --check-sources` |
| `financial-analysis` | `/financial-analysis --company "<Name>" --proposal "<Proposal name>"` | Read the recorded figures: what basis they rest on, levels and direction by period, why a ratio is N/A, covenant results as recorded | `policy_check.py` (no `--draft`) |

Each is described in the registry; the files are `.claude/skills/<name>/SKILL.md`, and the files are authoritative for
exactly what the model is told.

## Using them

- **Start from a deal that has a record.** Each finds the latest dated folder for the company and proposal and reads
  `state.json`. With no record it says so and stops.
- **Run them before drafting, or in another session, when the independence of the audit matters.** The skill's
  instructions stay out of your conversation because it is forked, but what it returns is visible to the later steps of
  that session, `/review` included, exactly as anything you type is. That narrows the exposure; it is not isolation
  ([#204](https://github.com/ShamikM88/open-cam-framework/issues/204) tracks the wider question).
- **They are shaped by the record.** A gap list is only as good as what has been recorded; an evidence audit can show that a
  document was saved, never that it supports a claim (it says so on every such row); the analysis does not explain causes
  the record does not show.

### `information-gaps`

Lists steps not completed (only `spread` blocks `/assemble`), whether cited sources were ever saved, the conditions,
covenant results and security gaps the policy check already derives from the recorded state, and what the record itself
shows is absent (inputs, an analyst-supplied convention note, an unavailable downside, empty covenants or collateral).
With no calibrated credit policy it says so rather than assuming one. Priority is *blocks `/assemble`*, *the draft would
be rejected* (a policy-check reason, quoted verbatim), *needed for a complete CAM* or *optional*; it never states a view on
the borrower.

### `evidence-discipline`

Sorts each claim-bearing record into exactly one class: **saved source**, **cited, not saved** ("no manifest entry
matches", not "does not exist"), **analyst-supplied** (including `pd`, `lgd` and bureau scores), **read from an image**
(the analyst's confirmation is a record of what they said, not proof) or **unsupported**. It never writes, completes or
repairs a citation, a URL, a filename or a date, and the deal-level `--check-sources` result is a floor, not a one-to-one
match.

### `financial-analysis`

States what the figures rest on (framework-computed: every ratio recomputed from raw lines; analyst-supplied: recorded as
given, with the convention note), then the recorded levels by period, direction between adjacent periods with both figures
quoted, covenant results with their status and headroom as the policy check printed them (forward results are disclosure
only; downside breaches are enforced when the draft is checked), and recorded figures that sit oddly together. It produces
no growth rate, margin or other new number: a movement metric would be a calculation, so it would be a separate, tested
script.

## Requirements

These are Claude Code features, so they follow its behaviour: a skill in `.claude/skills/<name>/SKILL.md`, a `context:
fork` subagent, `disable-model-invocation`, and `allowed-tools` as a pre-approval of the read-only scripts (it is not a
restriction; `disallowed-tools` removes the write tools). The `background: false` setting makes the result come back in
the same turn and needs a recent Claude Code; an older version ignores it and returns the result when it is ready.

## Changing or adding a skill

A skill pull request meets the contract in [Skill design](skill-design.md#phased-delivery), and the tests that enforce
it are in `tests/test_skills.py` and `tests/test_skill_inventory.py`: the frontmatter equals the registry row, the
`allowed-tools` name only read-only scripts, every source it cites exists, the files carry no real names or figures, and
the scripts it runs leave a deal byte-for-byte unchanged. A skill that wrote to a deal, was named by a command or prompt, or
changed what the Maker or the Checker is told would be a different kind of thing and is not permitted without its own
design.
