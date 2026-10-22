# Operations runbook

For the person who maintains the repository: how to verify it, keep it healthy and tell when a change is ready. Each procedure says what it is for, because a procedure followed without its purpose is the one that gets skipped or gamed. Contributors should start with [contributing](contributing.md); the descriptions of what each tool checks are in [Testing and CI](testing.md); the kinds of evidence behind the system are in [AI assurance](ai-assurance.md).

This page is evergreen. It does not record what the latest run showed; current status lives in the tracking issues and the dated [project status](README.md#project-status).

## Verify a change: the full suite

**Purpose.** The suite is the only thing standing between a change and a silent regression in a governance module, so the complete run is the unit of evidence. A partial run proves only that the part you ran passes.

```bash
ruff check .
bandit -r scripts/ -ll
pytest tests/ -n 4 --dist=loadfile --durations=30 --cov --cov-report=term --cov-report=xml --cov-report=json | tee pytest_output.txt
python scripts/check_test_count.py pytest_output.txt
python scripts/check_coverage.py coverage.json
```

That is what CI's Ubuntu `test` job runs, in that order; on a pull request it also runs `diff-cover coverage.xml --config-file pyproject.toml` and `diff-cover coverage.xml --config-file diff_cover_critical.toml` (the `--config-file` matters: without it the floor is silently zero). Windows runs `pytest tests/ -n 2 --dist=loadfile --durations=30` without coverage. `pytest-xdist` (in `requirements-dev.txt`) supplies the bounded workers; `--dist=loadfile` keeps one test file on a worker, and `--durations=30` records the 30 slowest tests. The suite needs no key and no network; a test over its 120-second limit fails by itself.

A green local run is not the same as a green CI run, because CI also runs on Windows and in a clean environment with only `requirements.txt` (below). It is the right thing to have before you push.

## The test-count badge

**Purpose.** `badges/test-count.json` is a public, checked-in statement of how many tests pass. A prose count in a document goes stale; a number CI compares with pytest's own summary line cannot ([D14](decisions.md#d14-the-test-count-badge-is-checked-by-ci-and-never-corrected-by-it)).

- **Check:** `python scripts/check_test_count.py pytest_output.txt` (CI's step; it only ever reads).
- **Update:** after a change that adds or removes passing tests, from a complete green run, `python scripts/check_test_count.py pytest_output.txt --write`. It refuses output with failures, errors, deselected tests or an early halt, and it cannot see that you ran only part of the suite.
- **Why CI never writes it:** a bot committing to a branch or to `main` would bypass review.
- **Two open pull requests that both change the count:** merge them one at a time. The second rebases, reruns the whole suite and runs `--write` again; do not resolve the conflict by hand-editing the number.
- **Sanity check at review:** the new number is the old number plus the tests the pull request adds, minus any it removes. A difference means a test was skipped, deselected or collected differently.

## Diagnose a red CI run

CI has four jobs (`test`, `test-windows`, `runtime-smoke`, `security`) and a separate weekly mutation workflow. `test`, `test-windows` and `runtime-smoke` are required checks on `main` (each pinned to the GitHub Actions app, and the branch must be up to date); `security` is deliberately not required, because a newly published advisory can turn it red on an unrelated pull request, but it is still expected to be green and a red one is a finding, not noise. Start by reading which step failed.

| Red job and step | Usually means | First move |
| :--- | :--- | :--- |
| `test` / Lint with Ruff | A rule in `pyproject.toml` (`B`, `UP`, `S`, `PLW1514` ...) | `ruff check .` locally; fix, or add a `# noqa: <code>` with the reason beside it for a finding in `scripts/` |
| `test` / Security scan with Bandit | A medium-or-higher finding in `scripts/` | Fix it, or `# noqa` with the reason; never lower the level on the command line |
| `test` / Run test suite | A real failure, a warning (they fail the run), a network attempt (`--disable-socket`) or a test over 120 s | Read the first failure, not the last; reproduce with `pytest path::test -x` |
| `test` / Verify checked-in test count | The badge does not match the run | Update it from a complete green run ([above](#the-test-count-badge)); if the count looks wrong, find the test that did not run |
| `test` / Enforce coverage floors | A governance module fell below 97% or the total below 95% | `python scripts/check_coverage.py coverage.json --report-only` for the table; add the missing test; do not lower a floor to pass (a lower floor is a reviewed decision) |
| `test` / Enforce coverage of the lines this pull request changes (and, in the governance modules) | Changed lines are under 95% covered, or under 98% in one of the eleven governance modules | `diff-cover coverage.xml --config-file pyproject.toml` (or `--config-file diff_cover_critical.toml`) lists the lines; code that only runs on Windows needs `# pragma: no cover - <reason>` |
| `test-windows` | A platform bug: cp1252 console, backslash paths, CRLF, an `open()` without `encoding=` | Reproduce on Windows; the fix is usually an explicit encoding or `pathlib`, not a skip |
| `runtime-smoke` | A script imports a development-only package, or `requirements.txt` is missing a runtime dependency a dev package happened to supply | Run `tests/runtime_smoke.py --expect-clean` in a venv built from `requirements.txt` alone |
| `security` / pip-audit | A dependency has a published advisory (this can turn red on an unrelated pull request, which is the point) | Pin a fixed version; only if none exists, follow the reviewed ignore procedure in [Testing and CI](testing.md#ci) |
| `security` / zizmor | A workflow weakness (unpinned action, excess permission, template injection) | Fix the workflow; a suppression is a `# zizmor: ignore[<rule>]` on the line with the reason, reviewed the same way |

CI keeps `coverage.xml`, `coverage.json`, `pytest_output.txt` and `pip freeze` for 30 days as the `ci-records-ubuntu` artifact, uploaded even when a step fails; the resolved dependency versions are often the difference between "fails here" and "passes on my machine".

**CodeQL** runs from the repository's code-scanning settings, not from a workflow file, so no file here describes it. Treat an alert as real until shown otherwise. A green CodeQL check on a pull request is **not** proof that an alert is closed: pull-request analyses report on the code the change touches, so an alert on a line the pull request leaves alone can sit unchanged in a green check. To confirm a fix, check the alert's state on `main` after the merge, once `main` has been re-analysed, and read the alert's own `state` rather than the workflow's conclusion.

## Coverage: how to read it

**Purpose.** Coverage answers "did a test run this line?", never "would a test notice if it were wrong?". It is a floor against untested governance code, not a score ([D13](decisions.md#d13-mutation-testing-and-coverage-are-diagnostics-and-floors-not-a-scoring-game)).

- The overall floor (95%) catches broad neglect; the per-module floor (97% for eleven governance modules) stops a well-covered helper hiding an untested policy module; the changed-lines floor (95%, 98% in a governance module) holds new code to the bar from the first pull request.
- Branch coverage is on. A line that runs but never takes one side of an `if` is the usual cause of a miss that "looks covered".
- When a number is under, ask which behaviour is untested, then write the test for the behaviour. A test written only to execute the line raises the figure and protects nothing.
- Floors are in `pyproject.toml`; `tests/test_check_coverage.py` pins the minimums so lowering one cannot be done quietly.

## Mutation testing: reading the report and triaging survivors

**Purpose.** Mutation testing changes one line of a governance module at a time and checks that a test fails. A **survivor** is a change no test noticed: a line that coverage counts as run but nothing asserts on. It is the best available sign of where the suite is weak.

The workflow (`.github/workflows/mutation.yml`) runs weekly and on demand from the Actions tab, on Ubuntu only, never on a pull request, and never fails over a score. Download the `mutation-report` artifact and read the job summary:

- **The per-module table** gives `(killed + timed out) / (mutants - skipped)` for each module. A mutant no test reaches counts against its module. The eleven governance modules have a diagnostic target of 75% (`[tool.opencam.mutation] target_critical`); the report marks each met or below and never gates on it.
- **Completeness warnings** (a configured module with no mutants, a module whose mutants mostly have no test, mutants never checked) mean the *run* is suspect before the score is.
- **The largest groups of survivors** are where to start.

To triage:

1. **Group, don't go mutant by mutant.** Survivors cluster by function. A cluster is one issue.
2. **Classify each cluster.**
   - *Missing assertion:* the behaviour exists and is exercised, but no test checks the result. Add the assertion. This is the common and valuable case.
   - *Unreachable by the tests as written:* for example command-line `main()` wrappers that are only run by subprocess tests, which neither run under mutmut nor are visible to it (#182). The fix is to test them in-process, not to ignore them.
   - *Equivalent mutant:* the change cannot alter observable behaviour (a log message, a redundant guard). Note it and move on; do not contort the code.
   - *Timeout:* counted as killed, but a pattern of them can mean a mutant caused a loop; look once.
3. **File an issue per cluster** with the function, the surviving mutations and the assertion that would kill them. Existing examples are #182, #183 and #184. Search the open issues for the function first; the cluster may already be filed.
4. **Fix with a test that fails against the mutant**, then re-run the weekly job to see the module's score move. Do not chase 100%: the target is a to-do list, and the cost of the last survivors usually exceeds their value.
5. **Never change the scope or the target casually.** `only_mutate` must equal the critical modules plus the named supporting list (a test pins it), and changing the target is a reviewed change.

To run it by hand (Linux or macOS only; mutmut forks): `pip install -r requirements-dev.txt -r requirements-mutation.txt && ln -s scripts src && mutmut run`, then remove the `src` link.

## Golden snapshots

**Purpose.** Snapshots catch an unintended change in what a client receives (the `.docx` and `.xlsx` text), including one caused by a library upgrade.

When `tests/test_golden_outputs.py` fails, decide first whether the change was intended. If a dependency bump caused it, check that the difference is cosmetic (a serialisation detail) before regenerating. If it was intended, run `pytest tests/test_golden_outputs.py --update-snapshots`, read the diff like code, and commit the snapshots with the change. A missing snapshot fails instead of being created, and CI never regenerates.

## Re-tuning test parallelism

**Purpose.** CI runs the suite with a fixed number of pytest-xdist workers (`-n 4` on Ubuntu, `-n 2` on Windows), chosen by measurement in issue #212. As the suite grows those counts can stop being the best choice, and the only reliable way to choose again is to measure again. `.github/workflows/ci-parallelism-benchmark.yml` does the measuring. It runs only when started by hand and is never a gate.

**When.** When the suite step of ordinary CI runs has become clearly slower than the last measured baseline (its link is at the top of the workflow file), over several runs rather than one: hosted runners vary.

**Run it.**

1. Use `main`, or a branch whose CI is green and whose `badges/test-count.json` is current. The Linux jobs also run the test-count, coverage-floor and changed-line checks, and a failure there says nothing about speed.
2. Start it from the Actions tab (CI Parallelism Benchmark, Run workflow) or with `gh workflow run ci-parallelism-benchmark.yml --ref main`. One dispatch is 18 jobs (two operating systems, three worker counts, three repeats), at most six at a time, so start it when nothing else is waiting for runners.
3. Each job writes a one-row table to its summary on the run page: operating system, workers, repeat, pytest's elapsed time, the coverage total (Ubuntu only) and pytest's result line. The full `pytest_output.txt`, and on Ubuntu the coverage files, are kept as artifacts for 30 days (`gh run download <run id>`).

**Read it.**

- Use pytest's elapsed time, not the job's duration, which includes queueing and setup.
- For each operating system and worker count, take the median of the three repeats. Never compare single runs: in #212 the fastest and slowest of three runs of one configuration differed by up to 45%.
- Every job must report the same passed and xfailed counts, and every Ubuntu job the same coverage total. If they differ, the runs did not run the same tests and their timings are not comparable.

**Decide.** Prefer the smallest worker count whose median is within a few per cent of the best one; more workers add start-up cost and contention for little gain. Keep a fixed count rather than `-n auto`, whose value depends on the runner.

**Apply.** If the choice changes, change these in one pull request:

- the `-n` value in `.github/workflows/ci.yml`;
- the same command in [the full-suite section](#verify-a-change-the-full-suite) above and in [Testing and CI](testing.md);
- the strings `tests/test_ci_workflow.py` pins (it pins the benchmark's commands as well, so keep the two workflows in step);
- the baseline link at the top of the benchmark workflow.

Put the table of medians on the pull request or its issue so the next tuning has a baseline. This page does not hold results.

## Dependencies and advisories

**Purpose.** The deterministic core has few dependencies, and the audit exists to find out when one becomes unsafe.

### Reproducible dependency set

`requirements*.txt` retain minimum compatibility floors. `constraints.txt` pins the exact versions used by the repository's Python 3.11 runtime, dev-test, security and mutation profiles, including transitive packages. Each CI installation uses `-c constraints.txt`; the security audit resolves all four requirements files against the same constraints. The constraints file does not install packages on its own. The weekly `model-freshness` workflow is the one deliberate exception: it installs the requirements without constraints, because its report is only useful if it reflects the newest SDK's model lists.

**Refreshing the pins is a deliberate dependency change**, not an automatic consequence of changing a floor:

1. In clean Python 3.11 environments, install each profile from its requirements files without the existing constraints, then capture `pip freeze`. Runtime uses `requirements.txt`; tests use `requirements-dev.txt`; security adds `requirements-security.txt`; mutation adds `requirements-mutation.txt` on top of dev.
2. Combine those environment records into one sorted pin set. Each normalized package name must occur once. Resolve profile differences intentionally; if one global version cannot satisfy every profile, stop and revise the lock design instead of silently taking one environment's value.
3. Update `constraints.txt` with the observed direct and transitive versions, then rerun the matching full workflows with the candidate lock. Keep the requirements floors, constraints and the audited dependency set in the same reviewed change.
4. Use the uploaded `pip-freeze.txt`, `pip-freeze-runtime.txt`, `pip-freeze-security.txt` and `pip-freeze-mutation.txt` records as the evidence for their respective profiles. Do not infer transitive versions from minimums or regenerate from only the dev-test environment.
5. Weekly Dependabot PRs remain a signal to review upstream versions, not proof that the multi-profile constraints set has been refreshed. Make sure the affected constraints are reviewed and CI-tested in the same dependency-update PR; if the constraint file is not changed by the bot, refresh it using this procedure.


- **Dependabot** opens weekly pull requests for Python requirements and for GitHub Actions (including `requirements-security.txt`'s tools). Merge one only with CI green; a version bump that changes the badge or a snapshot is a finding to understand, not to patch around. Several bumps open together are merged one at a time so each failure has a single cause.
- **Actions stay pinned.** A Dependabot action update keeps the full commit SHA and the `# vX.Y.Z` comment; a test fails on a tag or branch reference.
- **A `security` job failure** is handled in this order: pin a fixed version; if none exists and the advisory has been reviewed as not exploitable here, a reviewed pull request adds `--ignore-vuln <ID>` with the required reason-and-revisit-date comment ([the procedure](testing.md#ci)); remove the ignore in the pull request that pins the fix, and dismiss the same advisory in GitHub's Dependabot alerts with a reason that links it.
- **Never** weaken the job: no `continue-on-error`, no `|| true`, no dropped requirements file.
- **Secret scanning and push protection** are repository settings, as is the ruleset on `main`; they are outside the files in this repository and are changed by the maintainer in GitHub.

## Model freshness report

**Purpose.** Tell maintainers when configured Claude model IDs may be stale or deprecated without silently changing
runtime behaviour. `.github/workflows/model-freshness.yml` runs every week and on demand only; it is not a pull-request
check or required status check. It installs the repository dependencies, reads model tables packaged in the installed
Anthropic SDK, and runs `scripts/check_model_freshness.py`. No API key, network request or model call is used. The
report is only as fresh as that installed SDK.

- **Findings create or update one issue.** If the report has findings, the workflow updates the open issue titled
  `Model freshness report`, labelled `tech-debt`, or creates it when no open issue with that exact title exists. It
  does not open a new issue for each run. With no findings it does nothing.
- **Treat the result as a prompt for review, not an automatic migration.** A newer ID is a stable version found in the
  same family, not proof that it is behaviourally interchangeable. Unknown IDs or a missing SDK table are inconclusive.
- **A model bump is its own pull request.** Do not edit `config/settings.json` or change `_DEFAULT_MODEL` as a side
  effect of this report. Treat model changes as behaviour changes: include an evaluation statement, use an available
  baseline for like-for-like comparison where one exists, and state clearly when a live evaluation/baseline has not
  been performed. The first bump to newer models is deliberately separate from the diagnostic. Removing the duplicate
  `_DEFAULT_MODEL` in `calibrate.py` is also a separate follow-up.

To inspect the current checkout without waiting for the schedule:

```bash
python scripts/check_model_freshness.py
python scripts/check_model_freshness.py --json model-freshness-report.json
```

See [Configuration](configuration.md#model-freshness-diagnostic) for the fields and limitations, and the
[CLI reference](cli-reference.md#model-freshness-check) for the report contract.

## Documentation maintenance

**Purpose.** The documentation is trustworthy only while it is checked, so most of the upkeep is running the checks and acting on them.

- **Run them:** `pytest tests/test_docs.py tests/test_docs_examples.py tests/test_docs_contracts.py tests/test_command_flags.py tests/test_prompt_consistency.py`. Each failure names a file and, where it can, a line.
- **After any source change,** read the pages that describe it. The checks catch renames and removals; they do not catch a behaviour that changed under an unchanged name.
- **Examples** are real runs of `docs/examples/synthetic_co/`. Change the page and the quoted output together, and let the test confirm they agree.
- **The index** (`docs/README.md`) links every page; its [project status](README.md#project-status) is the only dated statement in the corpus. Re-check that paragraph against issues #150 and #151 whenever it is touched, and when a live evaluation is run or a baseline is committed.
- **Evergreen pages stay evergreen:** no dates, no "not yet", no current counts. Put status in the index or the tracking issue.
- **The move ledger is frozen;** it records a past reorganisation.
- **The wiki is generated, not authored** ([Publishing the wiki](#publishing-the-wiki)): `docs/` stays the only authored source; direct wiki edits are overwritten by the next publish.

## Publishing the wiki

**Purpose.** Make the `docs/` corpus discoverable through GitHub's Wiki surface without creating a second documentation source. The `wiki-publish` workflow (`.github/workflows/wiki-publish.yml`, issue #192) publishes `docs/` to the repository's wiki one-way: `docs/` is canonical and authored; the wiki is generated from it and overwritten on every publish. The generated `_Sidebar.md` labels each page with its own first heading (its file name when it has none) and closes with an Examples group listing the two published example pages, which stay out of the docs index table, and a heading containing a construct the label reducer does not know how to render (raw HTML beyond comments and open/close tags, a link beyond the simple forms) falls back to that page's file name rather than risk a wrong label.

**What runs and when.** Every push to `main` that changes `docs/**`, `scripts/wiki_publish.py` or the workflow file itself, plus manual dispatches from the Actions tab. One job checks out `main`, runs `python3 scripts/wiki_publish.py --repo "$GITHUB_REPOSITORY" --publish --source-sha "$GITHUB_SHA"` with the runner's own Python (no installs), and pushes the generated pages to the wiki repository's default branch (`master`). The publisher replaces the wiki's entire content with the generated set: wiki files that are not in the generated tree are deleted, so a wiki edit is overwritten by the next publish -- change `docs/`, not the wiki. A publish that would change nothing commits and pushes nothing ("no change"). The one-time setup (enabling the wiki and creating its first page, which initialised `master`) is already done.

**The supported Markdown subset.** The generator rewrites links by pairing inline code spans across the lines of a block (a paragraph, a list item with its continuation lines, or a run of blockquote lines; a heading or table-row line is a block of its own) and recognises fenced code indented by at most three spaces. It supports only the Markdown subset the corpus uses, and fails the build -- naming file and line -- on a construct outside it rather than silently guess: a fence-looking line indented four or more spaces is an indented code block in CommonMark at the top level (inside a list item it can be a real fence, which the generator does not model), and misdetecting it would wrongly expose or hide the links around it (issue #192). A page that genuinely needs such a line is a maintainer decision about extending the subset, not something the publisher papered over.

**The permission exception.** The `wiki-publish` workflow is the one deliberate write-permission exception (the other documented exception is `model-freshness`'s `issues: write`): its single job holds `contents: write`, the candidate permission for pushing to the wiki -- whether that credential can in fact write the wiki is the one unverified fact the first run settles. The guards: the job runs only on `main` (re-checked in the job, because a dispatch can select any ref), never on a fork and never on a pull request; the checkout leaves no credential behind; the token reaches the publisher only through its environment and the script passes it to git only as an HTTP header -- never in a URL, a log line or a file. The credential itself carries main-repository contents authority; the fixed script is what confines its use to the one wiki push.

**Reading a failed run.** A generator error (a broken link or anchor in `docs/`, named file and line) fails before anything is cloned or pushed; fix the page. A red push step is one of three things, and its one `error:` line says which: a **transport error** (re-run the workflow; publishing is idempotent), an **authorisation rejection** — the signal that the repository's own token cannot write to the wiki, which is a maintainer decision (accept that the wiki does not publish, or change how the wiki is written to) and never something to work around with a fallback token or a personal access token; no such credential exists to misuse, and none should be created — or a failure whose **cause is not identified**, which is inconclusive and says nothing about the token: the publisher deliberately does not echo git's raw output into the run's log, so distinguishing it needs the maintainer's own investigation (for example re-running the workflow, or checking the wiki directly), not the log.

**Turning it off.** Disable the workflow from the Actions tab or delete `.github/workflows/wiki-publish.yml`; nothing else depends on it (it is not a required check), and the wiki then keeps whatever was last published.

## Live evaluation: execution and baseline boundaries

**Purpose.** The evaluation harness is the only instrument that measures whether a real model follows the prompts. It spends money and produces numbers that are easy to over-read, so every step is deliberate. The background and limits are in [Evaluation](evaluation.md) and [AI assurance](ai-assurance.md).

Boundaries that do not move:

- **Only by hand, with the maintainer's own key.** Nothing in CI, the tests or an automated session runs it, and no assistant is given the key. A GitHub secret for it is not created.
- **Capped before the key is read.** The plan (cases x repeats) is printed and refused over `--max-calls` (default 80, never above 250). The cap bounds calls, **not** tokens or cost; the plan shows the models, `max_tokens` and the output-token bound before you type `yes`.
- **Synthetic data only; results stay local.** Output goes under the git-ignored `evals/results/`.
- **A baseline is committed only by a deliberate manual step, in its own pull request,** as the exported summary (rates, model and served model, prompt hashes, dataset version; no model text).

The sequence, each step gated on reviewing the one before:

1. `python scripts/run_evals.py --validate` and `--dry-run` (zero model calls) on the current checkout.
2. A one-call smoke test of a single case (`--live --cases <id> --repeats 1`), then read its output and the review pack.
3. Only then the full run; never raise the repeats or the cap to chase a number.
4. `--export-baseline` on a **complete** run. It refuses a partial run (aborted, errored, interrupted, truncated, fewer runs than planned) unless `--allow-partial`, which marks the file partial; a partial file is evidence of a problem, not a baseline.
5. Copy the summary into `evals/baselines/<file>.json` by hand and open a pull request for it alone. The reviewer checks the model ID, prompt hashes and dataset version against what is on `main`.
6. For any later change to a prompt or the model, `--compare` the new run with that baseline before adopting it ([contributing](contributing.md#changing-an-agent-prompt)).

A baseline goes stale the moment a prompt, the model or the dataset changes; the hashes it records say so. Record the outcome of a run (and a failed or credit-rejected attempt) in issue #151, not in the evergreen pages.

## Readiness checklist

Use before merging a pull request, and before calling a stretch of work done.

- [ ] Working tree clean; the branch contains only the intended commits; no stray scratch branches or draft pull requests left open.
- [ ] `ruff check .`, `bandit -r scripts/ -ll` and the complete `pytest` run are green locally; the badge equals the run's passing count.
- [ ] All four CI jobs green on the head commit you reviewed; merge that commit (`gh pr merge <n> --squash --match-head-commit <sha>`), so nothing pushed after your review is merged with it.
- [ ] No open code-scanning alert introduced or left unexplained; after a merge that fixes one, its state on `main` is `fixed`.
- [ ] Open Dependabot pull requests triaged (merged, deferred with a reason, or closed).
- [ ] Documentation checks pass; every page that describes changed behaviour changed with it; no dated statement outside the index.
- [ ] The diff touches nothing under `agents/`, `config/`, `scripts/`, `.claude/`, `.github/` or `pyproject.toml` unless that was the purpose, stated in the description.
- [ ] No real data, key or secret in the diff, the description or the comments.
- [ ] Open issues reflect reality: closed if done, updated if scope changed, a new issue for anything found and deliberately not fixed.
