# Contributing

Practical guidance for changing this repository: what to treat as true, what must stay green, what must never be committed, and how a change is sequenced into `main`. It assumes you have read the [architecture](architecture.md) page; the reasons behind the rules are in [design decisions](decisions.md). Maintainer procedures (diagnosing a red CI run, reading a mutation report, refreshing a snapshot) are in the [operations runbook](operations.md).

## Set up

```bash
python -m venv venv                    # then activate it (venv\Scriptsctivate on Windows)
pip install -r requirements-dev.txt    # runtime requirements + pytest, ruff, bandit, coverage and diff tools
pytest                                 # the whole suite; no API key and no network needed
```

CI runs Python 3.11 on Ubuntu and on Windows; Windows is the primary development platform, so a change must work with a cp1252 console, backslash paths and CRLF line endings. `pytest` rejects its own options until `requirements-dev.txt` is installed.

## What is true when sources disagree

Documentation describes the system; it does not define it. When two sources disagree, the one nearer the top is right and the other is a bug to fix in the same change.

| Fact | Authoritative source | Explained in |
| :--- | :--- | :--- |
| What the code does | The implementation and its tests (`scripts/`, `tests/`) | [CLI reference](cli-reference.md), [financial model](financial-model.md), [data model](data-model.md) |
| What a slash command does | `.claude/commands/*.md` | [Commands](commands.md) |
| What an agent is told to do | `agents/*.md` | [Architecture](architecture.md), [AI assurance](ai-assurance.md) |
| How CI, coverage and mutation testing are configured | `.github/workflows/`, `pyproject.toml` | [Testing and CI](testing.md) |
| What Claude Code must follow when it changes this repository | `CLAUDE.md` | `CLAUDE.md` |
| Everything else a reader needs | `docs/` | [The index](README.md) |

