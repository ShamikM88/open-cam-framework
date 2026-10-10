# OpenCAM Skill & Command Registry

The one inventory of everything a Claude Code session can run by name in this repository: the slash commands (the
workflow steps) and the skills (analyst and maintainer aids). The design behind it -- what a skill is allowed to be,
how it is invoked, why none of them can reach the Maker or the Checker -- is [docs/skill-design.md](../docs/skill-design.md);
`tests/test_skill_inventory.py` keeps this table equal to the files.

## Inventory

| Name | Kind | Audience | Status | Invocation | Role in the pipeline | Headless |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/calibrate` | command | analyst | implemented | user | Setup | yes |
| `/calibrate-policy` | command | analyst | implemented | user | Setup | no |
| `/triage` | command | analyst | implemented | user | Maker | yes |
| `/research` | command | analyst | implemented | user | Maker | no |
| `/spread` | command | analyst | implemented | user | Maker | yes |
| `/commercial` | command | analyst | implemented | user | Maker | yes |
| `/collateral` | command | analyst | implemented | user | Maker | yes |
| `/project` | command | analyst | implemented | user | Maker | yes |
| `/assemble` | command | analyst | implemented | user | Maker | yes |
| `/review` | command | analyst | implemented | user+model | Checker | yes |
| `information-gaps` | skill | analyst | implemented: phase 2 | user | None (aid) | no |
| `evidence-discipline` | skill | analyst | implemented: phase 2 | user | None (aid) | no |
| `financial-analysis` | skill | analyst | implemented: phase 2 | user | None (aid) | no |
| `cam-change-verification` | skill | maintainer | planned: phase 3 | user | None (aid) | no |
| `docs-maintenance` | skill | maintainer | planned: phase 3 | user | None (aid) | no |

- **Invocation** is what the file's frontmatter says. `user`: `disable-model-invocation: true`, so it runs only when a
  person types it. `user+model`: no restriction; `/review` has to be this, because `/assemble` and `/research` run it
  in their loops. `model`: `user-invocable: false`. No skill is `user+model` or `model`; that needs a design change
  first.
- **Role in the pipeline** says whose input a file is. A command acts as the Maker (`agents/underwriter_agent.md`), the
  Checker (`agents/risk_reviewer_agent.md`) or sets the framework up. A skill is an aid: it is never any of these.
- **Headless**: `yes` means the headless scripts produce the same deal outputs (some modes of a `yes` command need a
  person and exist only interactively; see [architecture](../docs/architecture.md#two-ways-to-run-it)). A skill is `no`
  by design: it leaves no record in a deal, so nothing the headless pipeline cannot reproduce can depend on it.
- **Status**: a skill is `planned: phase N` until the pull request that adds its `SKILL.md` under
  `.claude/skills/<name>/` flips it to `implemented: phase N`. A name is unique across both kinds: in Claude Code a skill
  wins over a command of the same name, so a clash would silently disable the command.

## Commands

Execute the following actions when triggered by their respective slash commands:

1. `/triage`
   - Primary Inputs: Company Registration Number, Credit Bureau Summary, Mortgages/Charges register.
   - Core Action: Validate legal identity, identify Parent/UBO structure, verify active charges, and output an initial Go/No-Go screening status.

2. `/research` (standalone alternative to the full pipeline)
   - Primary Inputs: Everything `/triage` and `/commercial` each ask for.
   - Core Action: Combines `/triage`'s Go/No-Go screen and `/commercial`'s company/sector research into one step and one exported standalone brief -- for a deal that doesn't (yet, or ever) need a full CAM. Writes the exact same `triage`/`commercial` state.json keys those two commands would, so the deal can still continue into `/spread` → ... → `/assemble` later without redoing anything. Loops `/review --research-brief` until APPROVED before exporting, giving it its own scoped-down Checker pass.

3. `/spread`
   - Primary Inputs: 3–5 years of Profit & Loss and Balance Sheet data.
   - Core Action: Extract and spread line items. Calculate key financial metrics: Tangible Net Worth (TNW), EBITDA, Debt Service Coverage Ratio (DSCR), EBIT/Interest, Gross Leverage, Net Debt / EBITDA, Gearing %, Current Ratio, FCF Conversion %, and Working Capital Days. Figures read from an image (a screenshot, a photo, a scanned page) in either mode are recorded only after an explicit read-back confirmation through `scripts/transcription_check.py` (issue #132).

4. `/commercial`
   - Primary Inputs: Sector name, management bios, customer/supplier notes, business model description.
   - Core Action: Draft Company History, Executive Management overview, Parent/UBO Support analysis, Sector Dynamics, Customer/Supplier Concentration, and Competitive Landscape.

5. `/collateral`
   - Primary Inputs: Asset Description, Valuation/Invoice Amount, Down Payment %, Loss Given Default (LGD) Grade & %, Residual Value (RV) %, Probability of Default (PD) Grade.
   - Core Action: Calculate Gross/Net Exposure, RV Exposure, Collateral Coverage %, LGD %, and Estimated Net Uncovered Risk.

6. `/project`
   - Primary Inputs: Forward-year (FY+1-FY+3) P&L/Balance Sheet forecasts, stress-test assumptions (revenue haircut %, opex increase %, interest rate bump bps), covenants, and guarantees. Every piece is independently optional.
   - Core Action: Spreads forward-year financials the same way `/spread` does for historical years, deterministically derives a downside (stressed) case from any stress assumptions given, and records any covenants/guarantees for `/assemble`'s code-enforced policy checks. Opt-in analyst-supplied mode (issue #124): forward-year subtotals/ratios recorded as given through `scripts/supplied_forecast.py`, with the downside the analyst's own scenario, derived only where the framework's shocks genuinely apply, or recorded as unavailable.

7. `/assemble`
   - Primary Inputs: Aggregated outputs from steps `/triage` through `/project`.
   - Core Action: Synthesize all data into the target Markdown CAM template (Header Tables, Facility T&Cs, Spreading Table, Risk/Mitigant Matrix, and Final Recommendation), invoking `/review` before export.

8. `/review`
   - Primary Inputs: A drafted CAM (from `/assemble`, or pasted directly).
   - Core Action: Runs the Risk Reviewer ("Checker") agent — re-verifies ratio calculations, flags ungrounded assertions or missing sources, challenges weak mitigants, and returns a verdict of `APPROVED` or `REJECTED` with revision notes. This is the Maker-Checker loop's audit step made runnable; `/assemble` calls it automatically, but it can also be run standalone against any draft.

9. `/calibrate`
   - Primary Inputs: Sample CAM PDFs in `inputs/calibration_samples/`.
   - Core Action: Reads the samples directly (no API key needed), extracts writing style/tone into `config/style_guide.md`, and derives a generic CAM template into `templates/local/cam/<type>_cam.md` that overrides the shipped default for that deal type.

10. `/calibrate-policy`
    - Primary Inputs: This institution's own credit policy document(s) in `inputs/credit_policy/`.
    - Core Action: Reads the documents directly (no API key needed) and extracts lending criteria, required mitigants, structuring norms, and risk appetite boundaries into `config/credit_policy.md`. Org-wide, one-time setup (unlike `/calibrate`, not per-deal-type) — once present, every future `/assemble`/`/review` run (and `orchestrator.py`'s headless pipeline) automatically picks it up: the Underwriter drafts with awareness of it, and the Risk Reviewer audits the draft against it.

See [`.claude/commands/`](../.claude/commands/) for the runnable slash-command implementation of
each step above. Every step (`/calibrate` and `/calibrate-policy` excepted) also takes `--company
"<Name>" --proposal "<Proposal name>"` and checkpoints its results to
`deals/<Company>/<Proposal>_<Date>/state.json` on completion — see CLAUDE.md's "Context Window &
State Management Protocol".