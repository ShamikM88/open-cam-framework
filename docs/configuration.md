# Configuration

> The sections "Overview" and "Which settings are read" were moved from the README and `CLAUDE.md` (unchanged apart from the fixes in the [move ledger](move-ledger.md)); the rest of the page is written for readers.

Everything that changes how a run behaves: the configuration files, the environment variables, the rules that decide which file or value wins, which settings are really read, and where credentials go.

## Configuration map

Everything that changes how a run behaves, in one place. Items marked *git-ignored* are generated from your own
material and never committed (see [Security](security.md)).

### Files

| File | Written by | Read by | Effect |
| :--- | :--- | :--- | :--- |
| `config/settings.json` (tracked) | you | `orchestrator.py`, `calibrate.py`, `check_model_freshness.py` | Which model drafts and audits, optional temperatures. The freshness diagnostic reads model IDs to report stale or deprecated choices; only the model and temperature keys affect model calls. See [Which settings are read](#which-settings-are-read) |
| `config/style_guide.md` *git-ignored* | `/calibrate`, `calibrate.py` | the Underwriter (the orchestrator puts it in the Maker's prompt only; in Claude Code the Underwriter role is told to follow it) | House tone and layout. Absent means the neutral default tone |
| `templates/local/cam/<type>_cam.md` *git-ignored* | `/calibrate`, `calibrate.py`, or auto-saved for a new deal type | `/assemble`, the orchestrator | A calibrated CAM template; **takes precedence** over the shipped one for that type |
| `templates/cam/<type>_cam.md` (tracked) | the project | the same | The shipped default templates (`corporate_credit`, `asset_finance`) |
| `config/credit_policy.md` *git-ignored* | `/calibrate-policy` | both agents | Your institution's lending rules. The Underwriter drafts with awareness of it; the Risk Reviewer audits against it and a violation is a mandatory rejection finding |
| `config/credit_policy_notes.md` *git-ignored* | `/review`, on explicit confirmation | both agents | Persisted corrections to how a policy clause is interpreted; mandatory for the Reviewer |
| `config/deal_learnings.md`, `deals/<Company>/_learnings.md` *git-ignored* | `/assemble`, `/research`, on explicit confirmation | the Underwriter | Advisory end-of-deal takeaways, enterprise-wide or per borrower |
| `config/spreading_conventions.json`, `deals/<Company>/_conventions.json` *git-ignored* | `/spread` via `conventions.py` | `/spread` | A confirmed spreading convention; always re-confirmed, never silently applied |
| `inputs/calibration_samples/`, `inputs/credit_policy/` *git-ignored* | you | the calibration commands | The documents calibration reads |
| `config/skills_registry.md` (tracked) | the project | people (and the slash-command text points at it) | The inventory of commands and skills; the files in `.claude/commands/` (and a skill's `SKILL.md`) are what runs |

### Environment variables

| Variable | Read by | Meaning |
| :--- | :--- | :--- |
| `ANTHROPIC_API_KEY` | `calibrate.py`, `orchestrator.py`, `run_evals.py --live` | Explicit API-key input. Without it, `calibrate.py` falls back to `--mock` and `run_evals.py --live` refuses to start. `orchestrator.py` passes `None` to the SDK when it is absent, so SDK credential discovery may still apply. Never needed by slash commands. See [Credentials](#credentials). |
| `ANTHROPIC_BASE_URL` | the `anthropic` SDK | If set, the SDK sends requests there; `run_evals.py` shows it in the plan it prints before a live run. Leave it unset unless you route API traffic through your own gateway. |
| `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_PROFILE`, `ANTHROPIC_IDENTITY_TOKEN` | the installed `anthropic` SDK, when supported by that version | SDK-level alternatives for authentication/profile selection. These are not read directly by OpenCAM's CLI code; inspect the installed SDK's documented credential precedence before use. |
| `ANTHROPIC_CUSTOM_HEADERS` | the installed `anthropic` SDK, when supported by that version | SDK-level request configuration; it can affect request behavior and is not read directly by OpenCAM's CLI code. |
| `HYPOTHESIS_PROFILE` | the test suite | `ci` (default, fixed seed) or `explore` (searches harder with random seeds), for `tests/test_properties.py`. |

OpenCAM does not load a `.env` file or fetch application configuration from the network. The `anthropic` SDK may read additional environment variables or on-disk credentials/profile configuration; the examples above are not an exhaustive inventory for every SDK version. Review the SDK version and credential sources used by your environment before running a headless command.

### Rules that decide which file or value is used

- **Model:** `maker_model` is required (a missing one stops the orchestrator with an error rather than guessing);
  `checker_model` defaults to the Maker's model; `calibrate_model` defaults to `maker_model`; a temperature that is not
  set is not sent, so the API default applies.
- **Template:** `templates/local/cam/<type>_cam.md` if it exists, else `templates/cam/<type>_cam.md`, else the deal
  type is new and the first draft's structure is saved as a local template.
- **Dated folder:** the most recent `<Proposal>_<YYYY-MM-DD>` folder for that company and proposal; a new deal, or
  `orchestrator.py --new-review`, gets today's date.
- **Orchestrator defaults you should override:** `--pd` defaults to `0.20%`, `--lgd` to `LGD 3 (15%)` and `--type` to
  `corporate_credit`. The PD and LGD defaults are example values; if you omit the flags they flow into the CAM as if
  you had supplied them. Always pass your own.

## Overview

[`config/settings.json`](../config/settings.json) sets `maker_model` (required), plus optional
`checker_model`/`maker_temperature`/`checker_temperature` to independently configure the Risk
Reviewer vs. the Underwriter. It also accepts `max_tokens`/`default_currency`/`output_directory`/
`template_directory`/`spreading_template_directory`, though no script reads them -- they have no
effect (see "Which settings are read" below). Agent behaviour is set by `agents/*.md` alone; there is no shared
system-prompt file.

## Which settings are read

Only the model and temperature keys are read; the other keys the shipped file carries are **inert** -- editing them
changes nothing, because the corresponding values are constants in the code (issue #108). An operator who raises
`max_tokens` because drafts are cut off must change the code, not this file.

| Key | Read by | Effect |
| --- | --- | --- |
| `maker_model` (required) | `orchestrator.py` (`_resolve_maker_checker_config`), `calibrate.py` (fallback) | Model that drafts; missing = the pipeline refuses to start |
| `checker_model` | `orchestrator.py` | Model that audits; defaults to `maker_model` |
| `maker_temperature`, `checker_temperature` | `orchestrator.py` | Optional sampling temperature per agent; unset = the API default |
| `calibrate_model` | `calibrate.py` | Model for `calibrate.py`; defaults to `maker_model` |
| `max_tokens` | **nothing** | No effect. Real values: `orchestrator.py` 4000 (draft and revision) and 2000 (audit) at the three `messages.create` calls, `calibrate.py` 3000 per call and `MERGE_MAX_TOKENS` = 6000; the eval harness mirrors 4000/2000 in `eval_runner.py` for its budget |
| `default_currency` | **nothing** | No effect; no script labels a currency |
| `output_directory` | **nothing** | No effect; deal output is always `deals/` (`state_manager.DEALS_DIR`) |
| `template_directory` | **nothing** | No effect; CAM templates are always `templates/local/cam/` then `templates/cam/` (`template_resolver`) |
| `spreading_template_directory` | **nothing** | No effect; the workbook layout is generated by `spreading_builder.py` (`templates/spreading/` is only a reference copy) |

Wiring these keys in is deliberately not done: one `max_tokens` could not stand for three call sites with different
limits, and the evaluation harness's budgets and baselines assume the current values. The five inert keys stay in the
shipped file so existing forks keep a valid config; do not rely on them.

## Model freshness diagnostic

Run `python scripts/check_model_freshness.py [--json PATH]` to compare the configured `maker_model`,
`checker_model` and optional `calibrate_model`, plus the literal `_DEFAULT_MODEL` in `scripts/calibrate.py`, with the
known-model and deprecation tables packaged in the installed Anthropic SDK. The calibration default is read using
Python's AST parser; the script does not import `calibrate.py`. It does not call the API, need a key, or access the
network. It only reports; it never edits `config/settings.json` or changes a model.

The report lists newer stable IDs only within the same family, deprecation dates and IDs it cannot parse or find. A
missing or reshaped SDK table is reported as `SDK table not found` rather than guessed around. The report is only as
fresh as the installed SDK version, which Dependabot refreshes through the normal dependency-update process. See the
[CLI reference](cli-reference.md#model-freshness-check) and [operations runbook](operations.md#model-freshness-report).

## Credentials

OpenCAM explicitly reads `ANTHROPIC_API_KEY` in its headless scripts (`scripts/calibrate.py`,
`scripts/orchestrator.py` and the live mode of `scripts/run_evals.py`). Their behavior differs when it is absent:
calibration selects `--mock`, live evaluation requires the key, and the orchestrator passes `api_key=None` to the
SDK rather than refusing at that point. The SDK can then resolve credentials/profile settings through its own
supported environment variables or on-disk configuration (depending on its installed version). Therefore,
`ANTHROPIC_API_KEY` is the framework's explicit key input, **not a guarantee that no other SDK credential source
can be used**. The slash commands use your Claude Code login and do not need this API key for their normal
interactive workflow.

- **Where to set it:** in your own shell or your scheduler's secret store (for example `export
  ANTHROPIC_API_KEY=...` on macOS/Linux, `$env:ANTHROPIC_API_KEY="..."` in PowerShell, `set
  ANTHROPIC_API_KEY=...` in `cmd`). Use your own key; the evaluation harness is built around that.
- **Where it must not go:** a file in this repository (`config/settings.json` is git-tracked and is not read for
  keys; a `.env` file and `*.key` files are git-ignored as a safety net, but nothing in the framework reads a
  `.env` file); a deal folder; a prompt, command or issue; an AI assistant's chat, where it ends up in transcripts
  and logs; a CI workflow file (CI runs the tests with no key at all).
- **Without a key:** `calibrate.py` falls back to `--mock` and says so; `orchestrator.py` stops before it reads or
  writes anything, with one `error: no Anthropic credentials were found` line and exit status 1, and sends nothing; the test
  suite never needs one.
- **What costs money:** any headless run that reaches the API, including `python scripts/run_evals.py --live`,
  which prints its plan and refuses to go over `--max-calls` before it reads the key. The slash commands are
  covered by your Claude Code plan.

Examples in this documentation never contain a real key; use a placeholder such as `sk-ant-...`.
