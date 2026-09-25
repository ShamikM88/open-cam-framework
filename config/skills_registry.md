# OpenCAM Skill & Command Registry

Execute the following actions when triggered by their respective slash commands:

1. `/triage`
   - Primary Inputs: Company Registration Number, Credit Bureau Summary, Mortgages/Charges register.
   - Core Action: Validate legal identity, identify Parent/UBO structure, verify active charges, and output an initial Go/No-Go screening status.

2. `/research` (standalone alternative to the full pipeline)
   - Primary Inputs: Everything `/triage` and `/commercial` each ask for.
   - Core Action: Combines `/triage`'s Go/No-Go screen and `/commercial`'s company/sector research into one step and one exported standalone brief -- for a deal that doesn't (yet, or ever) need a full CAM. Writes the exact same `triage`/`commercial` state.json keys those two commands would, so the deal can still continue into `/spread` → ... → `/assemble` later without redoing anything. Loops `/review --research-brief` until APPROVED before exporting, giving it its own scoped-down Checker pass.

3. `/spread`
   - Primary Inputs: 3–5 years of Profit & Loss and Balance Sheet data.
   - Core Action: Extract and spread line items. Calculate key financial metrics: Tangible Net Worth (TNW), EBITDA, Debt Service Coverage Ratio (DSCR), EBIT/Interest, Gross Leverage, Gearing %, Current Ratio, and Working Capital Days.

4. `/commercial`
   - Primary Inputs: Sector name, management bios, customer/supplier notes, business model description.
   - Core Action: Draft Company History, Executive Management overview, Parent/UBO Support analysis, Sector Dynamics, Customer/Supplier Concentration, and Competitive Landscape.

5. `/collateral`
   - Primary Inputs: Asset Description, Valuation/Invoice Amount, Down Payment %, Loss Given Default (LGD) Grade & %, Residual Value (RV) %, Probability of Default (PD) Grade.
   - Core Action: Calculate Gross/Net Exposure, RV Exposure, Collateral Coverage %, LGD %, and Estimated Net Uncovered Risk.

6. `/project`
   - Primary Inputs: Forward-year (FY+1-FY+3) P&L/Balance Sheet forecasts, stress-test assumptions (revenue haircut %, opex increase %, interest rate bump bps), covenants, and guarantees. Every piece is independently optional.
   - Core Action: Spreads forward-year financials the same way `/spread` does for historical years, deterministically derives a downside (stressed) case from any stress assumptions given, and records any covenants/guarantees for `/assemble`'s code-enforced policy checks.

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