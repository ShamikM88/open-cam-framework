# Skill design

How Claude Code skills fit this repository, and what they are not allowed to become. This page is the Phase 1 design
for [#196](https://github.com/ShamikM88/open-cam-framework/issues/196): it settles the questions the later phases
build on. The three analyst aids of Phase 2 are implemented and described in [Skills](skills.md); so are the two
maintainer procedures of Phase 3, `cam-change-verification` and `docs-maintenance`, and the Phase 4 follow-ons
`mutation-triage`, `prompt-change-gate`, `policy-fit` and `credit-challenge`. The inventory of commands and skills is
[`config/skills_registry.md`](../config/skills_registry.md); this page is the reasoning behind it, and
`tests/test_skill_inventory.py` keeps the two in step with the files.

## In brief

- A **command** answers "what workflow do I run?" (`/spread` spreads). A **skill** answers "how should I work on this
  kind of job?" (`financial-analysis` explains what `/spread` recorded). A skill points to the rule and runs the
  script; it is never the only copy of either.
- Every skill in this initiative is an **aid**. It is not the Maker's or the Checker's input, leaves no record in a
  deal, and has no headless equivalent. A skill that would change what the Maker or the Checker is told is a different
  kind of thing and is not permitted here.
- All skills in this initiative are **typed by a person** (`disable-model-invocation: true`). The model never
  loads one on its own, and no command or prompt names one.
- The registry file is **evolved in place** into the one inventory, not renamed and not duplicated.
- `annual-review` cannot be built over today's primitives ([#203](https://github.com/ShamikM88/open-cam-framework/issues/203)),
  and the independence of `/review` on the slash-command path was settled by [#204](https://github.com/ShamikM88/open-cam-framework/issues/204): the interactive Checker
  is a fresh, read-only sub-agent, so `credit-challenge` can remain an analyst aid as long as it does not issue a verdict or enter the Checker's inputs.

## What Claude Code does that this design depends on

These are the behaviours of Claude Code, taken from its documentation, that the decisions below rest on. They are
Claude Code's, not this repository's, so whoever implements a skill re-reads the current documentation first; the
registry test cannot see a change in the platform.

| Platform behaviour | What the design does about it |
| :--- | :--- |
| A command file and a skill create the same `/name`, and **the skill wins** on a clash | Names are unique across both kinds, enforced by a test. A skill called `triage` would silently disable `/triage`. |
| `disable-model-invocation: true` keeps a skill's description out of the model's context and lets only a person invoke it | The invocation of every first-tranche skill. It also means `/assemble`'s review loop cannot reach one. |
| `paths` limits automatic activation to matching files; command files do not support it | Reserved for a later, path-scoped maintainer skill. None in the first tranche. |
| An invoked skill's text **stays in the conversation** and is re-attached after compaction | Anything loaded stays visible to every later step in the session, `/assemble` included. Since #204 the Reviewer that `/review` starts is not one of those steps: it is a fresh sub-agent that reads files, not the conversation. This is why an aid is forked (next row), and why this page claims no more than that. |
| `context: fork` runs the skill in a subagent that **does not see the conversation**; only its result comes back | The aid skills fork: their instructions never join the conversation, and they read the deal from `state.json` as the rehydration rule already says. |
| `allowed-tools` **pre-approves** tools for the turn; it does not restrict them. `disallowed-tools` removes tools while the skill is active | Read-only is expressed with `disallowed-tools` as well as a short `allowed-tools`, and reviewed like a workflow change: a checked-in skill can grant itself broad tool access. |
| The description is capped (1,536 characters with `when_to_use`) and sits in context for every model-invocable skill | Another reason to keep the first tranche user-only: no always-on descriptions. |
| Skills belong to a Claude Code session | `orchestrator.py`, `calibrate.py` and `run_evals.py` call the Anthropic SDK with prompts they build themselves. They cannot load a skill, and a test keeps them from reading `.claude/`. |

## Taxonomy

Two things decide what a skill is allowed to be: who it is for, and what it does to a session.

| Effect | What it is | Allowed here |
| :--- | :--- | :--- |
| **Report** | Reads a named deal's records and scripts' output (or, for a maintainer, a downloaded report) and gives an account in chat. Read-only, forked, writes nothing. | Yes: `information-gaps`, `evidence-discipline`, `financial-analysis`; for a maintainer, `mutation-triage` |
| **Procedure** | Gives a maintainer a sequence of actions over the repository (run these checks, find these pages), run in the maintainer's own session. Touches no deal. | Yes: `cam-change-verification`, `docs-maintenance` |
| **Practice** | Changes how the model works for the rest of the conversation. | Deferred. It cannot avoid shaping later drafting in the same session, and a persistent reminder of a rule already in `CLAUDE.md` is a second copy. |
| **Input** | Changes what the Maker or the Checker is told, on any interface. | **Not permitted.** It would need provenance and an evaluation baseline, and slash-command runs have no provenance at all today ([AI assurance](ai-assurance.md#where-the-enforcement-runs-matters)). A separate design and issue come first. |

Where the other layers stay: behaviour in the implementation and its tests; what a command does in
`.claude/commands/*.md`; what an agent is told in `agents/*.md`; the explanations in `docs/`; the rules Claude Code
must always know in `CLAUDE.md` ([Contributing](contributing.md#what-is-true-when-sources-disagree)). A skill adds a
procedure on top and links down to these.

## The seven constraints

### 1. Provenance and evaluation

**Decision.** Every skill here is an aid with no role in the pipeline, so none is hashed into `model_provenance` or
covered by an evaluation baseline. The test for any future skill is one question: *does its text enter the Maker's or
the Checker's input on any interface?* If so it is an **Input** skill and is not permitted until interactive
provenance exists, the skill is hashed into it and into the baseline, and a maintainer has decided it in its own issue.

**Why.** `model_provenance` and an evaluation baseline record the agent prompts' hashes (`orchestrator.py`,
`eval_report.py`). A skill loaded into a drafting or auditing session would change the input without appearing in
either. The slash-command path records no provenance, so for an interactive skill option (b) in the issue does not
exist yet.

**What this does not claim.** A report skill's *output* appears in the conversation, and a later `/assemble` in the
same session can read it, exactly as it can read anything the analyst types. The forking keeps the skill's instructions
out of the session, and the skill's output is labelled as an aid and written nowhere. Since [#204](https://github.com/ShamikM88/open-cam-framework/issues/204) the
Risk Reviewer that `/review` starts does not see the conversation, so that output does not reach it: it is given paths,
and a skill writes no file. That holds because `/review` passes only paths, which is a command instruction (contract-tested,
not enforced at run time); the session that orchestrates it can still read the output.

**Held by.** The `Role in the pipeline` column (a skill row must say `None (aid)`), and the test that no agent prompt
or command names a skill, points at the skills folder or lets a session run one.

### 2. Headless parity

**Decision.** All skills in this initiative are interactive only. They write no `state.json` key, append nothing to
`steps_completed` or `review_trail`, create no draft, export, convention, learning or saved source. So no deal can
differ because someone used one, and no record implies a capability the headless pipeline lacks.

**Why.** `orchestrator.py` and `calibrate.py` are SDK scripts and cannot load a skill. The same asymmetry already
exists for persisted conventions: the headless pipeline can *inherit* what an analyst confirmed but cannot *originate*
it ([architecture](architecture.md#two-ways-to-run-it)). A future skill that needs to keep something does so through an
existing script the headless side can read, or it does not keep it.

**Held by.** `Headless` is `no` for every skill row, and no production script (so no helper a headless entry point imports) is allowed to build a path into `.claude/`.
Each skill's own tests (Phase 2 and 3) check that it can run only read-only scripts.

### 3. Maker-Checker independence

**Decision.** `credit-challenge` is a Phase 4 analyst report, typed by a person and forked. It reads only the named deal's
record, the saved-source manifest and an unambiguously identified current draft. It is named nowhere in `agents/` or a
command, writes nothing to `review_trail`, and never emits an overall verdict, rating or recommendation. It is not a
substitute for `/review`. `credit-risk` remains a separate, not-yet-designed skill.

**Why the gate, and what changed.** `/review` used to run in the *same conversation* as the draft: it loaded the role
"for the rest of this task" and reviewed "the most recently drafted content earlier in this conversation", so "never in
the Checker's context" could not be promised for anything that had been in the session. [#204](https://github.com/ShamikM88/open-cam-framework/issues/204) settled
that: every interactive `/review` now delegates the audit to a fresh, read-only `risk-reviewer` sub-agent that reads the
saved draft and the deal's files, not the conversation ([D1](decisions.md#d1-the-maker-and-the-checker-are-independent-prompts)).
The adversarial aid remains outside the Reviewer's inputs: it writes nothing, its instructions stay in a forked context,
and its output is not passed to the Checker. The output may be visible to the analyst's wider conversation, so the skill
labels itself as an aid and does not use verdict language as its own conclusion.

**Held by.** The same wiring test as constraint 1: `agents/*.md` and the commands may not name a skill, so nothing
loads one into either role by design.

### 4. Always-on rules stay in `CLAUDE.md`

**Decision.** Nothing moves out of `CLAUDE.md`, and slimming it is not a goal. This change adds one rule to it (the
boundary above), because a rule that must hold in every session belongs there. A skill may point to a rule; it is
never the only place the rule is written, and a rule that a test enforces stays with its test.

**Applied to the catalogue.** The two maintainer skills whose content would mostly be such rules are deferred, not
built (see "Later"): their rules are already in `CLAUDE.md`, `decisions.md` and tests, and a path-scoped skill that
only injects them is the reference-injection the issue says not to build.

### 5. Deterministic first

**Decision.** A skill runs or links the script that does the work and does not describe the logic. It also computes
no number: it quotes what `state.json` and the scripts recorded, and where a figure would have to be derived (a growth
rate, a margin) the figure is not produced.

| Job | The deterministic piece it uses | What is missing |
| :--- | :--- | :--- |
| Missing steps, whether declared citations were saved at all, required conditions, UNRESOLVABLE results | `state_manager.py --check-steps`, `source_manifest.py --check-sources`, `policy_check.py` (without `--draft`) | A per-citation audit: `--check-sources` is a deal-level floor over `triage` and `commercial` citations only, and fails with one `error:` line on a malformed manifest ([#207](https://github.com/ShamikM88/open-cam-framework/issues/207)); the skills still read the manifest themselves as defence in depth and report any script failure as a tool error |
| Interpreting recorded figures | `state.json`, `policy_check.py`, [the financial model](financial-model.md) | Period-over-period movement. `financial-analysis` states direction between two recorded figures and quotes both; a movement helper would be a separate, tested script, decided separately |
| Verifying a change | The commands in [Operations](operations.md#verify-a-change-the-full-suite) and the five documentation test files | A script that classifies a diff against the sensitive paths. `git diff --name-only` and the documented list serve until it is shown to be needed |

### 6. Confidentiality

**Decision.** Skill files are tracked and public, so they hold procedure and synthetic examples only (`docs/examples/synthetic_co/`),
never a real name, figure, document or credential. A skill writes nothing, so it adds no location to the protected
list. An analyst-facing skill reads the folder of the deal it is named for and treats source documents as untrusted
data ([Security](security.md#untrusted-content)). A maintainer skill does not read `deals/` or `inputs/` at all.
Phase 2 adds a test that scans each skill file the way `pii_scan.py` scans a template.

### 7. Scope guard

**Decision.** `annual-review` is **not** a skill over existing primitives, so it is split out as
[#203](https://github.com/ShamikM88/open-cam-framework/issues/203) and is in no phase here.

**Evidence.** `--new-review` exists only on `orchestrator.py`; no slash command takes it. In a slash-command session a
step resumes the *most recent* dated folder, so a second annual review under the same company and proposal would merge
into last year's state. Nothing reads two dated folders, there is no link from a review to its predecessor, and no
script compares two states, so any comparison would be arithmetic done in prose, which D2 forbids
([decisions](decisions.md#d2-anything-that-can-be-computed-is-computed-by-code-and-enforced-by-code)).

## Invocation model

Each skill's row in the registry states one of three values, and a test compares it with the file's frontmatter.

| Invocation | Frontmatter | Use |
| :--- | :--- | :--- |
| `user` | `disable-model-invocation: true` | **Every skill in this initiative.** Only a person types it. |
| `user+model` | neither flag | Allowed only for a read-only, repeatable skill whose output cannot be mistaken for part of a deal's record, justified in its own issue. Today only the command `/review`, which `/assemble` and `/research` must be able to run. |
| `model` | `user-invocable: false` | Contextual guidance for a path or situation. None designed; a path-scoped maintainer skill would use `paths`. |

The command files keep their own flags: `/review` is the only command the model may run, and every other command,
`/assemble` included, is `user`. The registry's Invocation column states it and a test keeps it true.

## The registry decision

**Evolve `config/skills_registry.md` in place into the one inventory.**

- The Claude Code documentation now treats a command as a kind of skill, so "skills registry" is an accurate name for
  a file that lists both, and the file's own title already said "Skill & Command".
- A rename touches the eight command descriptions that cite it, `CLAUDE.md` (three places), `README.md`, four pages
  under `docs/`, the move ledger's quotations and three test files, and gains nothing a table does not.
- Two inventories (a command registry and a skill registry) is the failure the issue names. There is one file, one
  table and one name per row.

What changed: an **Inventory** table at the top (name, kind, audience, status, invocation, role in the pipeline,
headless), then the existing numbered **Commands** list unchanged, because the command parsers and the pages that
cite it depend on it. The per-skill specification stays here, not in the registry, so a skill is described once; the
registry holds only what a test can compare with a file.

## The first tranche

Chosen from the issue's catalogue against what the repository already has, not as a wish list. The aid skills sit on
scripts that exist today; the procedures wrap documented sequences for change verification, documentation maintenance and prompt-sensitive review.

| Skill | Phase | Audience | Invocation | Effect | Interactive only |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `information-gaps` | 2 | analyst | user | Report | Yes, no record |
| `evidence-discipline` | 2 | analyst | user | Report | Yes, no record |
| `financial-analysis` | 2 | analyst | user | Report | Yes, no record |
| `cam-change-verification` | 3 | maintainer | user | Procedure | Not a deal skill |
| `docs-maintenance` | 3 | maintainer | user | Procedure | Not a deal skill |
| `mutation-triage` | 4 | maintainer | user | Report | Not a deal skill |
| `prompt-change-gate` | 4 | maintainer | user | Procedure | Not a deal skill |
| `policy-fit` | 4 | analyst | user | Report | Yes, no record |
| `credit-challenge` | 4 | analyst | user | Report | Yes, no record |

Automatic invocation is not permitted for any of them: each is something a person asks for by name, none is
contextual, and a model-invocable description would sit in every session's context. None is hashed or evaluated
(constraint 1). None implicitly loads into the Maker or the Checker.

### `information-gaps` (Phase 2)

- **Purpose.** Turn what is on disk for a named deal into a request list: item, why it matters, where it should come
  from, priority, whether it blocks the next step.
- **Reads / delegates to.** `state.json` (rehydration), `state_manager.py --check-steps`, `source_manifest.py
  --check-sources`, `policy_check.py` without `--draft` (required conditions, covenant results, UNRESOLVABLE ratios,
  security gaps). Step requirements are those in `.claude/commands/assemble.md`; definitions are in
  [the financial model](financial-model.md) and [the data model](data-model.md).
- **Side effects.** None. Chat output only; no file, no state.
- **Limits.** It knows only what is recorded. A gap is a gap in the record, not a view on the borrower, and what blocks
  approval is decided by `policy_check.py`'s reasons, not by the list. With no calibrated policy it says so rather
  than assuming one.
- **Versus commands.** `/assemble` stops on a hard-required missing step; this works earlier and across steps. No name
  clash.

### `evidence-discipline` (Phase 2)

- **Purpose.** Audit a deal's evidence: for each claim-bearing record, is it from a saved source document, cited but
  not saved, analyst-supplied, read from an image, or unsupported? It never writes or completes a citation.
- **Reads / delegates to.** `sources/manifest.json` and the `sources` lists in `state.json`,
  `source_manifest.py --check-sources`, `financials_source` and the transcription record, and `CLAUDE.md`'s
  "Source material persistence". The standard is the Underwriter's Guideline 1, which it points to and does not restate.
- **Side effects.** None.
- **Limits.** It can show that a document is saved, not that it supports the claim; it marks those for a person and
  never says "verified". `--check-sources` says only that nothing was saved despite a declared `triage` or `commercial`
  citation; the matching of citations to entries is the skill's own reading. The catalogue's "keep analysis grounded" is built as an on-demand audit, not as an always-on
  reminder: a persistent reminder is a Practice skill and would duplicate Guideline 1.
- **Versus commands.** `/review` challenges a draft's assertions as the Checker; this audits the deal's records before
  drafting and is not a Checker.

### `financial-analysis` (Phase 2)

- **Purpose.** Explain the recorded figures to the analyst: levels and direction across periods, what a `null` ratio means
  (a zero or negative denominator in a framework-computed deal; recorded as given, with no reason held, in an
  analyst-supplied one), what a covenant result means as recorded (including the downside and the disclosure-only forward results), and the
  framework-computed against analyst-supplied distinction and what a reader may rely on in each case.
- **Reads / delegates to.** `state.json` (`financials`, `ratios`, `financials_source`, `financials_source_note`,
  `downside_case`), `policy_check.py`, and [the financial model](financial-model.md) for every definition. It does not run
  `spreading_check.py`, which writes.
- **Side effects.** None.
- **Limits.** Recorded values only; no derived number is produced. For an analyst-supplied deal it states that the
  figures were not recomputed. It does not write into the CAM: the Maker still drafts the Financial Analysis section.
- **Versus commands.** `/spread` produces the figures, this interprets them, `/assemble` drafts. No name clash.

### `cam-change-verification` (Phase 3)

- **Purpose.** Run, in order, and report the checks a change needs before review: targeted tests, the full suite with
  coverage, Ruff, Bandit, the badge and coverage checks, `diff-cover` on committed changes, the documentation checks, a
  diff-boundary and sensitive-path review (`agents/`, `config/`, `scripts/`, `.claude/`, `.github/`, `pyproject.toml`),
  and a clean tree.
- **Reads / delegates to.** The sequence in [Operations](operations.md#verify-a-change-the-full-suite) and
  [Contributing](contributing.md#before-you-ask-for-review), and `pii_scan.py` for added text. It cites them for the
  flags; the flags themselves are checked against `--help` by `tests/test_command_flags.py`.
- **Side effects.** Runs tests locally (no network, no model) and writes coverage artefacts; it makes no ad hoc edit to a
  tracked file, and the badge write is separate and gated. It runs
  `check_test_count.py --write` only after a complete green run and a maintainer's go-ahead, and it never commits,
  pushes, merges or touches repository settings. The `--write` call is deliberately not pre-approved, so by default it asks; the go-ahead is the rule either way.
- **Implemented.** `.claude/skills/cam-change-verification/SKILL.md`; the pre-approved commands are exact strings
  (`ruff check .`, `bandit -r scripts/ -ll`, the one full-suite `pytest` command, the read forms of the two `check_*`
  scripts, `diff-cover ... --config-file pyproject.toml`, `pii_scan.py`, and four read-only `git` forms), and
  `Write`, `Edit` and `NotebookEdit` are removed while it runs.
- **Limits.** A green local run is not a green CI run (Windows, a clean environment); it says so. It does not judge the
  design. It reads no deal.
- **Folded in.** The sensitive-path and security review of `security-and-confidentiality` is a step here, using
  [the review list](contributing.md#security-review).

### `docs-maintenance` (Phase 3)

- **Purpose.** For a given change, find the pages and executable examples that describe the changed source, update
  them in the same change, and run the documentation checks.
- **Reads / delegates to.** The authoritative-source table in [the docs index](README.md#where-each-kind-of-fact-is-authoritative),
  [Contributing](contributing.md#keeping-the-documentation-in-step), [Operations](operations.md#documentation-maintenance),
  and `pytest` on the documentation test files.
- **Side effects.** Edits `docs/`, `README.md` and, only when asked, command text. It never edits `agents/*.md` (it stops
  and points to [the prompt-change process](contributing.md#changing-an-agent-prompt)), never regenerates a golden
  snapshot, and never touches the move ledger.
- **Limits.** The checks catch renames and removals, not behaviour that changed under an unchanged name, so it reads the
  page against the diff and says what it could not confirm. Wiki publication (#192) is separate.
- **Implemented.** `.claude/skills/docs-maintenance/SKILL.md`. Unlike `cam-change-verification` it edits files, so it
  does not pre-approve `Edit` or `Write`: each edit goes through the maintainer's permission settings, which ask by
  default but can be set to allow or to skip prompts, so the skill states its edit boundary (`docs/` and `README.md`;
  command text only when asked; never `agents/*.md`, `CLAUDE.md`, source, tests, configuration, workflows, the move
  ledger or a snapshot) as its own rule. It pre-approves the read-only `git` forms, the five documentation test files
  named in the runbook as one exact command, and `pii_scan.py <path>`.

### `mutation-triage` (Phase 4)

- **Purpose.** Walk a maintainer through a mutation report: the scores as recorded, the largest groups of survivors by
  function, which tests name each function and whether the mutation run's test selection includes them, a candidate class
  for each group, the existing issue that may already cover it, and a proposed disposition with its evidence. It is the
  runbook's triage steps applied to one report, not a new method.
- **Reads / delegates to.** The downloaded `mutation-report` artifact (`mutation-report.json`, `mutation-survivors.txt`,
  `mutation-all.txt`), the runbook in [Operations](operations.md#mutation-testing-reading-the-report-and-triaging-survivors),
  the run's description in [Testing](testing.md#mutation-testing), `pyproject.toml`'s `[tool.mutmut]` (scope and test
  selection) and the source and tests it names. It runs no script: the JSON is what `mutation_report.py` already built.
- **Side effects.** None. Chat output only. It edits nothing, files no issue, runs no mutation or test, and never changes
  the mutation scope, the 75% diagnostic target, the configuration or the workflow ([D13](decisions.md#d13-mutation-testing-and-coverage-are-diagnostics-and-floors-not-a-scoring-game)).
- **Implemented.** `.claude/skills/mutation-triage/SKILL.md`. It is a *report* in the taxonomy: forked, typed by a person,
  `Read`, `Glob` and `Grep` pre-approved, and `Write`, `Edit`, `NotebookEdit` and `Bash` removed while it runs. It
  pre-approves no command at all, because it needs none. A tool that is not removed stays under the user's permission
  settings, so the skill states in its own rule that it uses only the three read tools; a deny rule would enforce it.
- **Limits.** The artifact lists mutant names and statuses, not the mutated source, so every class is a *candidate*:
  "equivalent" is never concluded without the mutated line (`mutmut show`, run by the maintainer on Linux or macOS).
  The link from a function to its tests is by name, so a function reached only through a caller looks untested. The
  artifact records no commit, so the source it reads must be the revision that was mutated. It cannot search the issue
  tracker; it names the issues the docs attach to a group and asks the maintainer to search for the rest.
- **Versus the runbook.** The runbook is the procedure and stays authoritative; the skill adds the checks that are
  tedious by hand (test-selection membership, finding the function's source and tests) and a fixed report shape.



### `policy-fit` (Phase 4)

- **Purpose.** Explain deterministic policy output for a named deal: current covenant results and reasons, required CPs/CSs, security gaps, downside breaches and forward-year disclosures.
- **Reads / delegates to.** The deal's state and structured output from `scripts/policy_check.py` called without `--draft`; `scripts/policy_engine.py` and `scripts/policy_checks.py` define the semantics. The skill quotes IDs, statuses, thresholds, headroom and reasons rather than reimplementing checks.
- **Side effects.** None. It runs a read-only CLI and returns a chat report; no deal record, draft, file or review trail is written.
- **Limits.** Without `--draft`, draft-level checks (required IDs appearing in the draft, source declarations, risk taxonomy, reported figures, downside acknowledgements and certain disclosures) have not been run. The top-level `compliant` flag can therefore be true without a draft audit and is never an overall approval. `forward_covenant_results` are disclosure-only; downside breaches must be surfaced and acknowledged in a draft but do not alone create an automatic adverse credit verdict. The JSON does not grant human exceptions or evaluate every clause of narrative policy. This aid makes no lending recommendation and never substitutes for `/review`.

### `prompt-change-gate` (Phase 4)

- **Purpose.** Gather and report the repository evidence required before a maintainer changes a production agent prompt, the interactive Checker agent, or a slash-command instruction. It is a procedure/checklist, not an approval bot.
- **Reads / delegates to.** The current diff; [Contributing: changing an agent prompt](contributing.md#changing-an-agent-prompt); [Evaluation](evaluation.md) and [AI assurance](ai-assurance.md); the existing prompt-hash implementation, baseline schema and `tests/test_prompt_consistency.py` including `EXPECTED_SUBJECTS`. It points to those sources rather than copying their rules.
- **Side effects.** None by design. It reads repository state and reports in chat. The write tools are removed; the rest is an
  instruction, because `Bash` stays available and an unlisted command only asks for permission. It does not edit prompts, commands, test expectations, provenance records or baselines; it does not call `gh`, fetch from a remote, commit, push, merge or make live model calls.
- **Gate logic.** It distinguishes an observed issue/PR/authorisation reference from an assertion supplied in arguments. Where issue or review discussion is not in the local evidence, it marks that gate unknown. It checks whether a relevant, complete baseline exists and distinguishes assembled-prompt hashes from source-file hashes. Issue #150 remains blocked until the applicable pre-change baseline required by #151 exists; a green structural test is not generative-quality evidence.
- **Limits.** It cannot grant authorisation or verify remote GitHub discussion without the evidence being supplied. It reports present/missing/unknown, never “approved”, “safe” or “ready to merge”. The maintainer decides whether the evaluation rationale is sufficient and whether any change may proceed.

## Later, and why

| Skill | Status | Reason and gate |
| :--- | :--- | :--- |
| `credit-assurance` | Deferred, evidence-gated | Its rules (deterministic enforcement is authoritative, fail closed) are already in `CLAUDE.md`, D2 and D5 to D7 and enforced by tests; a path-scoped skill would restate them. Built only if a governance regression gets past those, and then as a change-impact map, not a copy of the rules. |
| `security-and-confidentiality` | Folded into `cam-change-verification` | Its procedure is the security review in Contributing and `pii_scan.py`; its rules are in `CLAUDE.md` and `test_confidential_paths.py`. |
| `policy-fit` | Implemented, Phase 4 | A read-only report of policy_check.py's recorded structural results, required CP/CS IDs, and current/downside/forward covenant outputs. It quotes engine results and explicitly avoids an overall verdict; its value is a structured, deal-specific reading of the output, not a restatement of the policy logic. |
| `credit-challenge` | Implemented, Phase 4 | Analyst-invoked, adversarial question set over recorded evidence. Read-only and forked; no verdict, score, recommendation, persistence or Checker wiring. The detailed contract and limits are below. |
| `credit-risk` | Design proposed in a separate stacked PR for #196 | A structured, evidence-grounded analyst risk picture across applicable categories. It remains separate from `credit-challenge`, `policy-fit`, financial interpretation and formal `/review`; implementation must meet the design contract below. |
| `annual-review` | Blocked by [#203](https://github.com/ShamikM88/open-cam-framework/issues/203) | Needs primitives that do not exist (constraint 7). |
| `covenant-and-downside`, `collateral-security`, `borrower-research`, `committee-prep`, `deal-consistency` | Not designed | Chosen from how Phase 2 is used. Four overlap a command or a check (`borrower-research` with `/research` and `/commercial`, `committee-prep` with `/assemble`, `deal-consistency` with `policy_check.py` and `/review`, `covenant-and-downside` with the policy engine) and must show what they add. |
| `state-migration`, `release-readiness` | Not designed | Each overlaps a runbook section already in Contributing or Operations. (`mutation-triage` had the same overlap and was built because it gathers evidence against one report; `prompt-change-gate` has the same rationale: gather change-specific evidence rather than restate the runbook.) |
| The issue's "nice to have" skills | Out of scope | Unchanged from the issue. |



### `credit-challenge` — design contract

**Decision recorded here:** the design-only proposal is preserved separately from the executable skill implementation. The implementation lives in a follow-up PR stacked on the design PR (it merges after it) and must satisfy this contract. It remains an analyst aid, not an input to the Maker or Checker.

**Job to be done.** After the analyst has a deal record (and, when available, a draft), provide a disciplined adversarial second look. It asks “what could be wrong, incomplete or weaker than the narrative suggests?” rather than producing a general risk summary, a verdict, or a second policy engine. It is an aid to analyst thinking, not a formal decision step.

**Invocation and execution boundary.**

- Person-invoked only: `disable-model-invocation: true`; `context: fork`; not model-invocable, backgrounded or auto-loaded.
- Read-only. No `Write`, `Edit` or `NotebookEdit`; no script is needed to formulate a challenge report in the first implementation. No file writes, state changes, draft edits, new evidence-manifest entries, review-trail entries or persistent learnings.
- No Maker/Checker wiring. It is not named or invoked by `agents/*.md`, `.claude/agents/risk-reviewer.md`, any command, headless script or `CLAUDE.md`. It does not share its reasoning with the formal Checker. The formal `/review` path stays separate and authoritative.
- No web browsing, live model/API call from a script, source fetching, or synthetic "evidence" generation. The skill operates only over the existing local deal record and a current draft if one exists; only synthetic examples may be included in repository tests and documentation.

**Permitted inputs.** The named deal's current dated `state.json`, existing saved-source manifest and the current draft if one exists and is identified unambiguously. Use the same company/proposal validation and exact latest `<proposal>_YYYY-MM-DD` folder rule as the other analyst aids. Do not read an arbitrary source file as if it were verified merely because it exists in the checkout. Trace a factual assertion to the specific recorded field or manifest/source identifier where that evidence is available. Distinguish sourced facts, analyst-supplied facts, model narrative, and absent/unverifiable evidence.

**Required observation format.** Each substantive challenge uses the same fields, in order:

1. **Challenge / question** — the exact assumption, inference, inconsistency or missing support worth testing, phrased as a question rather than a verdict.
2. **Recorded evidence** — the state key, draft passage, recorded value or source-manifest identifier that grounds it. Quote the relevant content or identify the exact path/key; do not invent citations.
3. **Potential impact** — conditional and explicit about the chain of reasoning; distinguish a plausible risk from a fact proved by the record.
4. **Current mitigant** — the evidence in the record that answers or reduces the challenge, or “none recorded in the material reviewed”. Do not manufacture a mitigant.
5. **Residual uncertainty** — what still cannot be concluded from available evidence.
6. **Information gap / next question** — the specific question or evidence request that would resolve the uncertainty, or “no further question identified from this observation”.

If no material challenge is identified, state only that no challenge was identified **in the material reviewed** and list the sources inspected. Never imply that absence of a finding proves the deal is sound or the review is complete.

**Prohibited output and claims.**

- No overall decision or verdict words as the skill's own conclusion: no `APPROVED`, `REJECTED`, `PASS` or `FAIL`. Do not produce a borrower score/rating, risk grade, lend/decline recommendation or claim to “clear” a deal.
- It is not the Checker, not a replacement for `/review`, and not a re-run of the deterministic policy engine. If a policy result is relevant, it can point to or clearly attribute the existing recorded output; it must not restate the result as its own judgement or calculate a replacement.
- No formula, covenant headroom, financial ratio, probability, severity score or exposure amount is recalculated. Quote recorded figures without deriving new ones.
- Do not convert missing data to a negative finding; distinguish “not present in the record” from “does not exist”. Do not generate an objection just to fill all output fields, and do not suppress a challenge because a source is analyst-supplied.
- Narrative is not a deterministic gate. Any exception/waiver or credit judgement belongs to an authorised human process and is neither decided nor written by this skill.

**Overlap boundaries.**

- `policy-fit` explains the actual deterministic policy output, covenant statuses and formal CP/CS/security/downside records. `credit-challenge` may ask a question about those recorded items but must not reproduce a second policy report or change their meaning.
- `credit-risk`, if separately designed later, would organise the risk picture (risk/evidence/impact/mitigant/residual risk/information gap). `credit-challenge` is narrower and adversarial: it actively tests assumptions and counter-evidence, and may conclude no challenge was found in the material reviewed; it does not deliver the full risk taxonomy.
- `/review` runs the separate Checker and deterministic draft compliance path. `credit-challenge` does not issue a review verdict, alter a draft or pass its own output into the Checker.

**Implementation acceptance gates.**

- Contract tests establish frontmatter/inventory consistency, user-only invocation, forked context, no command/prompt/headless wiring, and the tool boundary. The write tools, `Bash`, `WebFetch` and `WebSearch` are removed with `disallowed-tools` (the one thing the tool settings enforce); the pre-approval is `Read`, `Glob` and `Grep` and nothing else. "No shell, no script, no browsing" is therefore enforced for those tools; for anything else (an MCP tool, for example) it is a rule, and that tool stays under the user's permission settings.
- **Scenario coverage is behavioural, not a text search.** A skill is a prompt, so no test can show that a model obeys it, and no test here makes a model call. What can be tested is the contract a report must meet, applied to synthetic deals. The implementation PR therefore carries (a) a small set of synthetic deal records, one per scenario below, built with the real state schema and the real dated-folder layout, (b) for each, a hand-written expected report in the required six-field format, and (c) one validator that reads a report and the record it is about and returns the problems it finds. Each expected report must validate clean and a deliberately broken variant of it must be caught, so the validator is shown to catch what it claims to. The validator checks that: the six fields appear in order for every challenge; every quoted value occurs in the record or the named source; every number in the report occurs in the record (no figure is derived); a fact is labelled sourced or analyst-supplied as the record says; no `APPROVED`, `REJECTED`, `PASS`, `FAIL`, grade, score or lend/decline wording appears as the report's own conclusion; and the closing caveat is present. These tests establish that the contract is checkable and that the skill text asks for each property; they do not prove a model will obey the prompt.
- The scenarios, each a synthetic record with the property its expected report must show:

  | Scenario | Property the expected report must show |
  | :--- | :--- |
  | Sourced and analyst-supplied facts side by side | Each is labelled as the record labels it; an analyst-supplied figure is not presented as sourced |
  | A narrative claim with no support in the record | Raised as a question, with "not present in the material reviewed", not as a finding that it is false |
  | Two recorded values that contradict each other | Both values quoted side by side with their paths; no third figure computed |
  | A mitigant that genuinely answers the challenge | Quoted under Current mitigant, and the challenge is not overstated |
  | A claim with a manifest entry but no saved file, or no manifest | Reported as missing provenance, with the manifest's limits stated; an empty manifest is not read as proof |
  | No draft | The report says no draft was available and reviews the record only; it does not guess a draft |
  | Nothing to challenge | Says "No challenge identified in the material reviewed" and lists what was inspected; claims neither soundness nor completeness |
  | A refused name, an ambiguous or capped lookup, an unsafe draft path | Refusal or stop with the reason, no report |

- The ordinary skill quality gates apply: path/link and confidentiality checks, full test suite, Ruff/Bandit, an accurate test-count badge, and exact-head CI.
- The implementation PR must cite this design PR and report any deliberate deviation. A deviation that touches the formal Checker, prompts, policy semantics, persistence, live model calls or scoring requires a new explicit design decision rather than an incidental implementation choice.


### `credit-risk` — design proposal (implementation is a separate PR)

**Job to be done.** Organise the material risk picture for a named deal into a consistent, evidence-grounded analyst view. Unlike `credit-challenge`, which actively pressure-tests assumptions, `credit-risk` gives a structured account of risks evidenced in the available material. It is not a credit decision, rating, formal review or substitute for the deterministic policy engine.

**Invocation and execution boundary.**

- Person-invoked only: `disable-model-invocation: true`; `context: fork`; never auto-loaded, backgrounded or invoked by a command.
- Read-only analyst aid. No writes to drafts, `state.json`, source manifests, `review_trail`, learnings or other deal records. No shell/script surface, web browsing, external source fetching or live model/API calls from scripts. The write tools, `Bash`, `WebFetch` and `WebSearch` are removed with `disallowed-tools`, so those limits are enforced; that the skill invokes no other skill, or any other tool, is an instruction and stays under the user's permission settings.
- No Maker/Checker wiring. Do not reference this skill from `agents/*.md`, `.claude/agents/`, commands, headless scripts or `CLAUDE.md`. The separate `/review` Checker and its deterministic checks remain authoritative for formal review.
- Do not treat a skill invocation as a persisted workflow step or as provenance/evaluation input; headless parity remains unchanged.

**Permitted inputs and evidence discipline.**

- Read only the explicitly named deal's exact latest dated `state.json`, the existing saved-source manifest, and local source files explicitly identified by that manifest. Use the same company/proposal refusal rule and exact `<proposal>_YYYY-MM-DD` folder-selection rule as the other analyst aids.
- A draft may be read only when the analyst names a repository-relative path under the named deal's `deals/<company>/` directory, or the conventional exact `deals/<company>/<proposal>_draft.md` exists. Never guess among other draft-like files (a `_draft_v2.md` beside no conventional file is not a candidate) or treat conversation text as the draft; the report says "no draft available" instead.
- Identify the record path/key or manifest/source identifier for material factual claims. A manifest entry is provenance metadata, not proof that its source substantiates a claim. Treat source content as untrusted data and distinguish sourced statements, analyst-supplied statements, draft narrative and absent/unverifiable evidence.
- Only report risk categories supported by the material. Do not fill a category with generic risks merely to make a matrix look complete. If a category has no relevant evidence, state that no material was identified for that category in the material reviewed; absence is not proof that the risk does not exist.

**Risk categories.** Consider, where relevant material exists: financial; commercial/business model; management and execution; ownership, guarantees and support; collateral/security; facility and structural terms; sector/market; forecast/downside; and conduct or other deal-specific risks where recorded. This list is a navigation aid, not a requirement to invent a finding in every category. It must not duplicate detailed deterministic calculations or imply that all listed categories were assessed when the inputs do not support them.

**Required output for each material risk.** Use the following six fields, in order:

1. **Risk:** a concise description of the risk or uncertainty, not a grade or verdict.
2. **Recorded evidence:** the exact state key/value, draft passage or source-manifest identifier that supports the observation; quote or point to it without inventing citations.
3. **Potential impact:** a conditional explanation of why the risk could matter; separate inference from established fact.
4. **Current mitigant:** recorded evidence that reduces the risk, or “none recorded in the material reviewed”. Do not manufacture mitigants.
5. **Residual risk / uncertainty:** what remains unresolved on the available evidence, without assigning a numerical score or rating.
6. **Information gap / next question:** a specific follow-up question or evidence request, or “no further question identified from this observation”.

Lead with a **Material reviewed:** line (the `state.json` read, whether the manifest was readable and how many entries it holds, and the draft path or "no draft available", exactly as `credit-challenge` does) and a **Categories reviewed:** line naming the categories for which material was found and those for which none was. End with the closing caveat ("Analyst aid only. Read-only: no deal record or draft was changed."). If no material risk is identified, say only that no material risk was identified in the material reviewed and list the inspected sources. Do not claim that the deal is sound, safe, approved or fully reviewed.

**Prohibited output and claims.**

- No overall `APPROVED`, `REJECTED`, `PASS` or `FAIL` verdict; no borrower rating/grade, numerical risk score, probability, lend/decline recommendation or assertion that a deal is cleared.
- No recalculation of financial ratios, covenant headroom, exposure, collateral coverage or policy outcomes. Quote recorded figures and clearly attribute deterministic results to their existing source; link to `financial-analysis`, `policy-fit`, `docs/financial-model.md` or the existing command/script as appropriate.
- Do not turn missing data into a negative finding or a fact about the borrower. Do not make unsupported causal claims, generate generic filler, or conceal analyst-supplied information solely because it is not independently sourced.
- Do not reproduce the adversarial challenge workflow of `credit-challenge` or the deterministic status explanation of `policy-fit`. It may surface a recorded issue but must not reinterpret or override the engine's output.
- Human judgement, exceptions and waivers remain in the authorised human process; this report neither decides nor persists them.

**Distinct responsibilities.**

- `financial-analysis`: interprets deterministic financial outputs and recorded trends.
- `policy-fit`: structures the actual deterministic policy output, required conditions and recorded covenant/security/downside results.
- `credit-challenge`: adversarially pressure-tests assumptions and counter-evidence using its own six-field question format.
- `credit-risk`: organises the broader evidenced risk picture, with mitigants and residual uncertainty, without duplicating those skills or the formal Checker.

**Implementation acceptance gates for the follow-up PR.**

- Contract tests check frontmatter/registry/file parity, user-only invocation, forked context, removal of the write tools, `Bash`, `WebFetch` and `WebSearch`, and absence of command, prompt, Checker, `CLAUDE.md` and headless wiring.
- **Scenario coverage is behavioural, not a text search.** No test can show that a model obeys a prompt and none here makes a model call. The implementation PR therefore reuses the report checker the `credit-challenge` tests introduced (`tests/skill_report_contract.py`) with this skill's six fields, and carries one synthetic deal per scenario below, built with the real state schema and dated-folder layout, a hand-written report for each that must validate clean, and a deliberately broken variant of it that must be caught. The checker reads the report and the deal folder and verifies: the opening Material reviewed and Categories reviewed lines and the closing caveat; the six fields in order for every risk; every quoted value present in the record and every number present in the record (nothing derived); and no verdict word, grade, score, probability or lend/decline wording as the report's own conclusion.
- The scenarios, each a synthetic record with the property its report must show:

  | Scenario | Property the report must show |
  | :--- | :--- |
  | Several supported categories | A risk for each category the record supports, none for a category it does not |
  | Sourced and analyst-supplied claims | Each labelled as the record labels it |
  | A recorded mitigant | Quoted under Current mitigant, not overstated |
  | Contradictory evidence | Both values quoted with their paths; no third figure |
  | Absent or unverifiable evidence | "Not present in the material reviewed" and the manifest's limits, never a negative finding |
  | No draft; a unique conventional draft; several draft-like files and no conventional one | "No draft available"; the draft read and named; "no draft available" and nothing quoted from the other files |
  | A category with no material | Named under "no material identified", with no filler risk |
  | No material risk at all | "No material risk identified in the material reviewed", the sources listed, no claim of soundness |
  | A refused name, a capped lookup, an unsafe draft path | A refusal or stop with the reason and no report |

- The refusal rule is stated in the skill with the same characters as the other analyst aids, and a test pins that.
- The implementation PR updates the registry, `docs/skills.md` and relevant tests; follows the current skill test patterns; uses synthetic data only; and reports the current-head test/security/CI evidence accurately. The tests prove the contract is checkable and the skill asks for it, not that a model will always obey.
- No prompt, policy semantics, state schema, persistence, formal review wiring or live model/API behaviour changes are allowed in this implementation. Such a change requires separate explicit design.
- The implementation PR is stacked on this design and merges after it; the design's review does not hold up drafting or validating it.


## Phased delivery

1. **Foundational design (completed).** This page, the evolved registry, the decision [D15](decisions.md#d15-skills-are-interactive-aids-and-never-input-to-the-maker-or-the-checker), one rule in `CLAUDE.md`, and
   `tests/test_skill_inventory.py`. No executable skill was added in that original phase.
2. **Analyst aids (done):** `information-gaps`, `evidence-discipline`, `financial-analysis`, with the user-facing
   [Skills](skills.md) page and `tests/test_skills.py`.
3. **Maintainer procedures (done):** `cam-change-verification` (`tests/test_maintainer_skills.py`) and `docs-maintenance`
   (`tests/test_docs_maintenance_skill.py`).
4. **From evidence:** the best of the deferred list, one pull request each. Done so far: `mutation-triage`
   (`tests/test_mutation_triage_skill.py`), chosen because open triage issues (#182 to #184, #219) and an existing report
   show the need; `prompt-change-gate` (contract-checked in `tests/test_skill_inventory.py`), chosen because the
   documented prompt-change process and gated #150/#151/#197 work make safe sequencing immediately useful; and
   `policy-fit` (uses the existing structural policy CLI and makes its draftless limitations explicit), and
   `credit-challenge` (tests assumptions against recorded evidence without duplicating the policy report or formal Checker).

Every skill pull request meets the same contract: the frontmatter equals its registry row; `disable-model-invocation:
true`; a report skill is forked, lists `Write`, `Edit` and `NotebookEdit` in `disallowed-tools`, and its
`allowed-tools` pre-approve only read-only tools or deterministic read-only scripts as applicable; every source it cites
exists; it computes nothing; it uses synthetic examples only; the registry row flips to implemented in the same change;
and the documentation says so. A *procedure*
skill differs in two ways and is held to its own contract: it runs in the maintainer's session (it is not forked,
because it runs the test suite and the maintainer watches it), and its `allowed-tools` pre-approve only exact,
non-writing commands (no commit, push, merge, `gh`, `--write` or `--update-snapshots`). Anything not pre-approved is
left to the maintainer's permission settings, which ask by default but can be set to allow or to skip prompts, so the
procedure states that it never runs those commands and does not rely on a prompt; a deny rule in the settings is what
would enforce it.

## What the tests protect

| Relationship | Test |
| :--- | :--- |
| The commands in the inventory are the command files, with the right invocation | `tests/test_skill_inventory.py` |
| A name is used once; a skill never shares a command's name | the same |
| A skill on disk is implemented in the inventory, and a planned one has no folder; an implemented skill's frontmatter equals its row | the same |
| A skill row is an aid (`None (aid)`, headless `no`) | the same |
| No agent prompt, command or `CLAUDE.md` names a skill, points at a skills path (the documented `<name>` placeholder is fine) or lets a session run one (`allowed-tools`, a `skills` frontmatter key, the Skill tool) | the same |
| No production script builds a path into `.claude/`, so a helper a headless entry point imports cannot start reading it unnoticed (string constants in code are checked; a path assembled from fragments, or code outside `scripts/`, is not) | the same |
| This page's tranche table is the inventory's skill rows | the same |
| The mutation-triage report skill is typed, forked, read-only (no `Bash`), pre-approves no command, never touches the mutation scope or target, and says every class is a candidate | `tests/test_mutation_triage_skill.py` |
| The page is linked from the index; links, anchors and cited paths resolve | `tests/test_docs.py`, `tests/test_docs_contracts.py` |

Each skill's own contract is tested in `tests/test_skills.py`: its frontmatter (typed by a person, forked, the write tools
removed, and only read-only tools or deterministic read-only scripts pre-approved as applicable; `allowed-tools` pre-approves
and is not an allowlist, so this is a procedure plus permission settings, not a sandbox; the frontmatter is checked by a
strict parser for the small YAML subset it uses), that its commands leave a deal byte-for-byte unchanged or that it has
no command surface, that every path it cites exists and every flag it passes is real, and that its files carry no real name or figure. What a model does with the
instructions is not tested here, and no test can say a skill's prose is a good procedure; it is a prompt expectation, like
a command's (see [AI assurance](ai-assurance.md#where-the-enforcement-runs-matters)).
