# Design decisions

The decisions that shaped the framework and that someone maintaining it still needs to know about. Each entry
records what was decided, why, and what follows from it. This is not a change log: a decision appears here only
while it still constrains the code. For the rules themselves, see `CLAUDE.md`; for how things work, see the
[documentation index](README.md).

Entries use one shape: **Decision**, **Context**, **Choice**, **Why**, **Consequences**, **Related**. Add a new
entry when a change settles a question that a future maintainer would otherwise reopen; edit an entry (and say
so in its Related line) when a later decision supersedes it.

## Contents

- [D1. The Maker and the Checker are independent prompts](#d1-the-maker-and-the-checker-are-independent-prompts)
- [D2. Anything that can be computed is computed by code and enforced by code](#d2-anything-that-can-be-computed-is-computed-by-code-and-enforced-by-code)
- [D3. Every step checkpoints to a file; the conversation is never the record](#d3-every-step-checkpoints-to-a-file-the-conversation-is-never-the-record)
- [D4. Analyst-supplied figures are recorded as given and disclosed, never silently reconciled](#d4-analyst-supplied-figures-are-recorded-as-given-and-disclosed-never-silently-reconciled)
- [D5. A ratio with a non-positive denominator is N/A, and its covenant is UNRESOLVABLE](#d5-a-ratio-with-a-non-positive-denominator-is-na-and-its-covenant-is-unresolvable)
- [D6. Forward-year covenant results are disclosure only](#d6-forward-year-covenant-results-are-disclosure-only)
- [D7. A state file is never downgraded, and malformed state fails clearly](#d7-a-state-file-is-never-downgraded-and-malformed-state-fails-clearly)
- [D8. Persisted conventions are deterministic local memory, confirmed by a person](#d8-persisted-conventions-are-deterministic-local-memory-confirmed-by-a-person)
- [D9. Anything derived from real material is git-ignored, and a test enforces it](#d9-anything-derived-from-real-material-is-git-ignored-and-a-test-enforces-it)
- [D10. Slash commands are the primary interface; headless scripts share the same core](#d10-slash-commands-are-the-primary-interface-headless-scripts-share-the-same-core)
- [D11. One text encoding: UTF-8, never character substitution](#d11-one-text-encoding-utf-8-never-character-substitution)
- [D12. The live-model evaluation harness is local, explicit and capped](#d12-the-live-model-evaluation-harness-is-local-explicit-and-capped)
- [D13. Mutation testing and coverage are diagnostics and floors, not a scoring game](#d13-mutation-testing-and-coverage-are-diagnostics-and-floors-not-a-scoring-game)
- [D14. The test-count badge is checked by CI and never corrected by it](#d14-the-test-count-badge-is-checked-by-ci-and-never-corrected-by-it)
- [D15. Skills are interactive aids and never input to the Maker or the Checker](#d15-skills-are-interactive-aids-and-never-input-to-the-maker-or-the-checker)

## D1. The Maker and the Checker are independent prompts

- **Context.** A model reviewing its own reasoning tends to share its blind spots.
- **Choice.** Two role prompts (`agents/underwriter_agent.md`, `agents/risk_reviewer_agent.md`) that never import
  each other or share the Maker's reasoning, optionally run on different models (`maker_model`,
  `checker_model`). `/research` gets a scoped-down Checker pass too (grounding and narrative checks, not the
  credit-policy and risk-mitigant ones, since no lending decision exists yet).
- **Why.** The Checker's value is auditing the Maker's output cold.
- **Consequences.** The prompts must not be merged or cross-referenced. Prompt text is recorded by content hash
  in a deal's `model_provenance`, and an evaluation baseline records it too, so editing a prompt is a deliberate
  act that makes earlier comparisons stale.
- **Related.** #31 (separate Checker model), #87 (`/research` review).

## D2. Anything that can be computed is computed by code and enforced by code

- **Context.** A model asked to calculate a ratio or to notice a missing condition will sometimes be wrong, and
  an LLM review of the same numbers is not an independent check.
- **Choice.** Subtotals and ratios come from `spreading_builder.py` (run through `spreading_check.py`), covenant
  results and required conditions from `policy_engine.py`; both agents receive them as ground truth, and
  `policy_checks.py` checks the draft against them. A code-enforced reason rejects a draft even if the Checker
  approved it, never the reverse.
- **Why.** Exact questions get exact answers; the model is left with narrative and judgement.
- **Consequences.** A slash command that needs figures runs a script in a Bash step instead of recalculating in
  prose. New governance facts (like D5 and D6) are added to the engine first and to the prompts later.
- **Related.** #98.

## D3. Every step checkpoints to a file; the conversation is never the record

- **Context.** A CAM is built over many steps in sessions that get compacted or resumed.
- **Choice.** Each step reads the deal's `state.json` first and writes its results when it finishes. Absolute
  figures, inputs and verdicts exist on disk, never only in chat. This is checkpointing at step boundaries, not a
  pre-compaction hook, because no such hook exists.
- **Why.** At worst a compaction or a resumed session loses only the step in progress.
- **Consequences.** Anything that needs a figure re-hydrates it from `state.json`. Resolving which dated folder a
  deal lives in is the caller's job (`state_manager.resolve_date_str()`), so a deal finished days later still finds
  its own state. Raw figures live in two separate stores that are never blended (see D4).
- **Related.** #97 (dated folders), #121 (two raw-figure stores).

## D4. Analyst-supplied figures are recorded as given and disclosed, never silently reconciled

- **Context.** Institutions treat some line items differently (for example Depreciation embedded in Cost of
  Goods Sold), so an analyst's own spread cannot always be recomputed from this framework's raw schema.
- **Choice.** `/spread` has two modes. The default recomputes every ratio from raw line items. The alternative
  records the analyst's figures exactly as given (`financials_source: "analyst-supplied"`), keeps any raw
  breakdown in a separate store, and requires the CAM to carry an explicit caveat about the reduced audit
  guarantee.
- **Why.** Pretending to have independently verified a figure that was not recomputed would contradict the
  grounding rule.
- **Consequences.** Every consumer of "this deal's raw figures" must check `financials_source` first. A
  stored ratio whose denominator is not recorded (an analyst-supplied DSCR, say) cannot be cross-checked, and
  that limit is accepted and documented.
- **Related.** #55, #121.

## D5. A ratio with a non-positive denominator is N/A, and its covenant is UNRESOLVABLE

- **Context.** Negative EBITDA or equity used to give a finite negative leverage or gearing, which sits below
  every maximum threshold, so a loss-making borrower passed "leverage <= 3.5x".
- **Choice.** Only the denominator is tested: zero or negative gives N/A. A covenant on an N/A ratio is
  UNRESOLVABLE for minimum and maximum covenants alike (never PASS, never a false FAIL), with a one-sentence
  reason. A negative numerator over a positive denominator is a real figure and is kept (DSCR below zero, net
  cash). The workbook's formulas follow the same rule so the spreadsheet and `state.json` agree.
- **Why.** A ratio that is not a number cannot be tested against a threshold, and saying so is more honest than
  either outcome.
- **Consequences.** UNRESOLVABLE has always been a code-enforced rejection reason, so a loss-making deal with an
  untestable current-year covenant is rejected until the covenant or the figures change. Prompt wording about
  this is deliberately later work.
- **Related.** #169, PR #175.

## D6. Forward-year covenant results are disclosure only

- **Context.** After D5, a covenant that fails or cannot be tested in a forward year's own base case appeared
  nowhere: current-year results cover the current year, and downside breaches report only a covenant that passes
  in the base case.
- **Choice.** A covenant applies to every projected forward year (`FY+1` to `FY+3`). `policy_state` keeps a
  complete `forward_covenant_results` list (PASS included, one entry per year and covenant, with a stable
  `forward_id`). It is a separate list from the current-year results, and no code-enforced reason reads it: a
  forward FAIL or UNRESOLVABLE never rejects a deal by itself, but it can never go unrecorded. The model is shown
  only the non-PASS entries, as plain data.
- **Why.** Projections are expected to be worse than history, and a code rejection would stop deals whose
  forward year is legitimately weak, while silence hides them. Downside breaches stay enforced, because those
  are structuring facts the draft must address.
- **Consequences.** The current-year list keeps its one-entry-per-covenant shape, which several consumers rely
  on. The state has no covenant tenor, so a covenant that really ends earlier is still reported for later
  projected years.
- **Related.** #176, PR #180.

## D7. A state file is never downgraded, and malformed state fails clearly

- **Context.** An older checkout writing to a newer state file would silently drop fields; a corrupt or
  wrongly-typed file used to surface as an opaque traceback.
- **Choice.** `write_state()` and `append_review_trail()` refuse a newer or malformed `schema_version`
  (comparing the numeric parts, so `1.10.0` is newer than `1.9.0`). Each consumer validates only the keys it
  reads and reports one `error:` line and exit status 1. Deliberate tolerances are kept (a null value is "not
  recorded"; the exporter treats some malformed shapes as empty).
- **Why.** Refusing is safer than guessing, and a deal is never rejected for a key the step does not use.
- **Consequences.** The fix for a newer file is to update the checkout, never to edit the version by hand.
  Slash commands that write `state.json` directly bypass the guard (a documented limit).
- **Related.** #170 (PR #172), #171 (PR #177), #152.

## D8. Persisted conventions are deterministic local memory, confirmed by a person

- **Context.** An analyst's confirmed spreading convention, a policy-interpretation correction or an end-of-deal
  takeaway would otherwise have to be re-established on every deal.
- **Choice.** Three file-based stores (spreading conventions, credit-policy notes, deal learnings), each written
  only on an explicit "yes", each disclosed where it is applied, none inferred from the conversation continuing.
  The headless pipeline can inherit what is already confirmed but can never originate a new entry.
- **Why.** This is local memory, not machine learning: nothing is trained, and an unconfirmed assumption never
  silently changes a deal.
- **Consequences.** Deal learnings are advisory only; credit-policy notes are mandatory for the Checker.
- **Related.** #86, #89.

## D9. Anything derived from real material is git-ignored, and a test enforces it

- **Context.** The repository is public and meant to be forked; calibration samples, a calibrated policy, deal
  folders and evaluation output are confidential by nature.
- **Choice.** Every location that can hold such material is listed in `.gitignore` before any code writes to it,
  and `tests/test_confidential_paths.py` fails CI if one is tracked or loses its rule. Shipped templates stay
  generic; anything derived from a user's documents stays in their fork.
- **Why.** A documented rule is easy to break; a failing test is not.
- **Consequences.** A new confidential location is added to the test's protected list in the same change as its
  ignore line. Real borrower names never appear in issues, PRs or examples; examples here are synthetic.
- **Related.** #149.

## D10. Slash commands are the primary interface; headless scripts share the same core

- **Context.** Many users already work in a Claude Code session and should not need a second credential, while
  automation needs something scriptable.
- **Choice.** The slash commands are primary; `calibrate.py` and `orchestrator.py` are the headless equivalents.
  The shared work (export, spreading, policy checks, state) lives in scripts with no `anthropic` dependency, so
  a slash command's Bash step and the headless pipeline run the same code.
- **Why.** One implementation of anything deterministic, and a clear cost boundary: the commands use the session's
  login, the scripts use an API key.
- **Consequences.** A capability that needs a person's confirmation (persisted conventions, `/calibrate-policy`)
  has no headless equivalent, and neither does `/research` (a standalone brief, not a CAM): the headless
  pipeline produces only the full CAM. See [Architecture](architecture.md#two-ways-to-run-it).

## D11. One text encoding: UTF-8, never character substitution

- **Context.** Python's default encoding on Windows is cp1252, which garbled non-ASCII text in prompts and
  crashed on output redirection.
- **Choice.** Every text file is UTF-8, read through one policy (`scripts/textio.py`): strict for shipped files,
  UTF-8 then cp1252 with a warning for user-owned legacy files, and an error for anything else. Characters are
  never replaced. Every script's entry point makes stdout and stderr UTF-8.
- **Why.** A pound sign quietly turned into `?` in a policy is worse than a failure.
- **Consequences.** New `open()` calls pass `encoding=` (a lint rule enforces it); new CLIs start their
  `__main__` block with `configure_stdio()` (a test enforces it).
- **Related.** #137, #154.

## D12. The live-model evaluation harness is local, explicit and capped

- **Context.** The test suite cannot show whether the actual model follows the prompts.
- **Choice.** `scripts/run_evals.py` runs only when invoked by hand, with the user's own key, on synthetic data,
  under a hard call cap checked before the key is read, writing only to git-ignored `evals/results/`. Nothing in
  CI or the tests runs it; the tests drive it with fake clients.
- **Why.** Cost, confidentiality and the difference between a form check and a judgement check all argue for
  keeping it deliberate.
- **Consequences.** Pass rates are observations about a prompt and model at one time, never a proof of safety.
  See [Evaluation](evaluation.md).
- **Related.** #151.

## D13. Mutation testing and coverage are diagnostics and floors, not a scoring game

- **Context.** Line coverage shows what ran, not whether an assertion would notice a wrong answer.
- **Choice.** Coverage has floors (overall, per governance module, and for the lines a pull request changes)
  that CI enforces. Mutation testing runs weekly and on demand, never on a pull request, reports a score per
  module against a diagnostic target for the governance modules, and never fails a build over a score.
- **Why.** Mutation runs are slow and their scores move with every test added; a gate would be noise, a report
  is a to-do list.
- **Consequences.** Survivors are triaged into issues rather than ignored.
- **Related.** #142, #146 (PR #185).

## D14. The test-count badge is checked by CI and never corrected by it

- **Context.** Prose test counts in the documentation drifted repeatedly.
- **Choice.** `badges/test-count.json` records the passing count; CI fails if it does not match pytest's own
  summary line, and never rewrites it. Whoever changes the count updates the file with
  `check_test_count.py --write` in the same pull request, and pull requests that touch it merge one at a time.
- **Why.** A bot committing a correction would contradict the rule of no direct commits to `main`.
- **Related.** #101, #116, #147.

## D15. Skills are interactive aids and never input to the Maker or the Checker

- **Context.** A Claude Code skill changes what the model is told once it is loaded, by a person or by the model. A
  deal records which prompts drafted and audited it only on the headless path, and the headless scripts cannot load a
  skill.
- **Choice.** Every skill is an aid: typed by a person, leaving no record in a deal, with no headless equivalent, and
  named by no agent prompt or command. `config/skills_registry.md` is the one inventory of commands and skills. A skill
  that would change the Maker's or the Checker's input is not permitted without its own design, provenance and
  evaluation.
- **Why.** Provenance and an evaluation baseline cover the agent prompts. A skill that changed the input outside them
  would make a deal unreproducible and a comparison meaningless. A skill that shares a command's name replaces the
  command silently, because Claude Code prefers the skill.
- **Consequences.** Names are unique across commands and skills. A skill points to the rule and runs the script and is
  never the only copy of either. In a session an aid's output stays visible to later steps, so this is not an
  isolation guarantee, and `/review` running in the draft's conversation is its own question. See
  [Skill design](skill-design.md).
- **Related.** #196 (the initiative), #203 (`annual-review` needs new primitives), #204 (the context `/review` runs in).
