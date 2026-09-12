---
description: Extract and spread 3-5 years of P&L/Balance Sheet data into the core credit ratios (see config/skills_registry.md).
argument-hint: "[P&L and Balance Sheet figures, or a path to the source document]"
disable-model-invocation: true
---

## Task: /spread

Read `agents/underwriter_agent.md` and act according to that role for the rest of this task.

**Primary inputs** (ask the user for whatever's missing): 3-5 years of Profit & Loss and Balance
Sheet data.

$ARGUMENTS

Extract and spread the line items, then calculate: Tangible Net Worth (TNW), EBITDA, Debt
Service Coverage Ratio (DSCR), EBIT/Interest, Gross Leverage, Gearing %, Current Ratio, and
Working Capital Days. Show which raw line items each ratio comes from so it can be checked, and
never estimate a figure that isn't in the source data provided.
