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
- [D16. The headless Maker and Checker use provider-default sampling parameters](#d16-the-headless-maker-and-checker-use-provider-default-sampling-parameters)
- [D17. Headless runs cannot reset an analyst-supplied historical basis](#d17-headless-runs-cannot-reset-an-analyst-supplied-historical-basis)
- [D18. Code defines, the register describes](#d18-code-defines-the-register-describes)
- [D19. Group accounts: case A first, and a credit aggregation is never called a consolidation](#d19-group-accounts-case-a-first-and-a-credit-aggregation-is-never-called-a-consolidation)
- [D20. Governance modules are typed with mypy: advisory first, no escape hatches](#d20-governance-modules-are-typed-with-mypy-advisory-first-no-escape-hatches)
- [D21. The policy engine's results are typed contracts, pinned byte for byte](#d21-the-policy-engines-results-are-typed-contracts-pinned-byte-for-byte)
- [D22. Group accounts: one shared entity record for cases B and C](#d22-group-accounts-one-shared-entity-record-for-cases-b-and-c)
- [D23. Group accounts, case C: a guarantor is an entity record, and a covenant names one](#d23-group-accounts-case-c-a-guarantor-is-an-entity-record-and-a-covenant-names-one)
- [D24. Mutation survivors: behaviour gets tests, help text is accepted](#d24-mutation-survivors-behaviour-gets-tests-help-text-is-accepted)
- [D25. The credit skills fail closed when the deal lookup does not complete](#d25-the-credit-skills-fail-closed-when-the-deal-lookup-does-not-complete)
- [D26. CAM template guidance is an HTML comment, and the draft lint rejects leftovers](#d26-cam-template-guidance-is-an-html-comment-and-the-draft-lint-rejects-leftovers)
- [D27. The Underwriter's voice and the Checker's consistency checks](#d27-the-underwriters-voice-and-the-checkers-consistency-checks)
- [D28. A research brief opens with a Go/No-Go](#d28-a-research-brief-opens-with-a-gono-go)
- [D29. A Claude Code hook, not a git hook, guards commits on main](#d29-a-claude-code-hook-not-a-git-hook-guards-commits-on-main)
- [D30. The PR scope check is advisory and separate from CI](#d30-the-pr-scope-check-is-advisory-and-separate-from-ci)
- [D31. The headless pipeline stays on the Anthropic SDK 1.x](#d31-the-headless-pipeline-stays-on-the-anthropic-sdk-1x)
- [D32. Test-suite speed: the lazy import was the fix, and splitting the CLI test file was rejected](#d32-test-suite-speed-the-lazy-import-was-the-fix-and-splitting-the-cli-test-file-was-rejected)
- [D33. A rules register names the governance rules and the tests that cite them](#d33-a-rules-register-names-the-governance-rules-and-the-tests-that-cite-them)

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
- **Decision update, 2026-10-10: the interactive Checker is isolated (#204).** As first built, only the headless pipeline met the
  choice above in full: `orchestrator.py` makes the Checker call as a separate message that carries no Maker prompt or
  reasoning, while an interactive `/review` ran in the draft's own conversation, so it was a separate role prompt and
  not a separate context. The maintainer chose to remove that difference: every interactive `/review` delegates the
  qualitative audit to a fresh `risk-reviewer` sub-agent (`.claude/agents/risk-reviewer.md`), a normal custom sub-agent
  (never a fork, which inherits the conversation) with read-only tools (Read, Glob, Grep), no memory and no way to start
  another agent. The principle is to **isolate the Reviewer's reasoning, not its access to evidence**:
  - It reads the exact draft saved on disk (there is no "most recent draft in this conversation" fallback), the deal's
    `state.json`, the source manifest and the saved source documents, and the policy files, by path.
  - It is not given the draft text, the Maker's reasoning, a summary of the conversation or any analyst remark. It audits
    from the evidence first and reads the deal's earlier `review_trail` only afterwards, to see whether earlier findings
    were fixed; an earlier verdict is history, not evidence.
  - A new Reviewer is started for every review, including every re-review of a revised draft; none is resumed.
  - `/review` stays the orchestrator and owns what must not depend on the Reviewer: the deterministic checks (the
    Reviewer receives their output), the rule that a code-enforced reason makes the result `REJECTED`, failing closed
    when no valid verdict comes back, and the append-only `review_trail`. The Reviewer writes nothing.
  - Not changed: the analyst makes the credit decision; the headless pipeline; the deterministic checks.
  What this does not give: a different underlying model (the sub-agent inherits the session's model, so model
  independence is still the headless `checker_model`), statistically independent judgement, any defence against an
  instruction planted in a source document, or a runtime proof that a given invocation followed its instructions.
  Details and limits: [AI assurance](ai-assurance.md#where-the-enforcement-runs-matters).
- **Related.** #31 (separate Checker model), #87 (`/research` review), #204 (interactive isolation).

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
  never the only copy of either. In a session an aid's output stays visible to the Maker's later steps; the
  interactive Reviewer is a fresh sub-agent that reads files, not the conversation (D1's update). See
  [Skill design](skill-design.md).
- **Related.** #196 (the initiative), #203 (`annual-review` needs new primitives), #204 (the interactive Checker's context, settled in D1's update).

## D16. The headless Maker and Checker use provider-default sampling parameters

- **Context.** The shipped Maker and Checker model configuration rejects non-default sampling parameters. Exposing `maker_temperature` and `checker_temperature` therefore made a documented setting capable of breaking every headless call, even though the shipped defaults omitted those keys.
- **Choice.** The headless orchestrator does not support sampling-temperature overrides. It uses the provider's default sampling behaviour. If a fork's `config/settings.json` still carries either legacy key, the orchestrator emits a warning and ignores it.
- **Why.** A setting must not promise control that the configured models do not accept. Silently sending a rejected request is worse than a visible migration warning with deterministic request shape.
- **Consequences.** Maker/Checker model selection remains independent; sampling controls must not be added back without a separately reviewed, model-validated design. The key names are documented as unsupported in [Configuration](configuration.md#which-settings-are-read).
- **Related.** #274.

## D17. Headless runs cannot reset an analyst-supplied historical basis

- **Context.** `financials_source` describes the audit basis of the deal's historical figures. A headless forward-only update could previously recompute every preserved period when raw history existed and then relabel the whole deal `framework-computed`, even though the input contained no replacement historical data.
- **Choice.** A headless `orchestrator.py` run must not recompute or reset a deal whose `financials_source` is `"analyst-supplied"`; refuse before the model call or state write and direct the maintainer to `/spread`'s analyst-supplied mode. The single allowed exception to the normal one-raw-store-per-deal rule is an interactive, forward-only `/project` call: framework-computed forward-year raw lines may coexist with analyst-supplied historical lines.
- **Why.** A deal-wide audit label must not imply that supplied history was independently recomputed. The interactive forward-only route does not recompute history and therefore does not weaken that guarantee.
- **Consequences.** `financials_source` is unchanged by headless runs on analyst-supplied deals. The exception introduces no new state key and does not permit mixing raw figures from the two stores within any one period. See [the data model](data-model.md#two-raw-figure-stores-never-blended-within-a-period).
- **Related.** #345, #305, #271, #249, D4.

## D18. Code defines, the register describes

- **Context.** Every computed figure was described in prose across the documentation, but an analyst signing off
  a figure had no single page listing formula, unit, edge rules, implementation reference and status side by
  side, and nothing failed a build when prose and code drifted apart.
- **Choice.** `docs/formula-register.md` is the one reviewable page of every computed metric, with stable `F-NNN`
  ids. The code is the source of truth and the register describes it; a change to a formula must update the
  register in the same pull request, and `tests/test_formula_register.py` fails the build when a formula row in
  the workbook, a key the financial model produces, or a policy-engine rule has no register entry, or when an
  entry names a file or function that does not exist. Analyst sign-off is recorded outside the repository; the
  page records only role, date and commit, and no personal names.
- **Why.** A description that cannot fail a build drifts; the guard makes a missing entry, a broken reference or
  a changed definition fail the build or force a fingerprint refresh — though no guard can prove the prose itself
  was rewritten, which is what the analyst review is for. Sign-off is a human act with a human record, not a file
  in the repo.
- **Consequences.** Entries are never renumbered; a withdrawn entry keeps its id with a status saying so.
  Deliberate disagreements between the register and the code are recorded on the page under "Known
  differences" rather than fixed silently. See [the formula register](formula-register.md).
- **Related.** #366 (and #266 for the proposed collateral entries), D14's one-PR badge discipline for its test
  count.

## D19. Group accounts: case A first, and a credit aggregation is never called a consolidation

- **Context.** The framework models one entity per deal: `evaluate_financial_model()` takes raw figures keyed by period
  only, `state.json` holds one set of figures, and a covenant is tested against one period's ratios. Deals with a
  group structure (a HoldCo with operating subsidiaries) need a numeric group view, but eliminations of intragroup
  balances are judgement-heavy: leaving a 200 intercompany loan in a synthetic example overstated group debt by 40%
  (700 against 500) with no error. Issue #48 settled the design with the maintainer's decisions of 2026-10-20.
- **Choice.** Three cases, built in this order. **Case A:** the borrower supplies group accounts that are already
  consolidated; the framework records and discloses the basis (reporting entity, entities covered, source, a single
  period end, whether the figures were supplied or recomputed) and does no new arithmetic: no eliminations, no new
  formula. **Case B:** the framework combines entity statements using analyst-supplied eliminations (a later, separately
  reviewed, domain-risk step). **Case C:** a guarantor or parent is spread on its own, with no combination.
  Vocabulary: a group figure the framework derives is a **credit aggregation**, never a statutory or IFRS
  consolidation, until investment and equity, goodwill, non-controlling interests and the other material adjustments
  are covered. The perimeter is confirmed by the analyst and never inferred from an ownership percentage. Period ends
  must match among the entities one aggregation uses (a mismatch there is refused, never aligned by guesswork); the
  check belongs to the aggregation step, not to registering an entity record, and a standalone guarantor (case C) may
  have a different year-end (decision of 2026-10-28, [#472](https://github.com/ShamikM88/open-cam-framework/issues/472#issuecomment-6464570816)).
  A covenant will carry an explicit basis (borrower,
  consolidated, guarantor group), defaulting to borrower. An input keyed by entity is refused: the evaluator and its
  callers accept only the framework's period labels (`FY-2`, `FY-1`, `FY-Current`, `FY+1`, `FY+2`, `FY+3`) and name
  the first unexpected key in one error line.
- **Why.** Audited group accounts are the common case and need no new arithmetic, so recording them is the smallest
  useful release; eliminations are where a wrong figure looks plausible, so they come later and under review
  (D2: compute by code; D4: supplied figures are recorded as given and disclosed). The entity-keyed input was accepted
  silently, treating entity names as periods; a loud refusal is the first control, before any group code exists.
- **Consequences.** New state keys are optional and additive and never replace or blend `financials`, `ratios` or the
  two raw-figure stores; a deal without them behaves exactly as before. Any new formula enters
  `docs/formula-register.md` as Proposed while the analyst review is parked. Changes to the agent prompts and to the
  command files are gated on exact wording recorded on an issue. The headless pipeline never invents a confirmation:
  it accepts only already confirmed structured input and otherwise fails closed. The work is delivered in small
  pull requests: the guard and this record, then case A, then entity records, then the elimination engine.
  The covenant basis (decision of 2026-10-22, #48) is built as an engine change: a covenant carries an optional
  `basis`; the engine states which one view the recorded ratios represent and tests a covenant only on its own basis,
  returning UNRESOLVABLE with a reason otherwise, never a fallback. It computes rather than refuses; asking the
  analyst to confirm an UNRESOLVABLE result is an interactive-step behaviour delivered separately, under the
  prompt-change gate.
- **Related.** #48 (the decisions comment of 2026-10-20), #49 (no FX), #96 (narrative parent research), #124, #266,
  D2, D4, D18.

## D20. Governance modules are typed with mypy: advisory first, no escape hatches

- **Context.** The deterministic core passes a deal's meaning between modules as dictionaries, and several defects
  were of exactly that kind (a wrong key, a null where an object was expected). A language migration was estimated at
  months and nothing in the project needs it; types are the cheapest way to add most of the remaining safety.
- **Choice.** The eleven governance modules are annotated with mypy in small, behaviour-preserving pull requests, one
  per module. The check is advisory: `[tool.mypy]` and the separate `typecheck.yml` report errors per module and are
  not a required check. The shared `JsonValue` alias lives only in `scripts/state_manager.py` and every other module
  imports it (a test pins that); module-specific shapes stay with the module that owns the data. An annotation PR
  may also carry minimal narrowing that mypy needs once a parameter is typed (bind a value once, test it with
  `isinstance`), but every non-annotation change is listed line by line in the PR, and none uses `# type: ignore`,
  `cast()`, an `assert` for narrowing, or `from __future__ import annotations`. A diagnostic that needs a real
  logic change stops the PR and is reported for a new decision. The nine triage items T1 to T9 were each signed off
  (T8 and T9 keep the `"N/A"` marker and the `{}` fallbacks; T5's option of turning an unexpected `None` into a
  placeholder is not authorised), and T10 to T14 were approved in their PRs (T13 and T14 keep two loose local
  declarations, `low_exponent: Any` and `section: Any`, where a library's stubs are wider than the code's guarantee).
- **Why.** Typing a parameter makes mypy check the code that uses it, so a pure annotation change does not exist for
  every module. Listing each non-annotation change keeps the review honest, and an `assert` disappears under
  `python -O`, so it cannot be the narrowing.
- **Consequences.** The formula-register fingerprints (raw source lines, unchanged scheme) are refreshed in each PR
  that annotates a registered function. Phase 2 is complete: all eleven modules report zero errors. Phase 3, a
  failing type check, is not started: making a check required is a ruleset change that stays the maintainer's, and
  it needs its own plan and sign-off, so #424 stays open for it.
- **Related.** #424 (comments of 2026-10-25, 2026-10-26 and 2026-10-27), PRs #465, #474, #476 to #482 and #484 to #489,
  D13, D18.

## D21. The policy engine's results are typed contracts, pinned byte for byte

- **Context.** The policy payload (covenant results, forward results, downside breaches, conditions and the whole
  policy state) crossed module boundaries as untyped dictionaries. Findings CG482-2 on the policy-engine annotation
  and CG477-1 on the committee summary asked for typed contracts; the maintainer staged both as #483 (decision of
  2026-10-26) instead of fixing them inside an annotation PR.
- **Choice.** Six `TypedDict` contracts in `scripts/policy_engine.py`: `CovenantResult`, `ForwardCovenantResult`,
  `DownsideBreach`, `ConditionPrecedent`, `ConditionSubsequent` and `PolicyState`; `evaluate_deal_policy` returns
  `PolicyState`; statuses are exact `Literal` unions; `DownsideBreach.reason` is `str | None` to match the current
  null output. It is one type-complete PR; `PolicyCheckOutput` and the committee-summary payload are typed in the
  same change; the orchestrator's read-only consumers are annotated with `Mapping` without widening the mypy target;
  there is no runtime validator; minimal construction and narrowing rewrites are allowed only when listed, tested and
  output-identical; the formula register's output-shape prose is updated only where needed and no formula changes.
  The approved deviation from the design note: `DownsideBreach.base_actual` is `int | float | None` and `threshold`
  is `JsonValue`, not the narrower first proposal.
- **Why.** The change must be static only. The output was pinned before the typing: 18 golden payload tests
  (`tests/test_policy_payload_contract.py`, goldens in `tests/fixtures/policy_payloads/`), captured before the change
  and compared as bytes.
- **Consequences.** A change to the payload's shape updates the `TypedDict` and its goldens in the same PR.
- **Related.** #483 (design note and the decision of 2026-10-27), #482, #477, PR #492, D18, D20.

## D22. Group accounts: one shared entity record for cases B and C

- **Context.** D19 settled the three cases and refuses entity-keyed input. Cases B and C need to hold more than one
  entity's figures without a second source of truth for the borrower.
- **Choice.** An optional top-level `entity_records` object keyed by stable entity ID, shared by B and C
  (decisions of 2026-10-26 and 2026-10-28 on #472). `SCHEMA_VERSION` goes to 1.2.0 with step 1 (PR #494, open at the time of writing), and a state without
  the key reads as "no entities recorded" and behaves as before. Period labels stay inside each record, and an
  entity-keyed input where a period label is expected is still refused at every entry point. Each entity has one
  primary role (`subsidiary`, `parent`, `guarantor`, plus any roles the step-1 design names for other group
  members); other relationships are explicit links such as `guarantee_ids`,
  never a second role. The borrower is not a role in step 1: its figures stay in the existing stores. If a case B
  credit aggregation and case A's `group_basis` would both claim the consolidated view, the engine fails closed;
  insertion order or last write never chooses. A standalone guarantor alongside case A is allowed. Step 1 records and
  validates only (no subtotals, ratios, eliminations or covenant routing, no new formula). Period-end agreement is
  not enforced when an entity is registered: it is checked only among the entities one aggregation uses, in the
  later aggregation step, so a standalone guarantor may have a different year-end.
- **Why.** The recorded reasons are one source of truth per figure and no hidden choice between two claimed
  consolidated views; recording first keeps the judgement-heavy arithmetic for a separately reviewed step.
- **Consequences.** The decisions authorise no merge and no edit to the agent prompts, commands, skills or the CAM
  template; the accounting review before the elimination engine is still required.
- **Related.** #472 (comments of 2026-10-26 and 2026-10-28), #473, #48, D19, PR #494 (step 1).

## D23. Group accounts, case C: a guarantor is an entity record, and a covenant names one

- **Context.** Case C spreads a guarantor or parent on its own, with no combination (D19).
- **Choice.** Decided for the first release of case C, not built at the time of writing: a guarantor is an entity
  record with `role: guarantor` under D22's contract and explicit
  `guarantee_ids`; there is no separate guarantor model. A `guarantor_group` covenant names one guarantor entity
  through a new optional field `basis_entity_id`; a legal guarantee is joined to an entity only through an explicit
  `guarantee_id`, and a legacy guarantee without an ID is never matched by provider name. A set of several guarantors
  is not part of the first release. A view that is missing, stale or unlinked stays UNRESOLVABLE with a reason. An
  entity's spread records a fingerprint of the raw inputs it was computed from; if they change, the spread is stale,
  is not shown as current and cannot be used by a covenant. Missing accounts are disclosure only: the guarantee
  conditions precedent and subsequent are unchanged and independent of whether accounts exist, and no new
  legal-standing condition is invented. "Consolidated" is never used for case C.
- **Why.** One contract serves both cases, and a view that is missing, stale or unlinked stays UNRESOLVABLE with a
  reason.
- **Consequences.** The wording of the CAM section for a standalone guarantor and every disclosure phrase are not
  decided; they are decided when the presentation step starts, with the exact text recorded on the issue. A new
  deterministic "strength" measure needs its own agreement and a Proposed register entry, and a credit or accounting
  reviewer verifies which statement lines and bases the evaluator may use for guarantors before the spreading step.
- **Related.** #473 (comment of 2026-10-26), #472, D19, D22.

## D24. Mutation survivors: behaviour gets tests, help text is accepted

- **Context.** A mutation run (D13) leaves survivors. Some are untested behaviour; others are argparse help prose or
  equivalent mutants that no test should pin.
- **Choice.** Behavioural survivors are killed by test-only pull requests, one per module: behaviour first and never
  argparse help prose, each test mapped to mutant numbers, re-measured with a name-filtered mutation run on the
  branch, mutants believed equivalent named in the PR with their reason, no production change, and an independent
  review (ChatGPT or Perplexity) before merge. The argparse help-text survivors of the four `main()` functions
  (`spreading_check`, `deal_export`, `committee_summary`, `export_readback`) are accepted as survivors, with no
  `# pragma: no mutate` and no source change. The weekly run is incremental (cached verdicts, new or changed functions
  and the survivors retested) with a module filter for manual runs; cache keep-alive was accepted as documented once
  the incremental mode was checked against a full run.
- **Why.** Survivors are a to-do list, not a score (D13), and help text is not behaviour.
- **Consequences.** A survivor that is accepted is accepted for a stated reason, in the PR or on the issue.
- **Related.** #182 (its "Done looks like" and the comments of 2026-10-18 and 2026-10-27), #408, #435, #219, PR #446,
  D13; the incremental-run description is in `docs/operations.md` and `docs/testing.md`.

## D25. The credit skills fail closed when the deal lookup does not complete

- **Context.** `credit-challenge` and `credit-risk` find a deal with `Glob`. Whether `Glob` is present in a forked
  skill on a given platform is a Claude Code runtime fact that this repository's tests cannot see, and a failed
  lookup read as "no deal" would be a false negative.
- **Choice.** The tools stay as they are (`allowed-tools` is `Read`, `Glob`, `Grep`; `Bash` stays disallowed). Before
  reporting a missing deal each skill establishes whether the lookup completed: if `Glob` is unavailable or errors it
  says the deal could not be checked and that this does not mean it is absent, and only a successful, untruncated
  lookup with no qualifying paths supports "No qualifying record was found". The older stop sentence depends on a
  completed lookup, by a one-clause edit. A fixed lookup script and near-match suggestions were not chosen.
  macOS behaviour is not verified at runtime (decision of 2026-10-20): it relies on Claude Code's documented tool
  set, and the documentation says so.
- **Why.** Making the skill fail closed removes the false negative without widening its tools. macOS cannot be tested
  here: no Mac is available and API work is on hold.
- **Consequences.** `tests/test_skills.py` pins the tool contract and the three distinctions. `mutation-triage` is
  out of scope.
- **Related.** #280 (comments of 2026-10-20 and 2026-10-25), PRs #466 to #468, D15.

## D26. CAM template guidance is an HTML comment, and the draft lint rejects leftovers

- **Context.** Shipped template guidance could reach the exported document, and a bracket placeholder in the
  signature would collide with the lint's unfilled-placeholder rule.
- **Choice.** The shipped guidance lines are HTML comments (`<!-- Guidance: ... -->`) that the exporter drops,
  single-line and multi-line; an unterminated comment refuses the export, with a clear error, rather than stripping
  to the end of the file. The signature is a blank line for the signing analyst (for example `________`), never a
  bracket placeholder: the drafter never fills it and an exported signature never carries an agent identity. A fork
  wraps its own guidance in comments (documented in `docs/outputs.md`); there is no export-time warning. The draft
  lint rejects a hit before export and names the term and its line; its term list lives in code and is pinned by a
  test, not in `config/`; it checks against the template resolved for the deal, local override included, through an
  optional `deal_type`; and it is not governance-critical until its false-positive rate is visible.
- **Why.** A bracket placeholder would collide with the lint's unfilled-placeholder rule, and the lint's template
  check catches guidance a fork's own template left uncommented.
- **Consequences.** The golden snapshots changed only by the intended comment and signature differences.
- **Related.** #439 and #440 (decisions of 2026-10-24), PRs #449 and #453.

## D27. The Underwriter's voice and the Checker's consistency checks

- **Context.** The Underwriter's conclusions did not always follow from the figures and its voice was not an analyst's
  (#441), and the Checker had no items for whether the verdict follows the evidence, for internal consistency or
  for product and accounting mechanics (#442).
- **Choice.** Guideline 13 is appended to `agents/underwriter_agent.md` (Guidelines 1 to 12 keep their numbers):
  conclusions follow from the decisive figures, repayment capacity includes the proposed facility's own repayments,
  product mechanics decide title and the secondary source of repayment, trends are not drawn across different
  bases, asset finance assesses the financed asset and its residual value and, where an introducer shares residual-value
  risk, that introducer as a counterparty, never in promotional terms, and the voice is impersonal with sentences of
  about 25 words or fewer as guidance, not a hard limit; missing information is stated once, not repeated. The
  local style guide overrides the voice rules only where it states a different rule explicitly, and the rules live in
  the Guideline, not in a separate default style guide. The Checker gains Audit Checklist items 5 to 7 (the verdict
  follows the evidence, internal consistency, product and accounting mechanics) before the verdict, now item 8. A
  substantiated material failure of item 5 or 7 rejects; item 6 rejects a material contradiction, while rounding or
  formatting that preserves value, period and basis is minor; a failure means a real finding, never a different view
  of the credit decision.
- **Why.** Both changes landed together so the evaluation baseline goes stale once.
- **Consequences.** `docs/evaluation.md` says a baseline produced under an earlier prompt hash is stale and not
  comparable. `.claude/agents/risk-reviewer.md` needed no change.
- **Related.** #441 and #442 (decisions of 2026-10-24), PR #450, D1.

## D28. A research brief opens with a Go/No-Go

- **Context.** #443 asked for a research brief that supports an early Go/No-Go decision.
- **Choice.** The first substantive section after the header is `## Go/No-Go`, short enough for one page: GO,
  CONDITIONAL GO or NO-GO with the action that follows, the three decisive facts each with figure and source, and no
  more than five conditions in descending order; if fewer than three decisive facts are supported, or no
  conditions are needed, it says so rather than inventing them. Passing legal identity, charges and bureau checks is
  not by itself a credit Go when the commercial evidence points the other way. About 1,500 words is guidance,
  never an export-time limit. The review-status disclosure belongs to the exporter: the drafter never writes it, and
  `scripts/research_export.py` inserts the dated paragraph straight after the title, as before, only when the analyst
  explicitly chooses the disclosed manual-review release.
- **Why.** The other placement options were more change than the gain justified.
- **Consequences.** A command-file test pins that the drafter writes no review-status line.
- **Related.** #443 (decision of 2026-10-24), PR #451.

## D29. A Claude Code hook, not a git hook, guards commits on main

- **Context.** #445 asked to prevent Claude Code commits on `main` and `master`; the maintainer's user-level guard
  was about 180 lines and could be ported.
- **Choice.** The guard is a Claude Code hook in `.claude/hooks/` with its own pytest file. It asks when it cannot
  classify a command (repository or branch unknown) and denies when the guard itself errors. A test pins
  `.claude/settings.json` to exactly one `PreToolUse` guard entry so an added command shows up in review, and a
  CODEOWNERS entry
  covers `.claude/settings.json` and `.claude/hooks/`. Merge approval stays user-level, and blocking pushes to `main`
  stays with the ruleset. A git `pre-commit` hook is a possible follow-up if commits outside Claude Code turn out to
  matter.
- **Why.** The failure behaviour was checked against the current Claude Code documentation before the build (what a
  non-zero exit, a timeout or invalid output does), and the script also catches its own exceptions and denies.
- **Consequences.** The CODEOWNERS entry only takes effect if the ruleset requires code-owner review, which is the
  maintainer's change. See [Security](security.md#repository-shipped-claude-code-commit-guard).
- **Related.** #445 (decision of 2026-10-24), PRs #452 and #463.

## D30. The PR scope check is advisory and separate from CI

- **Context.** #456 asked for a mergeability rubric and an advisory comparison of a pull request's changed files
  with the scope it declares.
- **Choice.** `scripts/pr_scope_check.py` (standard library only) compares the changed files with the PR body's
  `## Declared file scope` section, read from the event JSON and never interpolated into a shell, since the body is
  hostile data. It is advisory: valid input exits 0 whatever the findings, malformed input exits 2, and a missing
  scope is reported as not declared. The protected-path list is fixed in code, includes `.claude/**` and
  `diff_cover_critical.toml`, and reports declared and undeclared protected paths separately, with no exceptions
  mechanism. It runs in its own workflow, `.github/workflows/scope-check.yml`, with `contents: read` only; `ci.yml`
  is not edited and no required check or ruleset changes. A mergeability rubric in `docs/contributing.md` states the
  merge criteria, including that every confirmed finding is fixed or waived and the validation evidence matches the
  current head.
- **Why.** The report is a reviewer aid: in a `pull_request` run the script comes from the PR's own merge commit, so
  it is neither a gate nor a proof.
- **Consequences.** A pull request without a declared scope reports "not declared" and is not blocked.
- **Related.** #456 (decisions of 2026-10-24), PRs #460 and #461.

## D31. The headless pipeline stays on the Anthropic SDK 1.x

- **Context.** Version 2 of the `anthropic` SDK changes the authentication header (so the whitespace-only-key
  credential check fails on it) and the migration guide changes the default API version.
- **Choice.** `requirements.txt` keeps `anthropic>=1.13.0,<2` and `constraints.txt` keeps `anthropic==1.15.0`.
  2.x is not supported until a live smoke test can be run.
- **Why.** The upgrade is not a version bump, and a 2.x smoke test needs credits while API work is on hold.
- **Consequences.** No change to the pins or to the credential check ahead of that test. When it is possible: make the
  credential check correct for both header formats with a whitespace-only-key test at all three entry points, add a
  2.x CI lane, run the gated smoke sequence, and only then move the pin and raise the cap.
- **Related.** #458 (decision of 2026-10-25, open for the live check), PR #459, D12, D16.

## D32. Test-suite speed: the lazy import was the fix, and splitting the CLI test file was rejected

- **Context.** `tests/test_cli_subprocess.py` was 229 s of 488 s (47%) and the critical path under per-file
  scheduling.
- **Choice.** `anthropic` is imported lazily for the CLI fast paths, which cut pytest time by 43% on Ubuntu (four
  workers) and 42% on Windows (two workers) with identical passed counts. Splitting the file was rejected: after the
  lazy import it is 106 s of about 600 s (17.5%) in a single-process profile, no longer the critical path, and the
  rest is spread across several files of 20 to 80 s that are already about as small as they reasonably get.
- **Why.** A split would not shorten the CI critical path.
- **Consequences.** The current profile does not justify the split.
- **Related.** #425 (closed 2026-10-27), PR #428, D14.

## D33. A rules register names the governance rules and the tests that cite them

- **Context.** The governance core has behavioural requirements stated as prose in `CLAUDE.md` and `docs/`, with no
  stable id connecting each atomic claim to a test that asserts it. The formula register (D18) checks the inventory of
  computed metrics and implementation drift; it does not map a claim to a test.
- **Choice.** Decided on #495 (the design note, and the maintainer's sign-off of 2026-10-30 with four amendments). A
  new register page in the documentation holds one entry per rule with a stable, never-reused id `R-<AREA>-NNN` (areas
  `POL`, `STATE`, `SPREAD`, `EXPORT`), numbered sequentially within each area from `001`, three digits. Each entry has
  one atomic claim, a link to its source sentence or heading in `CLAUDE.md` or `docs/` rather than a copy of it, the
  implementing module or function, a status (Covered, Not covered or Withdrawn) and a `Tracking` field: `—` for a
  Covered rule, the issue link for a Not covered rule, and the reason and successor id(s) for a Withdrawn rule. An
  "IDs ever issued" list inside the register lets the gate catch an id renumbered or deleted from the entries alone;
  that the list itself is append-only is enforced in review, because a coordinated deletion from both places cannot be
  seen from the file. A test cites a rule only with a `Rule: R-...` line in its docstring, never in `scripts/`. A new
  gate test in the suite fails when a Covered rule has no citing test, a test cites an unknown id, an id is
  duplicated, an issued id has no retained entry, an entry's id is missing from the issued list, a Not covered rule
  has no issue link, or a test cites a Withdrawn rule. A Not covered rule that carries an issue link does not fail the
  build. The failure messages are the actionable ones proposed in the design note (section 5) and approved by the
  sign-off. Diff-cover is vacuous for a test-only gate, so the gate is validated by self-tests on fixtures, and
  nothing in the workflows or `pyproject.toml` changes. The scope is the governance core, starting with about 27
  rules; the code remains the source of truth and the register describes it (D18).
- **Why.** The register makes the governance claims reviewable and makes a missing claim-to-test link fail visibly,
  without copying source prose or changing behaviour. A citation declares that a named test is responsible for a
  claim; it does not prove the test is strong, which mutation testing and review still check (D13).
- **Consequences.** Ids are retained on withdrawal; a module move changes only the implementation pointer; a split
  withdraws the old entry and issues new ids, and a merge keeps one surviving id and withdraws the others. A change to
  a registered rule updates the register and the citing docstring in the same pull request. The four amendments of the
  sign-off are binding. A rule whose tests verify only part of a compound claim is not Covered for the whole claim: it
  is split into atomic rules, or the rule is marked Not covered with an issue link for the missing half, and a cited
  test name that does not exist as written is corrected. The docs-index link ships with the register. The grammar of
  the "Implemented in" field, including what the gate checks for existence and how a row that names no function says
  so, is defined before it is used. The register states what the gate cannot see (tests that are skipped, deselected
  or generated in a loop), and the gate's fixtures live outside the scanned test glob. The work is staged as #497,
  #499, #498, #500 and #501. #500 is the temporary Not covered tracker only until real test-to-claim verification,
  after which each real gap gets its own issue; #501 adds the `CLAUDE.md` bullet, in the wording the sign-off
  approved: "A change to a registered governance rule updates docs/rules-register.md and the citing test docstring in
  the same PR; code and the linked source remain authoritative." (The sign-off sets the two file names in code
  formatting; it is dropped here only because the page does not exist yet.) #503 decides, with a month's evidence,
  whether to keep the register.
- **Related.** #495 (the plan, the design note, the critiques and the sign-off), #496 to #501 and #503, D13, D14, D18,
  D19.
