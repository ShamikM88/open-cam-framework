# Slash command reference

Each Claude Code slash command in [`.claude/commands/`](../.claude/commands/) performs one step of the workflow
(the numbered list is in [`config/skills_registry.md`](../config/skills_registry.md)). This page is the human-readable
reference; **the command files are authoritative** for exactly what Claude is told to do. Walkthroughs with
worked examples are in [Workflows](workflows.md).

## What every command shares

- **Run it** inside a Claude Code session opened on this repository, in the same conversation as the other steps so
  later steps can see earlier ones' output. (The conversation is not the record: see "State" below.)
- **Deal identity.** Every step except `/calibrate` and `/calibrate-policy` takes
  `--company "<Name>" --proposal "<Proposal name>"`, which together name the deal's folder
  `deals/<Company>/<Proposal>_<YYYY-MM-DD>/`.
- **State.** A step starts by reading the deal's `state.json` (treating every figure already there as the source of
  truth) and ends by writing its results back, merged with what other steps recorded and appending its name to
  `steps_completed`. Nothing important lives only in the conversation. See the [data model](data-model.md).
- **Sources.** A step that fetches or is given a document saves the document itself to the deal's `sources/` folder
  with `scripts/source_manifest.py`, so a claim can be checked against the exact material later. Scratch artefacts are
  not saved.
- **Roles.** `/triage`, `/research`, `/spread`, `/commercial`, `/collateral`, `/project` and `/assemble` act as the
  Underwriter (`agents/underwriter_agent.md`); `/review` acts as the independent Risk Reviewer
  (`agents/risk_reviewer_agent.md`).
