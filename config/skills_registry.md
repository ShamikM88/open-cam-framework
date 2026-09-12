# OpenCAM Skill & Command Registry

Execute the following actions when triggered by their respective slash commands:

1. `/triage`
   - Primary Inputs: Company Registration Number, Credit Bureau Summary, Mortgages/Charges register.
   - Core Action: Validate legal identity, identify Parent/UBO structure, verify active charges, and output an initial Go/No-Go screening status.

2. `/spread`
   - Primary Inputs: 3–5 years of Profit & Loss and Balance Sheet data.
   - Core Action: Extract and spread line items. Calculate key financial metrics: Tangible Net Worth (TNW), EBITDA, Debt Service Coverage Ratio (DSCR), EBIT/Interest, Gross Leverage, Gearing %, Current Ratio, and Working Capital Days.

3. `/commercial`
   - Primary Inputs: Sector name, management bios, customer/supplier notes, business model description.
   - Core Action: Draft Company History, Executive Management overview, Parent/UBO Support analysis, Sector Dynamics, Customer/Supplier Concentration, and Competitive Landscape.

4. `/collateral`
   - Primary Inputs: Asset Description, Valuation/Invoice Amount, Down Payment %, Loss Given Default (LGD) Grade & %, Residual Value (RV) %, Probability of Default (PD) Grade.
   - Core Action: Calculate Gross/Net Exposure, RV Exposure, Collateral Coverage %, LGD %, and Estimated Net Uncovered Risk.

5. `/assemble`
   - Primary Inputs: Aggregated outputs from steps `/triage` through `/collateral`.
   - Core Action: Synthesize all data into the target Markdown CAM template (Header Tables, Facility T&Cs, Spreading Table, Risk/Mitigant Matrix, and Final Recommendation), invoking `/review` before export.

6. `/review`
   - Primary Inputs: A drafted CAM (from `/assemble`, or pasted directly).
   - Core Action: Runs the Risk Reviewer ("Checker") agent — re-verifies ratio calculations, flags ungrounded assertions or missing sources, challenges weak mitigants, and returns a verdict of `APPROVED` or `REJECTED` with revision notes. This is the Maker-Checker loop's audit step made runnable; `/assemble` calls it automatically, but it can also be run standalone against any draft.

7. `/calibrate`
   - Primary Inputs: Sample CAM PDFs in `inputs/calibration_samples/`.
   - Core Action: Reads the samples directly (no API key needed), extracts writing style/tone into `config/style_guide.md`, and derives a generic CAM template into `templates/local/cam/<type>_cam.md` that overrides the shipped default for that deal type.

See [`.claude/commands/`](../.claude/commands/) for the runnable slash-command implementation of
each step above.