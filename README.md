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

Both paths produce the same outputs and share the same templates, style guide, and
confidentiality rules — pick whichever fits how you work.

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

| Agent | File | Role |
| :--- | :--- | :--- |
| Underwriter ("Maker") | [`agents/underwriter_agent.md`](agents/underwriter_agent.md) | Drafts the CAM: calculates TNW, EBITDA, DSCR, Gross Leverage, Working Capital Days; cites sources for every qualitative claim; never invents figures; discloses when spreading was analyst-supplied rather than independently recomputed; drafts with awareness of a calibrated credit policy and any persisted deal learnings once they exist. |
| Risk Reviewer ("Checker") | [`agents/risk_reviewer_agent.md`](agents/risk_reviewer_agent.md) | Independently audits the draft: re-verifies ratio calculations, flags ungrounded assertions or missing sources, challenges weak mitigants, flags any violation of a calibrated credit policy (mandatory, once one exists), and returns `APPROVED` or `REJECTED` with revision notes. |

The two prompts are kept deliberately independent — the Reviewer's value comes from auditing
the Maker's work cold, not from sharing its reasoning.

### Grounding rule

Every fact in a generated CAM — company history, market/competitor data, management bios,
financial figures — must trace back to a verified, credible source: audited financial
statements, a company's own filings/website, a recognized credit bureau or rating agency
report, or another primary document you supply. Neither agent should ever estimate or invent a
figure it cannot source. Where no external rating system or in-house scoring tool (e.g. a
Moody's/bureau integration) is wired up, PD/LGD grades are **user-supplied inputs** (via the
`--pd` / `--lgd` flags) and are labelled as such in the output — the framework does not pretend
to have scored the risk itself.

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

`/triage`, `/research`, `/spread`, `/commercial`, `/collateral`, and `/project` each load
[`agents/underwriter_agent.md`](agents/underwriter_agent.md)'s role; `/review` loads
[`agents/risk_reviewer_agent.md`](agents/risk_reviewer_agent.md)'s. `/assemble` resolves the
right CAM template (local override, else shipped default), drafts into it, loops `/review` until
`APPROVED`, then calls [`scripts/deal_export.py`](scripts/deal_export.py) — the folder-creation
and `.docx`/`.xlsx` export logic, factored out of `orchestrator.py` specifically so it has no
`anthropic` dependency and can run from a slash command's Bash step. `/research` calls the
lighter [`scripts/research_export.py`](scripts/research_export.py) instead, so a research-only
deal never triggers the full-CAM side effects (template auto-save, `.xlsx` export).

## Templates

All reference templates live under [`templates/`](templates/) — see
[`templates/README.md`](templates/README.md) for the full breakdown. In short:

- [`templates/cam/corporate_credit_cam.md`](templates/cam/corporate_credit_cam.md) — general
  corporate lending (RCF/term loan/overdraft): facility terms, covenants, security package, full
  financial spreading, industry/competitive analysis, risks & mitigants.
- [`templates/cam/asset_finance_cam.md`](templates/cam/asset_finance_cam.md) — asset-backed /
  equipment or fleet finance: adds per-asset LGD/RV/collateral-cover tables and asset-specific
  facility conditions (max asset age, sublet terms, residual value treatment) on top of the same
  financial-analysis and risk sections.
- [`templates/spreading/default_spreading_template.xlsx`](templates/spreading/default_spreading_template.xlsx) —
  reference copy of the financial spreading workbook layout (see below), blank/formula-only.

The CAM templates are plain Markdown with bracketed `[placeholder]` fields — safe to fork and
edit directly, and readable as a diff in git. New deal types are added the same way (manually,
under `templates/cam/`, or auto-generated by the orchestrator the first time that `--type` is
used).

### Calibrated overrides (`templates/local/`)

