# OpenCAM Framework documentation

The detailed, human-facing documentation. The [project README](../README.md) is the landing page; this folder is
the reference. [`CLAUDE.md`](../CLAUDE.md) is a different thing: the operating rules and engineering contracts that
Claude Code follows when it works in this repository.

## Start here

| If you want to... | Read |
| :--- | :--- |
| Understand how the pieces fit together, and the difference between running through Claude Code and running the Python scripts | [Architecture](architecture.md) |
| Walk through a deal end to end with worked examples | [Workflows](workflows.md) |
| Look up what a slash command asks for and writes | [Command reference](commands.md) |
| Understand the figures: ratios, covenants, the policy engine, spreading | [Financial model and policy engine](financial-model.md) |
| Understand `state.json`, source manifests and persisted conventions | [Data model](data-model.md) |
| Run or script the tools | [CLI reference](cli-reference.md) |
| Customise templates or understand the exported documents | [Outputs and templates](outputs.md) |
| Know where credentials go and which settings do anything | [Configuration](configuration.md) |
| Run the tests, understand CI, coverage and mutation testing | [Testing and CI](testing.md) |
| Know what must never be committed, and what the security layers are | [Security and confidentiality](security.md) |
| Judge how far a generated memo can be trusted, and on what evidence | [AI assurance](ai-assurance.md) |
| Understand the local live-model evaluation harness | [Evaluation](evaluation.md) |
| Change the repository: rules, sources of truth, what must stay green | [Contributing](contributing.md) |
| Maintain it: verify, diagnose CI, triage mutation survivors, handle advisories, check readiness | [Operations runbook](operations.md) |
| Fix an error message or a surprising result | [Troubleshooting](troubleshooting.md) |
| Know why something is the way it is | [Design decisions](decisions.md) |

## Reading paths

