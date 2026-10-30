# Slash command reference

Each Claude Code slash command in [`.claude/commands/`](../.claude/commands/) performs one step of the workflow
(the inventory is in [`config/skills_registry.md`](../config/skills_registry.md)). This page is the human-readable
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
  `steps_completed`. When looking up state, consider only an exact `<proposal>_YYYY-MM-DD` folder and choose the latest by comparing the date text; stop if the lookup reaches 100 files because the list may be incomplete. Nothing important lives only in the conversation. See the [data model](data-model.md).
- **Missing deal record.** Interactive starters (`/triage`, `/spread` and `/research`) announce when no matching record exists and say they will start a new deal under the supplied name. `/commercial`, `/collateral`, `/project` and `/assemble` stop if no record exists; `/review` stops too and offers only returned normalization-only near matches. This is deliberately stricter than the headless orchestrator, which warns and carries on.
- **Sources.** A step that fetches or is given a document saves the document itself to the deal's `sources/` folder
  with `scripts/source_manifest.py`, so a claim can be checked against the exact material later. Scratch artefacts are
  not saved.
- **Roles.** `/triage`, `/research`, `/spread`, `/commercial`, `/collateral`, `/project` and `/assemble` act as the
  Underwriter (`agents/underwriter_agent.md`). `/review` does not play the Risk Reviewer in your conversation: it runs
  the deterministic checks and hands the audit to a fresh, read-only `risk-reviewer` sub-agent
  (`.claude/agents/risk-reviewer.md`) that applies `agents/risk_reviewer_agent.md` to the saved draft and the deal's
  files, without your conversation. See [AI assurance](ai-assurance.md#where-the-enforcement-runs-matters) for what that
  does and does not guarantee.
- **Cost and authentication.** All of them run on your Claude Code login, with no `ANTHROPIC_API_KEY`. The
  deterministic scripts they call run locally with no network. Whatever the session reads or you type is sent to the
  model that serves your Claude Code session, and `/triage`, `/research` and `/commercial` may also fetch public web
  pages. See [Architecture](architecture.md#two-ways-to-run-it).
- **Security.** Real borrower material belongs only in git-ignored locations (`inputs/`, `deals/`). Never type an API
  key, password or other credential into a command or the conversation. See [Security](security.md).
- **Invocation.** Every command except `/review` is marked `disable-model-invocation`, so it runs only
  when you type it; `/review` can also be run by `/assemble` and `/research` as part of their loops (the registry's
  Invocation column lists each command, and a test keeps it equal to the files). `/research`,
  `/assemble` and `/review` list the script commands they may run in their `allowed-tools`; the others run scripts
  through Bash steps under your own Claude Code permission settings.
- **Skills are not commands.** Analyst and maintainer aids are typed by a person, leave no record in a deal and are
  never part of the Maker's or the Checker's input. A skill never shares a command's name. See [Skills](skills.md)
  and [Skill design](skill-design.md).

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

### /start-review

- **What:** prepares a new, empty dated review and records an optional date-only link to a previous review. It never copies data from the older review.
- **Use when:** the same company and proposal are being reviewed again, for example in a new annual cycle. For a genuinely new deal, use `/triage`, `/spread` or `/research`.
- **Inputs:** one non-empty `--company "<Name>"` and `--proposal "<Proposal name>"`; optionally `--date YYYY-MM-DD`. Names must be safe path components. Company and proposal names match exactly; keep the proposal name unchanged across years if the earlier review should be linkable or comparable.
- **Steps:** Runs `python scripts/state_manager.py --review-candidates --company "<company>" --proposal "<proposal>"` read-only (adding `--date <date>` only when you supplied one). It uses the returned `date` field rather than calculating a date itself. When candidates exist, it checks whether the newest review has an assembled CAM. If it is not recorded as assembled, it explains that the review may still be in progress and asks whether to continue or stop; only an explicit yes continues. It lists valid earlier reviews newest first, identifies the default, and offers three choices: link the default, link another listed date, or start unlinked. If the listing reports earlier reviews it could not read (`skipped`), it names their dates and reasons and does not offer them as a link. With no candidates, it says so (or, when only unreadable reviews exist, says that they exist but could not be read) and asks whether to start unlinked or stop. It waits for your explicit choice and runs exactly one `--start-review` call with either `--prior-date <chosen date>` or `--no-prior`. If linked, it offers `review_compare.py` (pre-approved in the command's `allowed-tools`, read-only) but runs the comparison only if you ask.
- **State:** the script creates a new dated folder with a minimal `state.json` containing `company`, `proposal`, `date`, `schema_version`, and `prior_review_date` only if linked. Nothing is copied from the earlier review, and starting a review does not add a `steps_completed` entry. Existing reviews are never edited or overwritten.
- **Can go wrong:** missing or unsafe names, a malformed date, a failed/non-JSON result from either read-only script, an existing dated folder, a date not later than the newest state-bearing review, a company that has no deal folder yet (use `/triage` or `/spread` for a deal that has never been reviewed), or a chosen date that is not a valid earlier candidate. On any script failure or refusal, it reports the script's error and stops; it does not retry with another date or choose a different link.
- **Headless equivalent:** `python scripts/orchestrator.py --company "<company>" --proposal "<proposal>" --new-review` creates today's review and links the nearest earlier review with the exact same company/proposal, or starts unlinked if none exists. Unlike this interactive command, the headless path does not prompt for the prior choice. A rerun resumes today's existing `state.json` without changing its link.

## Deal steps

### /triage

- **What:** a legal-identity, ownership and charges screen with a Go/No-Go status.
- **No-record behavior:** Before creating anything, runs `state_manager.py --find`; if no record exists, it announces that `/triage` will create a new deal under the supplied name and names any returned near matches so you can correct a typo first.
- **Use when:** the first step of a full CAM. For a screen plus company research and no financials, use `/research`.
- **Inputs:** company registration number, credit bureau summary, charges/mortgages register (it asks for what is
  missing).
- **Produces:** the Go/No-Go verdict and rationale, grounded in a source you supplied or a credible public record
  (it never invents a registration detail, charge or ownership link).
- **State:** a `triage` key, bureau and registry figures under `inputs` (for example `inputs.experian_score`), and
  `triage` in `steps_completed`. It saves source documents and then runs `source_manifest.py --check-sources`; if
  citations were declared but nothing saved, it must go back and save one or tell you why not; if the check reports `sources_declared: "unknown"` (a completed step has no valid `sources` list), it must restore the list in state and rerun the check.
- **Can go wrong:** missing inputs (it asks); an unsaveable source (it must say so rather than skip silently).

### /research

- **What:** `/triage` and `/commercial` combined, with one exported brief, no financials.
- **No-record behavior:** Before creating anything, runs `state_manager.py --find`; if no record exists, it announces that `/research` will create a new deal under the supplied name and names any returned near matches so you can correct a typo first.
- **Use when:** you need a screen and company research now, or ever, without a full CAM.
- **Inputs:** everything `/triage` and `/commercial` ask for.
- **Produces:** a standalone `<Company>_<Proposal>_Research_Brief.docx` in the deal's dated folder (no workbook, no
  template side effects), after a review by a fresh Risk Reviewer sub-agent (`/review --research-brief`) loops to `APPROVED`.
- **Brief shape:** the first substantive section is `## Go/No-Go`, on about one page, with the verdict/action, three decisive facts each supported by a figure and source, and no more than five priority-ordered proposed conditions. Aim for about 1,500 words; this is guidance, not an export limit.
- **Review-status disclosure:** the drafter must not write its own review-status or manual-release paragraph. The exporter adds the full dated disclosure immediately after the title only after an explicit analyst-authorised manual-review release; the session must not invent a release reason or infer consent.
- **State:** `triage` and `commercial` exactly as the separate commands would write them, `inputs`, saved sources, a
  `review_trail`, and both steps in `steps_completed`, so the deal can continue into `/spread` later. If the analyst
  defines a term in the step, it is checkpointed immediately in `analyst_terms` in `state.json`.
- **Can go wrong:** a `REJECTED` review means revise, overwrite and review again with a new Reviewer. After three consecutive
  `REJECTED` passes and each later rejection, `/research` asks a structured three-choice question before another
  pass: Run another review pass; Stop and export for manual review (unapproved, disclosed); or Stop without exporting.
  The pass count comes from the on-disk `review_trail`; silence, ambiguity or “carry on” is not permission to export.
  Only the explicit manual-review choice permits `--analyst-release-unapproved "<reason>"`, which inserts a dated
  not-approved status near the top of the DOCX and appends a record to the append-only `review_stopped_by_analyst` list in state. No saved sources
  (same check as `/triage`) or a missing/empty Sources/References section still blocks export before output is written.
  A populated final `## Sources` list is required, including document URLs and citation keys for short-form references. If the analyst sets a lower per-deal review maximum, `/research` uses that threshold and records it under `deal_structure_notes.review_cap` in `state.json`.
  Export without it requires the analyst's explicit chat authorisation and a reason, shown in a final DOCX footnote;
  the command never offers or infers this override. For a group borrower or related-party links, open the primary charge
  instruments and the borrower's accounts notes, check parent guarantees and direct subsidiary holders, and inspect
  PSC history including ceased entries. Read each year's available accounts, not only the latest, and make
  register/accounts disagreements analyst follow-ups. Save each relied-on document and report any inaccessible
  document as an evidence gap. After a manual-review release the session carries on with the
  remaining export steps as for an approved export; after “Stop without exporting” nothing is exported, but the
  end-of-deal learnings step still runs. It may afterwards offer end-of-deal learnings, written only on
  your explicit yes.
- **Term learning:** At end of deal, `analyst_terms` joins the same batch as the other learning candidates. Persist
  an entry only on an explicit yes and ask each time whether it applies to this borrower or every borrower. If the
  analyst says the term is industry-standard, point to docs/glossary.md and do not write it to a tracked file.
- **Headless equivalent:** none. Export only: `scripts/research_export.py`.

### /spread

- **What:** spreads profit-and-loss and balance-sheet data into the core ratios, or records your own pre-spread
  figures as given.
- **No-record behavior:** Before creating anything, runs `state_manager.py --find`; if no record exists, it announces that `/spread` will create a new deal under the supplied name and names any returned near matches so you can correct a typo first. If the company folder is absent, it says so instead of offering near matches.
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
- **Produces (analyst-supplied mode):** your subtotals and ratios as supplied, `financials_source:
  "analyst-supplied"`, and a `financials_source_note`; the CAM must then disclose the reduced audit guarantee.
  The fraction convention for `gearing`, `fcf_conversion_pct` and their covenant thresholds is defined in the
  [financial model](financial-model.md#percent-type-ratios-are-stored-as-fractions).
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
- **No-record behavior:** Runs `state_manager.py --find` before proceeding. If no record exists, it stops with the plain-language explanation and offers only returned near-miss candidates; it does not create a deal folder or state file. If the company folder is absent, it says so instead of offering near matches.
- **Inputs:** sector, management bios, customer/supplier notes, business model.
- **Produces:** the drafted sections, each claim cited to a source (the company, its filings, a credible public
  source or what you said); it traces the full ownership chain and checks actively for recent ownership change.
- **State:** a `commercial` key, saved sources, `commercial` in `steps_completed`. Same source check as `/triage`.

### /collateral

- **What:** exposure, collateral coverage and uncovered risk for each asset, and the charge over each.
- **No-record behavior:** Runs `state_manager.py --find` before proceeding. If no record exists, it stops with the plain-language explanation and offers only returned near-miss candidates; it does not create a deal folder or state file. If the company folder is absent, it says so instead of offering near matches.
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
- **No-record behavior:** Runs `state_manager.py --find` before proceeding. If no record exists, it stops with the plain-language explanation and offers only returned near-miss candidates; it does not create a deal folder or state file. If the company folder is absent, it says so instead of offering near matches.
- **Use when:** the deal has a forecast, covenants or guarantees; skip anything that does not apply.
- **Inputs:** `FY+1` to `FY+3` raw figures in the same shape as `/spread`; stress assumptions
  `revenue_haircut_pct`, `opex_increase_pct` (admin expenses only), `interest_rate_bump_bps` (each optional,
  default no shock); covenants as `{"metric", "type": "minimum"|"maximum", "threshold"}` where `metric` is a ratio
  name; guarantees as `{"provider", "type", "amount"}` (omit `amount` if not yet known, which reads "an amount to be confirmed"; write `an unlimited amount` for an uncapped guarantee; never `0`).
  `gearing`, `fcf_conversion_pct` and covenants on those metrics use the fraction convention in the
  [financial model](financial-model.md#percent-type-ratios-are-stored-as-fractions).
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
- **No-record behavior:** Runs `state_manager.py --find` before proceeding. If no record exists, it stops with the plain-language explanation and offers only returned near-miss candidates; it does not create a deal folder or state file. If the company folder is absent, it says so instead of offering near matches.
- **Inputs:** `--company`, `--proposal`, `--type <deal_type>`, `--pd`, `--lgd`; the results of `/triage`, `/spread`,
  `/commercial`, `/collateral` (from `state.json` or the conversation; if any is unavailable it says which and stops).
  `/project` is optional.
- **Steps:** resolve the template (local override, else shipped default, else a new type); hard-check
  `state_manager.py --check-steps ... --required spread` and stop if it is not `ok`; get `policy_state` from
  `policy_check.py`; draft, ending with the structured JSON block; save and `/review`; revise and overwrite until
  `APPROVED`; export with `deal_export.py`; report the output folder, then move the approved Markdown to the same dated folder as
  `draft_approved.md` and record its repository-relative path in `state.json` as `approved_draft_path`; optionally offer
  end-of-deal learnings.
- **Produces:** `<Company>_<Proposal>_CAM.docx` and `<Company>_<Proposal>_Spreading.xlsx` in the deal's dated folder; a
  new local template if the deal type had none.
- **State:** `deal_type`, `inputs.pd`/`inputs.lgd`, `draft_path`, `approved_draft_path`, `policy_state`, `assemble` in `steps_completed`
  (`/review` writes `review_verdict` and `review_trail`). If the analyst defines a term during the step, it is
  checkpointed immediately in `analyst_terms` in `state.json`.
- **Can go wrong:** `spread` missing (it stops before drafting); a code-enforced rejection that the Reviewer cannot
  override (fix the draft, or the recorded structure if the reason is a security gap or covenant); a deal type with no
  template, which is treated as new.
- **Term learning:** At end of deal, `analyst_terms` joins the same batch as the other learning candidates. Persist
  an entry only on an explicit yes and ask each time whether it applies to this borrower or every borrower. If the
  analyst says the term is industry-standard, point to docs/glossary.md and do not write it to a tracked file.

### /review

- **What:** audits a saved draft (or, with `--research-brief`, a saved `/research` brief) and returns `APPROVED` or
  `REJECTED` with revision notes. `/review` is the orchestrator; the qualitative audit is done by a fresh, read-only
  `risk-reviewer` sub-agent.
- **No-record behavior:** Before stopping, runs `state_manager.py --find` and offers returned normalization-only near-miss candidates, if any; it does not invent candidates.
- **Inputs:** `--company`, `--proposal`, optionally `--research-brief` and `--draft "<path>"`. The draft is a file on
  disk: by default `deals/<company>/<proposal>_draft.md` (a CAM) or `..._brief.md` (a brief), which `/assemble` and
  `/research` save before calling it. `/review` never reviews "the latest draft in the conversation" and does not take
  pasted draft text: with no file it stops and says so.
- **Steps:** for a CAM with a deal identity it first runs the code-enforced check (`policy_check.py --draft`; a failure
  stops the review, it is not a pass). It then starts a **new** `risk-reviewer` for every review (a re-review of a revised
  draft is a new one, never a resumed one) and sends it paths, not text: the draft, the deal's `state.json`, the source
  manifest, which policy files exist, and the `policy_check.py` output. The Reviewer reads the evidence itself, audits
  first, and only then reads the deal's earlier `review_trail`. `/review` checks that a valid verdict came back (if not,
  it stops and records nothing), makes the result `REJECTED` with every code-enforced reason quoted verbatim whenever
  there is one, and records the result. With `--research-brief` the code check is skipped and credit-policy and
  risk-mitigant items do not apply. If a named company/proposal has no matching dated `state.json`, `/review` stops before
  the reviewer-view export, the deterministic check and Reviewer delegation, records nothing, and tells the analyst no deal record exists so the
  spelling can be checked. `/review` never creates a missing deal record. A standalone review with `--draft` and no deal
  identity has no state and no code check, so the Reviewer verifies whatever it can by hand.
- **State:** only `/review` writes: it appends one entry to `review_trail` (never replaces it), sets `review_verdict`, adds
  `review` to `steps_completed`. The Reviewer writes nothing. It may offer to persist a policy-interpretation
  correction, only on an explicit yes.
- **Can go wrong:** no saved draft (it stops; save the draft and pass `--draft`); a stale draft file (the caller must
  overwrite it before re-reviewing, since `/review` does not write the draft); the sub-agent cannot be started, or it
  returns no valid verdict (the review stops, and an unapproved draft is never exported); a saved source the Reviewer
  cannot open (it reports that as an evidence gap rather than verifying the claim). For a `/research` brief, the Reviewer
  also checks closing Sources-list completeness and citation-key definitions, or that an override footnote replacing
  Sources has a reason and date. The authorised override is not itself a rejection reason; untraceable claims remain findings. A listed source that no claim cites is a minor point, not a reason to reject on its own.

## Reporting

### /committee-summary

- **What:** writes a short committee summary, `<Company>_<Proposal>_Committee_Summary.md` and `.docx`, into the deal's dated folder, from the recorded `state.json` and the deterministic policy check. It is a meeting aid, not a credit decision, rating, recommendation or approval, and it does not replace the CAM or `/review`.
- **Use when:** a committee wants a compact view of what the record holds: key terms, the financial snapshot, covenant results (now, forward, downside), conditions, collateral and security, open gaps and the recorded review status.
- **Inputs:** `--company "<Name>" --proposal "<Proposal name>"`; optionally `--date-str YYYY-MM-DD` (otherwise the deal's newest dated folder that has a `state.json`).
- **Steps:** checks the arguments (the same refusal rule as the other commands), runs `python scripts/committee_summary.py` once, treats any failure as a failure, and reports the folder, the two file paths and the section names. It does not read the summary back as advice.
- **State:** none. It writes no `state.json`, `steps_completed`, `review_trail`, draft, source manifest or template, and it does not add itself to `steps_completed`; the script writes only the two summary files.
- **Can go wrong:** missing or unsafe names, a malformed date, a deal or dated folder with no `state.json`, a malformed state key (including `group_basis`), an unusable source manifest, or a Word failure after the Markdown was written (the error says so). Details are in [Committee summary](cli-reference.md#committee-summary).
- **Headless equivalent:** run `python scripts/committee_summary.py` yourself; there is no orchestrator mode.

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
  anything). Loops `/review --research-brief` until APPROVED before exporting (issue #87), with an analyst checkpoint after the third consecutive rejection (see `/research` above), mirroring
  `/assemble`'s own review loop but scoped to what applies before any credit structuring exists —
  then exports a standalone brief via
  `python scripts/research_export.py --company ... --proposal ... --brief ...` as its Bash step.

- **`/review`** — the orchestrator of the Checker half of Maker-Checker. It resolves the saved draft, runs
  `policy_check.py`, delegates the audit to a fresh `risk-reviewer` sub-agent (`.claude/agents/risk-reviewer.md`, which
  applies the role in `agents/risk_reviewer_agent.md`), validates the verdict, applies the code-enforced reasons and
  appends the result to the `review_trail` (issue #204).

- **`/assemble`** — resolves the CAM template via `template_resolver` logic (local override,
  else shipped default), drafts into it from the prior steps' output, loops `/review` until
  `APPROVED`, then runs `python scripts/deal_export.py --company ... --proposal ... --type ... --draft ...`
  as its Bash step to create the output folder and export `.docx`/`.xlsx`.