The full table, including flags, settings and security controls, is in the [documentation index](README.md#where-each-kind-of-fact-is-authoritative). A page that says something the source does not is wrong, not the source.

## Making a change

1. **Start from an issue**, or open one. A change that settles a question someone would otherwise reopen gets an entry in [decisions](decisions.md).
2. **Branch from an up-to-date `main`** and keep the change to one concern. Unrelated clean-ups go in their own pull request.
3. **Change the source first**, then the page that explains it, in the same pull request (next section).
4. **Run the checks** in [Testing and static analysis](#testing-and-static-analysis) before you push.
5. **Open a pull request** that says what changed and why, what you ran, and what you deliberately did not touch. A useful description ends with a boundary statement: "nothing under `agents/`, `.claude/`, `config/`, `scripts/`, `.github/` or `pyproject.toml` changed" is a claim a reviewer can check against the diff.
6. **Merge through the ruleset.** `main` takes changes through a pull request with the `test` check passing and up to date; the maintainer merges. Do not push to `main`.

## Keeping the documentation in step

- **Put each kind of text in its layer.** A rule Claude Code must obey goes in `CLAUDE.md`; an explanation, example or reference goes in `docs/`; the landing page stays short. `CLAUDE.md` is not a user manual: if you are explaining, you are in the wrong file.
- **Write the page and the source in one pull request.** A renamed flag, a new command, a changed setting or a new CI job is incomplete until the page that names it changes too.
- **Let the tests tell you what you missed.** Documentation has executable checks, and each fails with the file and line:
  - links and heading anchors resolve; every page under `docs/` is linked from the index; the `CLAUDE.md` headings that code and commands cite still exist (`tests/test_docs.py`);
  - every `--flag` a document passes to a script exists in that script's `--help` (`tests/test_command_flags.py`);
  - every figure and message a page quotes from the synthetic worked deal is what the real scripts print (`tests/test_docs_examples.py`);
  - the slash commands, the documented settings, the CI jobs, the cited repository paths and the schema version agree with the source (`tests/test_docs_contracts.py`);
  - `Guideline N` / `Audit Checklist item N` references resolve (`tests/test_prompt_consistency.py`).
- **Changing a quoted example.** The worked examples are real runs of `docs/examples/synthetic_co/`. If you change a formula, a message or an input, run the deal again, update the page with the new output, and let `tests/test_docs_examples.py` confirm they match; never edit a figure by hand to make a test pass.
- **Keep evergreen pages evergreen.** No "as of" dates, "has not been run yet" or current test counts in the reference pages. The only dated statement is [Project status](README.md#project-status) in the index; volatile status otherwise lives in the tracking issue.
- **Add a new page to the index**, or the link test fails.
- **The move ledger is a record, not a living page.** It describes the first reorganisation and is not updated when pages change afterwards.

## Synthetic data only

The repository is public and forkable. Everything in it that looks like a deal is invented.

- Use obviously fictional names ("Synthetic Co", "Synthetic Fleet Loan") and round, invented figures in examples, fixtures, snapshots and documentation. The evaluation dataset requires every company name to start with `Synthetic `.
- Run `python scripts/pii_scan.py <file>` over any template or text you add if there is any doubt. It is a heuristic (UK-shaped patterns); a clean result is not a guarantee, and a by-hand read is still required.
- Never put a real borrower, person, institution, figure, document or screenshot into the repository, a test, an issue or a pull request. The tests cannot see issue or pull-request text, so that one is a process rule.
- Do not copy a reference document someone shared with you into the repository, even into an ignored folder: it creates a persistent copy nobody asked for.
- An example credential is a placeholder such as `sk-ant-...`, never a real key, and never one that merely looks real.

## Confidentiality and credentials

Anything derived from a user's real material lives in a git-ignored location, and a test enforces the list ([Security](security.md)).

- **Adding a feature that stores something derived from real material?** Add its location to `.gitignore` *before* anything can write there, and add it to `PROTECTED_PATHS` in `tests/test_confidential_paths.py` in the same pull request. A new ignore line that is *not* confidential (build noise) goes in `NOT_CONFIDENTIAL` with a reason. Never store such material under a tracked path such as `templates/cam/`.
- **Credentials.** The only credential is `ANTHROPIC_API_KEY`, read from the environment by the headless scripts. Do not write it into a file, a deal folder, a prompt, a command, an issue, a workflow or a chat with an AI assistant. A live key pasted anywhere should be treated as leaked and rotated. Do not add any other secret, token or `.env` reader without a reviewed design.
- **Live model calls.** No test, workflow or default code path makes one. Only `python scripts/run_evals.py --live`, run by hand with your own key under a call cap, does ([Evaluation](evaluation.md)).

## Testing and static analysis

A pull request is ready when all of these pass locally, because CI will run them:

```bash
ruff check .                      # rules live in pyproject.toml
bandit -r scripts/ -ll
pytest --cov --cov-report=json --cov-report=xml
python scripts/check_coverage.py coverage.json
```

What the rules ask of you, in short (the full description is in [Testing and CI](testing.md)):

- **Test the change.** New behaviour comes with tests that fail without it; a bug fix comes with a test that reproduces it. The tests that guard a rule also demonstrate they *can* fail (see how `tests/test_docs_contracts.py` runs each check on deliberately wrong input).
- **Respect the floors.** 85% overall, 90% for each of eight governance modules, and 90% for the lines your pull request changes. Code that only runs on Windows needs `# pragma: no cover - <reason>`.
- **No warning, network or hang.** Warnings fail the run, `pytest-socket` forbids network access, and a test that runs over 120 seconds fails.
- **Encoding.** Every `open()` of a text file passes `encoding=` (the UTF-8 policy; ruff enforces it), and every script's `__main__` block starts with `from textio import configure_stdio` then `configure_stdio()`.
- **A new CLI** is added to the list in `tests/test_cli_subprocess.py`; a finding in `scripts/` is fixed or carries a `# noqa: <code>` with the reason beside it.
- **Known-bad behaviour** that you find but are not fixing is a *strict* `xfail` naming its issue, so fixing it later fails the test until the marker is removed.
- **No model.** Tests use fake clients; a change that makes a test need a key or a network is a bug.

## State compatibility

`state.json` outlives the code that wrote it: deals are resumed weeks later by a newer checkout.

- **Adding an optional key is fine** and needs no version bump. `SCHEMA_VERSION` changes when the schema gains a new *top-level shape* a reader must know how to interpret, not on every field.
- **Keep the fixtures working.** A change to the shape must keep the historical and malformed fixtures in `tests/fixtures/state/` (and the malformed-shape matrix in `tests/test_legacy_state.py`) passing, or add a new fixture that records the new shape.
- **Never downgrade.** `write_state()` and `append_review_trail()` refuse a newer or malformed `schema_version` and leave the file unchanged ([D7](decisions.md#d7-a-state-file-is-never-downgraded-and-malformed-state-fails-clearly)); do not add a write path around that.
- **Validate what you read.** A consumer checks only the keys it reads (`read_state(..., keys=...)`) and a bad shape is one `error:` line and exit status 1, never a traceback.
- **Two raw-figure stores stay separate** (`multi_period_financials` and `analyst_supplied_financials`); read them through `deal_export.py`'s `_financial_data_from_state()` logic and do not merge them ([data model](data-model.md)).
- If you bump `SCHEMA_VERSION`, update the version shown in [the data model](data-model.md); `tests/test_docs_contracts.py` fails if the page and the code disagree.

## Golden snapshots

`tests/snapshots/` holds the reviewed text of the exported `.docx` and `.xlsx` for synthetic inputs. They change only on purpose.

1. Make the intended output change.
2. Run `pytest tests/test_golden_outputs.py --update-snapshots`.
3. Read the snapshot diff like code and confirm every changed line is intended. A new `python-docx` or `openpyxl` release that alters serialisation shows up here; regenerate only after confirming the difference is cosmetic.
4. Commit the snapshots with the change that caused them.

CI never passes `--update-snapshots`, and a missing snapshot fails rather than being created, so a forgotten refresh is caught.

## Security review

Before asking for review, check the change against this list. Each item is a place the repository has had to be careful.

- **New subprocess, network, file or path handling.** Does a path come from model-written text? Treat it as untrusted; restrict it or surface it for review (the image-embedding rule in `CLAUDE.md` is the pattern).
- **New dependency.** It is added to the right requirements file, so `pip-audit` covers it; prefer the standard library for the deterministic core.
- **Workflow change.** Every third-party action pinned to a full commit SHA with a `# vX.Y.Z` comment; `permissions: {}` at the top and `contents: read` per job; `persist-credentials: false`; no `pull_request_target`; no `${{ ... }}` interpolated into a shell command. `zizmor` runs in CI and the structure is pinned by `tests/test_ci_workflow.py`. Never weaken the `security` job with `continue-on-error`, `|| true` or by dropping a requirements file; the advisory-ignore procedure is in [Testing and CI](testing.md#ci).
- **Content that reaches the model.** Source documents, fetched pages and analyst notes are untrusted data. Do not make a code path that treats them as instructions; the prompt-level hardening is deliberately sequenced after a baseline ([AI assurance](ai-assurance.md#why-prompt-hardening-150-waits-for-a-baseline)).
- **Confidential locations and credentials**, as above.
- **Settings and rulesets.** Repository settings, branch protection and rulesets are changed by the maintainer in GitHub, never by a pull request, and are not described by files in this repository.

## Changing an agent prompt

`agents/underwriter_agent.md` and `agents/risk_reviewer_agent.md` are the model's specification, and editing one is a deliberate act of its own.

- **Never as a side effect.** Do not change a prompt while doing something else, and do not change one to make documentation or a test easier.
- **Why it is different.** A headless deal records a content hash of each prompt in `model_provenance`, and an evaluation baseline records the hashes it measured. Editing a prompt makes earlier deals and earlier comparisons refer to text that no longer exists, and there is no automated way to tell whether the edit made the model better or worse until a live baseline exists ([AI assurance](ai-assurance.md#why-prompt-hardening-150-waits-for-a-baseline)).
- **So a prompt change has its own pull request, its own issue and explicit maintainer authorisation**, and its description states the evaluation story: the baseline it is compared with, or why none is needed.
- **Keep the prompts independent.** Do not merge them or have one import the other's context ([D1](decisions.md#d1-the-maker-and-the-checker-are-independent-prompts)).
- **Numbered references are guarded.** Inserting a Guideline or Audit Checklist item mid-list re-points every later reference; `tests/test_prompt_consistency.py` resolves each one, so renumber deliberately and update `EXPECTED_SUBJECTS` in the same change.
- **Documentation does not depend on the prompts' layout,** and the prompts currently reference neither `README.md` nor `CLAUDE.md`; keep it that way, so moving documentation never forces a prompt edit.
- **Command files are not prompts but behave like them.** A `.claude/commands/*.md` edit changes what a session does; treat it with the same care, and keep the plain-prose way of including a file ("Read `agents/underwriter_agent.md`"); there is no `@path` inclusion.
- **A known inconsistency is tracked, not patched in passing.** For example, the Risk Reviewer prompt's statement about `/review` (#197) is corrected only through this process.

## Pull request sequencing and the test-count badge

`badges/test-count.json` records how many tests pass. CI compares it with pytest's own summary line and fails on a mismatch; it never corrects it, because a bot committing to a branch or to `main` would break the rule that every change goes through review.

- If your change adds or removes passing tests, update the file **in the same pull request**, from a complete green run of the whole suite:

  ```bash
  pytest tests/ | tee pytest_output.txt
  python scripts/check_test_count.py pytest_output.txt --write
  ```

  `--write` refuses output that is visibly not a complete green run (failures, errors, deselected tests, an early halt), but it cannot tell that you ran only part of the suite, so run all of it.
- Because every pull request that adds tests edits this one-line file, **such pull requests are merged one at a time.** The second one rebases onto the new `main`, reruns the suite and runs `--write` again.
- A change that does not alter the passing count leaves the file alone; a documentation-only change usually does not touch it.
- Review the file's diff as part of the pull request: the number should equal the old number plus the tests you added.

## Before you ask for review

- [ ] The change is one concern, from a current `main`.
- [ ] Source changed first; every page, example and command text that describes it changed with it.
- [ ] `ruff check .`, `bandit -r scripts/ -ll` and the whole of `pytest` pass; coverage floors met.
- [ ] The badge matches a complete green run (or is untouched because the count did not change).
- [ ] No real data, key or secret anywhere; a new protected location is in `.gitignore` and `PROTECTED_PATHS`.
- [ ] No unrelated change, and no edit to `agents/`, settings or rulesets that was not the point of this pull request.
- [ ] The description says what was verified and what was left alone.
