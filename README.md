# OpenCAM Framework

An open-source, agentic credit underwriting framework. It turns a folder of borrower
information into a drafted Credit Assessment Memorandum (CAM) — using a **Maker-Checker**
pair of LLM agents (an Underwriter that drafts, and a Risk Reviewer that audits) — and exports
the result as an editable `.docx` report plus an `.xlsx` financial spreading workbook.

It is designed to be forked: point it at your own historical CAMs and it calibrates itself to
your house writing style and your own CAM layouts, rather than assuming any one bank's format.

## How it works

1. **Setup (once per organization/desk):** drop a handful of your own past CAMs into
   `inputs/calibration_samples/` and run `scripts/calibrate.py --type <deal_type>`. This
   extracts your writing tone, structure, and standard risk phrasing into
   `config/style_guide.md` (used for every subsequent draft), and also derives a CAM template
   from those samples' structure — written to `templates/local/cam/<deal_type>_cam.md` — which
   **overrides the shipped default** for that deal type. `<deal_type>` should match whatever
   you'll later pass to `orchestrator.py --type`; it defaults to `corporate_credit`.
2. **Per deal:** run `scripts/orchestrator.py` with the borrower/company details. The
   **Underwriter Agent** drafts the CAM, grounded only in supplied source documents and
   user-provided risk inputs (never fabricated figures). The **Risk Reviewer Agent** then
   independently audits that draft — re-checking the ratio math, flagging unsourced claims, and
   challenging weak risk mitigants — before the deal is exported.
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
`templates/local/cam/` nor a shipped default under `templates/cam/` — the orchestrator treats it
as a genuinely new CAM type and saves the drafted structure as a starting template under
`templates/local/cam/` (never into the shared, git-tracked `templates/cam/`, since that first
draft carries this deal's real company name and figures) — so your template library grows to
match the kinds of deals you actually do, instead of forcing every deal through one fixed
layout.

## Maker-Checker agents

| Agent | File | Role |
| :--- | :--- | :--- |
| Underwriter ("Maker") | [`agents/underwriter_agent.md`](agents/underwriter_agent.md) | Drafts the CAM: calculates TNW, EBITDA, DSCR, Gross Leverage, Working Capital Days; cites sources for every qualitative claim; never invents figures. |
| Risk Reviewer ("Checker") | [`agents/risk_reviewer_agent.md`](agents/risk_reviewer_agent.md) | Independently audits the draft: re-verifies ratio calculations, flags ungrounded assertions or missing sources, challenges weak mitigants, and returns `APPROVED` or `REJECTED` with revision notes. |

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

[`config/skills_registry.md`](config/skills_registry.md) defines the step-by-step workflow an
agent (or a human analyst) works through to assemble a CAM:

| Command | Inputs | Produces |
| :--- | :--- | :--- |
| `/triage` | Registration number, credit bureau summary, charges register | Legal identity / UBO check, Go/No-Go screen |
| `/spread` | 3–5 years of P&L and Balance Sheet | TNW, EBITDA, DSCR, EBIT/Interest, Gross Leverage, Gearing %, Current Ratio, Working Capital Days |
| `/commercial` | Sector, management bios, customer/supplier notes | Company History, Management, Sector Dynamics, Concentration, Competitive Landscape |
| `/collateral` | Asset description, valuation, LGD/RV/PD grades | Gross/Net Exposure, RV Exposure, Collateral Coverage %, Net Uncovered Risk |
| `/assemble` | Outputs of the steps above | The final Markdown CAM, ready for `.docx`/`.xlsx` export |

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
3. **(Recommended) Calibrate to your own style and templates:** put a few of your own historical
   CAMs (PDF) into `inputs/calibration_samples/`, then run:
   ```bash
   python scripts/calibrate.py --type asset_finance
   ```
   This writes `config/style_guide.md` and a derived template at
   `templates/local/cam/asset_finance_cam.md` that overrides the shipped default. Skip this step
   to use the neutral default tone and templates as-is.
4. **Run a deal:**
   ```bash
   python scripts/orchestrator.py --company "Acme Corp" --proposal "Fleet Loan" --type "asset_finance" --pd "0.20%" --lgd "LGD 3 (15%)"
   ```
   Output lands in `deals/Acme Corp/Fleet Loan_<date>/`.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

The current suite ([`tests/test_spreading_builder.py`](tests/test_spreading_builder.py)) checks
the spreading workbook's structure (sheet names, headers, row order) and, since Excel formulas
aren't evaluated by the library that writes them, re-evaluates every formula against hand-picked
inputs to confirm each one still points at the row it's supposed to.

### Configuration

[`config/settings.json`](config/settings.json) sets the model, max token budget, default
currency, and the output/template directory names. [`config/system_instructions.md`](config/system_instructions.md)
is the shared top-level system prompt both agents inherit (objectivity, metric standardization,
structured Markdown output).

## Confidentiality — what never belongs in this repo

This is a **public, forkable framework repo**, not a place to store real deal data. Everything
under the following paths is git-ignored and must stay that way:

- `inputs/` — your calibration sample PDFs (real historical CAMs — confidential by nature)
- `config/style_guide.md` — derived from those samples, so treat it the same way
- `templates/local/` — calibration-derived or auto-saved template overrides; may reflect real
  deal structure even though the shipped defaults in `templates/cam/` never do
- `deals/` — generated output for real borrowers (names, financials, PII)

Only the framework itself (agent prompts, scripts, blank templates, config, docs) should ever
be committed. If you're contributing a template change, make sure every field is a generic
`[bracketed placeholder]` — never a real company name, person's name, or figure.

**Adding a new feature that touches real reference material?** Put its storage location in
`.gitignore` *before* writing anything there — never under a git-tracked path like
`templates/cam/` or `templates/spreading/`. The shipped defaults must stay generic and safe to
share across every fork; anything derived from one user's real documents belongs only in that
fork.

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
