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
| Know why something is the way it is | [Design decisions](decisions.md) |
| Fix an error message or a surprising result | [Troubleshooting](troubleshooting.md) |
| Run or script the tools, and know where credentials go | [Configuration](configuration.md), [CLI reference](cli-reference.md) |
| Understand the figures: ratios, covenants, the policy engine, spreading | [Financial model and policy engine](financial-model.md) |
| Understand `state.json`, source manifests and persisted conventions | [Data model](data-model.md) |
| Customise templates or understand the exported documents | [Outputs and templates](outputs.md) |
| Run the tests, understand CI, coverage and mutation testing | [Testing and CI](testing.md) |
| Know what must never be committed | [Security and confidentiality](security.md) |
| Understand the local live-model evaluation harness | [Evaluation](evaluation.md) |

## What is in this folder

| Document | Contents |
| :--- | :--- |
| [architecture.md](architecture.md) | The idea, the two execution modes (authentication, billing, what stays local), the pipeline, where code ends and the model begins, and the annotated repository map |
| [workflows.md](workflows.md) | Worked synthetic examples: calibration, research, spreading, policy checking, assembly and export, review, resuming a deal |
| [commands.md](commands.md) | The slash commands: inputs, outputs, state written, failure modes, cost and security |
| [decisions.md](decisions.md) | The durable design decisions, each with context, choice, rationale and consequences |
| [troubleshooting.md](troubleshooting.md) | Error messages, rejection reasons and surprising results, with fixes |
| [configuration.md](configuration.md) | Every config file and environment variable, which `settings.json` keys are read, credentials |
| [cli-reference.md](cli-reference.md) | Every script's usage, flags, output and exit status; the headless pipeline |
| [data-model.md](data-model.md) | `state.json`, its keys and versioning, the two raw-figure stores, sources, persisted conventions |
| [financial-model.md](financial-model.md) | Financial terms and ratios with worked numbers, N/A and UNRESOLVABLE, covenants, the downside case, what the policy engine decides |
| [outputs.md](outputs.md) | What a run produces: the `.docx` rendering rules, the workbook layout, the research brief, templates |
| [testing.md](testing.md) | The test suite, static analysis, coverage, CI, mutation testing, the test-count badge |
| [security.md](security.md) | Confidential paths, the PII scan |
| [evaluation.md](evaluation.md) | The evaluation harness: what it measures, its boundaries, how its runner works |
| [move-ledger.md](move-ledger.md) | Migration record: where every block of the old README and `CLAUDE.md` went |

Parts of these pages (marked at the point where they start) were moved from the README and `CLAUDE.md` rather than
rewritten; the examples on them are real runs of the synthetic deal in `docs/examples/synthetic_co/`, re-run by
the tests. The remaining guides (contributing, AI assurance, security and testing in depth) are being added
under the same issue (#117) and will be linked here as they land.

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
`Audit Checklist item N` reference, and the headings that code and commands cite by name.

## Project status

*As of 2026-10-04.* No live model evaluation has been run yet; the first run and first baseline are deliberate,
manual steps tracked in issue #151, and prompt hardening is gated on them (issue #150). This section is the only
dated status in the documentation; the [evaluation guide](evaluation.md) describes how the harness works and does
not depend on it.

## Keeping the documentation current

- Change the source first, then the page that explains it, in the same pull request.
- Put a rule Claude Code must obey in `CLAUDE.md`; put an explanation, example or reference here.
- Use synthetic names and figures in examples (for instance "Synthetic Co", `0.20%`); never real borrower data.
- Do not add dated or volatile statements to evergreen pages; put them under [Project status](#project-status)
  or in the tracking issue.
- A new page is linked from this index, or the link test fails.
