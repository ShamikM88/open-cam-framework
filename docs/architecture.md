# Architecture

How OpenCAM Framework is put together: the two agents, the two ways to run it, how a deal moves through the
pipeline, where its state lives, and where the code that must be right is separated from the model that must be
checked. For the commands and scripts themselves, see the reference pages linked from the
[documentation index](README.md).

## The idea in one paragraph

A **Maker** (the Underwriter agent) drafts a Credit Assessment Memorandum (CAM) from source documents and
user-supplied risk inputs; a **Checker** (the Risk Reviewer agent, in a context that holds none of the Maker's reasoning) audits the draft and returns
`APPROVED` or `REJECTED` with revision notes. Everything that can be computed deterministically (ratios,
covenant results, required conditions, security gaps) is computed by code, handed to both agents as ground
truth, and enforced by code on the draft, so the agents narrate and judge rather than calculate. Every step
checkpoints its results to a per-deal `state.json`, so nothing important lives only in a conversation. The
result is exported as an editable `.docx` and an auditable `.xlsx` (a research-only deal run through `/research` exports a standalone `.docx` brief instead).

## Two ways to run it

Both paths share the deterministic core (spreading, the policy engine and its checks, state handling, export),
the state model, the templates, and the style-guide and credit-policy configuration. The full-CAM workflow
(`/triage` through `/assemble`, or `orchestrator.py`) is available through both and produces the same `.docx` and
`.xlsx`. Some capabilities are specific to one interface: `/research` (a standalone research brief, exported as a
`.docx` through `research_export.py`, with no financials, no CAM and no workbook) and `/calibrate-policy` exist
only as Claude Code commands, and so does the interactive confirmation behind persisted conventions (the
headless pipeline can inherit what has been confirmed but cannot create it). The two paths differ in **who calls
the model, how it is authenticated and what it costs**.

| | Claude Code slash commands | Headless Python scripts |
| :--- | :--- | :--- |
| Entry points | `/calibrate`, `/calibrate-policy`, `/triage`, `/research`, `/spread`, `/commercial`, `/collateral`, `/project`, `/assemble`, `/review`, `/start-review`, `/committee-summary` (`/research`, `/calibrate-policy` and `/committee-summary` have no orchestrator mode) | `scripts/calibrate.py`, `scripts/orchestrator.py` (and the local evaluation harness, `scripts/run_evals.py`) |
| Where it runs | Interactively, inside a Claude Code session opened on this repository | From a terminal, a scheduler or CI |
| Who calls the model | Claude Code itself, as the model running your session | The `anthropic` Python SDK, from `scripts/` |
| Authentication | Your Claude Code login. **No `ANTHROPIC_API_KEY` is needed or read.** | `ANTHROPIC_API_KEY` in the environment of the process |
| Billing | Whatever your Claude Code plan covers | Per-token Anthropic **API** billing, separate from any Claude Code subscription |
| What stays local | Your files, `state.json`, the generated documents, and the deterministic scripts the commands run through Bash (`spreading_check.py`, `policy_check.py`, `deal_export.py`, ...) | The same files and scripts |
| What goes to an external service | What the session sends to Claude Code's model while it works (the prompts, the documents it reads, the figures you give it); the public web pages and filings a research step is told to fetch | The prompts and documents the script sends to the Anthropic API |
| Without a key | Not applicable | `calibrate.py` falls back to `--mock` (placeholder output, no API call); `orchestrator.py` stops before a model call with one `error: no Anthropic credentials were found` line. A `--new-review` run can already have created its minimal state and optional `prior_review_date` link before credential resolution; it writes no financial/model checkpoint or generated documents without credentials |
| Offline testing | The deterministic scripts and the test suite need no network | The test suite uses fake clients: no key, no network |

Two boundaries to keep in mind:

- **Credentials.** The only model credential the framework uses is the `ANTHROPIC_API_KEY` environment variable, and
  only the headless scripts read it. (The one other token is the `wiki-publish` workflow's own `GITHUB_TOKEN`, which
  GitHub issues to that job and `wiki_publish.py --publish` hands to git as an HTTP header; it is not a secret you
  set, and there is no fallback token.) Set it in your own shell; never write it into a file in this repository
  (`config/settings.json` is git-tracked and is not read for keys), never put it in a deal folder, a prompt or a
  command, and never paste a live key into an AI assistant's chat, where it ends up in transcripts and logs. A
  `.env` file and `*.key` files are git-ignored as a safety net, but nothing in the framework reads a `.env`
  file. See [Configuration](configuration.md#credentials).
- **Live model calls from the tests and the evaluation harness.** The test suite never makes a model call. The
  evaluation harness only does so when you run `python scripts/run_evals.py --live` yourself, with your own key,
  under a hard call cap, on synthetic data. See [Evaluation](evaluation.md).

## The deal pipeline

```mermaid
flowchart LR
    S["/triage<br/>/research"] --> SP["/spread"]
    SP --> C["/commercial"]
    C --> CO["/collateral"]
    CO --> P["/project<br/>(optional)"]
    P --> A["/assemble<br/>draft into template"]
    A --> R{"/review<br/>Risk Reviewer"}
    R -- REJECTED --> A
    R -- APPROVED --> E["deal_export.py<br/>.docx + .xlsx"]
    S -. checkpoint .-> ST[("state.json")]
    SP -.-> ST
    C -.-> ST
    CO -.-> ST
    P -.-> ST
    A -.-> ST
    R -.-> ST
```

Each step reads `state.json` first and writes its own results back when it finishes (the
[checkpoint and re-hydration rules](../CLAUDE.md#context-window--state-management-protocol) are normative).
`/research` is a shorter route for a deal that needs only the Go/No-Go screen and company research: it writes
the same `triage` and `commercial` state a full run would, so the deal can continue into `/spread` later.

The headless pipeline is the same loop in one process:

```mermaid
flowchart LR
    I["orchestrator.py<br/>load state, prompts, template"] --> F["spreading + policy engine<br/>(deterministic)"]
    F --> M["Underwriter draft<br/>(model call)"]
    M --> K["Risk Reviewer audit<br/>(model call)"]
    K --> D["code-enforced checks<br/>on the draft"]
    D -- REJECTED --> M
    D -- APPROVED --> X["deal_export.py"]
    I -. checkpoint .-> ST[("state.json")]
    M -.-> ST
    K -.-> ST
```

## Where the code ends and the model begins

| Layer | Decides | Examples |
| :--- | :--- | :--- |
| Deterministic code | What can be computed or checked exactly | Subtotals and ratios (`spreading_builder.py`, run through `spreading_check.py`); covenant PASS / FAIL / UNRESOLVABLE, required conditions precedent and subsequent, security gaps (`policy_engine.py`); whether a draft declares every required item and states figures that match the computed ones (`policy_checks.py`); state validation and schema versioning (`state_manager.py`); read-only dated-review movement and opt-in historical restatement comparison (`review_compare.py`); document export |
| Model, constrained by the prompts | Narrative, grounding in sources, judgement about ambiguity | The Underwriter's drafting; the Risk Reviewer's challenge of unsourced claims and weak mitigants |
| Human | Credit judgement and confirmation | Risk inputs (PD, LGD, bureau score); whether a spreading convention or a policy interpretation is confirmed for reuse; the final decision on a CAM |

A code-enforced rejection overrides a Risk Reviewer `APPROVED`. A passing deterministic check shows that the
draft is consistent with the computed facts and complete against the deal's own structure; it does not show that
the narrative is sound.

## Components

- **Agents** (`agents/underwriter_agent.md`, `agents/risk_reviewer_agent.md`): the two role prompts, kept
  independent on purpose (see [Decisions](decisions.md)).
- **The interactive Checker** (`.claude/agents/risk-reviewer.md`): a read-only sub-agent (Read, Glob, Grep) that
  `/review` starts afresh for every review. It applies the `agents/risk_reviewer_agent.md` role to a draft saved on disk
  and the deal's saved evidence, and returns a verdict; `/review` keeps the deterministic checks and writes the record
  ([AI assurance](ai-assurance.md#where-the-enforcement-runs-matters)).
- **Slash commands** (`.claude/commands/`): the interactive implementation of each step, described by
  `config/skills_registry.md`, which is also the inventory of skills (analyst and maintainer aids; six analyst aids are implemented, see [Skills](skills.md),
  and none is ever input to the Maker or the Checker, see [Skill design](skill-design.md)). They run the deterministic scripts through Bash steps and hand the model's
  drafting work back to the session.
- **Scripts** (`scripts/`): the deterministic core and the headless entry points. Most have no `anthropic`
  dependency so they can run from a slash command's Bash step and be tested without a model.
  `wiki_publish.py` generates the wiki pages from `docs/` and, with `--publish`, pushes them one-way to the
  GitHub Wiki (the `wiki-publish` workflow runs it on every push to `main` that changes `docs/`; see
  [Operations](operations.md#publishing-the-wiki)). `docs/` stays the only authored source.
- **Templates** (`templates/`): shipped generic CAM templates, plus git-ignored local overrides derived from your
  own documents.
- **State** (`deals/<Company>/<Proposal>_<Date>/state.json`, git-ignored): the checkpoint of every step, with
  `sources/` beside it for the documents the claims rest on.
- **Tests and CI**: the suite, golden snapshots, coverage floors, a weekly mutation run, and a local-only
  evaluation harness for the model's behaviour.

## Maker-Checker agents

| Agent | File | Role |
| :--- | :--- | :--- |
| Underwriter ("Maker") | [`agents/underwriter_agent.md`](../agents/underwriter_agent.md) | Drafts the CAM: calculates TNW, EBITDA, DSCR, Gross Leverage, Working Capital Days; cites sources for every qualitative claim; never invents figures; discloses when spreading was analyst-supplied rather than independently recomputed; drafts with awareness of a calibrated credit policy and persisted deal learnings. Guideline 13 also requires conclusions to follow decisive evidence, repayment capacity to include the proposed facility, accurate product mechanics and comparable accounting bases, impersonal British-English voice, and a concise statement of missing information. |
| Risk Reviewer ("Checker") | [`agents/risk_reviewer_agent.md`](../agents/risk_reviewer_agent.md) | Audits the draft in a context that holds none of the Maker's reasoning (a separate call headless; in a session a fresh read-only sub-agent started by `/review`, see [AI assurance](ai-assurance.md#where-the-enforcement-runs-matters)): re-verifies ratio calculations, flags ungrounded assertions or missing sources, challenges weak mitigants, and flags any calibrated credit-policy violation (mandatory, once one exists). Audit Checklist items 5–7 check whether conclusions follow the evidence, material internal consistency, and product/accounting mechanics; material findings reject, while cosmetic rounding/formatting differences are minor. The Reviewer returns `APPROVED` or `REJECTED` with revision notes. For `/research` briefs it also checks closing Sources-list completeness, citation-key definitions for short-form references, and that any analyst-override footnote states a reason and date; the override itself is not a rejection reason, but unsupported claims remain findings; a listed source that no claim cites is a minor point, not a reason to reject on its own. |

The two prompts are kept deliberately independent — the Reviewer's value comes from auditing
the Maker's work cold, not from sharing its reasoning. What is shared on purpose is the evidence: both work from the
same computed figures, saved sources and policy, and the Reviewer reads the saved draft and the deal's files itself.

### Grounding rule

Every fact in a generated CAM — company history, market/competitor data, management bios,
financial figures — must trace back to a verified, credible source: audited financial
statements, a company's own filings/website, a recognized credit bureau or rating agency
report, or another primary document you supply. Neither agent should ever estimate or invent a
figure it cannot source. Where no external rating system or in-house scoring tool (e.g. a
Moody's/bureau integration) is wired up, PD/LGD grades are **user-supplied inputs** (via the
`--pd` / `--lgd` flags) and are labelled as such in the output — the framework does not pretend
to have scored the risk itself.

## Slash commands and the agents

`/triage`, `/research`, `/spread`, `/commercial`, `/collateral`, and `/project` each load
[`agents/underwriter_agent.md`](../agents/underwriter_agent.md)'s role; `/review` runs the checks and
hands the audit to a fresh `risk-reviewer` sub-agent, which loads
[`agents/risk_reviewer_agent.md`](../agents/risk_reviewer_agent.md)'s. `/assemble` resolves the
right CAM template (local override, else shipped default), drafts into it, loops `/review` until
`APPROVED`, then calls [`scripts/deal_export.py`](../scripts/deal_export.py) — the folder-creation
and `.docx`/`.xlsx` export logic, factored out of `orchestrator.py` specifically so it has no
`anthropic` dependency and can run from a slash command's Bash step. `/research` calls the
lighter [`scripts/research_export.py`](../scripts/research_export.py) instead, so a research-only
deal never triggers the full-CAM side effects (template auto-save, `.xlsx` export).

## Repository map

The complete annotated tree, as maintained in `CLAUDE.md` before it was relocated here, follows. `CLAUDE.md`
keeps a short file map for orientation.

```
open-cam-framework/
├── .claude/agents/               The interactive Checker sub-agent (risk-reviewer), started by /review in a fresh, read-only context
├── .claude/hooks/                Offline PreToolUse guard (deny_main_commit.py) that denies a commit on main/master in the canonical repository -- see [Security](security.md#repository-shipped-claude-code-commit-guard)
├── .claude/settings.json         Registers the PreToolUse commit guard for Bash and PowerShell (one hook entry, pinned by a test); the file that runs repository-controlled code, so it is part of the security review surface -- see [Security](security.md#repository-shipped-claude-code-commit-guard)
├── .claude/commands/             Slash-command interface (primary) -- see "Slash commands" in `CLAUDE.md`
│   ├── calibrate.md, calibrate-policy.md
│   ├── triage.md, research.md, spread.md, commercial.md, collateral.md, project.md
│   ├── assemble.md
│   ├── review.md
│   ├── start-review.md
│   └── committee-summary.md
├── .claude/skills/               Person-invoked analyst and maintainer aids; see [Skills](skills.md) and the registry
├── agents/                      Maker-Checker agent prompts (see Maker-Checker agents above)
│   ├── underwriter_agent.md
│   └── risk_reviewer_agent.md
├── config/
│   ├── settings.json            maker_model (required) -- also an optional checker_model to
│   │                            configure the Risk Reviewer's model independently of the Underwriter's
│   │                            (it defaults to the Underwriter's model when unset; legacy
│   │                            maker_temperature/checker_temperature are ignored with a warning), and an optional calibrate_model for scripts/calibrate.py (defaults
│   │                            to maker_model when unset -- calibration has no need for a structurally
│   │                            different model the way the Checker does). max_tokens/default_currency/
│   │                            output_directory/template_directory/spreading_template_directory are also
│   │                            accepted in the file but not read by any script -- see "What
│   │                            config/settings.json actually controls" in `CLAUDE.md`
│   ├── skills_registry.md       Command and skill registry: /calibrate /calibrate-policy /triage /research /spread /commercial /collateral /project /assemble /review /start-review /committee-summary
│   ├── style_guide.md           Generated by /calibrate or scripts/calibrate.py (not checked in until first run)
│   ├── credit_policy.md         Generated by /calibrate-policy (not checked in until first run) -- this fork's own calibrated institutional credit policy, referenced during every future CAM draft/audit
│   ├── credit_policy_notes.md   Generated/appended by /review on explicit analyst confirmation (not checked in until first note) -- persisted corrections to how specific credit-policy clauses have been interpreted, referenced alongside credit_policy.md (see "Persisted conventions" in `CLAUDE.md`)
│   ├── spreading_conventions.json  Generated/updated by /spread on explicit analyst confirmation -- enterprise-wide (not per-borrower) spreading convention, see scripts/conventions.py and "Persisted conventions" below
│   └── deal_learnings.md        Generated/appended by /assemble on explicit analyst confirmation (not checked in until first entry) -- enterprise-wide end-of-deal takeaways, see "Persisted conventions" in `CLAUDE.md`
├── scripts/
│   ├── calibrate.py             Headless style + template calibration entry point (needs ANTHROPIC_API_KEY)
│   ├── orchestrator.py          Headless deal pipeline entry point (needs ANTHROPIC_API_KEY)
│   ├── deal_export.py           Folder creation + template auto-save + .docx/.xlsx export (no anthropic dependency; shared by orchestrator.py and /assemble)
│   ├── research_export.py       Standalone research-brief -> .docx export (no anthropic dependency; used by /research only -- kept separate from deal_export.py so a research-only deal never triggers CAM-specific side effects)
│   ├── state_manager.py         Reads/writes deals/<Company>/<Proposal>_<Date>/state.json (no anthropic dependency; see "Context Window & State Management Protocol")
│   ├── review_compare.py        Read-only comparison of two exact dated states; optional historical-period restatement check (no anthropic dependency)
│   ├── export_readback.py       Read-back check of a deal's exported .docx/.xlsx against what its record regenerates -- a change detector for edits after export and for stale exports, not proof the exporter is right; read-only for the deal (no anthropic dependency)
│   ├── committee_summary.py     Deterministic committee-meeting summary (.md and .docx) built only from one deal's recorded state and the policy check: no new calculation, verdict or recommendation (no anthropic dependency)
│   ├── covenant_cushion.py      Read-only covenant cushion report: exact EBITDA break-evens for the DSCR, gross leverage, net debt / EBITDA and EBITDA / interest covenants plus single-shock-at-a-time reverse-stress brackets over the three recorded shocks, printed as JSON from one deal's recorded state; reuses the policy engine's covenant rows verbatim; writes nothing (no anthropic dependency; see docs/financial-model.md and docs/formula-register.md)
│   ├── draft_lint.py            Deterministic lint for internal framework language and unfinished template scaffolding; shared by the CAM compliance checks and research-brief export, and an offline CLI (no anthropic dependency)
│   ├── pr_scope_check.py        Advisory report comparing a pull request's changed paths with its declared file scope; run by scope-check.yml, never a gate (no anthropic dependency)
│   ├── source_manifest.py       Saves fetched/given source material (PDFs, saved pages) into that deal's sources/ subfolder plus a manifest -- see "Source material persistence" in `CLAUDE.md` (no anthropic dependency)
│   ├── conventions.py           Reads/writes persisted analyst-confirmed spreading conventions -- borrower-specific (deals/<Company>/_conventions.json) and enterprise-wide (config/spreading_conventions.json); checked (never silently applied) by /spread before asking which mode applies -- see "Persisted conventions" in `CLAUDE.md` (no anthropic dependency)
│   ├── policy_engine.py         Deterministic covenant/security/CP/CS evaluation -- evaluate_deal_policy() (no anthropic dependency; see "Execution scripts" in `CLAUDE.md`)
│   ├── policy_checks.py         Parses the Underwriter's structured-output JSON and checks a draft's declared figures/CPs/taxonomy against ground truth -- check_draft_compliance() (no anthropic dependency)
│   ├── supplied_forecast.py     /project's analyst-supplied mode: records forward-year figures as given, with the downside the analyst's own, framework-derived where valid, or unavailable (no anthropic dependency; see docs/financial-model.md)
│   ├── transcription_check.py   Read-back, cross-foot and digest-gated commit for /spread figures transcribed from an image (no anthropic dependency; see docs/financial-model.md)
│   ├── policy_check.py          Standalone CLI wrapper around policy_engine.py/policy_checks.py -- compute(); callable from /assemble's and /review's own Bash steps so the slash-command interface gets the same code-enforced governance orchestrator.py's headless pipeline does (no anthropic dependency)
│   ├── pii_scan.py              Heuristic (UK-shaped, with a few international patterns) scan for likely-real PII left over in a calibrated template before promoting it upstream -- see the "Promoting a local override upstream" rule in `CLAUDE.md`'s confidentiality section (no anthropic dependency)
│   ├── textio.py                The repo's one text-reading policy: strict UTF-8 for shipped files, UTF-8-then-cp1252-with-warning for user-owned legacy files, never character substitution -- see issue #137 (no anthropic dependency; see "Execution scripts" in `CLAUDE.md`)
│   ├── run_evals.py             CLI for the local live-model evaluation harness (explicit invocation only: --validate/--list/--dry-run make zero model calls; --live spends real API calls under a hard cap; --export-baseline/--compare) -- with eval_cases.py (dataset schema/validation), eval_oracles.py (deterministic oracles), eval_report.py (results + review pack), eval_budget.py (hard call cap), eval_runner.py (the isolated live runner), eval_baseline.py (baseline export/compare); see "Execution scripts" in `CLAUDE.md` and issue #151
│   ├── check_test_count.py      Parses pytest's own "N passed" summary line and compares it against badges/test-count.json (`--write` updates the file by hand) -- CI's code-enforced guard against that count going stale (no anthropic dependency; see "Execution scripts" in `CLAUDE.md`)
│   ├── check_coverage.py        Checks coverage.py's JSON report against the floors in pyproject.toml -- overall plus a higher per-module floor for the eleven governance modules; CI's code-enforced coverage gate (no anthropic dependency; see "Testing and static analysis" in `CLAUDE.md`)
│   ├── mutation_report.py       Per-module mutation score + completeness report from `mutmut results --all true`, run by the weekly mutation workflow -- diagnostic only, never fails over a score (no anthropic dependency; see "Testing and static analysis" in `CLAUDE.md`)
│   ├── check_model_freshness.py  Reports newer/deprecated/unknown configured Claude model IDs from installed SDK metadata; weekly/manual diagnostic, never auto-upgrades (no API call)
│   ├── docx_builder.py          Markdown -> .docx export helper
│   ├── spreading_builder.py     Financial spreading -> .xlsx export helper
│   ├── spreading_check.py       Standalone CLI wrapper around spreading_builder.py's formula evaluation -- compute(); callable from /spread's and /project's own Bash steps so the slash-command interface gets the same code-enforced subtotal/ratio computation orchestrator.py's headless pipeline does (no anthropic dependency; see "Execution scripts" in `CLAUDE.md`)
│   └── template_resolver.py     Resolves default vs. calibrated-override CAM template paths
├── templates/                   Reference templates -- see templates/README.md
│   ├── cam/                     Shipped default Markdown CAM templates (corporate_credit_cam.md, asset_finance_cam.md)
│   ├── spreading/               default_spreading_template.xlsx -- reference copy of the spreading workbook layout
│   └── local/cam/               Calibrated overrides / auto-saved new-type templates (gitignored, see security.md)
├── evals/                       Local live-model evaluation harness data -- see evals/README.md and issue #151: dataset/<version>/ (synthetic cases, tracked), results/ (per-run output, gitignored), baselines/ (committed only by a deliberate manual step)
├── tests/                       Pytest suite (spreading_builder formulas, docx table rendering, template resolution, state persistence, property-based tests, golden .docx/.xlsx snapshots in snapshots/, synthetic state fixtures in fixtures/) -- see "Testing and static analysis" in `CLAUDE.md`
├── badges/
│   └── test-count.json          Checked-in `{"passed": <int>}` record of the currently-passing test count -- deliberately public/git-tracked (a project stat, not derived borrower/institutional data), kept honest by CI's "Verify checked-in test count" step (scripts/check_test_count.py) rather than hand-maintained -- see "Execution scripts" in `CLAUDE.md`
├── deals/                       Generated output, one subfolder per `[Company]/[Proposal]_[Date]`; a state-bearing deal folder holds `state.json` and `sources/` (see "Source material persistence" in `CLAUDE.md`), while an explicit `deal_export --date-str` may create an output-only folder ignored by state discovery. A company may also hold `_conventions.json` and `_learnings.md` one level up from its dated proposal folders (see "Persisted conventions" in `CLAUDE.md`)
├── inputs/calibration_samples/  Historical CAM PDFs used as calibration input (gitignored/local)
├── requirements.txt
├── diff_cover_critical.toml     The stricter changed-lines coverage tier (98%) for the eleven governance modules; the 95% tier is `[tool.diff_cover]` in `pyproject.toml`
├── pyproject.toml               Central tool configuration (no project metadata): ruff rules, pytest options, coverage, the mypy type-check configuration -- see "Testing and static analysis" in `CLAUDE.md`
├── requirements-dev.txt         requirements.txt + pytest, pytest-cov/-timeout/-socket, hypothesis, diff-cover, ruff, bandit, mypy (development only; type errors fail the typecheck job)
├── requirements-security.txt    pip-audit + zizmor, installed only by CI's `security` job
├── requirements-mutation.txt    mutmut, installed only by the weekly mutation workflow
├── .github/CODEOWNERS           Names the maintainer as owner of `.claude/settings.json` and `.claude/hooks/`; it only protects them if the repository's external ruleset requires code-owner review -- see [Security](security.md#repository-shipped-claude-code-commit-guard)
├── .github/workflows/typecheck.yml  mypy type check for the eleven governance modules (error count per module in the job summary; type errors fail the job; not a required check)
├── .github/workflows/scope-check.yml  Advisory PR scope check: changed paths against the pull request's declared file scope; reports only, never a required check or a gate
├── .github/workflows/mutation.yml  Weekly + manual mutation testing of the eleven governance modules (diagnostic report, never a merge gate)
├── .github/workflows/model-freshness.yml  Weekly + manual diagnostic of SDK model/deprecation metadata; updates one tech-debt issue, never a required check
├── .github/workflows/ci.yml     CI: `test` (Ubuntu, coverage + floors), `test-windows`, `runtime-smoke` (requirements.txt only), `security` (pip-audit, zizmor) -- see "Testing and static analysis" in `CLAUDE.md`
├── .github/workflows/ci-parallelism-benchmark.yml  Manual-only timing of the suite with 1, 2 and 4 workers, for re-choosing CI's worker counts (never a gate; see the operations runbook)
└── README.md
```

Not shown in the tree: `docs/` (this documentation corpus, indexed by [README.md](README.md), with the synthetic worked deal in `docs/examples/synthetic_co/`), `CLAUDE.md`, `LICENSE`, `.gitignore` and `.github/dependabot.yml`.
