# Credit Assessment Memorandum — Corporate Credit

| Company Name | Application / Reference No. | Purpose of Request | Total Proposed Credit Limit |
| :--- | :--- | :--- | :--- |
| [Borrower Legal Name] | [Internal Reference] | [New / Increase / Renewal / Amendment] | [Amount & Currency] |

| Sector | Business Founded (Year) | Company Registration No. | Business Activity |
| :--- | :--- | :--- | :--- |
| [Sector] | [Year] | [Registration Number] | [Description of core business activity] |

| Ownership | Relationship Manager / Team | Annual Review Date (Current) | Annual Review Date (Proposed) |
| :--- | :--- | :--- | :--- |
| [Private / Listed] | [Name or Team] | [Date] | [Date] |

---

## 1. Credit Limits & Approval Authority

| Facility # | Facility Type (e.g. RCF, Term Loan, Overdraft) | Currency | Existing Limit | Proposed Limit | Change | Current Exposure | Tenor | Estimated Uncovered Risk |
| :--- | :--- | :--- | ---: | ---: | ---: | ---: | :--- | ---: |
| 1 | | | | | | | | |
| **Total** | | | | | | | | |

## 2. Credit Ratings / Policy

| PD % | Public Rating (if any) | PD Grade | Credit Authority | Internal Score (e.g. bureau score) | Credit Policy Exceptions |
| :--- | :--- | :--- | :--- | :--- | :--- |
| | | | | | [Detail, or "N/A"] |

*Note the source and date of any external rating referenced (rating agency, upgrade/downgrade history) and how it maps to internal grading. Where no external rating or in-house model is available, the PD grade is a user-supplied input and must be labelled as such.*

## 3. Facility Terms & Conditions

| Condition | Detail |
| :--- | :--- |
| Repayment profile | [Bullet / Amortizing / Revolving] |
| Tenor | [months/years] |
| Pricing / margin | [Detail] |
| Security package | [e.g. Debenture, floating charge, guarantee — describe] |
| Corporate guarantees? | [Yes/No — from whom] |
| Personal guarantees? | [Yes/No] |
| Financial covenants | [e.g. Leverage, interest cover, minimum TNW] |
| Information / MI undertakings | [Detail] |
| Any non-standard terms? | [Detail, or "None"] |

## 4. Credit Analyst Recommendation

- **Verdict:** [Approve / Approve with conditions / Decline]
- [Key rationale point — e.g. rating change, payment history, group support]
- [Key rationale point]
- [Key rationale point]

**Credit Analyst:** [Name] **Date:** [Date]

---

## 5. Transaction Summary

**Source of Introduction:** [Broker / Direct relationship / Existing client]

**Purpose of Request**
- [Narrative — what is being requested, why, and how it relates to any prior facility]

**Transaction Structure**
- [Facility mechanics, drawdown/repayment mechanics, conditions precedent]

## 6. Company Overview & Group Structure

- [Company website, date/place of incorporation, core activity]
- [Parent company, ownership %, group structure]
- [Brief history relevant to credit assessment]

*Ground every statement in a verifiable source (company website, Companies House / equivalent registry, audited accounts, rating agency report). Never state a fact you cannot trace to a source.*

## 7. Management

- [Governance structure — Board / Executive Team composition]
- [Key individual — role, tenure, relevant track record, sourced from a public bio or company filing]

## 8. Industry

[Narrative: market structure, growth outlook, competitive dynamics — cite the source of any market-size or growth figures. Don't rely solely on the borrower's own strategic report/directors' report as the only source: actively seek independent third-party market context (e.g. an IBISWorld industry report for the borrower's sector, competitor information, general research) before finalizing. "Not assessed" is a last resort after a genuine attempt to source better data, not a first-pass shortcut — especially on a facility of meaningful size.]

| Concentration | Competition | Barriers to Entry | Substitutes | Buyer Power | Supplier Power |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [Low/Moderate/High] | [Low/Moderate/High + trend] | [Low/Moderate/High + trend] | [Low/Moderate/High + trend] | [Low/Moderate/High + trend] | [Low/Moderate/High + trend] |

## 9. Customers & Suppliers

- **Customers:** [Concentration, channel mix, contract terms]
- **Suppliers:** [Concentration, dependency, relationship terms]

## 10. Existing Funding Lines

| Lender | FY-4 | FY-3 | FY-2 | FY-1 | FY-Current | Grand Total |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| [Lender 1] | | | | | | |
| **Grand Total** | | | | | | |

## 11. Financial Analysis

Full line-by-line figures live in the accompanying spreading workbook (see `scripts/spreading_builder.py`). The summary table below is a floor, not a ceiling — for anything beyond a small/immaterial facility, this section should also cover: the full income statement (every P&L line, not just EBITDA/DSCR), the debt structure and funding sources (what kind of debt, who holds it, how the balance sheet is actually funded), dividend/distribution history with payout ratios, and working-capital component detail (stock/debtor/creditor days individually, not just the net cycle number) — expand with additional sub-tables/sub-sections as the deal size and complexity warrant.

| Metric | FY-2 | FY-1 | FY-Current |
| :--- | ---: | ---: | ---: |
| Revenue | | | |
| EBITDA | | | |
| Tangible Net Worth (TNW) | | | |
| DSCR | | | |
| Gross Leverage | | | |
| Gearing % | | | |
| Current Ratio | | | |
| Working Capital Cycle (days) | | | |

**Analyst commentary:** [Trend narrative on revenue, margin, leverage and liquidity — every figure must trace back to the audited accounts or management information used for the spreading.]

## 12. Credit Bureau / Legal Charges

- **Registered charges / mortgages:** [Detail, or "None identified"]
- **Credit bureau summary (e.g. CAIS-equivalent):** [Score, active accounts, delinquencies, judgments. This framework has no live bureau integration -- a bureau score (e.g. Experian) is normally a user-supplied input, so ask the user for it before finalizing this document rather than writing "N/A"/"Not provided" unasked. If the user confirms none is available or applicable, that's a valid, citable answer.]
- **External credit score (e.g. bureau-provided):** [Score, or "N/A" -- only after asking, per above]

## 13. Secondary Source of Repayment

[e.g. Sale of assets, parent/group guarantee, refinance — with rationale]

## 14. Risks & Mitigants

| Risk | Mitigant |
| :--- | :--- |
| [Risk] | [Mitigant — must be a concrete, enforceable policy condition, not a vague reassurance] |

---
*Every fact, figure and rating in this document must be traceable to a verified, credible source (audited financial statements, a recognized credit bureau or rating agency, company filings, or other primary documentation supplied by the user). Where no external score or system integration is available, the PD grade is a user-supplied input and must be labelled as such.*
