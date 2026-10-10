# Skill design

How Claude Code skills fit this repository, and what they are not allowed to become. This page is the Phase 1 design
for [#196](https://github.com/ShamikM88/open-cam-framework/issues/196): it settles the questions the later phases
build on. The three analyst aids of Phase 2 are implemented and described in [Skills](skills.md); the maintainer
procedures of Phase 3 are not. The inventory of commands and skills is
[`config/skills_registry.md`](../config/skills_registry.md); this page is the reasoning behind it, and
`tests/test_skill_inventory.py` keeps the two in step with the files.

## In brief

- A **command** answers "what workflow do I run?" (`/spread` spreads). A **skill** answers "how should I work on this
  kind of job?" (`financial-analysis` explains what `/spread` recorded). A skill points to the rule and runs the
  script; it is never the only copy of either.
- Every skill in this initiative is an **aid**. It is not the Maker's or the Checker's input, leaves no record in a
  deal, and has no headless equivalent. A skill that would change what the Maker or the Checker is told is a different
  kind of thing and is not permitted here.
- All five skills in the first tranche are **typed by a person** (`disable-model-invocation: true`). The model never
  loads one on its own, and no command or prompt names one.
- The registry file is **evolved in place** into the one inventory, not renamed and not duplicated.
- `annual-review` cannot be built over today's primitives ([#203](https://github.com/ShamikM88/open-cam-framework/issues/203)),
  and the independence of `/review` on the slash-command path has to be settled before an analyst-facing challenge
  skill is introduced ([#204](https://github.com/ShamikM88/open-cam-framework/issues/204)).

## What Claude Code does that this design depends on

These are the behaviours of Claude Code, taken from its documentation, that the decisions below rest on. They are
Claude Code's, not this repository's, so whoever implements a skill re-reads the current documentation first; the
registry test cannot see a change in the platform.

| Platform behaviour | What the design does about it |
| :--- | :--- |
| A command file and a skill create the same `/name`, and **the skill wins** on a clash | Names are unique across both kinds, enforced by a test. A skill called `triage` would silently disable `/triage`. |
| `disable-model-invocation: true` keeps a skill's description out of the model's context and lets only a person invoke it | The invocation of every first-tranche skill. It also means `/assemble`'s review loop cannot reach one. |
| `paths` limits automatic activation to matching files; command files do not support it | Reserved for a later, path-scoped maintainer skill. None in the first tranche. |
| An invoked skill's text **stays in the conversation** and is re-attached after compaction | Anything loaded stays visible to every later step in the session, `/assemble` and `/review` included. This is why an aid is forked (next row) and why this page does not claim isolation. |
| `context: fork` runs the skill in a subagent that **does not see the conversation**; only its result comes back | The aid skills fork: their instructions never join the conversation, and they read the deal from `state.json` as the rehydration rule already says. |
| `allowed-tools` **pre-approves** tools for the turn; it does not restrict them. `disallowed-tools` removes tools while the skill is active | Read-only is expressed with `disallowed-tools` as well as a short `allowed-tools`, and reviewed like a workflow change: a checked-in skill can grant itself broad tool access. |
| The description is capped (1,536 characters with `when_to_use`) and sits in context for every model-invocable skill | Another reason to keep the first tranche user-only: no always-on descriptions. |
| Skills belong to a Claude Code session | `orchestrator.py`, `calibrate.py` and `run_evals.py` call the Anthropic SDK with prompts they build themselves. They cannot load a skill, and a test keeps them from reading `.claude/`. |

## Taxonomy

Two things decide what a skill is allowed to be: who it is for, and what it does to a session.

| Effect | What it is | Allowed here |
| :--- | :--- | :--- |
| **Report** | Reads a named deal's records and scripts' output and gives the analyst an account in chat. Read-only, forked, writes nothing. | Yes: `information-gaps`, `evidence-discipline`, `financial-analysis` |
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

**What this does not claim.** A report skill's *output* appears in the conversation, and a later `/assemble` or
`/review` in the same session can read it, exactly as it can read anything the analyst types. The forking keeps the
skill's instructions out of the session, and the skill's output is labelled as an aid and written nowhere. That
narrows the exposure; it is not isolation. Run the aids before drafting, or in another session, when the independence
of the audit matters.

**Held by.** The `Role in the pipeline` column (a skill row must say `None (aid)`), and the test that no agent prompt
or command names a skill, points at the skills folder or lets a session run one.

### 2. Headless parity

**Decision.** All five skills are interactive only. They write no `state.json` key, append nothing to
`steps_completed` or `review_trail`, create no draft, export, convention, learning or saved source. So no deal can
differ because someone used one, and no record implies a capability the headless pipeline lacks.

**Why.** `orchestrator.py` and `calibrate.py` are SDK scripts and cannot load a skill. The same asymmetry already
exists for persisted conventions: the headless pipeline can *inherit* what an analyst confirmed but cannot *originate*
it ([architecture](architecture.md#two-ways-to-run-it)). A future skill that needs to keep something does so through an
existing script the headless side can read, or it does not keep it.

**Held by.** `Headless` is `no` for every skill row, and no production script (so no helper a headless entry point imports) is allowed to build a path into `.claude/`.
Each skill's own tests (Phase 2 and 3) check that it can run only read-only scripts.

### 3. Maker-Checker independence

**Decision.** `credit-risk` and `credit-challenge` are **not in the first tranche**. When they are designed they are
typed by a person, forked, named nowhere in `agents/` or a command, write nothing to `review_trail`, and never print a
verdict word. They are not a substitute for `/review`.

**Why the gate.** On the slash-command path `/review` runs in the *same conversation* as the draft: `review.md` loads
the role "for the rest of this task" and reviews "the most recently drafted content earlier in this conversation", and
`/assemble` runs it inline. The Checker's independence there is a separate role prompt plus the deterministic checks,
not a separate context; the headless pipeline makes a separate Checker call. So "never in the Checker's context"
cannot be promised for anything that has been in the session. That is a property of the existing design, not of
skills, and it is tracked in [#204](https://github.com/ShamikM88/open-cam-framework/issues/204). The adversarial
skills wait for that decision.

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
| Missing steps, whether declared citations were saved at all, required conditions, UNRESOLVABLE results | `state_manager.py --check-steps`, `source_manifest.py --check-sources`, `policy_check.py` (without `--draft`) | A per-citation audit: `--check-sources` is a deal-level floor over `triage` and `commercial` citations only, and does not yet fail cleanly on a malformed manifest ([#207](https://github.com/ShamikM88/open-cam-framework/issues/207)); the skills read the manifest themselves and report such a failure as a tool error |
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
scripts that exist today; the two procedures wrap documented sequences that a maintainer runs on every change.

| Skill | Phase | Audience | Invocation | Effect | Interactive only |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `information-gaps` | 2 | analyst | user | Report | Yes, no record |
| `evidence-discipline` | 2 | analyst | user | Report | Yes, no record |
| `financial-analysis` | 2 | analyst | user | Report | Yes, no record |
| `cam-change-verification` | 3 | maintainer | user | Procedure | Not a deal skill |
| `docs-maintenance` | 3 | maintainer | user | Procedure | Not a deal skill |

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
- **Side effects.** Runs tests locally (no network, no model) and writes coverage artefacts. It runs
  `check_test_count.py --write` only after a complete green run and a maintainer's go-ahead, and it never commits,
  pushes, merges or touches repository settings.
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

## Later, and why

| Skill | Status | Reason and gate |
| :--- | :--- | :--- |
| `credit-assurance` | Deferred, evidence-gated | Its rules (deterministic enforcement is authoritative, fail closed) are already in `CLAUDE.md`, D2 and D5 to D7 and enforced by tests; a path-scoped skill would restate them. Built only if a governance regression gets past those, and then as a change-impact map, not a copy of the rules. |
| `security-and-confidentiality` | Folded into `cam-change-verification` | Its procedure is the security review in Contributing and `pii_scan.py`; its rules are in `CLAUDE.md` and `test_confidential_paths.py`. |
| `policy-fit` | Phase 4 candidate | Explains PASS / FAIL / UNRESOLVABLE and the disclosure-only results, which [the financial model](financial-model.md) and [troubleshooting](troubleshooting.md) already do; it must never read as an override of a code-enforced rejection. Decide after the Phase 2 reports show the pattern. |
| `credit-risk`, `credit-challenge` | Gated by [#204](https://github.com/ShamikM88/open-cam-framework/issues/204) | Close to the Checker's job (constraint 3). |
| `annual-review` | Blocked by [#203](https://github.com/ShamikM88/open-cam-framework/issues/203) | Needs primitives that do not exist (constraint 7). |
| `covenant-and-downside`, `collateral-security`, `borrower-research`, `committee-prep`, `deal-consistency` | Not designed | Chosen from how Phase 2 is used. Four overlap a command or a check (`borrower-research` with `/research` and `/commercial`, `committee-prep` with `/assemble`, `deal-consistency` with `policy_check.py` and `/review`, `covenant-and-downside` with the policy engine) and must show what they add. |
| `state-migration`, `prompt-change-gate`, `mutation-triage`, `release-readiness` | Not designed | Each overlaps a runbook section already in Contributing or Operations. |
| The issue's "nice to have" skills | Out of scope | Unchanged from the issue. |

## Phased delivery

1. **Design (this change).** This page, the evolved registry, the decision [D15](decisions.md#d15-skills-are-interactive-aids-and-never-input-to-the-maker-or-the-checker), one rule in `CLAUDE.md`, and
   `tests/test_skill_inventory.py`. No skill.
2. **Analyst aids (done):** `information-gaps`, `evidence-discipline`, `financial-analysis`, with the user-facing
   [Skills](skills.md) page and `tests/test_skills.py`.
3. **Maintainer procedures:** `cam-change-verification`, `docs-maintenance`.
4. **From evidence:** the best of the deferred list, one pull request each.

Every skill pull request meets the same contract: the frontmatter equals its registry row; `disable-model-invocation:
true`; a report skill is forked, lists `Write`, `Edit` and `NotebookEdit` in `disallowed-tools`, and its
`allowed-tools` name only read-only scripts; every source it cites exists; it computes nothing; it uses synthetic
examples only; the registry row flips to implemented in the same change; and the documentation says so.

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
| The page is linked from the index; links, anchors and cited paths resolve | `tests/test_docs.py`, `tests/test_docs_contracts.py` |

Each skill's own contract is tested in `tests/test_skills.py`: its frontmatter (typed by a person, forked, the write tools
removed, only read-only scripts pre-approved; `allowed-tools` pre-approves and is not an allowlist, so this is a procedure plus
permission settings, not a sandbox, and the frontmatter is checked by a strict parser for the small YAML subset it uses), that the scripts it runs leave a deal byte-for-byte unchanged, that every path it
cites exists and every flag it passes is real, and that the files carry no real name or figure. What a model does with the
instructions is not tested here, and no test can say a skill's prose is a good procedure; it is a prompt expectation, like
a command's (see [AI assurance](ai-assurance.md#where-the-enforcement-runs-matters)).
