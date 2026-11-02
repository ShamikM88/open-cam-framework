# Formula register

Every metric the framework computes, on one reviewable page. The code is the source of truth; this page
describes it. An analyst signs off each entry here; the signed record itself lives **outside the repository**
(decided on [issue #366](https://github.com/ShamikM88/open-cam-framework/issues/366), comment 6259301815) —
this page records only the reviewer's role, the date and the commit, never a personal name.

How to keep this page true is a [design decision](decisions.md#d18-code-defines-the-register-describes): the
code defines, the register describes, and a formula change updates the register in the same pull request.
[tests/test_formula_register.py](../tests/test_formula_register.py) fails the build when an entry is missing, when an entry names a file or function that does not exist, and when a definition the register describes changes
without the recorded fingerprints being refreshed.

## Conventions used by every entry

- **Percent-type ratios are stored as fractions.** `0.87` means 87%. This applies to the ratio value and to any
  covenant threshold on it (`gearing`, `fcf_conversion_pct`). Amounts are stored in whatever currency units
  the deal supplies; the framework labels no currency.
- **A zero or negative denominator is N/A, not a number.** A ratio whose denominator is zero or negative is
  stored as `N/A`/`None` (issue #169), never as zero or a negative quotient. A covenant tested on an N/A ratio
  is **UNRESOLVABLE** — never PASS and never FAIL.
- **Non-finite inputs are refused.** NaN and infinity (and non-numeric values for recognised raw fields) are
  rejected with one error line before anything is written.
- **No rounding in storage.** Figures are stored at full precision; rounding is display-only.
- **Operating profit and EBITDA are before exceptional items and include other income.** This is a house
  convention (decision D-a of issue #504). Exceptional costs are deducted only in profit before tax
  ([F-007](#f-007--profit-before-tax)), never in operating profit ([F-004](#f-004--operating-profit-ebit)) or
  EBITDA ([F-006](#f-006--ebitda)), and other income is added in operating profit. Every ratio and covenant
  built on them is on that basis: operating margin (F-005), FCF and FCF conversion (F-009, F-010), DSCR (F-021),
  EBIT and EBITDA interest cover (F-022, F-023), gross leverage (F-026) and net debt / EBITDA (F-027).
- **Ids are stable.** `F-001`…`F-NNN` are never reused or renumbered; a withdrawn entry keeps its id with a
  status saying so, and a new entry takes the next number.

## Status forms

- **Implemented, not yet reviewed by an analyst** — the code computes it; no analyst has signed it off yet.
- **Reviewed (role, date, commit)** — an analyst signed it off; the signed copy is outside the repository.
- **Change requested (issue #N)** — an analyst asked for a change; the issue is the record.
- **Proposed, not implemented** — agreed design (conventions cited), no code yet.

## How to review

Review entry by entry, in id order. For each entry mark exactly one of **Agree**, **Change** or **Question**:

- **Agree** — the formula, units, edge rules, basis and implemented-in all match your understanding.
- **Change** — something is wrong; open an issue naming the entry id and say what must change. The entry's
  status becomes "Change requested (issue #N)".
- **Question** — you are not sure; ask in the tracking issue naming the entry id.

Sign-off is recorded **outside this repository**. When every implemented entry you reviewed is Agreed, update
each entry's status line here to `Reviewed (role, date, commit)` — role, date and the commit hash only; no
personal names appear anywhere on this page. If you cannot verify an entry's "Implemented in" reference, treat
it as a Question, not an Agree.

The entries below were written from the code, never from memory; where the code does not compute something,
the entry says so and it is listed under [Known differences](#known-differences).

---

## Inputs

### F-001 — Raw line items (inputs)

- **Name:** Raw line items — the deal's raw financial inputs per period; workbook rows use the labels in the
  "Workbook rows" field, CAM/state keys are the snake_case names in the "State keys" field
- **Formula:** Not computed — inputs. A missing key defaults to 0 (`g()` / `.get(field, 0)`); a `null` is the established spelling of an unavailable input. Periods: `FY-2`, `FY-1`, `FY-Current` (historical) and `FY+1`…`FY+3` (forward, [F-032](#f-032--forward-base-case))
- **Unit and storage:** currency amounts (or counts, for unit lines); full precision, no rounding in storage
- **Edge rules:** a recognised raw field that is not a finite JSON number or `null` is refused with one error
  line and nothing written; a period that is not an object of line items is refused; a key that is not one of
  the period labels above (for example an entity name such as `HoldCo`) is refused with one error line naming the
  key (the evaluator has no entity dimension, issue #48); non-finite numbers anywhere in the policy state are
  refused
- **State keys:** `revenue`, `cost_of_sales`, `admin_expenses`, `depreciation`, `amortisation`, `other_income`, `interest_paid`, `interest_received`, `scheduled_principal`, `capex`, `exceptional_costs`, `tax_paid`, `tangible_assets`, `intangible_assets`, `other_fixed_assets`, `cash`, `trade_debtors`, `stock`, `other_current_assets`, `trade_creditors`, `current_debt`, `overdraft`, `other_current_liabilities`, `long_term_debt`, `loan_notes`, `other_long_term_liabilities`, `provisions`, `share_capital`, `retained_profit`
- **Workbook rows:** Revenue · Cost of Goods Sold · Admin Expenses · Depreciation · Amortisation · Other Income · Interest Paid · Interest Received · Scheduled Principal Repayment · Capital Expenditure · Exceptional Costs / (Income) · Tax · Tangible Fixed Assets · Intangible Fixed Assets · Investments · Cash · Accounts Receivable · Stock · Other Current Assets · Accounts Payable · Current Portion - Debt · Overdraft / Revolving Debt · Other Current Liabilities · Long Term Debt · Loan Notes / Preference Shares · Other Long Term Liabilities · Provisions · Share Capital · Retained Profit
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (the `g()` default-0
  convention and `FIELD_LABELS` mapping); input validation in `scripts/numeric_validation.py` —
  `validate_raw_line_items()`, `validate_period_objects()` and `validate_period_keys()`;
  `validate_finite_numbers()` for the policy state
- **Basis:** house convention (default-0 inputs, null = unavailable)
- **Status:** Implemented, not yet reviewed by an analyst

## Statement subtotals and margins

### F-002 — Gross Profit

- **Name:** Gross Profit — workbook row "Gross Profit"; CAM/state key `gross_profit`
- **Formula:** gross_profit = revenue − cost_of_sales (workbook: Gross Profit = Revenue − Cost of Goods Sold)
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** `gross_profit`
- **Workbook rows:** Gross Profit
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `PNL_ROWS` (Profit & Loss)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-003 — Gross Profit Margin %

- **Name:** Gross Profit Margin % — workbook row "Gross Profit Margin %"; no state key (workbook-only: margins
  are not written to state.json)
- **Formula:** gross_profit_margin = gross_profit / revenue (workbook: Gross Profit Margin % = Gross Profit /
  Revenue)
- **Unit and storage:** ratio (x); workbook cell shows "N/A" outside the meaningful range; not stored in state
- **Edge rules:** revenue ≤ 0 → N/A (the workbook `_ratio` guard; the edge rules of [F-021](#f-021--dscr) apply to every ratio cell); no Python counterpart exists to store
- **State keys:** —
- **Workbook rows:** Gross Profit Margin %
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — the `_ratio()` workbook formula in `PNL_ROWS` only;
  there is no computed state key (see [Known differences](#known-differences), KD-1)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-004 — Operating Profit (EBIT)

- **Name:** Operating Profit / EBIT — workbook row "Operating Profit"; CAM/state key `operating_profit`
- **Formula:** operating_profit = gross_profit − admin_expenses − depreciation − amortisation + other_income (workbook: Operating Profit = Gross Profit − Admin Expenses − Depreciation − Amortisation + Other Income)
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** `operating_profit`
- **Workbook rows:** Operating Profit
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `PNL_ROWS` (Profit & Loss)
- **Basis:** house convention (decision D-a of issue #504): operating profit is **before exceptional items**,
  which are deducted only in profit before tax (F-007), and it **includes other income**; every ratio and
  covenant on operating profit is on that basis (see [Conventions](#conventions-used-by-every-entry)). The
  [financial model](financial-model.md#statement-figures) states EBIT on the same basis
- **Status:** Implemented, not yet reviewed by an analyst

### F-005 — Operating Margin %

- **Name:** Operating Margin % — workbook row "Operating Margin %"; no state key (workbook-only)
- **Formula:** operating_margin = operating_profit / revenue (workbook: Operating Margin % = Operating Profit / Revenue)
- **Unit and storage:** ratio (x); not stored in state
- **Edge rules:** revenue ≤ 0 → N/A (workbook `_ratio` guard); no Python counterpart exists to store
- **State keys:** —
- **Workbook rows:** Operating Margin %
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — the `_ratio()` workbook formula in `PNL_ROWS` only;
  there is no computed state key (see [Known differences](#known-differences), KD-1)
- **Basis:** house convention (operating profit is before exceptional items and includes other income, decision D-a of
  issue #504; see the Conventions section)
- **Status:** Implemented, not yet reviewed by an analyst

### F-006 — EBITDA

- **Name:** EBITDA — workbook row "EBITDA (before exceptionals, incl. other income)"; CAM/state key `ebitda`
- **Formula:** ebitda = operating_profit + depreciation + amortisation (workbook: EBITDA (before exceptionals, incl. other income) = Operating Profit + Depreciation + Amortisation)
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** may be zero or negative (a negative EBITDA makes every ratio over it N/A — see F-026)
- **State keys:** `ebitda`
- **Workbook rows:** EBITDA (before exceptionals, incl. other income)
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `PNL_ROWS` (Profit & Loss)
- **Basis:** house convention (decision D-a of issue #504): EBITDA is operating profit (F-004) plus
  depreciation and amortisation, so it is **before exceptional items**, which are deducted only in profit before
  tax (F-007), and it **includes other income**; every EBITDA-based ratio and covenant is on that basis (see
  [Conventions](#conventions-used-by-every-entry)). The [financial model](financial-model.md#statement-figures)
  states EBITDA on the same basis
- **Status:** Implemented, not yet reviewed by an analyst

### F-007 — Profit Before Tax

- **Name:** Profit Before Tax — workbook row "Profit Before Tax"; CAM/state key `profit_before_tax`
- **Formula:** profit_before_tax = operating_profit − interest_paid + interest_received − exceptional_costs (workbook: Profit Before Tax = Operating Profit − Interest Paid + Interest Received − Exceptional Costs / (Income))
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** `profit_before_tax`
- **Workbook rows:** Profit Before Tax
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `PNL_ROWS` (Profit & Loss)
- **Basis:** house convention (decision D-a of issue #504): exceptional costs are deducted here, at profit
  before tax, and nowhere above it; operating profit and EBITDA (F-004, F-006) are before exceptional items and
  include other income (see [Conventions](#conventions-used-by-every-entry)); net profit (F-008) inherits the
  deduction through profit before tax
- **Status:** Implemented, not yet reviewed by an analyst

### F-008 — Net Profit

- **Name:** Net Profit — workbook row "Net Profit"; CAM/state key `net_profit`
- **Formula:** net_profit = profit_before_tax − tax_paid (workbook: Net Profit = Profit Before Tax − Tax)
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** `net_profit`
- **Workbook rows:** Net Profit
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `PNL_ROWS` (Profit & Loss)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-009 — Free Cash Flow (FCF)

- **Name:** Free Cash Flow before working capital — workbook row "FCF (before working capital)"; CAM/state key `fcf` (the stored key
  keeps its name)
- **Formula:** fcf = ebitda − capex − tax_paid − interest_paid + interest_received (workbook: FCF (before working capital) = EBITDA (before exceptionals, incl. other income) − Capital Expenditure − Tax − Interest Paid + Interest Received). Scheduled principal repayment is deliberately NOT deducted: it is financing, and DSCR (F-021) already covers debt service
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** may be negative; no denominator
- **State keys:** `fcf`
- **Workbook rows:** FCF (before working capital)
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `RATIO_ROWS` (Key Ratios)
- **Basis:** house convention (decision D-b of issue #504): FCF **before working capital**, so a change in
  trade debtors, stock or trade creditors does not move it; scheduled principal is deliberately excluded (DSCR,
  F-021, covers debt service); EBITDA is before exceptional items and includes other income (F-006). A
  working-capital-adjusted measure is a separate follow-up (issue #520); `fcf` and any covenant on it are
  unchanged. See the [financial model](financial-model.md#statement-figures)
- **Status:** Implemented, not yet reviewed by an analyst

### F-010 — FCF Conversion %

- **Name:** FCF Conversion, on FCF before working capital (F-009) — workbook row "FCF Conversion % (before working capital)"; CAM/state
  key `fcf_conversion_pct`
- **Formula:** fcf_conversion_pct = fcf / ebitda (workbook: FCF Conversion % (before working capital) = FCF (before working capital) / EBITDA (before exceptionals, incl. other income))
- **Unit and storage:** **percent-type ratio stored as a fraction** (`0.35` = 35%); applies to the value and to
  any covenant threshold on it; full precision, no rounding in storage
- **Edge rules:** ebitda ≤ 0 → N/A (`None` in state, "N/A" in the workbook) and any covenant on it is
  UNRESOLVABLE; a stored value or threshold with absolute magnitude > 10 is treated as a likely unit mismatch —
  the covenant engine refuses to PASS/FAIL it (see F-039)
- **State keys:** `fcf_conversion_pct`
- **Workbook rows:** FCF Conversion % (before working capital)
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div`);
  workbook formula in `RATIO_ROWS` (Key Ratios); unit guard in `scripts/policy_engine.py` — `_evaluate_covenant()`
  and `scripts/financial_conventions.py` — `warn_percent_type_values()`
- **Basis:** house convention (percent-type fraction storage, issue #357); the numerator is FCF before working
  capital (F-009, decision D-b of issue #504), so the ratio does not reflect cash absorbed by working capital
- **Status:** Implemented, not yet reviewed by an analyst

### F-011 — Total Fixed Assets

- **Name:** Total Fixed Assets — workbook row "Total Fixed Assets"; no state key (intermediate only: it feeds
  `total_assets`, which is the stored subtotal)
- **Formula:** total_fixed_assets = tangible_assets + intangible_assets + other_fixed_assets (workbook:
  Total Fixed Assets = Tangible Fixed Assets + Intangible Fixed Assets + Investments)
- **Unit and storage:** currency amount; not itself stored in the financials dict
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** —
- **Workbook rows:** Total Fixed Assets
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (intermediate) and the
  workbook formula in `BALANCE_SHEET_ROWS` (Balance Sheet)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-012 — Total Current Assets

- **Name:** Total Current Assets — workbook row "Total Current Assets"; CAM/state key `current_assets` (note
  the state key drops the "total_" prefix)
- **Formula:** current_assets = cash + trade_debtors + stock + other_current_assets
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** `current_assets`
- **Workbook rows:** Total Current Assets
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `BALANCE_SHEET_ROWS` (Balance Sheet)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-013 — Total Assets

- **Name:** Total Assets — workbook row "Total Assets"; CAM/state key `total_assets`
- **Formula:** total_assets = total_fixed_assets + current_assets (workbook: Total Assets = Total Fixed Assets + Total Current Assets; see F-011, F-012)
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** `total_assets`
- **Workbook rows:** Total Assets
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `BALANCE_SHEET_ROWS` (Balance Sheet)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-014 — Total Current Liabilities

- **Name:** Total Current Liabilities — workbook row "Total Current Liabilities"; CAM/state key
  `current_liabilities` (state key drops the "total_" prefix)
- **Formula:** current_liabilities = trade_creditors + current_debt + overdraft + other_current_liabilities (workbook: Total Current Liabilities = Accounts Payable + Current Portion - Debt + Overdraft / Revolving Debt + Other Current Liabilities)
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator); it is the denominator of the current
  ratio (F-024), where the zero/negative rule bites
- **State keys:** `current_liabilities`
- **Workbook rows:** Total Current Liabilities
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `BALANCE_SHEET_ROWS` (Balance Sheet)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-015 — Total Long Term Liabilities

- **Name:** Total Long Term Liabilities — workbook row "Total Long Term Liabilities"; no direct state key (the
  stored aggregate is `total_liabilities`, F-016)
- **Formula:** long_term_liabilities = long_term_debt + loan_notes + other_long_term_liabilities + provisions (workbook: Total Long Term Liabilities = Long Term Debt + Loan Notes / Preference Shares + Other Long Term Liabilities + Provisions)
- **Unit and storage:** currency amount; not itself stored in the financials dict
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** —
- **Workbook rows:** Total Long Term Liabilities
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (intermediate) and the
  workbook formula in `BALANCE_SHEET_ROWS` (Balance Sheet)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-016 — Total Liabilities

- **Name:** Total Liabilities — workbook row "Total Liabilities"; CAM/state key `total_liabilities`
- **Formula:** total_liabilities = current_liabilities + long_term_liabilities (workbook: Total Liabilities = Total Current Liabilities + Total Long Term Liabilities; F-014, F-015)
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** `total_liabilities`
- **Workbook rows:** Total Liabilities
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `BALANCE_SHEET_ROWS` (Balance Sheet)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-017 — Total Equity

- **Name:** Total Equity — workbook row "Total Equity"; CAM/state key `total_equity`
- **Formula:** total_equity = share_capital + retained_profit (workbook: Total Equity = Share Capital + Retained Profit)
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** may be zero or negative (a non-positive equity makes gearing N/A — see F-025)
- **State keys:** `total_equity`
- **Workbook rows:** Total Equity
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `BALANCE_SHEET_ROWS` (Balance Sheet)
- **Basis:** house convention (decision D-e of issue #504): equity has two input lines only. **Retained Profit
  (`retained_profit`) includes all reserves and share premium**; there is no separate line for share premium or
  other reserves. Total equity is therefore share capital plus retained profit
- **Status:** Implemented, not yet reviewed by an analyst

### F-018 — Tangible Net Worth (TNW)

- **Name:** Tangible Net Worth — workbook row "Tangible Net Worth (TNW)"; CAM/state key `tangible_net_worth`
- **Formula:** tangible_net_worth = total_equity − intangible_assets (workbook: Tangible Net Worth (TNW) = Total Equity − Intangible Fixed Assets)
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator); may be negative
- **State keys:** `tangible_net_worth`
- **Workbook rows:** Tangible Net Worth (TNW)
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `KEY_METRIC_ROWS` (Key Credit Metrics)
- **Basis:** house convention: total equity less intangible assets, where total equity is share capital plus
  a Retained Profit line that includes all reserves and share premium (F-017, decision D-e of issue #504)
- **Status:** Implemented, not yet reviewed by an analyst

### F-019 — TNW + Loan Notes / Preference Shares

- **Name:** TNW plus Loan Notes / Preference Shares — workbook row "TNW + Loan Notes / Preference Shares"; no
  state key (workbook-only memo subtotal)
- **Formula:** tnw_plus_loan_notes = tangible_net_worth + loan_notes (workbook: TNW + Loan Notes / Preference Shares = Tangible Net Worth (TNW) + Loan Notes / Preference Shares)
- **Unit and storage:** currency amount; not stored in state
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** —
- **Workbook rows:** TNW + Loan Notes / Preference Shares
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — the `KEY_METRIC_ROWS` formula, written to the sheet by
  `_write_financial_spreading()`; there is no computed state key
- **Basis:** house convention (a credit-agency-style tangible-equity adjustment; no external reference verified).
  Eligibility rule (decision D-f of issue #504): the combined Loan Notes / Preference Shares line counts **fully
  as interest-bearing debt** in total debt (F-020), whatever the instrument's terms. This row is a
  **workbook-only memo**: it has no state key, and no ratio or covenant uses it alongside F-020 (no other
  workbook formula refers to this row, and a covenant tests only a key of the period's stored ratios)
- **Status:** Implemented, not yet reviewed by an analyst

### F-020 — Total Debt (interest-bearing)

- **Name:** Total Debt — no workbook row of its own (it is the numerator of F-025, F-026, F-027); CAM/state
  key `total_debt`
- **Formula:** total_debt = current_debt ("Current Portion - Debt") + overdraft ("Overdraft / Revolving Debt") + long_term_debt ("Long Term Debt") + loan_notes ("Loan Notes / Preference Shares") (workbook: no row of its own — the same aggregate is written inline in the Gearing % / Gross Leverage / Net Debt / EBITDA formulas). Provisions and trade creditors are deliberately excluded
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** none beyond the input rules of F-001 (no denominator)
- **State keys:** `total_debt`
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; the same aggregate is
  written inline in the `KEY_METRIC_ROWS` ratio formulas
- **Basis:** house convention (interest-bearing debt). Eligibility rule (decision D-f of issue #504): the
  combined Loan Notes / Preference Shares line counts **fully** as interest-bearing debt, whatever the
  instrument's terms; no part of it is treated as equity in any ratio. Its quasi-equity view appears only in the
  workbook-only memo F-019, which no ratio or covenant uses alongside this figure
- **Status:** Implemented, not yet reviewed by an analyst

## Coverage and leverage ratios

### F-021 — DSCR

- **Name:** DSCR (debt service coverage ratio), EBITDA-based — workbook row "DSCR (EBITDA-based)"; CAM/state key `dscr`
- **Formula:** dscr = ebitda / (interest_paid + scheduled_principal) (workbook: DSCR (EBITDA-based) = EBITDA (before exceptionals, incl. other income) / (Interest Paid + Scheduled Principal Repayment))
- **Unit and storage:** ratio (x); full precision, no rounding in storage
- **Edge rules:** denominator ≤ 0 → N/A (`None`) and any covenant on DSCR is UNRESOLVABLE, with the reason
  naming the denominator (e.g. "dscr not meaningful: interest paid plus scheduled principal is zero or
  negative")
- **State keys:** `dscr`
- **Workbook rows:** DSCR (EBITDA-based)
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div`),
  `describe_undefined_ratio()` for the UNRESOLVABLE reason; workbook formula in `RATIO_ROWS` (Key Ratios)
- **Basis:** house convention (decision D-d of issue #504): **EBITDA-based** DSCR, EBITDA over interest paid
  plus scheduled principal, not cash flow available for debt service; EBITDA is before exceptional items and
  includes other income (F-006). A cash-flow-based DSCR is a follow-up metric (issue #521)
- **Status:** Implemented, not yet reviewed by an analyst

### F-022 — EBIT/Interest

- **Name:** EBIT interest cover — workbook row "EBIT/Interest"; CAM/state key `ebit_interest_cover` with the
  alias `EBIT/Interest` (both keys carry the same value)
- **Formula:** ebit_interest_cover = operating_profit / interest_paid (workbook: EBIT/Interest = Operating Profit / Interest Paid)
- **Unit and storage:** ratio (x); full precision, no rounding in storage
- **Edge rules:** interest_paid ≤ 0 → N/A (`None`) and any covenant on it is UNRESOLVABLE
- **State keys:** `ebit_interest_cover`, `EBIT/Interest`
- **Workbook rows:** EBIT/Interest
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div`);
  workbook formula in `RATIO_ROWS` (Key Ratios)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-023 — EBITDA/Interest

- **Name:** EBITDA interest cover — workbook row "EBITDA/Interest"; CAM/state key `ebitda_interest_cover` with
  the alias `EBITDA/Interest`
- **Formula:** ebitda_interest_cover = ebitda / interest_paid (workbook: EBITDA/Interest = EBITDA / Interest Paid)
- **Unit and storage:** ratio (x); full precision, no rounding in storage
- **Edge rules:** interest_paid ≤ 0 → N/A (`None`) and any covenant on it is UNRESOLVABLE
- **State keys:** `ebitda_interest_cover`, `EBITDA/Interest`
- **Workbook rows:** EBITDA/Interest
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div`);
  workbook formula in `RATIO_ROWS` (Key Ratios)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-024 — Current Ratio

- **Name:** Current Ratio — workbook row "Current Ratio"; CAM/state key `current_ratio`
- **Formula:** current_ratio = current_assets / current_liabilities (workbook: Current Ratio = Total Current Assets / Total Current Liabilities)
- **Unit and storage:** ratio (x); full precision, no rounding in storage
- **Edge rules:** denominator ≤ 0 → N/A (`None`) and any covenant on it is UNRESOLVABLE
- **State keys:** `current_ratio`
- **Workbook rows:** Current Ratio
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div`);
  workbook formula in `KEY_METRIC_ROWS` (Key Credit Metrics)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-025 — Gearing %

- **Name:** Gearing — workbook row "Gearing % (Interest-Bearing Debt / Equity)"; CAM/state key `gearing`
- **Formula:** gearing = total_debt / total_equity (workbook: Gearing % = (Current Portion - Debt + Overdraft / Revolving Debt + Long Term Debt + Loan Notes / Preference Shares) / Total Equity)
- **Unit and storage:** **percent-type ratio stored as a fraction** (`0.87` = 87%); applies to the value and to
  any covenant threshold on it; full precision, no rounding in storage
- **Edge rules:** total_equity ≤ 0 → N/A (`None`) and any covenant on gearing is UNRESOLVABLE; a stored value
  or threshold with absolute magnitude > 10 is treated as a likely unit mismatch — the covenant engine refuses
  to PASS/FAIL it (see F-039)
- **State keys:** `gearing`
- **Workbook rows:** Gearing % (Interest-Bearing Debt / Equity)
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div`);
  workbook formula in `KEY_METRIC_ROWS` (Key Credit Metrics); unit guard in `scripts/policy_engine.py` —
  `_evaluate_covenant()` and `scripts/financial_conventions.py` — `warn_percent_type_values()`
- **Basis:** house convention (percent-type fraction storage, issue #357). Total debt includes the whole loan-notes line
  (D-f of issue #504, see F-020) and equity is on the D-e basis (Retained Profit includes all reserves, see F-017)
- **Status:** Implemented, not yet reviewed by an analyst

### F-026 — Gross Leverage

- **Name:** Gross Leverage — workbook row "Gross Leverage"; CAM/state key `gross_leverage`
- **Formula:** gross_leverage = total_debt / ebitda (workbook: Gross Leverage = (Current Portion - Debt + Overdraft / Revolving Debt + Long Term Debt + Loan Notes / Preference Shares) / EBITDA)
- **Unit and storage:** ratio (x); full precision, no rounding in storage
- **Edge rules:** ebitda ≤ 0 → N/A (`None`) and any covenant on it is UNRESOLVABLE, with the reason naming
  EBITDA (e.g. "gross leverage not meaningful: EBITDA is negative")
- **State keys:** `gross_leverage`
- **Workbook rows:** Gross Leverage
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div`),
  `describe_undefined_ratio()`; workbook formula in `KEY_METRIC_ROWS` (Key Credit Metrics)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-027 — Net Debt / EBITDA

- **Name:** Net Debt / EBITDA — workbook row "Net Debt / EBITDA"; CAM/state key `net_debt_to_ebitda`
- **Formula:** net_debt_to_ebitda = (total_debt − cash) / ebitda (workbook: Net Debt / EBITDA = (Current Portion - Debt + Overdraft / Revolving Debt + Long Term Debt + Loan Notes / Preference Shares − Cash) / EBITDA); negative when cash exceeds debt (net cash)
- **Unit and storage:** ratio (x); full precision, no rounding in storage
- **Edge rules:** ebitda ≤ 0 → N/A (`None`) and any covenant on it is UNRESOLVABLE
- **State keys:** `net_debt_to_ebitda`
- **Workbook rows:** Net Debt / EBITDA
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div`);
  workbook formula in `KEY_METRIC_ROWS` (Key Credit Metrics)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

## Working capital days

### F-028 — Trade Debtor Days

- **Name:** Trade Debtor Days — workbook row "Trade Debtor Days"; CAM/state key `trade_debtor_days`
- **Formula:** trade_debtor_days = trade_debtors / revenue × 365 (workbook: Trade Debtor Days = Accounts Receivable / Revenue × 365)
- **Unit and storage:** days; full precision, no rounding in storage
- **Edge rules:** denominator ≤ 0 → N/A (`None`)
- **State keys:** `trade_debtor_days`
- **Workbook rows:** Trade Debtor Days
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div_days`);
  workbook formula in `WORKING_CAPITAL_ROWS` (Working Capital)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-029 — Trade Creditor Days

- **Name:** Trade Creditor Days — workbook row "Trade Creditor Days"; CAM/state key `trade_creditor_days`
- **Formula:** trade_creditor_days = trade_creditors / cost_of_sales × 365 (workbook: Trade Creditor Days = Accounts Payable / Cost of Goods Sold × 365)
- **Unit and storage:** days; full precision, no rounding in storage
- **Edge rules:** denominator ≤ 0 → N/A (`None`)
- **State keys:** `trade_creditor_days`
- **Workbook rows:** Trade Creditor Days
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div_days`);
  workbook formula in `WORKING_CAPITAL_ROWS` (Working Capital)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-030 — Stock Days

- **Name:** Stock Days — workbook row "Stock Days"; CAM/state key `stock_days`
- **Formula:** stock_days = stock / cost_of_sales × 365 (workbook: Stock Days = Stock / Cost of Goods Sold × 365)
- **Unit and storage:** days; full precision, no rounding in storage
- **Edge rules:** denominator ≤ 0 → N/A (`None`)
- **State keys:** `stock_days`
- **Workbook rows:** Stock Days
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via `safe_div_days`);
  workbook formula in `WORKING_CAPITAL_ROWS` (Working Capital)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-031 — Working Capital Cycle

- **Name:** Working Capital Cycle — workbook row "Working Capital Cycle (days)"; CAM/state key
  `working_capital_cycle_days`
- **Formula:** working_capital_cycle_days = trade_debtor_days + stock_days − trade_creditor_days (workbook: Working Capital Cycle (days) = Trade Debtor Days + Stock Days − Trade Creditor Days; F-028 + F-030 − F-029)
- **Unit and storage:** days; full precision, no rounding in storage
- **Edge rules:** if any component day count is N/A, the cycle is N/A (`None`) — never a partial sum
- **State keys:** `working_capital_cycle_days`
- **Workbook rows:** Working Capital Cycle (days)
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()`; workbook formula in
  `WORKING_CAPITAL_ROWS` (Working Capital)
- **Basis:** standard definition
- **Status:** Implemented, not yet reviewed by an analyst

### F-064 — FCF after Working Capital

- **Name:** FCF after Working Capital — FCF less the change in operating working capital; CAM/state key
  `fcf_after_working_capital`. The operating working capital LEVEL is deliberately not stored as its own
  state key or workbook row (decision on #520, comment 6555374790) — only this result is
- **Formula:** fcf_after_working_capital = fcf − (OWC − OWC_prior), where OWC = trade_debtors + stock +
  other_current_assets − trade_creditors − other_current_liabilities (workbook: FCF after Working Capital =
  FCF (before working capital) − ((Accounts Receivable + Stock + Other Current Assets − Accounts Payable −
  Other Current Liabilities) − (the same five rows in the prior period's column)))
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** the earliest supplied period, any period whose immediately preceding period key is not
  present in the supplied data, and any period whose prior period records none of the five OWC lines
  (present, non-null), is N/A (`None` in state, "N/A" in the workbook) — never zero: a fabricated
  prior OWC would make the delta a fabricated number. An omitted OWC line reads as 0 (the file-wide
  omitted-defaults rule), so an omitted line is not N/A. The downside case's delta equals the base case's
  delta (the shock transforms a period's own figures, never its prior-period comparison). No conversion
  ratio and no covenant on this metric in this change (explicitly deferred on #520)
- **State keys:** `fcf_after_working_capital`
- **Workbook rows:** FCF after Working Capital
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` (via
  `_prior_operating_working_capital()`), `evaluate_downside_case()`; workbook formula in
  `WORKING_CAPITAL_ROWS` (Working Capital); `SUPPLIED_SUBTOTALS`/`SUPPLIED_ROW_LABELS`;
  `scripts/supplied_forecast.py` — `SUBTOTAL_DEPENDS`; `scripts/transcription_check.py` —
  `SUBTOTAL_FIELDS` (reported as not_assessed by `cross_foot()`: it also reads the prior period's
  working capital, which is never on the same transcription)
- **Basis:** standard definition (issue #520 design note, comment 6536111534)
- **Status:** Proposed, not implemented — this PR adds the implementation; the entry stays Proposed until the
  maintainer reviews it (per the decision on #520), then flips to "Implemented, not yet reviewed by an analyst"

## Forward base case

### F-032 — Forward base case

- **Name:** Forward base case — the FY+1…FY+3 columns of every workbook row and the `FY+1`…`FY+3` entries of
  the state's `financials`/`ratios`
- **Formula:** No construction formula: the framework extrapolates nothing. Forward periods are raw line items
  (F-001) supplied by the analyst or a forecast, evaluated by exactly the same subtotal and ratio formulas
  (F-002…F-031) as the historical periods. The default forward periods are `FY+1`, `FY+2`, `FY+3`
- **Unit and storage:** as each underlying entry; a forward period is simply absent if not supplied
- **Edge rules:** a missing forward period is not an error — nothing is computed for it and every covenant
  list says nothing about it (F-041). An analyst-supplied forecast is never extrapolated by the framework;
  its downside is the analyst's own stressed scenario where given, else the framework's shocks where they
  genuinely apply, else an explicit `unavailable` record (see F-036)
- **State keys:** —
- **Workbook rows:** the FY+1…FY+3 columns of every section row; downside columns are headed
  "{period} (Downside)"
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_financial_model()` over all supplied periods,
  `DEFAULT_FORWARD_PERIODS`, `_column_headers()`; workbook written by `_write_financial_spreading()`
- **Basis:** house convention (the framework never invents projections)
- **Status:** Implemented, not yet reviewed by an analyst

## Downside stress shocks

### F-033 — Revenue haircut shock

- **Name:** Revenue haircut — stress input `revenue_haircut_pct` (percent, whole-percent scale)
- **Formula:** shocked_revenue = revenue × (1 − revenue_haircut_pct / 100) — revenue is the state key for the workbook's Revenue row (workbook: the (Downside) column of the Revenue row). The haircut changes only revenue: other requested shocks may also change admin expenses and interest paid (F-034, F-035); all remaining raw fields are preserved
- **Unit and storage:** the shocked revenue replaces `revenue` in the stressed copy only (the base case is
  never mutated)
- **Edge rules:** non-negative and < 100 (a haircut of 100 or more would flip revenue negative or to zero);
  non-finite or non-numeric refused; a value strictly between 0 and 1 is applied as written with a warning
  that it is not a whole-percent value
- **State keys:** `revenue_haircut_pct`
- **Workbook rows:** the "(Downside)" columns of the Profit & Loss rows
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `apply_stress_shocks()`; validated by
  `scripts/numeric_validation.py` — `validate_stress_assumptions()`
- **Basis:** house convention (agreed stress design)
- **Status:** Implemented, not yet reviewed by an analyst

### F-034 — Opex increase shock

- **Name:** Operating expense increase — stress input `opex_increase_pct` (percent, whole-percent scale)
- **Formula:** shocked_admin_expenses = admin_expenses × (1 + opex_increase_pct / 100) — admin_expenses is the state key for the workbook's Admin Expenses row (workbook: the (Downside) column of the Admin Expenses row). Admin expenses only: cost of sales is never stressed
- **Unit and storage:** the shocked admin expenses replace `admin_expenses` in the stressed copy only
- **Edge rules:** non-negative; non-finite or non-numeric refused; a value strictly between 0 and 1 is applied
  as written with a warning
- **State keys:** `opex_increase_pct`
- **Workbook rows:** the "(Downside)" columns of the Profit & Loss rows
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `apply_stress_shocks()`; validated by
  `scripts/numeric_validation.py` — `validate_stress_assumptions()`
- **Basis:** house convention (agreed stress design)
- **Status:** Implemented, not yet reviewed by an analyst

### F-035 — Interest rate bump shock

- **Name:** Interest rate bump — stress input `interest_rate_bump_bps` (basis points)
- **Formula:** shocked_interest_paid = interest_paid ("Interest Paid") + (current_debt ("Current Portion - Debt") + overdraft ("Overdraft / Revolving Debt") + long_term_debt ("Long Term Debt") + loan_notes ("Loan Notes / Preference Shares")) × interest_rate_bump_bps / 10000 — i.e. the period's interest-bearing debt (F-020) repriced by the bump (workbook: the (Downside) column of the Interest Paid row)
- **Unit and storage:** the shocked interest paid replaces `interest_paid` in the stressed copy only
- **Edge rules:** non-negative; non-finite or non-numeric refused
- **State keys:** `interest_rate_bump_bps`
- **Workbook rows:** the "(Downside)" columns of the Profit & Loss rows
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `apply_stress_shocks()`; validated by
  `scripts/numeric_validation.py` — `validate_stress_assumptions()`
- **Basis:** house convention (agreed stress design)
- **Status:** Implemented, not yet reviewed by an analyst

### F-036 — Downside case evaluation

- **Name:** Downside case — the state's `downside_case` (`financials`, `ratios`, and `unavailable`)
- **Formula:** for each forward period present in the base case, apply the three shocks (F-033, F-034, F-035)
  to that period's raw lines and re-run the whole model (F-002…F-031) on the shocked inputs. Nothing is
  stressed in historical periods
- **Unit and storage:** a full parallel set of `financials` and `ratios` per stressed forward period; stored
  under `downside_case`, never over the base case
- **Edge rules:** an input key that is not one of the six framework period labels is refused with one error, not
  dropped (issue #48); then two paths. Framework-computed forward years: the requested shocks are applied to the raw
  lines as given and the model is re-run — this path does not perform the supplied-forecast reconciliation
  check. Analyst-supplied forecasts: the analyst's own stressed scenario is used where given; otherwise the
  framework's shocks apply only where they genuinely meet their assumptions — the analyst gave that year's
  raw lines, every line a requested shock acts on, every line the covenant ratios read (an omitted line
  would read as a zero the analyst never confirmed), and the framework's formulas reproduce the subtotals
  and ratios the analyst gave (within `RECONCILE_TOLERANCE` = 0.005, the code's two-decimal-place
  tolerance). Where neither an analyst-supplied downside scenario nor an eligible framework-derived downside
  is available for a requested year, the year is recorded under `unavailable` with the reason, and a
  covenant that PASSes there in the base case is recorded UNRESOLVABLE, never silently absent (issue #124)
- **State keys:** —
- **Workbook rows:** the "(Downside)" columns
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `evaluate_downside_case()` and
  `apply_stress_shocks()`; the per-year choice between the analyst's scenario, framework-derived shocks and
  an `unavailable` record is made by `scripts/supplied_forecast.py` — `build_downside()`; workbook downside
  columns written by `_write_financial_spreading()`
- **Basis:** house convention (agreed stress design)
- **Status:** Implemented, not yet reviewed by an analyst

### F-037 — Stress assumption validation

- **Name:** Stress assumption validation — the guard on the three shock inputs before any stress is applied
- **Formula:** rules, not arithmetic: each of `revenue_haircut_pct`, `opex_increase_pct`,
  `interest_rate_bump_bps` must be a finite JSON number; all three must be non-negative;
  `revenue_haircut_pct` must be < 100
- **Unit and storage:** no stored figure — refusal or warning only
- **Edge rules:** a violation raises one error line and nothing is written (warnings are emitted only after
  all errors have been checked); a percent-type value strictly between 0 and 1 warns "applied as written, not
  treated as a whole-percent value"
- **State keys:** `revenue_haircut_pct`, `opex_increase_pct`, `interest_rate_bump_bps`
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/numeric_validation.py` — `validate_stress_assumptions()`
- **Basis:** house convention (agreed stress design)
- **Status:** Implemented, not yet reviewed by an analyst

## Covenant evaluation

### F-038 — Covenant test

- **Name:** Covenant test — the per-covenant PASS/FAIL/UNRESOLVABLE evaluation
- **Formula:** minimum covenant: PASS iff actual ≥ threshold; headroom_pct = (actual − threshold) / |threshold|.
  maximum covenant: PASS iff actual ≤ threshold; headroom_pct = (threshold − actual) / |threshold|. Equality
  passes for both directions; the headroom is positive when the covenant passes and negative when it fails for
  any non-zero threshold, whatever its sign
- **Unit and storage:** status string plus `headroom_pct` as a fraction of the threshold's absolute value; actual/threshold kept
  at full precision
- **Edge rules:** an N/A ratio (zero/negative denominator, the rule of [F-021](#f-021--dscr)) makes the covenant UNRESOLVABLE —
  never PASS, never FAIL — with a one-sentence reason naming the metric and denominator; an unrecognized
  metric, an unrecognized type (not "minimum"/"maximum"), a missing or non-numeric threshold, or a
  non-numeric actual is UNRESOLVABLE, never silently skipped; a threshold of exactly 0 leaves headroom_pct
  `None` (undefined, not zero) but the status is still decided by direct comparison; a RECORDED value next to a
  zero/negative denominator is ignored — the ratio is N/A whatever number is stored (issue #169). A covenant is
  tested only on its own `basis` (`borrower`, `consolidated` or `guarantor_group`; absent or null means
  `borrower`): the engine states which one view the recorded ratios represent (`consolidated` when the state
  records a `group_basis`, otherwise `borrower`), and a covenant on any other basis, or on a basis that is not one
  of the three, is UNRESOLVABLE with a one-sentence reason — never tested against the figures that happen to be
  recorded (issue #48). A result names its `basis` when the covenant stated one or the recorded view is not the
  borrower's; an ordinary deal's results keep their earlier shape
- **State keys:** `covenants[].basis`, `group_basis`
- **Workbook rows:** —
- **Policy outputs:** `covenant_results`
- **Implemented in:** `scripts/policy_engine.py` — `_evaluate_covenant()` (called by
  `evaluate_deal_policy()`, which hands each period's financials to the reason sentence via `_period_dict()`)
- **Basis:** house convention (issue #169 equality and N/A rules)
- **Status:** Implemented, not yet reviewed by an analyst

### F-039 — Percent-type unit guard

- **Name:** Percent-type unit guard — the unit check on `gearing` and `fcf_conversion_pct` before any covenant
  comparison
- **Formula:** if the absolute value of the recorded actual ratio or of the covenant threshold exceeds 10 on a
  percent-type metric, the covenant is UNRESOLVABLE with a reason asking for the unit to be confirmed (a
  value that looks like 87 rather than 0.87)
- **Unit and storage:** no stored figure — refusal only; the existing N/A rules take precedence over this guard
- **Edge rules:** advisory at input (one aggregated stderr warning, value retained as supplied) and blocking
  at covenant time (UNRESOLVABLE, never compared)
- **State keys:** `gearing`, `fcf_conversion_pct`
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/policy_engine.py` — `_evaluate_covenant()` (unit-mismatch branch) and
  `scripts/financial_conventions.py` — `warn_percent_type_values()`
- **Basis:** house convention (percent-type fraction storage, issue #357)
- **Status:** Implemented, not yet reviewed by an analyst

### F-040 — Downside covenant breaches

- **Name:** Downside covenant breaches — where the stress breaks a covenant the base case passes
- **Formula:** for each covenant and each forward period that has both a base-case and a downside ratio set:
  base PASS and downside FAIL or UNRESOLVABLE → a breach entry with a stable id `DOWNSIDE-<period>-<metric>`
  (numeric suffix -2, -3… when two covenants name the same metric in the same year). A downside UNRESOLVABLE
  (the stress drove a denominator to zero or below) counts as a breach exactly like a FAIL; a downside year
  with no analysis (the unavailable rule of [F-036](#f-036--downside-case-evaluation)) records the same way when the base case PASSes
- **Unit and storage:** a list of breach records (year, metric, base_actual, downside_actual, threshold,
  breach_id, downside_status, reason, and optional `basis` copied from the base-case covenant result when present);
  `reason` is `None` for a FAIL and one sentence for an UNRESOLVABLE; never mutates the base case
- **Edge rules:** a breach is only recorded where the base case PASSes — a covenant already failing in the
  base case is that failure, not a downside breach; a covenant whose basis is not the recorded view (F-038) is
  UNRESOLVABLE in the base case, so it is never a downside breach. The `basis` key is copied only when present in
  the base-case result; absence remains absence, not null. An unstated basis on consolidated-view figures is
  UNRESOLVABLE and cannot produce a breach
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** `downside_covenant_breaches`
- **Implemented in:** `scripts/policy_engine.py` — `_evaluate_downside_covenants()` (called by
  `evaluate_deal_policy()`)
- **Basis:** house convention (issue #169 / #124 rules carried into the stress)
- **Status:** Implemented, not yet reviewed by an analyst

### F-041 — Forward covenant results

- **Name:** Forward covenant results — every covenant against every forward base-case year
- **Formula:** for each forward period (`FY+1`, `FY+2`, `FY+3`) that has a recorded, non-empty ratio set, each
  covenant is evaluated exactly as F-038 and recorded with a stable id `FORWARD-<year>-<metric>` (numeric
  suffix on collision), in year order then covenant order
- **Unit and storage:** a complete disclosure record next to `covenant_results`; DISCLOSURE ONLY — nothing
  code-enforced reads it, so a forward-year FAIL or UNRESOLVABLE never rejects a deal by itself but can never
  go unrecorded
- **Edge rules:** a year with no projection (absent, null or empty) is not evaluated; state.json carries no
  covenant tenor, so "covered by the projection" is the whole definition — a covenant that really ends earlier
  is still reported for later projected years; a covenant whose basis is not the recorded view (F-038) is
  reported UNRESOLVABLE for every projected year, with its `basis`
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** `forward_covenant_results`
- **Implemented in:** `scripts/policy_engine.py` — `_evaluate_forward_covenants()` (called by
  `evaluate_deal_policy()`)
- **Basis:** house convention (issue #176)
- **Status:** Implemented, not yet reviewed by an analyst

## Policy-engine rules

### F-042 — Security package evaluation

- **Name:** Security package evaluation — the collateral-to-security cross-reference
- **Formula:** collateral assets and security charges are joined on `asset_id` ↔ `secures_asset_id`; every
  charge on an asset is evaluated. Gaps: an asset with no charge ("Uncharged Asset"), a charge whose
  `perfection_status` is not "Perfected" ("Unperfected Security"), a charge whose `ranking` is not "First"
  ("Subordinate Ranking"), and a charge referencing an asset absent from the collateral records ("Dangling
  Reference")
- **Unit and storage:** a list of human-readable gap strings plus one Conditions Precedent per gap (see F-043)
- **Edge rules:** each failure gets its own gap entry and its own CP — a compliant charge never hides a
  non-compliant one on the same asset
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** `security_gaps`
- **Implemented in:** `scripts/policy_engine.py` — `_evaluate_security()` (called by `evaluate_deal_policy()`)
- **Basis:** house convention
- **Status:** Implemented, not yet reviewed by an analyst

### F-043 — Required Conditions Precedent

- **Name:** Required Conditions Precedent — the CP list a draft must carry
- **Formula:** the standard two (`KYC-AML`, `FACILITY-EXECUTION`) plus one CP per security gap (ids
  `SEC-MAPPING-…`, `SEC-PERFECT-…`, `SEC-PRIORITY-…`) plus one per guarantee (`GUARANTEE-<slug>`, keyed on
  `guarantee_id` when supplied, else provider, with a stable numeric suffix on collision)
- **Unit and storage:** a list of `{"cp_id", "text"}`; `cp_id` is the join key the Underwriter must echo back
  exactly — never paraphrased
- **Edge rules:** an absent/blank guarantee amount reads "an amount to be confirmed" (the CP says "for an amount
  to be confirmed"); a falsy-but-real amount like 0
  is kept, never overwritten; a guarantee with no type, or a blank (empty or whitespace-only) type, reads
  "Execution of Guarantee by <provider> for <amount>." (the word "Guarantee" once), while a typed one reads
  "Execution of <type> Guarantee by <provider> for <amount>." — except that a type which already contains the
  word "Guarantee" (any case, e.g. "Corporate Guarantee", "Guarantee Bond") is used as written, so the word is not repeated
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** `required_conditions_precedent`
- **Implemented in:** `scripts/policy_engine.py` — `evaluate_deal_policy()` with `_evaluate_security()` and
  `_guarantee_cps()`; id building in `_unique_id()` / `_slugify()`
- **Basis:** house convention
- **Status:** Implemented, not yet reviewed by an analyst

### F-044 — Required Conditions Subsequent

- **Name:** Required Conditions Subsequent — the post-drawdown monitoring obligations
- **Formula:** the standard `MI-REPORTING` plus one deal-level `CS-COVENANT-COMPLIANCE` when any covenants
  exist (one for the whole package, not one per covenant), plus one `CS-SEC-REPERFECT-<asset>` per
  currently-Perfected charge, plus one `CS-GUARANTEE-<slug>` per guarantee (same keying as F-043)
- **Unit and storage:** a list of `{"cs_id", "text"}`; `cs_id` is a namespace distinct from `cp_id`
- **Edge rules:** a charge that is not Perfected today gets no CS here — its CP (F-042) already covers it;
  a qualitative obligation carries no invented numeric deadline (the grounding rule bars inventing figures)
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** `required_conditions_subsequent`
- **Implemented in:** `scripts/policy_engine.py` — `evaluate_deal_policy()` with `_covenant_compliance_cs()`,
  `_security_reconfirmation_cs()` and `_guarantee_standing_cs()`
- **Basis:** house convention
- **Status:** Implemented, not yet reviewed by an analyst

## Draft audit

### F-045 — Ground-truth figures set

- **Name:** Ground-truth figures — the complete set of figures a draft may cite a number for
- **Formula:** every key present in a period's computed `financials`/`ratios` dicts — the subtotals and
  ratios of F-002…F-031 the model actually produces (workbook-only margins and memo subtotals such as
  F-003, F-011, F-015 and F-019 have no state key and never appear) — under its plain metric name for
  FY-Current; every other base-case period suffixed `_<period>` (e.g. `ebitda_FY-1`, `dscr_FY+1`);
  downside figures suffixed `_<period>_downside`; plus the aggregate collateral cover percentage (F-047),
  which is the one figure computed here. The model's figures are copied as already computed
- **Unit and storage:** a flat dict of figure name → value; a ratio the model left `None` is excluded
  entirely (it is not a figure this deal has a number for), as is the collateral cover when unresolvable
- **Edge rules:** an unknown reported figure is flagged UNRESOLVABLE rather than ignored; the nested `raw`
  input dicts are not figures and are excluded
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/policy_checks.py` — `ground_truth_figures()`
- **Basis:** house convention
- **Status:** Implemented, not yet reviewed by an analyst

### F-046 — Figure-matching tolerance

- **Name:** Figure-matching tolerance — the draft-audit comparison rule for reported figures
- **Formula:** a reported figure matches iff |reported − actual| / |actual| ≤ 0.005 (0.5% relative), applied
  uniformly to ratios and currency amounts alike
- **Unit and storage:** no stored figure — a pass/fail rule
- **Edge rules:** an actual of exactly 0 matches only a reported value with absolute magnitude < 1e-9
  (relative tolerance is undefined against zero); a non-numeric reported or actual value is a mismatch, never
  an error
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/policy_checks.py` — `values_match()`, compared by `check_reported_figures()`
- **Basis:** house convention (fixed 0.5%, one rule for every metric)
- **Status:** Implemented, not yet reviewed by an analyst

### F-047 — Collateral cover percentage (aggregate)

- **Name:** Collateral cover percentage — the aggregate cover across the collateral list, as quoted in a CAM
- **Formula:** collateral_cover_pct = (Σ collateral_value / Σ exposure) × 100 — summed over the collateral list, expressed on a 0-100 scale (matching how a CAM quotes cover), not the 0-1 fraction convention of the percent-type ratios; no workbook row (the workbook's cover column is F-048)
- **Unit and storage:** percent (0-100 scale); only added to the ground-truth figures when it resolves
- **Edge rules:** N/A (`None`, so the figure is omitted entirely rather than compared as a zero) when there is
  nothing to compute from (no collateral), when the total exposure is zero or negative, when any single asset has
  a negative or non-finite exposure or collateral value (a negative asset is invalid input and never offsets
  another asset's positive exposure), or when the result is not finite (issue #365); for valid non-negative inputs
  with a positive exposure the calculation and the 0-100 scale are unchanged. The scale difference from the
  workbook's fraction is [Known differences](#known-differences) KD-2
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/policy_checks.py` — `compute_collateral_cover_pct()` (used by
  `ground_truth_figures()`)
- **Basis:** house convention
- **Status:** Implemented, not yet reviewed by an analyst

## Collateral and exposure (workbook)

### F-048 — CV % of Exposure (workbook row)

- **Name:** CV % of Exposure — the per-asset and total cover column of the workbook's "Collateral & Exposure"
  sheet. State keys `exposure` and `collateral_value` are deal inputs (the sheet does not compute them)
- **Formula:** cv_pct = collateral_value / exposure, only where exposure > 0 (workbook:
  `=IFERROR(IF(B{row}>0,G{row}/B{row},"N/A"),"N/A")` per asset and the same on the total row)
- **Unit and storage:** ratio as written by Excel (0-1 fraction of the inputs supplied); display-only cell
- **Edge rules:** the same strictly-positive-denominator guard as the ratio cells (issue #365): a zero or
  negative exposure shows "N/A" instead of a number. The guard looks at the exposure cell it divides by, so the
  total row (a plain SUM of the per-asset rows, F-058) can still net a negative asset into a positive total and
  show a percentage where F-047 returns N/A for the same input — an accepted, documented divergence,
  [Known differences](#known-differences) KD-2 (tracked in issue #378)
- **State keys:** —
- **Workbook rows:** CV % of Exposure
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `_write_collateral_sheet()` (the per-asset cover formula
  and the total-row cover formula)
- **Basis:** house convention
- **Status:** Implemented, not yet reviewed by an analyst

### F-058 — Collateral sheet totals (workbook row)

- **Name:** Collateral sheet totals — the "Total" row of the workbook's "Collateral & Exposure" sheet
- **Formula:** each column sums its per-asset rows: total_exposure = Σ exposure ("Exposure (Rental + RV)"),
  total_number_of_units = Σ number_of_units ("Number of Units"), total_cap_value = Σ cap_value
  ("Cap / Model Value"), total_non_recovery = Σ non_recovery ("Non-Recovery"), total_costs = Σ costs
  ("Costs"), total_collateral_value = Σ collateral_value ("Collateral Value") (workbook:
  `=SUM(B{first_row}:B{last_row})` … `=SUM(G{first_row}:G{last_row})`)
- **Unit and storage:** sums of the supplied inputs, at full precision; display-only cells
- **Edge rules:** literal column sums of the supplied values (ordinary SUM, which would offset a
  mixed-sign input algebraically — the refusal of negative and mixed-sign amounts is a rule proposed for
  F-049-F-057, not something this sheet enforces); the total row's cover cell is F-048's formula, not a
  sum of the per-asset covers
- **State keys:** —
- **Workbook rows:** Total
- **Policy outputs:** —
- **Implemented in:** `scripts/spreading_builder.py` — `_write_collateral_sheet()` (the total-row SUM formulas)
- **Basis:** house convention (plain column sums)
- **Status:** Implemented, not yet reviewed by an analyst

## Collateral and exposure (proposed)

The entries below are **Proposed, not implemented**: no code computes them yet. They record the conventions
agreed on [issue #266](https://github.com/ShamikM88/open-cam-framework/issues/266) (comment 6259256219) so the
reviewing analyst signs off the design before it is built. RV is a deal input supplied by the risk team, like
PD and LGD; the framework does not estimate it.

### F-049 — Non-RV funded exposure (at origination)

- **Name:** Non-RV funded exposure (at origination) — the non-residual principal component of the amount financed
- **Formula:** rv_exposure = valuation × est_rv_pct (F-050); non_rv_funded_exposure = valuation × (1 − down_payment_pct) − rv_exposure. This is the remaining-rentals/non-RV principal slice at origination, excluding finance charges; it assumes the rental schedule amortises exactly the non-RV slice. It is not a later-date balance or a rental cash-flow total
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** valuation and percentages are origination inputs; down_payment_pct and est_rv_pct are fractions in [0, 1]. Refuse est_rv_pct > 1 − down_payment_pct with one `error:` line and no write: the residual cannot exceed the financed share. Negative monetary inputs are refused. This is an origination proxy, not an accrued balance
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented — proposed in issue #266; maintainer formula decisions recorded in comment 6565421162
- **Basis:** maintainer-approved candidate B on #266: remaining rentals plus residual, with RV counted exactly once; the derivation assumes a pure amortisation of the non-RV slice and no finance charge/profit margin in the derived amount. The analyst should verify this origination convention
- **Status:** Proposed, not implemented

### F-050 — RV Exposure

- **Name:** RV Exposure — the residual-value component of the financed amount at origination
- **Formula:** rv_exposure = original_valuation × est_rv_pct. Original valuation (or original cost where that is the recorded valuation basis) is the base; never multiply RV % by the non-RV financed amount or add the residual twice
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** RV is a risk-team input, not estimated by the framework; est_rv_pct is a fraction in [0, 1] and must not exceed 1 − down_payment_pct. Residual is counted once in F-051. No accrual, arrears or late-charge component
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented — proposed in issue #266; maintainer formula decisions recorded in comment 6565421162
- **Basis:** original-price valuation basis is the maintainer-approved convention on #266; RV % is supplied by the risk team, like PD and LGD
- **Status:** Proposed, not implemented

### F-051 — Total Exposure (at origination)

- **Name:** Total Exposure (at origination) — the financed amount, with residual value counted exactly once
- **Formula:** total_exposure = non_rv_funded_exposure + rv_exposure (F-049 + F-050) = valuation × (1 − down_payment_pct). RV is a decomposition of the financed amount, not an addition on top of it
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** sum across assets without netting; mixed-sign monetary inputs refused. This is an origination exposure proxy and excludes accrued interest, arrears and late charges
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented — proposed in issue #266; maintainer formula decisions recorded in comment 6565421162
- **Basis:** candidate B accepted by the maintainer on #266: remaining rentals plus residual, counted once; do not add RV to the full financed amount
- **Status:** Proposed, not implemented

### F-052 — Net Exposure (at origination)

- **Name:** Net Exposure (at origination) — total exposure after eligible non-asset mitigants
- **Formula:** eligible_mitigants = Σ recorded non-asset mitigants explicitly declared available on default and not already netted in the financing; net_exposure = max(0, total_exposure − eligible_mitigants). Any excess eligible mitigant above total exposure is reported separately; it does not create negative net exposure
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** never deduct an unrecorded mitigant, a mitigant unavailable on default, or an amount already netted in the financed amount. Each mitigant requires an explicit eligibility declaration; no inference from its label. Negative amounts and mixed-sign inputs are refused. Net exposure is floored at zero; excess is disclosed separately
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented — proposed in issue #266; maintainer formula decisions recorded in comment 6565421162
- **Basis:** maintainer-approved netting rule on #266: deduct only mitigants available on default and not already netted; floor at zero and report excess separately
- **Status:** Proposed, not implemented

### F-053 — Collateral Value

- **Name:** Collateral Value — the recoverable value of the collateral at origination
- **Formula:** collateral_value = max(0, cap_or_model_value − non_recovery − costs); floored at zero
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** costs means enforcement/recovery costs only and is the sole cost deduction in this calculation. No accrued interest, arrears or late charges are included. Negative monetary inputs refused; mixed-sign inputs refused
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented as a computation — proposed in issue #266; the Basel comprehensive-approach floor at zero is the cited reference (primary Basel text could not be retrieved)
- **Basis:** house convention; the primary Basel text could not be retrieved, so this is not a verified citation. Residual realisability, security/title, valuation provenance and cross-collateralisation remain narrative judgement, not computed
- **Status:** Proposed, not implemented

### F-054 — Coverage %

- **Name:** Coverage % — collateral cover of total exposure at origination
- **Formula:** coverage_pct = collateral_value / total_exposure; not capped at 100%
- **Unit and storage:** percent-type ratio stored as a fraction (0.87 = 87%), per F-010/F-025
- **Edge rules:** total exposure zero or negative → N/A; coverage is not capped at 100%. Per-asset value is a fraction, distinct from aggregate F-047's 0–100 display scale (KD-2)
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented — proposed in issue #266; existing workbook F-048 and aggregate F-047 retain their existing meanings
- **Basis:** house convention agreed on #266; retain KD-2's documented 0–100 versus fraction scale distinction
- **Status:** Proposed, not implemented

### F-055 — Gross Uncovered Risk

- **Name:** Gross Uncovered Risk — the collateral shortfall before non-asset mitigants
- **Formula:** gross_uncovered_risk = Σ over assets of max(0, total_exposure − collateral_value). Floored at zero per asset; deliberately not exposure × LGD
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** calculated per asset before summing; no netting between assets. Zero exposure gives zero uncovered risk. Negative monetary inputs refused
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented — proposed in issue #266; maintainer decision recorded in comment 6565421162
- **Basis:** maintainer decision: retain F-055 as the gross asset-collateral shortfall; add F-065 for the separate net shortfall after eligible mitigants
- **Status:** Proposed, not implemented

### F-056 — Max LGD

- **Name:** Max LGD — the worst input loss given default across the deal's assets
- **Formula:** max_lgd = max over assets of lgd_pct. LGD % is a risk-team input, never derived from collateral
- **Unit and storage:** percent stored as a fraction (0.45 = 45%); full precision, no rounding in storage
- **Edge rules:** LGD outside [0, 1] refused with one error line and nothing written. Empty asset list, no recorded LGD, or any asset with a missing LGD → N/A, never zero or a maximum over a partial subset
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented — proposed in issue #266
- **Basis:** house convention; LGD is a risk-team input, like PD and RV. Strict missing-value treatment follows the cold-review recommendation accepted by the maintainer
- **Status:** Proposed, not implemented

### F-057 — Avg LGD

- **Name:** Avg LGD — total-exposure-weighted average input loss given default at origination
- **Formula:** avg_lgd = Σ (lgd_pct × total_exposure) / Σ total_exposure; weights are total exposure, the gross exposure-at-default proxy at origination, not EAD or net exposure
- **Unit and storage:** percent stored as a fraction (0.45 = 45%); full precision, no rounding in storage
- **Edge rules:** LGD outside [0, 1] refused. Empty asset list, no recorded LGD, any asset with a missing LGD, or total exposure denominator zero/negative → N/A, never zero or a partial weighted average
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented — proposed in issue #266
- **Basis:** maintainer decision recorded in comment 6565421162: total exposure is the weighting basis and is the gross exposure-at-default proxy at origination; no separate EAD input is introduced
- **Status:** Proposed, not implemented

## Covenant cushion

The read-only cushion report of issue #470 (`scripts/covenant_cushion.py`; see
[financial-model.md](financial-model.md#the-covenant-cushion-report)). Its entries describe only the report's own
derivations: every covenant row, status and limit the report prints is the policy engine's own result, reused
through `scripts/policy_engine.py` — `evaluate_single_covenant()`, never re-implemented. The EBITDA basis is the
house convention throughout (decision D-a of issue #504: before exceptional items, including other income); the
DSCR is the EBITDA-based DSCR (decision D-d of issue #504; a cash-flow DSCR is issue #521). The report writes
nothing; nothing here is stored in state.

### F-059 — DSCR EBITDA break-even

- **Name:** DSCR EBITDA break-even — the EBITDA at which the recorded DSCR covenant switches status
- **Formula:** ebitda* = threshold × (interest_paid + scheduled_principal). The DSCR rises with EBITDA, so the
  passing side follows the covenant's recorded type exactly as the policy engine evaluates it: a minimum covenant
  passes at-or-above the boundary, a maximum at-or-below; equality is PASS for either type
- **Unit and storage:** the currency units of the period's recorded raw line items; report-only, never stored
- **Edge rules:** debt service (interest_paid + scheduled_principal) zero or negative → the ratio is N/A and the
  covenant is UNRESOLVABLE for every EBITDA; no break-even is reported (the zero/negative denominator rule, never
  a numerical cushion). A threshold that is not a recorded number — the policy engine's own contract: an int or a
  float, never a boolean or a numeric string, so the report never presents a boundary for a row the engine records
  as UNRESOLVABLE — a period with no recorded raw line items, or a
  period whose figures are analyst-supplied (`financials_source` for FY-Current, `forecast_source` for a forward
  year) reports the break-even as unavailable with its reason; forward-year rows are disclosure-only
- **State keys:** `interest_paid` · `scheduled_principal` (the period's raw line items)
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/covenant_cushion.py` — `ebitda_break_even()` (the DSCR branch), reading the
  covenant row through `scripts/policy_engine.py` — `evaluate_single_covenant()`
- **Basis:** house convention (decisions D-a and D-d of issue #504)
- **Status:** Implemented, not yet reviewed by an analyst

### F-060 — Gross leverage EBITDA break-even

- **Name:** Gross leverage EBITDA break-even — the EBITDA at which the recorded gross leverage covenant switches
  status
- **Formula:** ebitda* = (current_debt + overdraft + long_term_debt + loan_notes) / threshold. The passing side
  follows the covenant's recorded type together with the ratio's direction as EBITDA rises (falling over positive
  debt, rising over net cash — a negative numerator): a maximum covenant passes at-or-above the boundary over
  positive debt and at-or-below over net cash; a minimum covenant is the mirror image; equality is PASS for every
  combination, and the report never contradicts the policy engine's own evaluation of the recorded type
- **Unit and storage:** the currency units of the period's recorded raw line items; report-only, never stored
- **Edge rules:** a threshold of exactly zero → no EBITDA boundary exists (the ratio would have to equal zero);
  reported as unavailable, not as infinity. A boundary at or below zero EBITDA → the ratio is defined only for
  EBITDA > 0 and every testable point lies above the boundary, so the covenant passes at every testable EBITDA
  (a minimum covenant over positive debt with a negative threshold, or a maximum covenant over net cash with a
  positive threshold) or at none (the mirror combinations), reported as a note, never as a numerical boundary. A
  zero numerator → the ratio is 0 at every EBITDA, so the boundary sits at zero EBITDA and the note says whether
  the covenant passes at every testable EBITDA or at none, per the recorded type and the threshold's sign. A
  threshold that is not a recorded number — the policy engine's own contract: an int or a float, never a boolean
  or a numeric string, so the report never presents a boundary for a row the engine records as UNRESOLVABLE —
  a raw line item that is not a
  number, a period with no recorded raw line items, or analyst-supplied figures report the break-even as
  unavailable with the reason; forward-year rows are disclosure-only
- **State keys:** `current_debt` · `overdraft` · `long_term_debt` · `loan_notes` (the period's raw line items)
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/covenant_cushion.py` — `ebitda_break_even()` (the leverage branch), reading the
  covenant row through `scripts/policy_engine.py` — `evaluate_single_covenant()`
- **Basis:** house convention (decision D-a of issue #504)
- **Status:** Implemented, not yet reviewed by an analyst

### F-061 — Net debt / EBITDA EBITDA break-even

- **Name:** Net debt / EBITDA EBITDA break-even — the EBITDA at which the recorded net debt / EBITDA covenant
  switches status
- **Formula:** ebitda* = (current_debt + overdraft + long_term_debt + loan_notes − cash) / threshold. The passing
  side follows the covenant's recorded type together with the ratio's direction as EBITDA rises (falling over
  positive net debt, rising over net cash — a negative numerator): a maximum covenant passes at-or-above the
  boundary over positive net debt and at-or-below over net cash; a minimum covenant is the mirror image; equality
  is PASS for every combination
- **Unit and storage:** the currency units of the period's recorded raw line items; report-only, never stored
- **Edge rules:** identical to the gross leverage break-even (F-060), with `cash` subtracted from the numerator:
  the type-aware pass side, a zero threshold → no boundary, the boundary-at-or-below-zero and zero-numerator
  notes per the recorded type and the numerator's sign, the engine's int-or-float threshold contract,
  non-numeric raw line items, no recorded raw line items,
  or analyst-supplied figures → unavailable with the reason; forward-year rows are disclosure-only
- **State keys:** `current_debt` · `overdraft` · `long_term_debt` · `loan_notes` · `cash` (the period's raw
  line items)
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/covenant_cushion.py` — `ebitda_break_even()` (the net-debt leverage
  branch), reading the covenant row through `scripts/policy_engine.py` — `evaluate_single_covenant()`
- **Basis:** house convention (decision D-a of issue #504)
- **Status:** Implemented, not yet reviewed by an analyst

### F-062 — EBITDA / interest cover EBITDA break-even

- **Name:** EBITDA / interest cover EBITDA break-even — the EBITDA at which the recorded EBITDA / interest
  covenant (the workbook's "EBITDA/Interest") switches status
- **Formula:** ebitda* = threshold × interest_paid. The ratio rises with EBITDA, so the passing side follows the
  covenant's recorded type exactly as the policy engine evaluates it: a minimum covenant passes at-or-above the
  boundary, a maximum at-or-below; equality is PASS for either type
- **Unit and storage:** the currency units of the period's recorded raw line items; report-only, never stored
- **Edge rules:** interest_paid zero or negative → the ratio is N/A and the covenant UNRESOLVABLE for every
  EBITDA; no break-even is reported. A threshold that is not a recorded number — the policy engine's own
  contract: an int or a float, never a boolean or a numeric string — no recorded raw line items, or
  analyst-supplied figures report the break-even as unavailable with the reason; forward-year rows are
  disclosure-only
- **State keys:** `interest_paid` (the period's raw line item)
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/covenant_cushion.py` — `ebitda_break_even()` (the EBITDA/interest branch),
  reading the covenant row through `scripts/policy_engine.py` — `evaluate_single_covenant()`
- **Basis:** house convention (decisions D-a and D-d of issue #504; EBIT/Interest is a different, EBIT-based
  metric and is deliberately outside the four break-even covenants)
- **Status:** Implemented, not yet reviewed by an analyst

### F-063 — Single-shock reverse-stress bracket search

- **Name:** Single-shock reverse-stress bracket search — the first non-PASS setting of one recorded shock, found
  one shock at a time
- **Formula:** a bracket [lo, hi] with the covenant PASS at lo and non-PASS (FAIL or UNRESOLVABLE alike) at hi,
  found by geometric expansion from the scenario's start (offset doubled each round, capped at the documented
  bound) and then bisection, narrowed until hi − lo ≤ tolerance. Two scenarios per shock: the base scenario starts
  from all shocks at zero; the downside scenario holds the other shocks at their recorded settings and starts the
  searched shock from its recorded setting (the reported cushion beyond the recorded setting is the interval
  above that start). No crossing point is reported — only the bracket and its tolerance
- **Unit and storage:** revenue haircut in percent of revenue (0–100, tolerance 0.01); opex increase in percent of
  admin expenses (bound 10,000, tolerance 0.01); interest-rate bump in basis points (bound 10,000 bps, tolerance
  0.5). Report-only, never stored; there is no cost-of-sales shock
- **Edge rules:** only the three recorded shocks are searched, and the recorded stress vector must pass the
  shared domain validator (`numeric_validation.validate_stress_assumptions()`, the write path's own contract):
  a present malformed or negative assumption refuses the whole report with its reason rather than being read as
  a zero shock; only an absent shock key defaults to zero. A baseline that is already FAIL or UNRESOLVABLE
  reports zero remaining cushion with no search; a ratio the shock cannot move (for example gross leverage under
  the interest bump) reports "not moved"; a PASS beyond the first non-PASS point is a non-monotone predicate and
  is refused with its reason; a model point the spreading builder refuses, a crossing outside the documented
  bound, or a period with no recorded raw line items or analyst-supplied figures is reported unavailable with the
  reason, never as a cushion; forward-year rows are disclosure-only
- **State keys:** `stress_assumptions` · `covenants` · `financials` · `ratios` · `multi_period_financials` ·
  `financials_source` · `forecast_source` · `analyst_supplied_financials` · `downside_case` · `group_basis`
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** `scripts/covenant_cushion.py` — `bracket_and_bisect()` (driven by `_scenario_search()`,
  which re-evaluates each point through `scripts/spreading_builder.py` — `apply_stress_shocks()` and
  `evaluate_financial_model()`)
- **Basis:** house convention (issue #470 sign-off: single shock at a time, brackets not points, the three
  recorded shocks only)
- **Status:** Implemented, not yet reviewed by an analyst

### F-065 — Net Uncovered Risk

- **Name:** Net Uncovered Risk — the collateral shortfall after eligible non-asset mitigants
- **Formula:** net_uncovered_risk = Σ over assets of max(0, net_exposure − collateral_value), where net_exposure is F-052 after only explicitly eligible mitigants. Floor at zero per asset before summing; do not net one asset's surplus against another asset's shortfall
- **Unit and storage:** currency amount; full precision, no rounding in storage
- **Edge rules:** uses the F-052 eligibility rule (available on default and not already netted); no eligible mitigants means net_exposure equals total_exposure. Negative monetary inputs are refused. This is distinct from F-055 gross uncovered risk; it is not exposure × LGD
- **State keys:** —
- **Workbook rows:** —
- **Policy outputs:** —
- **Implemented in:** Not implemented — proposed in issue #266; maintainer decision recorded in comment 6565421162
- **Basis:** added by maintainer decision: retain F-055 as gross uncovered risk and add this separate post-mitigant measure
- **Status:** Proposed, not implemented

## Collateral formula review record

**Stand-in analyst review — 1 November 2026.** The maintainer designated the independent cold review of the rewritten collateral entries as the stand-in for analyst sign-off. The review compared the F-049–F-057 definitions against the recorded #266 conventions and challenged the exposure basis, mitigant eligibility, empty/missing LGD handling and weighting basis. The independent cold reviews were authored by ChatGPT (GPT-6) and Perplexity (GLM-5.3); Claude Code (Claude Opus 5.5) performed the comparative review and recorded the maintainer's accepted resolutions. The maintainer's accepted decisions are recorded on #266 in comment [6565421162](https://github.com/ShamikM88/open-cam-framework/issues/266#issuecomment-6565421162); the underlying cold-review notes are comments [6565003796](https://github.com/ShamikM88/open-cam-framework/issues/266#issuecomment-6565003796) and [6565015295](https://github.com/ShamikM88/open-cam-framework/issues/266#issuecomment-6565015295).

This is a recorded stand-in review, not a claim that a practising credit analyst independently certified the formulas. The entries remain **Proposed, not implemented** pending the maintainer's review/acceptance of this documentation PR and any further analyst sign-off required by #366.


## Known differences

Disagreements found while writing this register. None is fixed in this PR; each is recorded here for the
maintainer, per the issue's instruction.

- **KD-1. Margin rows have no state.json counterpart.** The workbook computes "Gross Profit Margin %"
  (F-003) and "Operating Margin %" (F-005) as ratio cells, but `evaluate_financial_model()` computes no
  margin state key, so the framework-computed model produces no CAM figure, covenant input or
  ground-truth entry for either. Both sides are
  working as designed: `scripts/spreading_builder.py` — the `_ratio()` formulas in `PNL_ROWS`
  (the two margin rows) — against the absence of any margin computation in
  `evaluate_financial_model()`. Documented at
  [financial-model.md](financial-model.md) (the "margin rows … have no `state.json` counterpart" note,
  financial-model.md:401-402). An analyst reviewing F-003/F-005 can only review the workbook formula.
- **KD-2. The two collateral cover calculations differ in scale and in how they treat a negative asset
  (issues #365 and #378).** Since issue #365 both follow the N/A rule: the workbook's "CV % of Exposure"
  (F-048) is `=IFERROR(IF(B{row}>0,G{row}/B{row},"N/A"),"N/A")` per asset and on the total row, and the CAM's
  aggregate (`compute_collateral_cover_pct()`, F-047) returns `None` for a zero or negative total exposure.
  What remains is deliberate or tracked: (1) the aggregate is quoted on a 0-100 scale while the workbook cell
  and the proposed Coverage % (F-054) are raw fractions; (2) the aggregate also returns `None` when any single
  asset has a negative or non-finite exposure or collateral value, whereas the workbook's total row is a plain
  SUM guarded only on the summed exposure, so it can net a negative asset into a positive total and show a
  percentage for input the aggregate omits. The second is an accepted divergence; refusing negative and
  non-finite per-asset values at input is tracked in issue #378.

## Drift guard

[tests/test_formula_register.py](../tests/test_formula_register.py) fails the build when:

1. a formula row in `SECTIONS` has no register entry (workbook rows uncovered);
2. a key produced by `evaluate_financial_model()` on the synthetic example in
   `docs/examples/synthetic_co` has no register entry (state keys uncovered);
3. a rule of the policy engine — a key of `evaluate_deal_policy()`'s result — has no register entry;
4. an entry names a file or function that does not exist in the repository;
5. one of the proposed collateral entries (F-049-F-057 or F-065) or its "Proposed, not implemented" status is
   deleted.

Removing an entry therefore fails the suite through the coverage checks; renaming a function or moving a
file fails it through the existence check. The guard is one-directional by design: the register may describe
more than the code computes (the proposed entries), never less.
