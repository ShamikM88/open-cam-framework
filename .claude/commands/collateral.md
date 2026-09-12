---
description: Calculate Gross/Net Exposure, RV Exposure, Collateral Coverage %, and Estimated Net Uncovered Risk (see config/skills_registry.md).
argument-hint: "[asset description] [valuation/invoice amount] [down payment %] [LGD grade & %] [RV %] [PD grade]"
disable-model-invocation: true
---

## Task: /collateral

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Primary inputs** (ask the user for whatever's missing): Asset Description, Valuation/Invoice
Amount, Down Payment %, Loss Given Default (LGD) Grade & %, Residual Value (RV) %, Probability of
Default (PD) Grade.

$ARGUMENTS

Calculate: Gross/Net Exposure, RV Exposure, Collateral Coverage %, LGD %, and Estimated Net
Uncovered Risk. Show the formula and inputs used for each figure. PD and LGD grades are
user-supplied inputs — this framework has no bureau/rating-agency integration — so use exactly
what's given; never invent or adjust a grade yourself.
