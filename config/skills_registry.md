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
   - Core Action: Synthesize all data into the target Markdown CAM template (Header Tables, Facility T&Cs, Spreading Table, Risk/Mitigant Matrix, and Final Recommendation).