- **Cost and authentication.** All of them run on your Claude Code login, with no `ANTHROPIC_API_KEY`. The
  deterministic scripts they call run locally with no network. Whatever the session reads or you type is sent to the
  model that serves your Claude Code session, and `/triage`, `/research` and `/commercial` may also fetch public web
  pages. See [Architecture](architecture.md#two-ways-to-run-it).
- **Security.** Real borrower material belongs only in git-ignored locations (`inputs/`, `deals/`). Never type an API
  key, password or other credential into a command or the conversation. See [Security](security.md).
- **Invocation.** Every command except `/assemble` and `/review` is marked `disable-model-invocation`, so it runs only
  when you type it; `/review` can also be run by `/assemble` and `/research` as part of their loops. `/research`,
  `/assemble` and `/review` list the script commands they may run in their `allowed-tools`; the others run scripts
  through Bash steps under your own Claude Code permission settings.

## Setup commands

### /calibrate

- **What:** reads your own sample CAMs and writes a house style guide and a derived template.
- **Use when:** once per deal type, before drafting with your own layout. Optional; without it the neutral shipped
  defaults are used.
- **Inputs:** `--type <deal_type>` (default `corporate_credit`); PDFs in `inputs/calibration_samples/`. With none it
  stops and tells you to add some.
- **Produces:** `config/style_guide.md` (a `# Calibrated Style Guide`) and `templates/local/cam/<type>_cam.md`, a
  structure-only template whose every real name, date and figure is a `[placeholder]`. It captures genuine house
  conventions and deliberately does not copy a quirk that would hurt readability.
- **State:** none (no deal exists yet). Both files are git-ignored.
- **Can go wrong:** an empty samples folder (it stops); a template that still contains real data, because
  scrubbing is prompted, not enforced. Review the file, and run `python scripts/pii_scan.py` on it before ever
  promoting it into `templates/cam/`.
- **Headless equivalent:** `scripts/calibrate.py` ([CLI reference](cli-reference.md#calibrate)).

### /calibrate-policy

- **What:** turns your institution's credit policy documents into rules the agents check drafts against.
- **Use when:** once per institution; it applies to every deal in the fork.
- **Inputs:** documents in `inputs/credit_policy/` (with none it stops).
- **Produces:** `config/credit_policy.md` (a `# Institutional Credit Policy (Calibrated)`), organised into checkable
  rules. It is git-ignored and keeps real institutional detail on purpose, since it is never promoted or shared.
- **Effect:** the Underwriter drafts with awareness of it (advisory); the Risk Reviewer audits against it and a
  violation is a mandatory rejection finding.
- **State:** none. **Headless equivalent:** none (the headless pipeline reads an existing file but cannot create one).

## Deal steps

### /triage

- **What:** a legal-identity, ownership and charges screen with a Go/No-Go status.
- **Use when:** the first step of a full CAM. For a screen plus company research and no financials, use `/research`.
- **Inputs:** company registration number, credit bureau summary, charges/mortgages register (it asks for what is
  missing).
- **Produces:** the Go/No-Go verdict and rationale, grounded in a source you supplied or a credible public record
  (it never invents a registration detail, charge or ownership link).
- **State:** a `triage` key, bureau and registry figures under `inputs` (for example `inputs.experian_score`), and
  `triage` in `steps_completed`. It saves source documents and then runs `source_manifest.py --check-sources`; if
  citations were declared but nothing saved, it must go back and save one or tell you why not.
- **Can go wrong:** missing inputs (it asks); an unsaveable source (it must say so rather than skip silently).

### /research

- **What:** `/triage` and `/commercial` combined, with one exported brief, no financials.
- **Use when:** you need a screen and company research now, or ever, without a full CAM.
- **Inputs:** everything `/triage` and `/commercial` ask for.
- **Produces:** a standalone `<Company>_<Proposal>_Research_Brief.docx` in the deal's dated folder (no workbook, no
  template side effects), after an independent review (`/review --research-brief`) loops to `APPROVED`.
- **State:** `triage` and `commercial` exactly as the separate commands would write them, `inputs`, saved sources, a
  `review_trail`, and both steps in `steps_completed`, so the deal can continue into `/spread` later.
- **Can go wrong:** a `REJECTED` review (revise, overwrite, review again); no saved sources (same check as
  `/triage`). It may afterwards offer end-of-deal learnings, written only on your explicit yes.
- **Headless equivalent:** none. Export only: `scripts/research_export.py`.

### /spread

- **What:** spreads profit-and-loss and balance-sheet data into the core ratios, or records your own pre-spread
  figures as given.
- **Use when:** the financial step of a full CAM. It is the only hard prerequisite of `/assemble`.
- **Inputs:** 3 to 5 years of figures, or a pre-spread snapshot. Raw line items use these field names (omit any the
  source lacks; they default to 0 and are never invented): `revenue`, `cost_of_sales`, `admin_expenses`,
  `depreciation`, `amortisation`, `other_income`, `interest_paid`, `interest_received`, `scheduled_principal`,
  `capex`, `exceptional_costs`, `tax_paid`, `cash`, `trade_debtors`, `stock`, `other_current_assets`,
  `tangible_assets`, `intangible_assets`, `other_fixed_assets`, `trade_creditors`, `other_current_liabilities`,
  `overdraft`, `current_debt`, `long_term_debt`, `loan_notes`, `other_long_term_liabilities`, `provisions`,
  `share_capital`, `retained_profit`.
- **Produces (default mode):** run through `scripts/spreading_check.py`, never recalculated by hand: TNW, EBITDA,
  DSCR, EBIT/interest, gross leverage, net debt / EBITDA, gearing, current ratio, FCF conversion and the working
  capital days. See the [financial model](financial-model.md).
- **Produces (analyst-supplied mode):** your subtotals and ratios exactly as given, `financials_source:
  "analyst-supplied"`, and a `financials_source_note`; the CAM must then disclose the reduced audit guarantee.
- **State:** `financials`, `ratios`, `multi_period_financials`, `financials_source`, `spread` in `steps_completed`;
  in analyst-supplied mode also `analyst_supplied_financials` if you gave a raw breakdown. Before asking which mode
  applies it reads any persisted convention (`conventions.py --enterprise --read`, `--company ... --read`), tells you
  what is on file, and requires an explicit answer; it asks whether a newly confirmed convention is borrower-specific
  or enterprise-wide and persists it only then.
- **Figures read from an image** (a screenshot, a photo, a scanned page), in either mode: nothing is written until you
  have explicitly confirmed a read-back. The session stages the transcription, `scripts/transcription_check.py`
  prints it grouped by period and statement with each figure as written, cross-foots the subtotals the image states
  against the lines that feed them and prints a digest; only `--commit --confirm <digest>` records it, and it
  refuses a stale digest (the digest covers the figures, the sign conventions and the image's bytes), an
  unresolved discrepancy, or a deal already in the other `/spread` mode. The image is saved as the source of record, the state
  gains a `financials_transcriptions` record, and in analyst-supplied mode the CAM caveat's
  `financials_source_note` says the figures were transcribed from an image. Walk-through:
  [Workflows](workflows.md#figures-read-from-an-image); rules: [financial model](financial-model.md#figures-read-from-an-image).
- **Can go wrong:** a field name the framework does not know is ignored, not guessed (use the names above); a
  non-positive denominator makes a ratio N/A (not an error: see the [financial model](financial-model.md)); a
  refused image commit ([Troubleshooting](troubleshooting.md#figures-read-from-an-image)).

### /commercial

- **What:** company history, management, parent/ultimate-owner support, sector, concentration and competition.
- **Inputs:** sector, management bios, customer/supplier notes, business model.
- **Produces:** the drafted sections, each claim cited to a source (the company, its filings, a credible public
  source or what you said); it traces the full ownership chain and checks actively for recent ownership change.
- **State:** a `commercial` key, saved sources, `commercial` in `steps_completed`. Same source check as `/triage`.

### /collateral

- **What:** exposure, collateral coverage and uncovered risk for each asset, and the charge over each.
- **Inputs:** asset description, valuation/invoice amount, down payment %, LGD grade and %, residual value %, PD
  grade, ranking of the charge, a bureau score if one exists. PD, LGD and any bureau score are **inputs** you supply;
  the framework has no rating integration and never adjusts them.
- **Produces:** the calculations with their formulas and inputs shown.
- **State:** `inputs.pd`, `inputs.lgd`, a flat `collateral` list (one entry per asset with a stable `asset_id`), a
  flat `security_package` list (one entry per charge), `collateral` in `steps_completed`. In `security_package`,
  code compares `perfection_status` to exactly `"Perfected"` and `ranking` to exactly `"First"`; any other text is
  treated as a gap and creates a condition precedent.
- **Can go wrong:** writing `"Registered"` or `"1st"` where the exact strings are needed, which the engine reads as
  unperfected or subordinate.

### /project

- **What:** forward-year financials, a stress-test downside case, covenants and guarantees; all four optional.
- **Use when:** the deal has a forecast, covenants or guarantees; skip anything that does not apply.
- **Inputs:** `FY+1` to `FY+3` raw figures in the same shape as `/spread`; stress assumptions
  `revenue_haircut_pct`, `opex_increase_pct` (admin expenses only), `interest_rate_bump_bps` (each optional,
  default no shock); covenants as `{"metric", "type": "minimum"|"maximum", "threshold"}` where `metric` is a ratio
  name; guarantees as `{"provider", "type", "amount"}` (omit `amount` for an uncapped guarantee, never `0`).
- **Produces:** run through `spreading_check.py ... --no-update-financials-source` (always, so forward figures never
  change the deal's `financials_source`): forward ratios and the downside case derived by three deterministic
  shocks. See the [financial model](financial-model.md#the-downside-case).
- **State:** `financials`, `ratios`, `multi_period_financials`, `stress_assumptions`, `downside_case`, a flat
  `covenants` list, a flat `guarantees` list (both merged with what exists), `project` in `steps_completed`.
- **Analyst-supplied mode (opt-in):** it asks which mode applies and never infers it. When the forecast convention does not
  fit the framework's raw schema, the analyst supplies forward-year subtotals and ratios (and optionally raw lines, and
  their own stressed forecast), run through `scripts/supplied_forecast.py` instead of `spreading_check.py`. They are recorded
  as given, with `forecast_source: "analyst-supplied"` and the deal-wide `financials_source: "analyst-supplied"` (so the
  CAM must carry the caveat; the analyst's convention note is added to `financials_source_note`). The framework's stress
  shocks apply to a year only if its raw lines reproduce the supplied figures; otherwise the downside is the analyst's own
  scenario or is recorded as unavailable, which the policy engine treats as UNRESOLVABLE under stress. One basis per deal:
  an incompatible deal is refused. See [the financial model](financial-model.md#analyst-supplied-forecasts-and-their-downside).

## Assembly and audit

### /assemble

- **What:** drafts the CAM into the template, audits it through `/review` until `APPROVED`, and exports it.
- **Inputs:** `--company`, `--proposal`, `--type <deal_type>`, `--pd`, `--lgd`; the results of `/triage`, `/spread`,
  `/commercial`, `/collateral` (from `state.json` or the conversation; if any is unavailable it says which and stops).
  `/project` is optional.
- **Steps:** resolve the template (local override, else shipped default, else a new type); hard-check
  `state_manager.py --check-steps ... --required spread` and stop if it is not `ok`; get `policy_state` from
  `policy_check.py`; draft, ending with the structured JSON block; save and `/review`; revise and overwrite until
  `APPROVED`; export with `deal_export.py`; report the folder and delete the temporary draft; optionally offer
  end-of-deal learnings.
- **Produces:** `<Company>_<Proposal>_CAM.docx` and `<Company>_<Proposal>_Spreading.xlsx` in the deal's dated folder; a
  new local template if the deal type had none.
- **State:** `deal_type`, `inputs.pd`/`inputs.lgd`, `draft_path`, `policy_state`, `assemble` in `steps_completed`
  (`/review` writes `review_verdict` and `review_trail`).
- **Can go wrong:** `spread` missing (it stops before drafting); a code-enforced rejection that the Reviewer cannot
  override (fix the draft, or the recorded structure if the reason is a security gap or covenant); a deal type with no
  template, which is treated as new.

### /review

- **What:** the Risk Reviewer audits a draft (or, with `--research-brief`, a `/research` brief) and returns
  `APPROVED` or `REJECTED` with revision notes.
- **Inputs:** `--company`, `--proposal`, optionally `--research-brief`; the draft (pasted, or the latest in the
  conversation).
- **Steps:** for a CAM with a deal identity it first runs the code-enforced check (`policy_check.py --draft`), whose
  reasons make `REJECTED` mandatory and must be quoted verbatim in the notes; then the qualitative checklist. With
  `--research-brief` the code check is skipped and credit-policy and risk-mitigant items do not apply. With no deal
  identity it falls back to full manual verification of whatever figures were supplied.
- **State:** appends one entry to `review_trail` (never replaces it), sets `review_verdict`, adds `review` to
  `steps_completed`. It may offer to persist a policy-interpretation correction, only on an explicit yes.
- **Can go wrong:** a stale draft file (it must be overwritten before re-checking, or the previous draft is re-checked).

## Implementation notes

The per-command implementation notes below were moved here from `CLAUDE.md` ([ledger](move-ledger.md)); the command
files and `CLAUDE.md`'s "Slash commands" section remain the authority for how they work.

- **`/calibrate --type <deal_type>`** — Claude reads the PDFs in `inputs/calibration_samples/`
  directly (native PDF support), writes `config/style_guide.md`, and derives a template to
  `templates/local/cam/<deal_type>_cam.md` -- the slash-command equivalent of `calibrate.py`.

- **`/calibrate-policy`** — org-wide, one-time setup (no `--type`, unlike `/calibrate`): Claude
  reads this institution's own credit policy document(s) from `inputs/credit_policy/` directly,
  extracts lending criteria, required mitigants, structuring norms, and risk appetite boundaries
  into `config/credit_policy.md`. Once present, every future `/assemble`/`/review` run (and
  `orchestrator.py`'s headless pipeline) automatically picks it up -- the Underwriter drafts with
  awareness of it (advisory), and the Risk Reviewer audits the draft against it (mandatory). No
  headless equivalent yet (slash-command-only, matching `/review`'s own original minimal-scope
  introduction).

- **`/triage`, `/spread`, `/commercial`, `/collateral`, `/project`** — each loads the Underwriter
  role (`agents/underwriter_agent.md`) and performs one step from `skills_registry.md`.
  `/project` is the slash-command capture point for forward-year financials, stress-test
  assumptions (deriving the downside case), covenants, and guarantees -- all four independently
  optional -- mirroring what `orchestrator.py`'s `--financials`/`--stress-assumptions` CLI flags
  and hand-edited `state.json` already support in the headless pipeline. `/spread`'s default mode
  and `/project`'s forward-year/downside-case handling both run
  `python scripts/spreading_check.py --company ... --proposal ... --financials ...
  [--stress-assumptions ...]` as a Bash step rather than having Claude recalculate the same
  subtotals/ratios by hand in prose (see issue #98) -- `/spread`'s analyst-supplied alternative
  mode is unaffected, since that path deliberately skips independent recomputation either way.

- **`/research`** — standalone alternative to the full pipeline for a deal that doesn't (yet, or
  ever) need a full CAM: combines `/triage`'s Go/No-Go screen and `/commercial`'s company/sector
  research into one step, writing the exact same `triage`/`commercial` state.json keys those two
  commands would (so the deal can still continue into the full pipeline later without redoing
  anything). Loops `/review --research-brief` until APPROVED before exporting (issue #87), mirroring
  `/assemble`'s own review loop but scoped to what applies before any credit structuring exists —
  then exports a standalone brief via
  `python scripts/research_export.py --company ... --proposal ... --brief ...` as its Bash step.

- **`/review`** — loads the Risk Reviewer role (`agents/risk_reviewer_agent.md`) and audits a
  draft, returning `APPROVED`/`REJECTED`. This is the Checker half of Maker-Checker actually
  made runnable as a command for the first time (the registry documented it; nothing wired it up
  before this).

- **`/assemble`** — resolves the CAM template via `template_resolver` logic (local override,
  else shipped default), drafts into it from the prior steps' output, loops `/review` until
  `APPROVED`, then runs `python scripts/deal_export.py --company ... --proposal ... --type ... --draft ...`
  as its Bash step to create the output folder and export `.docx`/`.xlsx`.