`templates/local/cam/<deal_type>_cam.md` — written by `scripts/calibrate.py` (from your own
samples) or auto-saved by `orchestrator.py` (from a genuinely new deal type's first draft) —
always takes precedence over the matching file in `templates/cam/`. It's git-ignored: unlike
the shipped defaults, anything under `templates/local/` may contain structure derived from your
own real, confidential deal history, so it stays local to your fork rather than getting
committed. Delete a file there to fall back to the shipped default for that deal type.

### Financial spreading workbook

[`scripts/spreading_builder.py`](scripts/spreading_builder.py) builds the `.xlsx` output as a
full P&L → Balance Sheet → key-ratio spread (Revenue down to Working Capital Cycle), with the
subtotals and ratios (Gross Profit, EBITDA, TNW, Gearing, Current Ratio, etc.) written as **Excel
formulas** referencing the raw input rows — not pre-baked numbers — so anyone reviewing the
workbook can see exactly how each figure was derived and re-check it. A second sheet,
`Collateral & Exposure`, gives the same treatment to asset-backed exposure/coverage figures.
`templates/spreading/default_spreading_template.xlsx` is a checked-in, blank reference copy of
that exact layout, so you can inspect the format without running any code.

> **Custom spreading templates:** if your organization already has a standard spreading Excel
> template, that's on the roadmap to support directly (see below) — it would live alongside the
> default under `templates/spreading/`. For now, `spreading_builder.py`'s layout is the only
> format produced.

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
4. **Run a deal:**
   ```bash
   python scripts/orchestrator.py --company "Acme Corp" --proposal "Fleet Loan" --type "asset_finance" --pd "0.20%" --lgd "LGD 3 (15%)"
   ```
   Output lands in `deals/Acme Corp/Fleet Loan_<date>/`. Internally this calls the same
   `scripts/deal_export.py` that the slash-command path's `/assemble` uses.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

The full suite needs no `ANTHROPIC_API_KEY`/network access to run -- every module that touches
`anthropic` (`calibrate.py`, `orchestrator.py`) is tested via mocking, and CI itself runs `pytest
tests/` with no key set at all. Fifteen test files cover the dependency-free modules directly
(`spreading_builder.py`, `docx_builder.py`, `template_resolver.py`, `deal_export.py`,
`state_manager.py`, `source_manifest.py`, `conventions.py`, `pii_scan.py`,
`policy_engine.py`/`policy_checks.py`/`policy_check.py`) plus a prompt-consistency suite
(`test_prompt_consistency.py`) that cross-checks the two agent prompts against the code they're
meant to stay in sync with.

CI also runs `ruff check . --select=E9,F63,F7,F82` and `bandit -r scripts/ -lll` before `pytest` --
run those locally too if you want to catch what CI will catch before pushing:
```bash
ruff check . --select=E9,F63,F7,F82
bandit -r scripts/ -lll
```

### Configuration

[`config/settings.json`](config/settings.json) sets `maker_model` (required), plus optional
`checker_model`/`maker_temperature`/`checker_temperature` to independently configure the Risk
Reviewer vs. the Underwriter. It also accepts `max_tokens`/`default_currency`/`output_directory`/
`template_directory`/`spreading_template_directory`, though those aren't read by any script yet
(tracked in issue #108). [`config/system_instructions.md`](config/system_instructions.md)
is the shared top-level system prompt both agents inherit (objectivity, metric standardization,
structured Markdown output).

## Confidentiality — what never belongs in this repo

This is a **public, forkable framework repo**, not a place to store real deal data. Everything
under the following paths is git-ignored and must stay that way:

- `inputs/` — your calibration sample PDFs and credit policy documents (real historical CAMs and
  house policy — confidential by nature)
- `config/style_guide.md` — derived from those samples, so treat it the same way
- `config/credit_policy.md` — your calibrated institutional credit policy (`/calibrate-policy`)
- `config/credit_policy_notes.md` — analyst-confirmed corrections to how specific policy clauses
  have been interpreted
- `config/spreading_conventions.json` — analyst-confirmed enterprise-wide spreading conventions
- `config/deal_learnings.md` — analyst-confirmed enterprise-wide end-of-deal takeaways
- `templates/local/` — calibration-derived or auto-saved template overrides; may reflect real
  deal structure even though the shipped defaults in `templates/cam/` never do
- `deals/` — generated output for real borrowers (names, financials, PII) — also holds, one level
  above each dated deal folder, any borrower-specific persisted spreading convention
  (`_conventions.json`) or deal learnings (`_learnings.md`) for that company

Only the framework itself (agent prompts, scripts, blank templates, config, docs) should ever
be committed. If you're contributing a template change, make sure every field is a generic
`[bracketed placeholder]` — never a real company name, person's name, or figure.

**Adding a new feature that touches real reference material?** Put its storage location in
`.gitignore` *before* writing anything there — never under a git-tracked path like
`templates/cam/` or `templates/spreading/`. The shipped defaults must stay generic and safe to
share across every fork; anything derived from one user's real documents belongs only in that
fork — *unless* it's a genuinely useful, generalized structure (not just one deal's content) that
other forks would benefit from. Once it's fully scrubbed of real data, that's worth contributing
back: open a PR to add it under `templates/cam/` as a new shared default.

## Roadmap

- [x] Derive a base CAM template shape from calibration samples, not just tone/style — done via
      `templates/local/cam/` overrides (see above). Still open: a first-run *guided* setup
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
