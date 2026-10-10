# Testing and CI

> The sections from "Running the tests" to "Test tooling scripts" were moved from the README and `CLAUDE.md` (unchanged apart from the fixes in the [move ledger](move-ledger.md)); "How the suite is organised", "What a green run means", "Writing a test" and "The contract tests" were written for this page. The source of truth for behaviour is the code and tests; see the [index](README.md). The maintainer procedures (reading a red run, a mutation report, a snapshot diff) are in the [operations runbook](operations.md).

## How the suite is organised

The suite checks the framework without a model, so it is organised by what a failure would tell you. Counts change with every pull request and are deliberately not quoted here; the checked-in figure is `badges/test-count.json`.

| What it protects | Test files | A failure here means |
| :--- | :--- | :--- |
| **The deterministic core**: figures, policy facts, state, sources, conventions, text I/O | `test_spreading_builder`, `test_spreading_check`, `test_transcription_check`, `test_supplied_forecast`, `test_policy_engine`, `test_policy_checks`, `test_policy_check`, `test_forward_covenants`, `test_nonpositive_denominators`, `test_state_manager`, `test_source_manifest`, `test_conventions`, `test_textio`, `test_stdio_encoding`, `test_properties` | A number, a verdict reason or a stored record is wrong |
| **State over time**: old and malformed `state.json` | `test_legacy_state` | A deal written earlier can no longer be read, or a bad shape is not handled |
| **The pipeline with scripted models**: the revision loop, the code-enforced override, checkpointing, calibration | `test_orchestrator`, `test_calibrate` | The control flow around the model is wrong (the model itself is replaced by a scripted reply) |
| **What a user receives**: the `.docx`, `.xlsx`, research brief, templates | `test_deal_export`, `test_docx_builder`, `test_research_export`, `test_template_resolver`, `test_golden_outputs` | A document changed content or layout |
| **The command-line surface and a fresh clone** | `test_cli_subprocess`, `test_command_flags`, `test_runtime_smoke` (which drives `runtime_smoke.py`) | A script cannot be run as a user runs it, or a document tells a reader to use a flag that is gone |
| **Contracts between files** | `test_prompt_consistency`, `test_docs`, `test_docs_examples`, `test_docs_contracts`, `test_skill_inventory`, `test_skills`, `test_ci_workflow`, `test_confidential_paths`, `test_repo_conventions`, `test_pytest_config` | Two files that must agree no longer do (see [The contract tests](#the-contract-tests)) |
| **The tooling itself**: the badge check, the coverage check, the mutation report, the PII scan | `test_check_test_count`, `test_check_coverage`, `test_mutation_report`, `test_pii_scan` | A guard that guards the others is broken |
| **The evaluation harness**, driven only with fake clients | `test_eval_cases`, `test_eval_oracles`, `test_eval_runner`, `test_eval_budget`, `test_eval_report`, `test_eval_baseline`, `test_run_evals` | The harness's validation, scoring, call cap or report is wrong (no live call is made) |

## What a green run means

A full green run means the code does what its tests say, on the inputs the tests construct. It does not say that a real model follows the prompts, that a narrative is grounded or that a memo is fit to approve: every model call in the suite is a scripted reply. The distinction, and what does measure model behaviour, is in [AI assurance](ai-assurance.md). Coverage tells you which lines ran; [mutation testing](#mutation-testing) asks whether a test would notice a wrong answer there; neither says anything about the model.

## Writing a test

- **Test behaviour through the public function** (or the real command line, as a subprocess, for a CLI), not the private helpers it happens to use. A refactor should not break a test that is about behaviour.
- **Show that a guard can fail.** Every contract test in this repository also runs its check on deliberately wrong input (a renamed flag, a missing anchor, an unpinned action) and expects it to be reported. A guard that has never been seen to fail is a guess.
- **Be deterministic.** No network (`pytest-socket` enforces it), no real clock or randomness without a fixed seed, no dependence on the working directory (use `tmp_path`), and the `ci` Hypothesis profile (fixed seed, no example database). Tests of text-file handling use the `cp1252_default_open` fixture in `tests/conftest.py`, which makes an `open()` with no encoding behave as on Windows so such a bug cannot hide on Linux.
- **Use the fakes, not the SDK.** Model calls are replaced by scripted clients (`tests/eval_fakes.py` and the orchestrator tests); a test that needs a key or a network is a bug.
- **Do not make the passing count depend on the environment.** A test that skips where a tool is missing makes the count differ between machines, which the badge cannot allow (this is why `zizmor` runs in CI's `security` job and not from the suite).
- **Prefer an assertion to a snapshot.** Snapshots are for rendered documents, where a human reviews the diff; a number or a message is asserted directly.
- **Record known-bad behaviour as a strict `xfail`** naming its issue, so fixing it fails the test until the marker is removed.
- **Use synthetic data** ([contributing](contributing.md#synthetic-data-only)).

## The contract tests

Some tests do not test behaviour; they pin a relationship between two files that nothing else would keep aligned. They are cheap and they fail with the file and line, so they are the first place to look when a rename breaks something far away.

| Relationship | Test | Pins |
| :--- | :--- | :--- |
| Prompts and the code that parses their output | `test_prompt_consistency` | The structured-output example parses, its schema matches the parser, the named risk categories and grounding headers exist, every `Guideline N` / `Audit Checklist item N` reference resolves to the pinned subject |
| Links and cited headings | `test_docs` | Relative links and heading anchors resolve; every `docs/` page is in the index; the `CLAUDE.md` headings that code cites exist; the evaluation page stays evergreen |
| Documents and the scripts' flags | `test_command_flags` | Every `--flag` a document or command passes to a script is in that script's `--help` |
| Quoted examples and the real output | `test_docs_examples` | Every figure and message the worked pages quote is what the scripts print |
| Documents and the source of truth | `test_docs_contracts` | The slash commands in `.claude/commands/` equal those on the commands page, in the registry and on the repository map; the settings documented as read or inert are the ones the code reads (and each inert key is named in `CLAUDE.md`); the CI jobs the pages name are the jobs in `ci.yml`; every repository path the pages cite as inline code exists (git-ignored locations excepted); the schema version the data-model page shows equals `SCHEMA_VERSION` |
| Commands, skills and what a skill may be | `test_skill_inventory` | The inventory in `config/skills_registry.md` equals the command files and skill folders (names unique, invocation equal to the frontmatter); a skill is an aid with no headless equivalent; no agent prompt, command or `CLAUDE.md` names, points at or allows a skill; no production script (so no headless helper) builds a path into `.claude/`; the design page's tranche is the inventory's skills |
| What each shipped skill may do | `test_skills` | A skill is typed by a person, forked, has no write tool and pre-approves only read-only scripts; those scripts leave a deal byte-for-byte unchanged; every path it cites exists and every flag it passes is real; the files carry no real name or figure |
| The CLI inventory | `test_cli_subprocess` | The list of scripts equals the scripts that have a `__main__` block |
| CI's structure and security rules | `test_ci_workflow` | Required job names, SHA-pinned actions, least privilege, no `pull_request_target`, the coverage and diff-cover steps, the advisory-ignore rule, the weekly mutation workflow |
| What may be tracked | `test_confidential_paths` | No protected path is tracked or loses its ignore rule; every ignore line is classified |
| Conventions in `scripts/` | `test_repo_conventions`, `test_pytest_config`, `test_stdio_encoding` | `# pragma: no cover` carries a reason; the tool settings really reject bad code; every CLI configures UTF-8 output first |

A change that adds a relationship worth pinning adds a small test here, named for the relationship. It does not add a generic parser: each check above is a few lines over the text it compares.

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The full suite needs no `ANTHROPIC_API_KEY`/network access to run -- every module that touches
`anthropic` (`calibrate.py`, `orchestrator.py`) is tested via mocking, and CI itself runs `pytest
tests/` with no key set at all. Dedicated test files cover the dependency-free modules directly
(`spreading_builder.py`, `spreading_check.py`, `docx_builder.py`, `template_resolver.py`,
`deal_export.py`, `research_export.py`, `state_manager.py`, `source_manifest.py`, `conventions.py`,
`pii_scan.py`, `textio.py`, `policy_engine.py`/`policy_checks.py`/`policy_check.py`,
`check_test_count.py`, `check_coverage.py`) plus a
prompt-consistency suite (`test_prompt_consistency.py`) that cross-checks the two agent prompts
against the code they're meant to stay in sync with. The command-line surface is tested for real:
`test_cli_subprocess.py` runs every script's `--help` / a representative input / a bad input as a
subprocess, and `test_command_flags.py` checks that every `--flag` the slash commands and docs tell
Claude to pass still exists in the script, naming the file and line when one does not.

CI also runs `ruff check .` and `bandit -r scripts/ -ll` before `pytest` -- run those locally too if you
want to catch what CI will catch before pushing:
```bash
ruff check .            # the rule set lives in pyproject.toml, not on the command line
bandit -r scripts/ -ll
```
`pyproject.toml` is the one place for tool settings (ruff rules, pytest options, coverage). The test
run is configured to fail on any warning, to forbid network access (`pytest-socket`) and to time out a
hung test (`pytest-timeout`), so install the dev requirements (`pip install -r requirements-dev.txt`)
before running `pytest`. Coverage is measured with `pytest --cov` (line + branch over `scripts/`). CI
enforces an 85% overall floor, a 90% floor on each of eight governance modules, and 90% on the lines a
pull request changes; the numbers and the module list live in `pyproject.toml`. To check locally:
```bash
pytest --cov --cov-report=json
python scripts/check_coverage.py coverage.json
```
CI runs four jobs: `test` (Ubuntu), `test-windows`, `runtime-smoke` (installs only `requirements.txt` and
checks that a fresh clone works) and `security` (`pip-audit`, `zizmor`). All actions are pinned to commit
SHAs and every job has read-only permissions. A reviewed advisory with no fix available is ignored only through
a reviewed PR that adds `--ignore-vuln <ID>` and a reason comment to the `pip-audit` command (see CLAUDE.md,
"Testing and static analysis"); the audit itself is never switched off. Separately, a **weekly mutation-testing run**
(`.github/workflows/mutation.yml`, Linux only, also startable by hand from the Actions tab) mutates the
eight governance modules (and the `calibrate.py` helpers) and reports a score per module plus the mutants
no test caught; it is a diagnostic report, not a merge gate.

Beyond example-based unit tests the suite includes property-based tests (`hypothesis`), historical and
malformed `state.json` fixtures, and golden-output tests that compare the exported `.docx`/`.xlsx` with
reviewed text snapshots in `tests/snapshots/`. After an intended change to an export, regenerate the
snapshots with `pytest tests/test_golden_outputs.py --update-snapshots` and review the diff; CI never
does this. See `CLAUDE.md` ("Testing and static analysis") for details.

**Test-count badge.** `badges/test-count.json` (`{"passed": <int>}`) is this project's own
checked-in record of how many tests currently pass -- unlike everything else this framework
persists, this one is deliberately public and git-tracked, not gitignored, since it's a project
stat, not derived borrower/institutional data. GitHub's public API exposes merged-PR and
closed-issue counts directly, but nothing queryable exposes "tests passing," so this repo tracks
it itself the same way it tracks everything else: code-enforced, not hand-maintained. CI's
"Verify checked-in test count" step (`scripts/check_test_count.py`) parses the real count out of
`pytest`'s own summary line and fails the build if it doesn't match this file -- it never
auto-corrects the value itself (that would mean a bot committing to `main`), so bumping the test
count means updating `badges/test-count.json` in the same PR. That is one command from your own
checkout: `pytest tests/ | tee pytest_output.txt` then
`python scripts/check_test_count.py pytest_output.txt --write` (CI only ever runs the plain check).
Run it on a complete green run of the whole suite: `--write` looks at pytest's summary line and refuses
one that reports failed, errored or deselected tests (the refusal quotes what matched), and a run that halted
early (Ctrl-C, a collection error, `pytest.exit()`, `--maxfail`). Test names and captured output with `-v`/`-s`
are not mistaken for failures, and colour codes are ignored. It cannot tell that you ran only part of the suite
by path, so run the whole suite. It reads UTF-8
(with or without a byte-order mark) and UTF-16, which Windows PowerShell 5.1 can produce for `tee`/`>`.
When two open PRs both change the count, merge them one at a time; whoever merges second rebases,
reruns pytest and runs that command (issue #147).

## Static analysis, coverage, CI and mutation testing

All tool settings live in [`pyproject.toml`](../pyproject.toml) (issues #139, #142); CI and local runs read the
same file, so a rule or threshold changes in one reviewed diff and never in a workflow command line.

### Ruff and Bandit

**Ruff** (`ruff check .`): `E9,F63,F7,F82` (syntax/undefined names) plus `B` (bugbear), `UP` (pyupgrade)
and `S` (security), and the preview rule `PLW1514` (`open()` without `encoding=`, the regression guard for
the UTF-8 policy; `explicit-preview-rules` keeps other preview rules off). `tests/` ignores only the
rules that are noise there: `S101` (assert), `S603`/`S607` (fixed `subprocess` argument lists),
`S105`/`S106` (fake credentials as test data) and `S307`. A finding in `scripts/` is fixed or carries a
`# noqa: <code>` with the reason beside it. **Bandit** runs at medium (`-ll`).

### Pytest

**Pytest**: `filterwarnings = ["error"]` (every warning fails; fix the cause or filter it in
`pyproject.toml` with a reason), `--disable-socket` (`pytest-socket`: no test may open a network
connection) and a 120-second per-test timeout (`pytest-timeout`). Install `requirements-dev.txt` before
running `pytest`, or pytest rejects the options.

### Coverage

**Coverage**: `pytest --cov` measures line + branch coverage of `scripts/`. `# pragma: no cover` is allowed
only with a reason on the same line (`# pragma: no cover - why`; `tests/test_repo_conventions.py`).
CI enforces the floors in `pyproject.toml`: `[tool.opencam.coverage]` (read by `scripts/check_coverage.py`)
sets an **overall floor of 85%** and a **90% floor for each of eight governance modules** on its own --
`policy_engine`, `policy_checks`, `policy_check` (covenant/security/CP evaluation and the draft audit),
`spreading_builder`, `spreading_check` (every ratio the CAM reports), `state_manager`, `deal_export`,
`docx_builder` (persisted state and the exported documents) -- because one overall number would let a
well-covered helper hide an untested governance module. `[tool.diff_cover]` sets a **90% floor for the lines
a pull request changes** (`diff-cover coverage.xml --config-file pyproject.toml`, pull requests only; a
change that touches no measured line passes). diff-cover reads `pyproject.toml` **only** with
`--config-file`; without it the floor is silently 0 -- `tests/test_ci_workflow.py` runs CI's exact command
against an uncovered change to prove it fails. Code that only runs on Windows looks uncovered on the Linux
job and needs `# pragma: no cover - reason`; this applies to later pull requests that touch `scripts/`.
Lowering a floor or dropping a module from the list is a reviewed decision (`tests/test_check_coverage.py`
pins the minimums); raising one is a one-line change.

### The command-line surface

**The command-line surface is tested as a user meets it** (issue #144). `tests/test_cli_subprocess.py` runs each
of the 14 scripts that have a `__main__` block as a real subprocess in a throwaway working directory, with the
Anthropic variables removed and the home/config directories pointed at an empty directory (the SDK also
reads an on-disk credentials profile, which would otherwise let a child make a paid call): `--help` exits 0, an unknown flag exits 2, a representative minimal input does the
job, and an impossible input exits non-zero. `orchestrator.py` is the one script that cannot be run end to end
without a model; its test checks that it parses, starts the pipeline, and fails non-zero without credentials.
A list of the CLI scripts is compared with the scripts that actually have a `__main__` block (parsed with `ast`),
so a new CLI cannot be forgotten. `tests/test_command_flags.py` reads every `python scripts/<name>.py --flags`
in `.claude/commands/*.md`, `README.md`, `CLAUDE.md`, `evals/README.md`, `config/*.md`, `templates/*.md` and
`agents/*.md` (inline code, fenced blocks, backslash continuations; pipes, redirects and quoted text are not the
script's flags) and asserts each flag appears in that script's real `--help`, so renaming or removing a flag
that a command still uses fails CI with the file and line. The children are followed by coverage
(`patch = ["subprocess"]`, `parallel = true`), so their `__main__` blocks count.

### The configuration is itself tested

**The configuration is itself tested**: `tests/test_pytest_config.py` runs the real tools with this
repository's `pyproject.toml` against deliberately bad snippets (a leaked file, a socket connection, an
overrun test, `zip()` without `strict=`, `open()` without an encoding, ...) and expects each to be
rejected, so a setting cannot be deleted without a test failing.

### Numbered cross-references between prompts and docs

**Numbered cross-references between prompts and docs are guarded** (`tests/test_prompt_consistency.py`, issue
#107). "Guideline N" and "Audit Checklist item N" are plain numbers in prose, so inserting an item mid-list
silently re-points every later reference. The test finds those two exact phrases (singular, capitalised, digits;
also when one line break splits them, since the files are hard-wrapped; `Guidelines 9 and 10` or `guideline 9`
are not recognised, so write `Guideline 9 and Guideline 10`) in `CLAUDE.md`, `README.md`, `.claude/commands/`,
`agents/`, `templates/`, `evals/README.md`, the two tracked `config/*.md` files and `scripts/*.py` (comments and
the text sent to the model), resolves
each number against the real numbered list (column-0 `N. ` lines of `agents/underwriter_agent.md`; the "###
Audit Checklist" section of `agents/risk_reviewer_agent.md`, which must run 1..N without gaps), and requires the
item to still be the subject pinned for that number in `EXPECTED_SUBJECTS`. A failure names file, line and
context. After a legitimate renumbering, fix the references it lists **and** update `EXPECTED_SUBJECTS` in the same
change; a reference to a number that is not pinned also fails, so every reference is deliberate.

### CI

**CI** (`.github/workflows/ci.yml`, structure pinned by `tests/test_ci_workflow.py`): four jobs. `test`
(Ubuntu; the ruleset's required check -- keep the name) runs ruff, bandit, the suite with coverage, the
test-count check, the coverage floors and diff coverage, and uploads `coverage.xml`/`coverage.json`/the
pytest output/`pip freeze` as a 30-day artifact. `test-windows` runs the same suite on Windows (the
primary development platform: cp1252 console, backslash paths, CRLF). `runtime-smoke` installs **only**
`requirements.txt` and runs `tests/runtime_smoke.py --expect-clean`: no development package importable,
every module imports, every CLI prints usage, a deal exports end to end -- what a fresh clone gets.
`security` runs `pip-audit` on all requirements files and `zizmor` on the workflows (an advisory
published tomorrow can turn it red on an unrelated PR; that is the point). **Ignoring a reviewed advisory
(issue #141):** the fix for a red `security` job is to pin a fixed version; only when none exists and the advisory
has been reviewed as not exploitable here is it ignored, and the *only* supported way is a reviewed pull request
that adds `--ignore-vuln <ID>` to the `pip-audit` command in `ci.yml` together with a comment line of exactly this
shape (a test fails on an ignore without one, and on the known ways of weakening the job: `continue-on-error`, `|| true`, `--no-deps`):
`# pip-audit ignore: <ID> -- <package> -- reason: <why it cannot be exploited here> -- revisit by: YYYY-MM-DD`.
Never `continue-on-error`, `|| true`, or dropping a requirements file from the audit; the ignore is removed in the
PR that pins a fixed version, and the same advisory dismissed in GitHub's Dependabot alerts is dismissed with a
reason that links that PR. Findings from `zizmor` are fixed, or silenced with a `# zizmor: ignore[<rule>]` comment
on the line itself plus the same reason, reviewed the same way. Workflow rules, all tested:
every third-party action is pinned to a full commit SHA with a `# vX.Y.Z` comment (Dependabot keeps
them current); `permissions: {}` at the top and `contents: read` per job; `persist-credentials: false`
on every checkout; no `pull_request_target`; CI never passes `--write` or `--update-snapshots`.

### Mutation testing

**Mutation testing** (`.github/workflows/mutation.yml`, configuration `[tool.mutmut]` in `pyproject.toml`,
issue #146): mutmut changes one line of a governance module at a time and checks that a test then fails;
a surviving mutant is a line that coverage counts as executed but nothing really asserts on. It runs
**weekly (Mondays) and on demand only -- never on a pull request or push**, on Ubuntu (mutmut forks, so
not Windows). **Scope:** the eight `critical_modules` of `[tool.opencam.coverage]` plus `calibrate.py` (its
chunker/merge helpers, as a *supporting* module -- scored and reported, but not governance-critical and so not in
the coverage floors); a test pins `only_mutate` to exactly those and the supporting list. The result is a
downloadable `mutation-report` artifact (survivors, **every mutant with its status** in `mutation-all.txt`, the
per-module report as JSON, statistics, per-file metadata under mutants/src, run log) and, in the job summary, the
overall score **and `scripts/mutation_report.py`'s per-module table**: a score per module
(`(killed + timed out) / (mutants - skipped)`, so a mutant no test reaches counts against its module), completeness
warnings (a configured module with no mutants, a module whose mutants mostly have no test, mutants never checked,
a survivors-only listing) and the largest groups of survivors, the starting point for triage.
**It is diagnostic: no score is enforced and the job is not a required check.** The eight governance modules have
a **diagnostic target of 75%** (`[tool.opencam.mutation] target_critical` in `pyproject.toml`, chosen after the
first full run, below); the report marks each of them met or below and keeps reporting a module that is below,
and it never fails a build over a score. Changing the target is a reviewed change. The job fails only if mutmut
produced no result at all.
**Survivor triage** (first full run with the per-module report: 3,741 mutants, 78.0% overall, 680 survivors, 142
with no test, 26 timeouts; critical modules from 96.6% for `state_manager` down to 71.0% for `deal_export` and
35.1% for `spreading_check`; four of the eight met the 75% target, `policy_checks`, `policy_check`, `deal_export` and
`spreading_check` were below): the biggest cluster is the `main()` command-line wrappers (~200 mutants), exercised
only by the subprocess tests (`tests/test_cli_subprocess.py`), which neither run under mutmut nor are visible to
it -- issue #182; the draft-audit and covenant functions of `policy_checks`/`policy_engine` are #183; the
`calibrate.py` helpers #184. To run it by hand on Linux/macOS: `pip install -r requirements-dev.txt -r requirements-mutation.txt &&
ln -s scripts src && mutmut run` (and `rm src` afterwards; `src` and `mutants/` are git-ignored).

### Property-based tests

**Property-based tests** (`tests/test_properties.py`, issue #143; `hypothesis`): invariants rather than
examples -- the chunker loses no text, the merge loop never drops a field, `values_match` is symmetric,
ratios are `N/A` whenever a denominator is zero or negative and finite otherwise. The default `ci` profile (60 examples, fixed seed, no example database)
is identical on every run; `HYPOTHESIS_PROFILE=explore pytest tests/test_properties.py` searches harder
with random seeds (its `.hypothesis/` database is git-ignored). Known-bad behaviour found this way is a
*strict* `xfail` naming the issue, so fixing it fails the test until the marker is removed. Meta-tests
deliberately break a function to prove each property can fail.

### Legacy and malformed state.json

**Legacy and malformed `state.json`** (`tests/test_legacy_state.py`, issue #152): five synthetic
historical-shape fixtures in `tests/fixtures/state/` run through `spreading_check.compute`,
`policy_check.compute` and `deal_export`. A change to `state.json`'s shape must keep those fixtures
working or add a new one. Wrongly-typed state (issue #171) is a matrix of 17 malformed shapes x 3 consumers
(`EXPECTED_ERROR`): where a consumer reads the key it must raise a `StateShapeError` naming it and leave the file
untouched, and each deliberate tolerance is asserted by name; `tests/test_cli_subprocess.py` runs the CLIs against
malformed, corrupt and newer-schema states and asserts one `error:` line, exit 1 and no traceback.

### Golden-output tests

**Golden-output tests** (`tests/test_golden_outputs.py`, issue #145): the exported `.docx`/`.xlsx` for
synthetic inputs are reduced by `tests/snapshot_utils.py` to readable text (no timestamps; adjacent
runs merged) and compared with `tests/snapshots/`. After an *intended* output change run
`pytest tests/test_golden_outputs.py --update-snapshots` and review the snapshot diff like code.
A missing snapshot fails rather than being created, and CI never passes the flag (tested). The same
file asserts that no client-facing document contains leaked Markdown, the stripped structured-output
JSON, `nan`/`None` or error text. A separate synthetic draft (`tests/fixtures/snapshots/synthetic_images_draft.md`,
its image files generated at test time, so no binary fixtures) pins what image lines become -- an embedded image
(`<image alt="...">` plus its italic caption, none without alt text, scaled down to the page width, never up) and
every kind of unhonourable reference (missing file, remote URL, unsupported type, corrupt file) as a visible
`[Image not embedded: ...]` placeholder plus a stderr warning -- in `cam_images.docx.txt` (issue #145). A new `openpyxl`/`python-docx` release that changes serialisation will
show up here as a snapshot diff: regenerate only after confirming the difference is cosmetic.

## Test tooling scripts

### `check_coverage.py`

**`scripts/check_coverage.py`** — no `anthropic` dependency. `python scripts/check_coverage.py coverage.json`
compares coverage.py's JSON report (`pytest --cov --cov-report=json`) with `[tool.opencam.coverage]` in
`pyproject.toml`: an `overall` floor and a higher `critical` floor that each module in `critical_modules` must
meet on its own (a critical module missing from the report fails; it is never a pass). Exit 0 when met, 1 when
not, 2 for an unreadable report or config; `--report-only` prints the table and always exits 0. See the Coverage section of the testing page for the floors.

### `mutation_report.py`

**`scripts/mutation_report.py`** — no `anthropic` dependency. Turns `mutmut results --all true` output into a score
per module (see Mutation testing on this page). `python scripts/mutation_report.py mutation-all.txt
[--config pyproject.toml] [--top 15] [--json report.json]` prints a Markdown table (the workflow appends it to the job
summary) and exits 0 whenever a report was produced, 2 for an unreadable results file or configuration. Module lists
come from `[tool.mutmut] only_mutate` and `[tool.opencam.coverage] critical_modules`.

### `check_test_count.py`

**`scripts/check_test_count.py`** — no `anthropic` dependency. `parse_passed_count(pytest_output)`
extracts the passed-test count from pytest's own summary line (e.g. `"430 passed in 16.27s"` or
`"428 passed, 2 skipped in 12.34s"`); `read_badge_count(badge_path=None)` reads
`badges/test-count.json`'s declared count; `check(pytest_output, badge_path=None)` returns
`(matches, actual, expected)`; `write_badge_count(count, badge_path=None)` rewrites the file and
returns the previous count; a missing/malformed badge or unreadable output is a clean exit-1
message, never a traceback. Deliberately doesn't run pytest itself -- it parses an
already-captured output file, so CI's own "Run test suite" step (which must pass regardless of
this check) and this comparison stay independent, and the suite never runs twice in one CI run.
Exists so `badges/test-count.json` (see the repository map in the architecture page) can't silently drift the way
README.md's/CLAUDE.md's own prose test-count mentions have drifted before (see #101, fixed in
#116) -- except here it's code-enforced in CI, not just a documentation-sync PR someone has to
remember to write. **Fails CI on a mismatch; never auto-corrects the file** -- a bot committing
a corrected value to a PR branch (or, worse, to `main`) would contradict this repo's own
"explicit confirmation for every push, zero direct commits to main" governance, so whoever's PR
changed the passing-test count must update `badges/test-count.json` in that same PR, and CI is
only the thing that catches it if they forget. **`--write`** (issue #147) makes that update one
command from a developer's own checkout -- it rewrites the file from the same captured pytest
output instead of failing, **refusing runs that are visibly not a complete green run** -- judged on
pytest's own summary line (failed / errored / deselected, colour codes stripped) and its `!!!!!`
halt banners (Ctrl-C, collection error, `pytest.exit()`, `--maxfail`), so `-v`/`-s` output is not
mistaken for failures and the refusal quotes what matched. It cannot know that you ran only part of
the suite by path (`pytest tests/test_x.py`), so run the whole suite -- and a test pins that no CI workflow file (any `.yml`/`.yaml` under `.github/`)
that runs the check ever passes it. Because every
PR that adds tests edits this one-line file, PRs that touch it are merged one at a time; whoever
merges second rebases, reruns pytest and runs `--write`:
```
python scripts/check_test_count.py pytest_output.txt          # the CI check
python scripts/check_test_count.py pytest_output.txt --write  # update the badge by hand
```
