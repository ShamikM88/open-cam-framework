# Credit Assessment Memorandum - Synthetic Co

- **Borrower:** Synthetic Co
- **Proposal:** Synthetic Fleet Loan
- **Facility:** Term loan, 5 years, 2,000 (synthetic units)

## 1. Executive Summary

Synthetic Co requests a term facility to refinance part of a vehicle fleet. Current-year earnings cover debt
service twice over (DSCR 2.0x) and gross leverage is 2.0x. The projections are weaker: debt service cover falls to
1.1x in FY+1 and EBITDA turns negative in FY+2, so the recommendation is conditional on the covenants and
conditions below. Every figure in this memorandum is a synthetic example.

## 2. Financial Analysis

| Metric | FY-Current |
| :--- | ---: |
| EBITDA | 1,000 |
| Tangible net worth | 2,200 |
| DSCR | 2.0x |
| Gross leverage | 2.0x |

Source: the borrower's FY-Current accounts as supplied (synthetic example).

## 3. Projections & Sensitivities

The FY+1 base case reaches gross leverage of 3.0x, inside the 3.5x covenant. Under the stress case (a 5% revenue
reduction and a 200 basis point rise in interest cost) FY+1 gross leverage rises to 5.16x, which breaches the 3.5x
maximum (breach DOWNSIDE-FY-1-GROSS-LEVERAGE). The proposed mitigant is a cash sweep that applies surplus cash to
the term loan whenever leverage exceeds 3.0x. The FY+1 base-case DSCR of 1.1x is below the 1.25x minimum, and the
FY+2 forecast shows negative EBITDA, so leverage cannot be tested for that year; both are for the committee to
weigh.

## 4. Conditions Precedent

- KYC / AML clearance on the borrower and its guarantor.
- Execution of the facility agreement.
- Execution of the corporate guarantee from Synthetic Parent Holdings.

## 5. Conditions Subsequent

- Monthly management information reporting.
- Annual certification of compliance with the financial covenants.
- Re-confirmation of the perfected charge over AST-001.
- Re-confirmation of the perfected charge over AST-002.
- Annual confirmation of the guarantor's continued standing.

```json
{
  "cp_ids_included": ["KYC-AML", "FACILITY-EXECUTION", "GUARANTEE-SYNTHETIC-PARENT-HOLDINGS"],
  "cs_ids_included": ["MI-REPORTING", "CS-COVENANT-COMPLIANCE", "CS-SEC-REPERFECT-AST-001",
                      "CS-SEC-REPERFECT-AST-002", "CS-GUARANTEE-SYNTHETIC-PARENT-HOLDINGS"],
  "risk_categories_covered": {
    "Market": {"status": "covered"}, "Refinance": {"status": "covered"}, "Operational": {"status": "covered"},
    "Concentration": {"status": "not_applicable", "justification": "Synthetic example: single-site operator."},
    "Key Man": {"status": "covered"}, "Financial": {"status": "covered"}, "Legal": {"status": "covered"}
  },
  "reported_figures": {
    "ebitda": 1000, "tangible_net_worth": 2200, "dscr": 2.0, "gross_leverage": 2.0,
    "gross_leverage_FY+1_downside": 5.15625
  },
  "downside_breaches_acknowledged": ["DOWNSIDE-FY-1-GROSS-LEVERAGE"],
  "sources": ["Synthetic Co FY-Current accounts (synthetic example)"]
}
```