- **An analyst using the framework:** [Architecture](architecture.md) (the two ways to run it), [Workflows](workflows.md), [Command reference](commands.md), then [Troubleshooting](troubleshooting.md) when something surprises you. [Financial model](financial-model.md) explains every figure.
- **Someone deciding whether to trust the output:** [Architecture](architecture.md#where-the-code-ends-and-the-model-begins), then [AI assurance](ai-assurance.md), then [Evaluation](evaluation.md) and the [project status](#project-status).
- **A contributor:** [Contributing](contributing.md), [Testing and CI](testing.md), [Security](security.md) and [Design decisions](decisions.md).
- **A maintainer:** [Operations runbook](operations.md), with [Testing and CI](testing.md) for what each check means and [Evaluation](evaluation.md) before running anything that spends money.

## What is in this folder

The pages are listed in reading order: how it works, how to use it, what it produces, how it is checked, how it is changed and run, and why it is the way it is.

| Document | Contents |
| :--- | :--- |
| [architecture.md](architecture.md) | The idea, the two execution modes (authentication, billing, what stays local), the pipeline, where code ends and the model begins, and the annotated repository map |
| [workflows.md](workflows.md) | Worked synthetic examples: calibration, research, spreading, policy checking, assembly and export, review, resuming a deal |
| [commands.md](commands.md) | The slash commands: inputs, outputs, state written, failure modes, cost and security |
| [financial-model.md](financial-model.md) | Financial terms and ratios with worked numbers, N/A and UNRESOLVABLE, covenants, the downside case, what the policy engine decides |
| [data-model.md](data-model.md) | `state.json`, its keys and versioning, the two raw-figure stores, sources, persisted conventions |
| [cli-reference.md](cli-reference.md) | Every script's usage, flags, output and exit status; the headless pipeline |
| [outputs.md](outputs.md) | What a run produces: the `.docx` rendering rules, the workbook layout, the research brief, templates |
| [configuration.md](configuration.md) | Every config file and environment variable, which `settings.json` keys are read, credentials |
| [testing.md](testing.md) | How the suite is organised and what a green run means, static analysis, coverage, CI, mutation testing, the test-count badge, the contract tests |
| [security.md](security.md) | The security layers and which are settings rather than files, what crosses a boundary, untrusted content, confidential paths, the PII scan |
| [ai-assurance.md](ai-assurance.md) | What code guarantees, what the tests show, what the prompts only expect, what live evaluation has not shown, what stays with a person |
| [evaluation.md](evaluation.md) | The evaluation harness: what it can and cannot show, reading a result, baselines, cost and safety, how its runner works |
| [contributing.md](contributing.md) | Sources of truth, keeping documentation aligned, synthetic data, confidentiality, testing expectations, state compatibility, snapshots, security review, prompt-change boundaries, pull-request sequencing |
| [operations.md](operations.md) | The maintainer runbook: full-suite verification, the badge, diagnosing CI, coverage and mutation reports, dependencies and advisories, documentation upkeep, live-evaluation boundaries, a readiness checklist |
| [troubleshooting.md](troubleshooting.md) | Error messages, rejection reasons and surprising results, with fixes |
| [decisions.md](decisions.md) | The durable design decisions, each with context, choice, rationale and consequences |
| [move-ledger.md](move-ledger.md) | Migration record: where every block of the old README and `CLAUDE.md` went |

Parts of these pages (marked at the point where they start) were moved from the README and `CLAUDE.md` rather than
rewritten; the examples on them are real runs of the synthetic deal in `docs/examples/synthetic_co/`, re-run by
the tests.

## Where each kind of fact is authoritative

Documentation describes the system; it does not define it. When a page and its source disagree, the source wins
and the page is a bug.

| Information | Authoritative source | Where it is explained |
| :--- | :--- | :--- |
| Command-line flags and their behaviour | The script itself (`python scripts/<name>.py --help`) | [CLI reference](cli-reference.md) |
| Slash-command behaviour | `.claude/commands/*.md` (the numbered list in `config/skills_registry.md`) | `README.md`, `CLAUDE.md` |
| Agent behaviour | `agents/underwriter_agent.md`, `agents/risk_reviewer_agent.md` | [Architecture](architecture.md) |
| Financial semantics | The implementation (`scripts/spreading_builder.py`, `scripts/policy_engine.py`, `scripts/policy_checks.py`) and its tests | [Financial model](financial-model.md) |
| State schema and versioning | `scripts/state_manager.py` and its tests | [Data model](data-model.md) |
| CI jobs and their rules | `.github/workflows/*.yml` and `tests/test_ci_workflow.py` | [Testing and CI](testing.md) |
| Tool settings, coverage floors, mutation scope | `pyproject.toml` | [Testing and CI](testing.md) |
| Security controls | The workflow files, `.gitignore` and the repository settings | [Security](security.md), `CLAUDE.md` |
| Which settings are read | The code that reads `config/settings.json` | [Configuration](configuration.md) |
| Rules Claude Code must follow when changing the repository | `CLAUDE.md` | `CLAUDE.md` |

Some facts are repeated on purpose (a flag in a quick start, a rule in `CLAUDE.md` and the page that explains
it). A few of those repetitions are checked by tests, so a rename or removal fails CI with the file and line:
every `--flag` a document tells a reader to pass, every relative link and heading anchor, every `Guideline N` /
`Audit Checklist item N` reference, the headings that code and commands cite by name, the figures and messages the
worked examples quote, the list of slash commands, the settings documented as read or inert, the CI jobs the pages
name, the repository paths they cite and the schema version the data-model page shows
([the contract tests](testing.md#the-contract-tests)).

## Project status

*As of 2026-10-09.* No live model evaluation has produced a result: the harness is built, and the two one-call
smoke-test attempts so far were rejected by the API for insufficient credit before any output, so there is no baseline
(issue #151). The first successful run and first baseline are deliberate, manual steps, and prompt hardening is
gated on them (issue #150). This section is the only
dated status in the documentation; the [evaluation guide](evaluation.md) describes how the harness works and does
not depend on it.

## Keeping the documentation current

- Change the source first, then the page that explains it, in the same pull request.
- Put a rule Claude Code must obey in `CLAUDE.md`; put an explanation, example or reference here.
- Use synthetic names and figures in examples (for instance "Synthetic Co", `0.20%`); never real borrower data.
- Do not add dated or volatile statements to evergreen pages; put them under [Project status](#project-status)
  or in the tracking issue.
- A new page is linked from this index, or the link test fails.
