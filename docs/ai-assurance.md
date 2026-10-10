# AI assurance

How far a generated Credit Assessment Memorandum can be trusted, and what each part of that trust rests on. The page separates four things that are easy to run together: what **code** guarantees, what the **automated tests** show, what the **model and prompts** are only expected to do, and what a **person** must decide. It is evergreen: it says what kind of evidence exists, not what has been measured lately. The current evaluation status is dated in the [project status](README.md#project-status) and tracked in issue #151.

If you read nothing else: **a passing code check shows the draft is consistent with the computed facts and complete against the deal's own structure. It does not show the narrative is true, and `APPROVED` is a review verdict, not a credit decision.**

## The four kinds of assurance

| Layer | What backs it | How strong | Where it stops |
| :--- | :--- | :--- | :--- |
| **Code guarantee** | Deterministic scripts and their tests | Exact and repeatable: the same input gives the same answer | Only what can be computed from the recorded structure (figures, covenants, conditions, completeness) |
| **Test evidence** | The pytest suite, run without a model | Shows the code around the model behaves, including when the model misbehaves in the ways the tests script | Says nothing about what a real model produces |
| **Prompt expectation** | `agents/underwriter_agent.md`, `agents/risk_reviewer_agent.md` and the command files | Intent: what the model is told to do | Unmeasured until a live evaluation exists; a model can ignore or misread any instruction |
| **Human responsibility** | The analyst, the credit officer, the maintainer | The only layer that decides | Depends on the person reading the work, not just the verdict |

The layers are meant to be stacked, never substituted: a prompt is not asked to do what code can do exactly ([decision D2](decisions.md#d2-anything-that-can-be-computed-is-computed-by-code-and-enforced-by-code)), and code is not credited with judgement it cannot exercise.

## What the code guarantees

These hold for any input the scripts accept, whatever the model wrote.

**Figures.** Every subtotal and ratio in a deal is recomputed from raw line items by `spreading_builder.py` (run through `spreading_check.py`); a model is never asked to calculate one. A ratio whose denominator is zero or negative is `N/A` (`None`), never a misleading number, and a covenant on an `N/A` ratio is `UNRESOLVABLE`, never `PASS` and never a false `FAIL` ([D5](decisions.md#d5-a-ratio-with-a-non-positive-denominator-is-na-and-its-covenant-is-unresolvable)). The exception is a deal whose figures an analyst supplied: those are recorded as given, labelled `analyst-supplied`, and the draft must say so ([D4](decisions.md#d4-analyst-supplied-figures-are-recorded-as-given-and-disclosed-never-silently-reconciled)).

**Policy facts.** From the recorded deal alone, `policy_engine.py` derives the required conditions precedent and subsequent, the security gaps (an uncharged asset, an unperfected charge, a subordinate ranking), each covenant's result, and any covenant that passes in the base case but fails under the downside stress. Forward-year covenant results are recorded as disclosure and can never reject a deal by themselves ([D6](decisions.md#d6-forward-year-covenant-results-are-disclosure-only)). See [financial model](financial-model.md#what-the-policy-engine-decides).

**Draft completeness.** `policy_checks.py` compares a draft with those facts and returns fixed-wording reasons ([the list](troubleshooting.md#review-and-rejection)): a required condition missing from the draft's declarations, a risk category not covered or wrongly marked not applicable, an undisclosed downside breach, a reported figure that differs from the computed one, no declared sources, an undisclosed analyst-supplied basis, a calibrated policy not declared as considered.

**Verdict precedence.** A code-enforced reason makes the verdict `REJECTED` whatever the Risk Reviewer said, and the override only ever goes that way. A reviewer reply that cannot be parsed is treated as `REJECTED`, not as an approval. The headless pipeline allows three review iterations and, if none is approved, exits non-zero without exporting.

**State and records.** Every step checkpoints to `state.json`; a state written by a newer framework is never overwritten or downgraded; malformed state fails with one clear error line rather than a traceback or a silent default ([D7](decisions.md#d7-a-state-file-is-never-downgraded-and-malformed-state-fails-clearly)); each export validates state before it creates a folder. A headless run records the models and a content hash of each agent prompt in `model_provenance`.

**An analyst-supplied forecast.** `/project`'s analyst-supplied mode records forward-year figures as given and never recomputes them (`supplied_forecast.py`; a test makes the framework's formulas fail if they are called to produce a recorded value). The framework's stress shocks reach such a year only if its raw lines are complete (an omitted line is not a confirmed zero) and reproduce the figures the analyst supplied; otherwise the downside is the analyst's own scenario or is recorded as unavailable, and the policy engine reports a covenant that passes in that year's base case as UNRESOLVABLE under stress, which the draft check will not let a draft ignore. Whether the analyst's forecast is *right* is theirs ([financial model](financial-model.md#analyst-supplied-forecasts-and-their-downside)).

**Figures read from an image.** A screenshot or scanned page is read by the session, not parsed by code, so a misread
figure could become the deal's ground truth. `transcription_check.py` makes the order of events and the arithmetic
code: nothing is written for image figures until a read-back of those exact figures was produced and its digest quoted;
a stale digest or an unresolved cross-foot discrepancy refuses the commit with nothing written; the image is saved as
the source of record and the state records that the figures were transcribed from it; every sign is read under a
declared convention that is shown and confirmed, and a mismatch that a different sign reading would explain cannot be
acknowledged away; the confirmation is bound to the image's bytes (a replaced screenshot is refused) and one deal
never mixes the two `/spread` modes; every predictable state or schema problem is detected before the image is
saved, so a refusal leaves the deal untouched, and the figures, the transcription record and the completed step are
written in one state update (the saved image precedes it and is not part of that update)
([financial model](financial-model.md#figures-read-from-an-image)). What stays outside the code: that the analyst
really said yes (the session passes the digest), that the transcription is true to the image, and that an image is
routed through the script at all (instructions in `.claude/commands/spread.md`). The read-back and the analyst's
comparison with the image are the safeguard; the cross-foot only finds slips that break a subtotal or the balance sheet.

**Confidentiality.** A test fails CI if any protected location is tracked, loses its ignore rule or gains an unclassified one ([D9](decisions.md#d9-anything-derived-from-real-material-is-git-ignored-and-a-test-enforces-it)).

### Where the enforcement runs matters

The same checks serve both interfaces, but they are applied differently, and the difference is part of the assurance story.

| | Headless (`orchestrator.py`) | Slash commands (`/assemble`, `/review`) |
| :--- | :--- | :--- |
| Who runs `policy_check`'s logic | The Python pipeline, as code, on every review iteration | The session model, following a Bash step in the command text |
| Who applies "code-enforced reason means `REJECTED`" | `_apply_deterministic_policy_checks()` in code | The model, because `review.md` says it must |
| Who stops the loop and decides to export | The pipeline | The model, following `/assemble`'s steps |
| Provenance recorded | Models and prompt hashes in `model_provenance` | None |
| What the Checker's context contains | A separate model call: one new message of the Checker prompt, the grounding context and the draft, with no Maker prompt or reasoning (pinned by `test_orchestrator.py`), optionally on a different model (`checker_model`) | The same conversation as the draft: `/assemble` and `/research` run `/review` inline, so the Maker's reasoning and anything else said earlier is visible to it, and it uses the session's one model |

So the Checker's independence is a different thing on each interface. Headless, it is a separate call. In a slash-command
session it is a separate role prompt plus the deterministic checks, in the draft's own conversation: conversation-level
independence is not guaranteed ([#204](https://github.com/ShamikM88/open-cam-framework/issues/204) tracks whether to
isolate it). Where an audit's independence matters, use the headless pipeline, or review a saved draft in a fresh
session (`/review` accepts the draft as an argument).

So in a slash-command session the deterministic *reasons* are exact, but whether they are run, quoted verbatim and obeyed depends on the model following the command text. That compliance is a prompt expectation, not a code guarantee, and it is part of what a live evaluation of the command path would have to measure (the current harness cannot drive it; see [what the harness cannot show](evaluation.md#what-the-harness-can-and-cannot-show)). A reader who needs the code guarantee end to end uses the headless pipeline, or runs `policy_check.py` themselves and compares.

A related wording note: `agents/risk_reviewer_agent.md` still says that the `/review` command is not wired to the deterministic layer. It is, whenever `/review` is run for a deal (see [`/review`](commands.md#review)); it is not for a standalone review with no deal identity or for a research brief. The prompt is a production file and is not changed here; the correction is tracked in #197 and goes through the prompt-change process in [contributing](contributing.md#changing-an-agent-prompt).

## What the automated tests show

The suite runs with no API key and no network; every model call is replaced by a scripted reply. That makes it good at one kind of claim and silent on another.

| Claim | Evidence | What a pass means |
| :--- | :--- | :--- |
| A ratio, subtotal or covenant result is computed correctly | Unit tests, property tests (invariants such as "N/A whenever a denominator is not positive"), worked-example tests that quote the documentation's figures, and the weekly mutation report | The formulas give the documented numbers and a deliberately broken formula is noticed |
| A draft that omits a required item is rejected, and an `APPROVED` reply cannot override it | `test_policy_checks.py`, `test_policy_check.py`, and the override tests in `test_orchestrator.py` | For every shape of gap the tests construct, the code says `REJECTED` |
| The pipeline revises, checkpoints, exports and stops as designed | `test_orchestrator.py` with scripted Maker and Checker replies | The loop logic is right *given* those replies |
| The prompts and the code describe the same contract | `test_prompt_consistency.py` | The structured-output example parses, its schema matches the parser, the risk categories and grounding-context headers named in the prompts exist, and every `Guideline N` / `Audit Checklist item N` reference resolves |
| An exported `.docx`/`.xlsx` has the intended content | Golden snapshots, checks for leaked Markdown or `nan` | The rendering is unchanged unless a reviewer approved the change |
| Old and malformed state is handled | State fixtures and the malformed-shape matrix | A consumer fails clearly or tolerates the shape on purpose |
| The documentation's examples are real | `test_docs_examples.py` re-runs a synthetic deal through the real scripts | Every quoted figure and message is what the scripts print |
| Nothing confidential is tracked; CI keeps its security rules | `test_confidential_paths.py`, `test_ci_workflow.py` | The repository's own files satisfy the rules |

Coverage floors say the governance modules are exercised; mutation testing asks whether the tests would notice a change ([D13](decisions.md#d13-mutation-testing-and-coverage-are-diagnostics-and-floors-not-a-scoring-game)). Both are diagnostics about the tests, not about the model.

### What the suite cannot show

- **That a real model follows a prompt.** A test feeds the pipeline a reply it wrote itself. It shows how the code treats that reply, not that any model would produce it.
- **That a narrative is grounded.** The code checks that the draft *declares* sources and that the figures it *declares* match the computed ones. A draft can declare a source and still state an unsupported fact in prose; nothing in the suite can tell.
- **That planted instructions are ignored.** Text in a source document, a collateral description or a persisted note reaches the model as text. No deterministic test shows the model treats it as data; that is the open work in #150.
- **That a slash-command session follows its command text** (see above).
- **Judgement quality.** Whether a mitigant is weak, a risk is understated or a tone is right is for a reader.

## What the prompts expect of the model

The two agent prompts are the specification of the model's job. Read as expectations, not guarantees:

- **The Underwriter** drafts from the supplied facts; uses the computed figures rather than its own; never invents a figure; cites a source for every qualitative claim; discloses an analyst-supplied basis; declares what it covered in the trailing structured block (conditions, risk categories, figures, sources).
- **The Risk Reviewer** audits with its own role prompt (independently of the Maker's reasoning on the headless path; in the same conversation as the draft in a slash-command session, see below), challenges ungrounded assertions and weak mitigants, applies a calibrated credit policy (a violation is a mandatory rejection finding), and returns `APPROVED` or `REJECTED` in a fixed JSON shape.

The code can verify only the *declared form* of the Underwriter's behaviour (the block exists, parses, lists what the structure requires and agrees with the computed numbers). Whether the prose behind the declarations is honest is the model's job and, ultimately, a person's. This is why the Checker is a separate prompt on a separate call ([D1](decisions.md#d1-the-maker-and-the-checker-are-independent-prompts)): it is a second, independent attempt at the part the code cannot do, not a duplicate of the part it can.

## What live evaluation has not shown

Until a versioned baseline exists, none of the expectations in the previous section has been measured against a real model. The [project status](README.md#project-status) says whether that is still the case. Specifically, nothing yet establishes any of: how often a model invents a figure when none is supplied; how often it flags a contradiction between sources; how it behaves when a source contains an instruction; whether the Checker rejects a flawed memo it should reject and approves a clean one.

The **evaluation harness** (issue #151, [evaluation](evaluation.md)) is the instrument built to measure exactly that, and it is deliberately constrained: run by hand with your own key, synthetic data, a hard call cap, results kept out of git. What it will report is limited in ways worth stating up front:

- A **pass rate is an observation** for one model, one set of prompt hashes and one dataset version. A rate over repeated runs is the unit; a single run says almost nothing, and no rate means "proven safe".
- **Most oracles check form**, not judgement. A "no invented figures" category passing means the model emitted a well-formed, self-consistent block, not that its narrative was careful. Judgement lives in a human-review pack with no pass/fail.
- A **canary** oracle reports that a planted token *appeared* in the output, not that it was *obeyed*, and cannot see obedience that leaves no token.
- Version 1 exercises the headless pipeline's surfaces and a **prompt-level approximation** of a source document. It does not drive the slash-command path, so it cannot establish injection resistance for `/research` or `/commercial` as they actually run.

### Why prompt hardening (#150) waits for a baseline

Issue #150 adds a clause to the agent prompts: source and fetched content are data, not instructions. That is a prompt edit, and a prompt edit changes the content hash that a deal records and that a baseline records. To say whether the edit helped, did nothing or quietly weakened something, there must be a measurement *before* it, taken with the same model, dataset version and repeated runs, to compare with one *after*. Without a valid, versioned baseline there is nothing to compare against, so the change would go in on argument alone and any regression (a Checker that starts rejecting clean memos, a Maker that hedges every figure) would be invisible. The issue therefore sequences hardening after the baseline, and its acceptance test is a rate with a model ID and prompt hashes, not a demonstration.

The same reasoning is why prompts are edited only deliberately, never as a side effect ([contributing](contributing.md#changing-an-agent-prompt)).

## What stays with a person

Nothing in this framework approves credit. These remain human decisions:

| Decision | Why it cannot be automated here |
| :--- | :--- |
| The credit decision, and whether an `APPROVED` memo is approved | `APPROVED` means the Risk Reviewer found no blocking issue and the code found nothing missing; it is not authority to lend |
| Risk inputs: PD, LGD, bureau score; the orchestrator's `--pd`/`--lgd` example defaults | They are judgements; the defaults are examples that flow into the memo if the flags are omitted |
| Which sources are the documents of record, and whether a claim really follows from them | The code checks that sources were declared and saved, not that they say what the memo says |
| Confirming a spreading convention, a policy interpretation or a deal learning for reuse | Persisted conventions are local memory that applies to later deals; the framework asks and never infers consent ([D8](decisions.md#d8-persisted-conventions-are-deterministic-local-memory-confirmed-by-a-person)) |
| The content of the credit policy and style guide, and any exception to it | Calibration summarises documents; a person owns what it says |
| Reading the narrative and the Reviewer's notes, not only the verdict | Form checks and an independent model cannot catch every ungrounded or misleading sentence |
| For maintainers: editing a prompt, promoting a baseline, changing a floor or a rule, merging | Each changes what earlier evidence means ([contributing](contributing.md), [operations](operations.md)) |

## Reading a claim against its evidence

A quick way to use the page: take a sentence someone says about the system and find what stands behind it.

| "The system..." | Backed by | Verdict |
| :--- | :--- | :--- |
| "...computes DSCR correctly" | Formula tests, property tests, a worked example the docs re-run | Code guarantee, tested |
| "...cannot approve a memo with a missing condition precedent" | `check_draft_compliance` plus the override; in headless it is code, in a slash-command session it is command text | Code guarantee headless; prompt expectation on the command path |
| "...never invents figures" | The Underwriter prompt, plus a code check that *declared* figures match | Prompt expectation, form-checked only; **not measured** |
| "...ignores instructions hidden in a source document" | Nothing yet (#150, #151) | **Not demonstrated** |
| "...would catch a weak risk mitigant" | The Checker prompt | Prompt expectation, **not measured** |
| "...keeps borrower data out of git" | `test_confidential_paths.py` and the ignore rules | Test-enforced for the listed locations; process rule for issue and PR text |
| "...the Checker audits independently of the Maker" | Headless: a separate call carrying no Maker prompt or reasoning (`test_orchestrator.py`). Slash command: a separate role prompt in the draft's own conversation | True of the headless pipeline. **Not guaranteed at conversation level** on the slash-command path (#204); the code-enforced checks apply on both |
| "...records which model wrote this deal" | `model_provenance` for headless runs | True for headless runs only; slash-command runs record none |
| "...cannot be steered by a Claude Code skill" | No agent prompt, command or `CLAUDE.md` names a skill and no script builds a path into `.claude/` (`test_skill_inventory`; a path assembled at run time is out of its reach); the design is [Skill design](skill-design.md) | Structural for the prompts and the headless path. In a session a skill's output stays visible to later steps, so not an isolation guarantee (#204) |
