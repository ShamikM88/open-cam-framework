# Skills

The analyst aids and the maintainer skills that ship with the framework. A skill is a short, reusable procedure that a person starts by name in a
Claude Code session opened on this repository; it is not a step of the CAM pipeline. This page is the user-facing
reference. Why skills are shaped this way, what they may never become, and what is planned or deferred is in
[Skill design](skill-design.md); the single inventory of commands and skills is
[`config/skills_registry.md`](../config/skills_registry.md).

## What every skill here is

- **Typed by a person.** Each is marked `disable-model-invocation`, so the model never starts one on its own, and no
  command, agent prompt or `CLAUDE.md` line names one.
- **Read-only by procedure, and tested for the commands it shows.** It runs in a forked subagent that cannot see your
  conversation. The `Write`, `Edit` and `NotebookEdit` tools are removed while it runs (`disallowed-tools`), and the only
  commands it pre-approves (`allowed-tools`) are three read-only script calls. `allowed-tools` pre-approves; it is not an
  allowlist, so any unlisted tool, such as `Bash` with another command, stays subject to your permission settings and
  would ask you. What keeps a skill read-only is therefore its procedure plus those settings, and the tests check that the
  commands it shows leave a deal unchanged. It is not an operating-system sandbox. It reads the deal's `state.json` (the
  record, not the conversation) and writes nothing by design: no `state.json` key, no `steps_completed` entry, no file.
- **An aid, not a step.** What it says is not part of the deal and is not provenance. It does not replace `/assemble`'s
  gates, the policy check or `/review`, and it never approves, rates or recommends.
- **Interactive only.** The headless pipeline (`orchestrator.py`, `calibrate.py`) cannot load a skill and has no
  equivalent, so using one never makes a deal that the headless pipeline could not reproduce.
- **Quote, do not compute.** Every number is copied from `state.json` or a script's output.

| Skill | Run | Use it to | Runs (all read-only) |
| :--- | :--- | :--- | :--- |
| `information-gaps` | `/information-gaps --company "<Name>" --proposal "<Proposal name>"` | Turn what is on disk into a prioritised request list: item, why it matters, where it should come from, priority, whether it blocks the next step | `state_manager.py --check-steps`, `source_manifest.py --check-sources`, `policy_check.py` (no `--draft`) |
| `evidence-discipline` | `/evidence-discipline --company "<Name>" --proposal "<Proposal name>" [claims]` | Audit the deal's evidence: saved source, cited but not saved, analyst-supplied, read from an image, or unsupported | `source_manifest.py --check-sources` |
| `financial-analysis` | `/financial-analysis --company "<Name>" --proposal "<Proposal name>"` | Read the recorded figures: what basis they rest on, levels and direction by period, what a `null` ratio means on that basis, covenant results as recorded | `policy_check.py` (no `--draft`) |

Each is described in the registry; the files are `.claude/skills/<name>/SKILL.md`, and the files are authoritative for
exactly what the model is told.

## Using them

- **Start from a deal that has a record.** Each first checks the company and proposal (it refuses one that is empty,
  starts with `-`, or contains a character that cannot be put safely inside a quoted command or a folder name, such as a
  double quote, `$`, a backtick, a slash or a colon, or a glob character, `* ? [ ] { }`, because the folder lookup is a
  glob), then reads the latest folder named exactly `<proposal>_YYYY-MM-DD` and says which folder it read; that is the
  same folder `state_manager` and the other scripts use. The lookup lists at most 100 files, newest-modified first; if it
  reaches 100 it says the latest folder could not be established and stops, rather than claim one. With no record it says
  so and stops.
- **A failing check is reported as a tool error, not as a finding about the deal.** A non-zero exit, a traceback or output
  that is not the expected JSON says nothing about the record.
- **What a skill returns stays in your conversation, and the Reviewer does not see it.** The skill's instructions stay
  out of your conversation because it is forked, but what it returns is visible to the later steps of that session,
  `/assemble` included, exactly as anything you type is. `/review` starts a fresh Risk Reviewer sub-agent that reads the
  saved draft and the deal's files and not your conversation, and a skill writes no file, so a skill's output is not part
  of the Reviewer's input ([#204](https://github.com/ShamikM88/open-cam-framework/issues/204); the limits are in [AI assurance](ai-assurance.md#where-the-enforcement-runs-matters)).
- **They are shaped by the record.** A gap list is only as good as what has been recorded; an evidence audit can show that a
  document was saved, never that it supports a claim (it says so on every such row); the analysis does not explain causes
  the record does not show.

