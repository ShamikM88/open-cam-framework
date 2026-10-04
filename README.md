# OpenCAM Framework

An open-source, agentic credit underwriting framework. It turns a folder of borrower
information into a drafted Credit Assessment Memorandum (CAM) — using a **Maker-Checker**
pair of LLM agents (an Underwriter that drafts, and a Risk Reviewer that audits) — and exports
the result as an editable `.docx` report plus an `.xlsx` financial spreading workbook.

It is designed to be forked: point it at your own historical CAMs and it calibrates itself to
your house writing style and your own CAM layouts, rather than assuming any one bank's format.

**Two ways to run it**, covered in full under [Getting started](#getting-started):

- **Claude Code slash commands** (primary, recommended) — `/calibrate`, `/calibrate-policy`,
  `/triage`, `/research`, `/spread`, `/commercial`, `/collateral`, `/project`, `/assemble`,
  `/review`, run interactively inside a Claude Code session pointed at this repo. Uses whatever
  Claude Code session/subscription you're already running in — **no separate
  `ANTHROPIC_API_KEY` required.**
- **Headless Python scripts** (`scripts/calibrate.py`, `scripts/orchestrator.py`) — for
  automation, CI, or batch runs outside an interactive session. These call the Anthropic API
  directly, so they need their own `ANTHROPIC_API_KEY` (a separate cost from a Claude Code
  subscription).

Both paths share the same core, templates, style guide, and confidentiality rules, and the full CAM
workflow (`/triage` through `/assemble`, or `orchestrator.py`) produces the same `.docx` and `.xlsx`
either way — pick whichever fits how you work. Two Claude Code commands have no headless
equivalent: `/research` (a standalone research brief, not a CAM or a workbook) and
`/calibrate-policy`.

## Authentication, cost and credentials

| | Claude Code slash commands | Headless Python scripts |
| :--- | :--- | :--- |
| Authentication | Your Claude Code login; no `ANTHROPIC_API_KEY` needed or read | `ANTHROPIC_API_KEY` in the environment of the process |
| Billing | What your Claude Code plan covers | Per-token Anthropic **API** billing, separate from any subscription |
| Without a key | n/a | `calibrate.py` runs in `--mock` mode (placeholder output, no API call); `orchestrator.py` fails at its first model call, sending nothing |
| What stays local | Your files, `state.json`, the generated documents and the deterministic scripts the commands run | The same |
| What goes to an external service | What the session sends to Claude Code's model while it works; the public pages and filings a research step fetches | The prompts and documents each script sends to the Anthropic API |

**Where credentials must not go.** The only credential the framework uses is the `ANTHROPIC_API_KEY` environment
variable, and only the headless scripts read it. Set it in your own shell. Never write it into a file in this
repository (`config/settings.json` is tracked and is not read for keys), a deal folder, a prompt or a command, and
never paste a live key into an AI assistant's chat. The test suite makes no model calls and needs no key; the
local evaluation harness calls the model only when you run it yourself with `--live`, under a hard call cap, on
synthetic data. More: [architecture](docs/architecture.md#two-ways-to-run-it) and
[configuration](docs/configuration.md#credentials).

## How it works

1. **Setup (once per organization/desk):** calibrate against a handful of your own past CAMs in
   `inputs/calibration_samples/` — via `/calibrate --type <deal_type>` (Claude Code) or
   `scripts/calibrate.py --type <deal_type>` (headless). Either extracts your writing tone,
   structure, and standard risk phrasing into `config/style_guide.md` (used for every subsequent
   draft), and derives a CAM template from those samples' structure into
   `templates/local/cam/<deal_type>_cam.md`, which **overrides the shipped default** for that
   deal type. `<deal_type>` should match whatever you'll use for `--type` on later runs; it
   defaults to `corporate_credit`. Optionally, also run `/calibrate-policy` (Claude Code only, no
   headless equivalent yet) to calibrate your institution's own credit policy document(s) from
   `inputs/credit_policy/` into `config/credit_policy.md` — org-wide, one-time, not per-`--type`.
   Once present, every future draft/audit automatically references it.
2. **Per deal:** work through `/triage` → `/spread` → `/commercial` → `/collateral` → `/project`
   (optional -- forward financials, stress testing, covenants, guarantees) → `/assemble` (Claude
   Code), or run `scripts/orchestrator.py` (headless) with the
   borrower/company details. Either way, the **Underwriter Agent** drafts the CAM, grounded only
   in supplied source documents and user-provided risk inputs (never fabricated figures), and
   the **Risk Reviewer Agent** independently audits that draft — re-checking the ratio math,
   flagging unsourced claims, challenging weak risk mitigants, and (when a credit policy has been
   calibrated) flagging any violation of house lending criteria — before it's exported. Along the
   way, an analyst-confirmed spreading convention or credit-policy interpretation can be persisted
   for reuse on future deals from the same borrower or institution (see `CLAUDE.md`'s "Persisted
   conventions" section) — always confirmed explicitly, never silently assumed.

   Don't need a full CAM yet? Run `/research` instead — it covers `/triage`'s Go/No-Go screen
   and `/commercial`'s company/sector research in one step, with no financials required, gets its
   own scoped-down Risk Reviewer audit before exporting, and exports a standalone brief in a few
   minutes. Nothing it captures is wasted if the deal later needs a full CAM: it writes the same
   state.json `/triage` and `/commercial` would, so you can just continue on to `/spread` →
   `/collateral` → `/project` → `/assemble` from there.
3. **Output:** a `.docx` CAM (so you can edit it like any Word document — much easier than
   editing a PDF) and an `.xlsx` financial spreading workbook (so the numbers are auditable, not
   just narrative), written to a per-deal folder:
   ```
   deals/
     [Company Name]/
       [Proposal Name]_[Date]/
         [Company]_[Proposal]_CAM.docx
         [Company]_[Proposal]_Spreading.xlsx
   ```

If a deal's `--type` doesn't match any template — neither a calibrated override under
`templates/local/cam/` nor a shipped default under `templates/cam/` — it's treated as a
genuinely new CAM type and the drafted structure is saved as a starting template under
`templates/local/cam/` (never into the shared, git-tracked `templates/cam/`, since that first
draft carries this deal's real company name and figures) — so your template library grows to
match the kinds of deals you actually do, instead of forcing every deal through one fixed
layout.

## Maker-Checker agents

An **Underwriter** agent drafts the CAM, grounded only in supplied source documents and user-provided risk inputs
(never fabricated figures); an independent **Risk Reviewer** agent audits the draft and returns `APPROVED` or
`REJECTED` with revision notes. The two prompts, [`agents/underwriter_agent.md`](agents/underwriter_agent.md) and
[`agents/risk_reviewer_agent.md`](agents/risk_reviewer_agent.md), are kept deliberately independent, and everything
that can be computed (ratios, covenant results, required conditions) is computed and enforced by code rather than
left to either model. Details, including the grounding rule: [architecture](docs/architecture.md).

## Skills / slash commands

[`config/skills_registry.md`](config/skills_registry.md) defines the step-by-step workflow;
[`.claude/commands/`](.claude/commands/) is the runnable implementation of it, as native Claude
Code slash commands (no `ANTHROPIC_API_KEY` needed — see [Getting started](#getting-started)):

| Command | Inputs | Produces |
| :--- | :--- | :--- |
| `/calibrate` | Sample CAM PDFs in `inputs/calibration_samples/` | `config/style_guide.md` + a derived template override |
| `/calibrate-policy` (org-wide, one-time) | Your institution's own credit policy document(s) in `inputs/credit_policy/` | `config/credit_policy.md` -- referenced automatically by every future `/assemble`/`/review` run |
| `/triage` | Registration number, credit bureau summary, charges register | Legal identity / UBO check, Go/No-Go screen |
| `/research` (standalone alternative) | Everything `/triage` + `/commercial` each ask for | `/triage`'s Go/No-Go screen and `/commercial`'s company/sector research, combined into one exported research brief (with its own scoped Risk Reviewer audit before export) -- no financials needed |
| `/spread` | 3–5 years of P&L and Balance Sheet | TNW, EBITDA, DSCR, EBIT/Interest, Gross Leverage, Net Debt / EBITDA, Gearing %, Current Ratio, FCF Conversion %, Working Capital Days |
| `/commercial` | Sector, management bios, customer/supplier notes | Company History, Management, Sector Dynamics, Concentration, Competitive Landscape |
| `/collateral` | Asset description, valuation, LGD/RV/PD grades | Gross/Net Exposure, RV Exposure, Collateral Coverage %, Net Uncovered Risk |
| `/project` (optional) | Forward-year P&L/Balance Sheet forecasts, stress-test assumptions, covenants, guarantees | Forward-year ratios, a deterministic downside (stressed) case, and covenant/guarantee records for `/assemble`'s code-enforced policy checks |
| `/assemble` | Outputs of the steps above | The final CAM, audited via `/review`, exported to `.docx`/`.xlsx` |
| `/review` | A drafted CAM (usually called automatically by `/assemble`), or a `/research` brief with `--research-brief` | `APPROVED`/`REJECTED` verdict + revision notes — the Risk Reviewer agent |

## Templates

Shipped CAM templates (`templates/cam/corporate_credit_cam.md`, `templates/cam/asset_finance_cam.md`) are plain
Markdown with bracketed `[placeholder]` fields. A template derived from your own documents lives under the
git-ignored `templates/local/cam/` and takes precedence. The `.xlsx` workbook is built with Excel formulas, not
pre-baked numbers. Details: [outputs and templates](docs/outputs.md).

## Getting started

### Option A: Claude Code slash commands (recommended, no separate API key)

Open this repo in Claude Code (or the desktop app's Code tab) — the commands under
[`.claude/commands/`](.claude/commands/) are picked up automatically.

1. **(Recommended) Calibrate to your own style and templates:** put a few of your own historical
   CAMs (PDF) into `inputs/calibration_samples/`, then run:
   ```
   /calibrate --type asset_finance
   ```
   Claude reads the PDFs directly (native PDF support), writes `config/style_guide.md`, and
   derives a template at `templates/local/cam/asset_finance_cam.md` that overrides the shipped
   default. Skip this step to use the neutral default tone and templates as-is.
2. **(Optional, org-wide, one-time) Calibrate your institution's own credit policy:** put your
   policy document(s) into `inputs/credit_policy/`, then run:
   ```
   /calibrate-policy
   ```
   Claude reads them directly and writes `config/credit_policy.md` -- referenced automatically by
   every future draft/audit, not just those of one `--type`.
3. **Run a deal**, working through each step in the same conversation so later steps can see
   earlier ones' output:
   ```
   /triage <registration number, credit bureau summary, charges register>
   /spread <P&L and Balance Sheet figures>
   /commercial <sector, management bios, customer/supplier notes>
   /collateral <asset description, valuation, LGD/RV/PD grades>
   /project <forward-year forecasts, stress assumptions, covenants, guarantees>   # optional
   /assemble --company "Acme Corp" --proposal "Fleet Loan" --type asset_finance --pd "0.20%" --lgd "LGD 3 (15%)"
   ```
   `/assemble` drafts the CAM into the resolved template, runs `/review` (the Risk Reviewer
   agent) until it's `APPROVED`, then exports it. Output lands in
   `deals/Acme Corp/Fleet Loan_<date>/`.

   Just need research, not a full CAM yet? Skip straight to:
   ```
   /research --company "Acme Corp" --proposal "Fleet Loan" <registration number, credit bureau summary, charges register, sector, management bios, customer/supplier notes>
   ```
   This exports a standalone research brief in the same folder and writes the same state.json
   `/triage` + `/commercial` would, so you can pick up with `/spread` onward later if needed.

### Option B: Headless Python scripts (scriptable, needs `ANTHROPIC_API_KEY`)

For automation, CI, or running outside an interactive Claude Code session.

1. **Install dependencies** (Python 3.10+ recommended):
   ```bash
   python -m venv venv
   venv\Scripts\activate   # on Windows; use `source venv/bin/activate` on macOS/Linux
   pip install -r requirements.txt
   ```
2. **Set your API key:**
   ```bash
   set ANTHROPIC_API_KEY=sk-ant-...   # on Windows (PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-...")
   ```
   Set this yourself in your own shell — never paste a live key into an AI assistant's chat (it
   ends up in transcripts/logs). No key set? `calibrate.py` automatically falls back to `--mock`
   mode instead of failing outright.
3. **(Recommended) Calibrate to your own style and templates:**
   ```bash
   python scripts/calibrate.py --type asset_finance
   ```
   Same output as `/calibrate` above. Add `--mock` (or just omit the API key) to smoke-test this
   without calling the API — it writes clearly-labeled placeholder output instead, to verify the
   PDF-reading/file-writing pipeline works before spending real API credits.

   Each API call takes at most 12,000 characters of sample text. If your samples are longer,
   the script stops before calling anything and asks whether to ignore the overflow or to split
   it into parts and merge the results (nothing is dropped silently). `--on-overflow
   {ask,split,ignore}` answers in advance; with no terminal (CI), the default is `split`. Your
   sample PDFs are never modified.
4. **Run a deal:**
   ```bash
   python scripts/orchestrator.py --company "Acme Corp" --proposal "Fleet Loan" --type "asset_finance" --pd "0.20%" --lgd "LGD 3 (15%)"
   ```
   Output lands in `deals/Acme Corp/Fleet Loan_<date>/`. Internally this calls the same
   `scripts/deal_export.py` that the slash-command path's `/assemble` uses.

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite needs no `ANTHROPIC_API_KEY` and no network. CI also runs `ruff check .`, `bandit -r scripts/ -ll`,
coverage floors, a Windows job and a security job. Details, including how to keep `badges/test-count.json`
correct: [testing and CI](docs/testing.md).

## Documentation

| Read | For |
| :--- | :--- |
| [docs/README.md](docs/README.md) | The documentation index and which source is authoritative for what |
| [docs/architecture.md](docs/architecture.md) | How the pieces fit; the two execution modes; the pipeline |
| [docs/workflows.md](docs/workflows.md) | Worked end-to-end examples: research, spreading, policy checking, assembly, review, calibration, resuming |
| [docs/commands.md](docs/commands.md) | Every slash command: inputs, outputs, state written, what can go wrong |
| [docs/decisions.md](docs/decisions.md) | Why it is built this way |
| [docs/configuration.md](docs/configuration.md) | `config/settings.json`, credentials |
| [docs/cli-reference.md](docs/cli-reference.md) | The headless scripts |
| [docs/data-model.md](docs/data-model.md) | `state.json`, source manifests, persisted conventions |
| [docs/financial-model.md](docs/financial-model.md) | Ratios, covenants, the policy engine |
| [docs/outputs.md](docs/outputs.md) | Templates and the exported `.docx` / `.xlsx` |
| [docs/testing.md](docs/testing.md) | Tests, CI, coverage, mutation testing |
| [docs/security.md](docs/security.md) | What must never be committed |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Symptoms, meanings and fixes |
| [docs/evaluation.md](docs/evaluation.md) | The local live-model evaluation harness |
| [`CLAUDE.md`](CLAUDE.md) | The rules Claude Code follows when it changes this repository |

## Confidentiality — what never belongs in this repo

This is a **public, forkable framework repo**, not a place to store real deal data. Everything derived from real
material is git-ignored and must stay that way: `inputs/` (calibration PDFs and policy documents),
`config/style_guide.md`, `config/credit_policy.md`, `config/credit_policy_notes.md`,
`config/spreading_conventions.json`, `config/deal_learnings.md`, `templates/local/`, `deals/` (including each
company's `_conventions.json` and `_learnings.md`) and `evals/results/`. `tests/test_confidential_paths.py` fails
CI if one is tracked or loses its `.gitignore` rule. Only the framework itself (agent prompts, scripts, blank
templates, config, docs) is ever committed, and every template field is a generic `[bracketed placeholder]`. The
annotated list and the rules for adding a new location: [security and confidentiality](docs/security.md).

## Roadmap

- [x] Derive a base CAM template shape from calibration samples, not just tone/style — done via
      `templates/local/cam/` overrides (see [outputs and templates](docs/outputs.md)). Still open: a first-run *guided* setup
      (interactive prompt to add samples) rather than a manual file drop into
      `inputs/calibration_samples/`.
- [ ] Support ingesting a user-supplied Excel spreading template, so subsequent spreads follow
      that exact format instead of the framework's default layout.
- [ ] Similarity-based (not just type-string-based) detection of when a new deal "sufficiently
      differs" from an existing template and should be saved as a new one.

> **Note on calibrated templates:** `calibrate.py` instructs the model to strip all real data
> out of the derived template, but that's a prompted behavior, not a code-enforced guarantee —
> review a newly derived `templates/local/cam/*.md` file before treating it as safely
> shareable (e.g. before ever promoting it into the git-tracked `templates/cam/` defaults).

## License

[MIT](LICENSE)