### `information-gaps`

Lists steps not completed (only `spread` blocks `/assemble`), whether the `triage` or `commercial` citations were saved at all (a floor, see below), the conditions,
covenant results and security gaps the policy check already derives from the recorded state, and what the record itself
shows is absent (inputs, an analyst-supplied convention note, an unavailable downside, empty covenants or collateral). A
`null` ratio is treated by basis: in a framework-computed deal it means a zero or negative denominator or a missing input;
in an analyst-supplied deal it was recorded as given and its reason is not in the record, so it becomes a question to the
analyst, not a missing input.
With no calibrated credit policy it says so rather than assuming one. Priority is *blocks `/assemble`*, *the draft would
be rejected* (a policy-check reason, quoted verbatim), *needed for a complete CAM* or *optional*; it never states a view on
the borrower.

### `evidence-discipline`

Sorts each claim-bearing record into exactly one class: **saved source**, **cited, not saved** ("no manifest entry
matches", not "does not exist"), **analyst-supplied** (including `pd`, `lgd` and bureau scores), **read from an image**
(the analyst's confirmation is a record of what they said, not proof) or **unsupported**. It never writes, completes or
repairs a citation, a URL, a filename or a date. The script it runs, `source_manifest.py --check-sources`, is a floor: it
prints `true` only when `triage` or `commercial` declares a non-blank citation under `sources` and the manifest has no
entries at all. It does not match citations to entries, check that a file exists, or look at citations kept elsewhere, and
`false` does not mean every citation is backed. The skill does that reading itself, checks that the manifest is a JSON list
of objects before using it, and reports an unreadable one as a tool error. The script itself now fails cleanly on an unreadable, non-JSON or
wrong-shaped manifest (one `error:` line, exit status 1, [#207](https://github.com/ShamikM88/open-cam-framework/issues/207)); the
skill keeps its own check as defence in depth.

### `financial-analysis`

States what the figures rest on (framework-computed: every ratio recomputed from raw lines; analyst-supplied: recorded as
given, with the convention note), then the recorded levels by period (a `null` ratio is explained by basis: a zero or
negative denominator in a framework-computed deal; recorded as given, reason not held, in an analyst-supplied one), direction between adjacent periods with both figures
quoted, covenant results with their status and headroom as the policy check printed them (forward results are disclosure
only; downside breaches are enforced when the draft is checked), and recorded figures that sit oddly together. It produces
no growth rate, margin or other new number: a movement metric would be a calculation, so it would be a separate, tested
script.

## The maintainer procedures

`cam-change-verification`, `docs-maintenance` and `prompt-change-gate` are for the person who maintains the repository, not for an analyst, and
they touch no deal. The bullets under "What every skill here is" describe the analyst aids; they differ where this
section says. `docs-maintenance` is described after the details of `cam-change-verification`.

| Skill | Run | Use it to |
| :--- | :--- | :--- |
| `cam-change-verification` | `/cam-change-verification [what the change is about]` | Before asking for review, run in order and report: what changed and which sensitive paths (`agents/`, `config/`, `scripts/`, `.claude/`, `.github/`, `pyproject.toml`) it touches, Ruff and Bandit, the full suite with coverage, the test-count badge, the coverage floors and changed-line coverage, the documentation checks, a scan of added text, and a clean tree |
| `docs-maintenance` | `/docs-maintenance [what changed]` | For a change, find the pages and worked examples that describe the changed source, update them in the same change, run the documentation checks, and say what the checks cannot confirm |
| `prompt-change-gate` | `/prompt-change-gate [issue/PR or intended instruction change, optional]` | Before changing a prompt or slash-command instruction, gather evidence for the dedicated issue, explicit maintainer authorisation, provenance/evaluation story and structural consistency checks; report missing or unknown gates without approving the change |

- **Same as the aids (all three procedures):** typed by a person (`disable-model-invocation`), named by no command, prompt or
  `CLAUDE.md` line, and never input to the Maker or the Checker. All three run in your own session and make no network or model call.
- **`cam-change-verification` is different:** it is not forked, so you see the run, and `Write`, `Edit` and `NotebookEdit` are removed
  while it runs, so it makes no ad hoc edit to any tracked file. What does write is by design: the checks generate local artefacts, and the badge update is a
  separate, explicitly gated write. The artefacts are coverage files and `pytest_output.txt`, ignored by git. It runs the
  test suite. The next three bullets are about it; `docs-maintenance` follows them.
- **What it pre-approves:** only exact commands, `git status --short`, `git branch --show-current`, two
  `git diff ... origin/main...HEAD` forms, `ruff check .`, `bandit -r scripts/ -ll`, the one full-suite `pytest` command
  with `tee pytest_output.txt`, `check_test_count.py pytest_output.txt`, `check_coverage.py coverage.json`,
  `diff-cover coverage.xml --config-file pyproject.toml` and `pii_scan.py <path>`. Everything else, including a targeted
  `pytest` run and `check_test_count.py --write`, is not pre-approved, so your permission settings decide, and by default
  they ask you.
- **What actually holds the limits:** the procedure, plus the removed write tools. `allowed-tools` pre-approves; it does not
  restrict, and a settings allow rule or a mode that skips prompts (such as `bypassPermissions`) removes the question for a
  command that is not pre-approved. The procedure therefore does not rely on a prompt: it will not run a commit, push, merge,
  `gh` call, `--write` without your go-ahead, or `--update-snapshots` in any mode. To enforce that rather than rely on it, add deny
  rules to your permission settings. No test here, and no frontmatter field, can enforce it.
- **What it will not do:** commit, push, merge, rebase, run `gh`, change a setting or ruleset, edit a file, regenerate a
  golden snapshot, edit an agent prompt, or hand-edit the badge. It offers `--write` only after a complete green run and
  waits for your go-ahead.
- **Limits:** a green local run is not a green CI run. It cannot show the Windows job, the clean-environment
  `runtime-smoke` job, `pip-audit`, `zizmor` or CodeQL, and says so. It does not judge the design, and the strongest thing
  it reports is "no blocker found by these checks". `diff-cover` sees only committed changes, and `origin/main` is as fresh
  as your last fetch (it does not fetch).
- **Source of truth:** [Operations](operations.md#verify-a-change-the-full-suite) and
  [Contributing](contributing.md#before-you-ask-for-review); the skill points to them for the flags and does not restate
  what each check means.

### `docs-maintenance`

Maps a change to the kind of fact that moved (using the authoritative-source table in the [docs index](README.md#where-each-kind-of-fact-is-authoritative)),
searches `docs/`, `README.md`, `CLAUDE.md`, `evals/README.md` and the command files for every name that changed, reads each
page against the diff (the checks catch renames and removals, not behaviour that changed under an unchanged name), brings the
page in step with the source, runs the five documentation test files, and reports each page it affected, whether it changed it
and what it confirmed.

- **What it edits:** `docs/` and `README.md`, to make them describe the source. Command text only when you ask. It does not
  edit `agents/*.md` (it stops and points to [the prompt-change process](contributing.md#changing-an-agent-prompt)),
  `CLAUDE.md`, source, tests, configuration or workflows; it never regenerates a golden snapshot, hand-edits a quoted example
  figure, or touches the move ledger. Where a page and the source disagree and the source looks wrong, it reports that and
  changes nothing.
- **What it pre-approves:** `git status --short`, `git branch --show-current`, the two `git diff ... origin/main...HEAD` forms,
  the documentation tests as one exact `pytest` command, and `pii_scan.py <path>`. It does **not** pre-approve `Edit` or
  `Write`, so by default each edit asks you; as for the other procedure, a settings allow rule or a mode that skips prompts
  removes that question, and the edit boundary above is therefore the skill's own rule in every mode. A deny rule in your
  permission settings would enforce it; no test here can.
- **Limits:** a green documentation run says the names, links, flags and quoted examples agree, not that every sentence is
  true. It says which pages it read against the diff and which it only searched. Wiki publication (#192) is separate.

## The maintainer report

`mutation-triage` is for the person who maintains the repository and touches no deal. Unlike the two procedures above it is
a *report*: it runs forked and read-only, like the analyst aids, but its input is a downloaded mutation report rather than a
deal.

| Skill | Run | Use it to |
| :--- | :--- | :--- |
| `mutation-triage` | `/mutation-triage <path to the downloaded mutation-report folder or its mutation-report.json> [module or module.function]` | Read a mutation report and triage the surviving mutants: the scores as recorded, the largest groups of survivors by function, which tests name each function and whether the mutation run's test selection includes them, a candidate class for each group, the issue that may already cover it, and a proposed disposition with its evidence |

### `mutation-triage`

Download the `mutation-report` artifact from a weekly or manual run of the mutation workflow (the workflow and what the
report holds are in [Testing](testing.md#mutation-testing); the triage procedure it follows is the runbook in
[Operations](operations.md#mutation-testing-reading-the-report-and-triaging-survivors)). Give the skill the path. It reads
`mutation-report.json`, the survivors list and the source and tests it names, and it reports in a fixed table. It
stops first, and triages nothing, if the report carries completeness warnings, because the run is then suspect before the
score is.

- **What it does for each group of survivors** (a function, with its count): finds the function's source; finds the tests that
  name it and says which are in the mutation run's test selection (`[tool.mutmut]` in `pyproject.toml`; a test outside it
  cannot kill a mutant, and a test that reaches the function only through a subprocess is invisible to mutmut); says which of
  the function's branches and values a selected test asserts on; and classifies the group as a **candidate**: missing
  assertion, unreachable as selected, possible equivalent, timeout, or cannot classify. It names the issue the docs already
  attach the group to (for example the command-line `main()` wrappers, the draft-audit and covenant functions, the calibrate
  helpers) and asks you to search open issues for the rest, so it points to existing work instead of duplicating it.
- **What you get back:** a proposed disposition per group (add an assertion and say which behaviour and what defect it would
  catch; add an in-process test; review the test selection; an existing issue covers it; record as a possible equivalent;
  needs `mutmut show` first; leave) with its evidence. You record it, in an issue or a pull request; the skill files nothing.
- **Example.** In the report of the manual run on `main` `59e3836` the 16 mutants of `deal_export._supplied_row_values`
  are `no tests`: no test names the function and the selected tests never reach it, so the candidate class is *unreachable as
  selected*, and the disposition is an in-process test or a reviewed look at the selection. The 75 survivors of
  `policy_engine._evaluate_downside_covenants` are `survived`, so the selected tests do reach it; the one test file that names
  it is `tests/test_forward_covenants.py`, which is not in the selection. That is a finding about the selection, reported
  for a reviewed change, and a candidate *missing assertion* for the behaviour no selected test checks. Both are
  [#219](https://github.com/ShamikM88/open-cam-framework/issues/219)'s groups.
- **Read-only, and what that rests on.** It pre-approves only `Read`, `Glob` and `Grep`, and `Write`, `Edit`, `NotebookEdit`
  and `Bash` are removed while it runs, so it runs no `mutmut`, no test and no script, and edits and files nothing. A tool
  that is not removed stays under your permission settings, so its own rule is to use only the three read tools; a deny rule
  would enforce that. It never changes the mutation scope, the 75% diagnostic target, the configuration or the workflow.
- **Limits.** The artifact lists mutant names and statuses, not the mutated source, so every class is a candidate:
  it never calls a mutant equivalent until you have seen the mutated line (`mutmut show <id>` after a local `mutmut run`, on
  Linux or macOS). The link from a function to its tests is by name, so a function reached only through its caller looks
  untested. The artifact records no commit, so the source it reads must be the revision that was mutated. It cannot search
  the issue tracker. A proposed test is never "to raise the score": it states the behaviour and the defect it would catch.

## Requirements

These are Claude Code features, so they follow its behaviour: a skill in `.claude/skills/<name>/SKILL.md`, a `context:
fork` subagent, `disable-model-invocation`, and `allowed-tools` as a pre-approval of the read-only scripts (it is not an
allowlist; `disallowed-tools` removes the write tools, and everything else stays under your permission settings). The `background: false` setting makes the result come back in
the same turn and needs a recent Claude Code; an older version ignores it and returns the result when it is ready. On
macOS, Linux and WSL the `Glob` tool is absent from Claude Code's default tool set (it searches with `find` through
`Bash`, which these skills do not pre-approve), so there you may be asked to approve the folder lookup, or enable `Glob`
for the session as Claude Code's documentation describes.

## Changing or adding a skill

A skill pull request meets the contract in [Skill design](skill-design.md#phased-delivery), and the tests that enforce
it are in `tests/test_skills.py` (the analyst aids), `tests/test_maintainer_skills.py` and
`tests/test_docs_maintenance_skill.py` (the procedures), `tests/test_mutation_triage_skill.py` (the report) and
`tests/test_skill_inventory.py`: the frontmatter equals the registry row, the
`allowed-tools` name only read-only scripts, every source it cites exists, the files carry no real names or figures, the
scripts it runs leave a deal byte-for-byte unchanged, and the frontmatter stays inside a small YAML subset that a strict
parser in the tests reads (it is not a general YAML validator). A skill that wrote to a deal, was named by a command or prompt, or
changed what the Maker or the Checker is told would be a different kind of thing and is not permitted without its own
design.
