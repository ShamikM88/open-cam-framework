import re

import openpyxl
from openpyxl.styles import Font

# Forward periods used both here (workbook columns) and by
# evaluate_downside_case() below -- defined once, at the top, so the two
# never drift apart.
DEFAULT_FORWARD_PERIODS = ("FY+1", "FY+2", "FY+3")

# The exported workbook always carries all three period "families" as their
# own static columns -- historical actuals, the forward-year base case, and
# the forward-year downside (stressed) case -- rather than only historical,
# so the forward-projection and stress-test figures already computed by
# evaluate_financial_model()/evaluate_downside_case() (and already narrated
# in the CAM) also reach the one artifact meant to be the auditable source
# of truth (see issue #38). A period family with nothing supplied for it
# (e.g. no stress_assumptions given, so no downside case at all) still gets
# its columns -- left blank, exactly like a historical period with no
# financial_data at all already does -- so the workbook's shape never
# depends on which figures happen to be available for a given deal.
HISTORICAL_PERIOD_KEYS = ["FY-2", "FY-1", "FY-Current"]
FORWARD_PERIOD_KEYS = list(DEFAULT_FORWARD_PERIODS)
PERIOD_KEYS = HISTORICAL_PERIOD_KEYS + FORWARD_PERIOD_KEYS
DOWNSIDE_HEADERS = [f"{period} (Downside)" for period in FORWARD_PERIOD_KEYS]
PERIOD_HEADERS = ["Metric"] + HISTORICAL_PERIOD_KEYS + FORWARD_PERIOD_KEYS + DOWNSIDE_HEADERS
PERIOD_COLS = ["B", "C", "D", "E", "F", "G", "H", "I", "J"]
HISTORICAL_COLS = PERIOD_COLS[:3]
FORWARD_COLS = PERIOD_COLS[3:6]
DOWNSIDE_COLS = PERIOD_COLS[6:9]

# Which raw-figures dict (financial_data vs. downside_financial_data) and
# which period key within it populates a given raw-input column -- see
# _write_financial_spreading().
COL_TO_PERIOD_KEY = dict(zip(HISTORICAL_COLS + FORWARD_COLS, HISTORICAL_PERIOD_KEYS + FORWARD_PERIOD_KEYS))
DOWNSIDE_COL_TO_PERIOD_KEY = dict(zip(DOWNSIDE_COLS, FORWARD_PERIOD_KEYS))

# {Label} in a formula template is resolved to `{col}{row}` for whatever row
# that label ends up on -- see _row_layout()/_resolve_formula(). This is what
# lets rows be inserted, removed, or reordered without hand-updating cell
# references: every formula names what it depends on, and its actual sheet
# position is derived, never hardcoded.
#
# Accounting guard: "Scheduled Principal Repayment" is a memo line for DSCR
# purposes only. It must never be referenced by the Profit Before Tax / Net
# Profit chain below -- principal repayments are a balance-sheet/financing
# event, not a P&L expense, and folding it into PBT would misstate earnings.
PNL_ROWS = [
    ("Revenue", None),
    ("Cost of Goods Sold", None),
    ("Gross Profit", "={Revenue}-{Cost of Goods Sold}"),
    ("Gross Profit Margin %", '=IFERROR({Gross Profit}/{Revenue},"N/A")'),
    ("Admin Expenses", None),
    ("Depreciation", None),
    ("Amortisation", None),
    ("Other Income", None),
    ("Operating Profit",
     "={Gross Profit}-{Admin Expenses}-{Depreciation}-{Amortisation}+{Other Income}"),
    ("Operating Margin %", '=IFERROR({Operating Profit}/{Revenue},"N/A")'),
    ("EBITDA", "={Operating Profit}+{Depreciation}+{Amortisation}"),
    ("Interest Paid", None),
    ("Interest Received", None),
    ("Scheduled Principal Repayment", None),
    # Capital Expenditure is a memo line for FCF purposes only, exactly like
    # Scheduled Principal Repayment above is a memo line for DSCR purposes
    # only -- it must never be referenced by the Profit Before Tax / Net
    # Profit chain below. Capex is a cash/investing-activity outflow, not a
    # P&L expense; folding it into PBT would misstate earnings.
    ("Capital Expenditure", None),
    ("Exceptional Costs / (Income)", None),
    ("Profit Before Tax",
     "={Operating Profit}-{Interest Paid}+{Interest Received}-{Exceptional Costs / (Income)}"),
    ("Tax", None),
    ("Net Profit", "={Profit Before Tax}-{Tax}"),
]

# DSCR lives here (references only P&L rows above it) rather than next to
# Gross Leverage, which needs Balance Sheet debt rows that don't exist yet
# at this point in the sheet -- see KEY_METRIC_ROWS below.
RATIO_ROWS = [
    ("DSCR", '=IFERROR({EBITDA}/({Interest Paid}+{Scheduled Principal Repayment}),"N/A")'),
    ("EBIT/Interest", '=IFERROR({Operating Profit}/{Interest Paid},"N/A")'),
    ("EBITDA/Interest", '=IFERROR({EBITDA}/{Interest Paid},"N/A")'),
    # FCF = cash generated after tax, net interest, and capex. Deliberately
    # does NOT net off Scheduled Principal Repayment -- that's a financing
    # (balance-sheet) outflow, not an operating/FCF concept, and DSCR above
    # already covers debt-service coverage on its own; folding principal
    # into FCF too would double-count the same obligation under two
    # different ratios. Lives here (Key Ratios) rather than Key Credit
    # Metrics because every row it references (EBITDA, Capital Expenditure,
    # Tax, Interest Paid, Interest Received) is already written by the end
    # of Profit & Loss above -- no Balance Sheet dependency, unlike Gross
    # Leverage / Net Debt / EBITDA below.
    ("FCF", "={EBITDA}-{Capital Expenditure}-{Tax}-{Interest Paid}+{Interest Received}"),
    ("FCF Conversion %", '=IFERROR({FCF}/{EBITDA},"N/A")'),
]

BALANCE_SHEET_ROWS = [
    ("Tangible Fixed Assets", None),
    ("Intangible Fixed Assets", None),
    ("Investments", None),
    ("Total Fixed Assets", "={Tangible Fixed Assets}+{Intangible Fixed Assets}+{Investments}"),
    ("Cash", None),
    ("Accounts Receivable", None),
    ("Stock", None),
    ("Other Current Assets", None),
    ("Total Current Assets", "={Cash}+{Accounts Receivable}+{Stock}+{Other Current Assets}"),
    ("Total Assets", "={Total Fixed Assets}+{Total Current Assets}"),
    ("Accounts Payable", None),
    ("Current Portion - Debt", None),
    ("Overdraft / Revolving Debt", None),
    ("Other Current Liabilities", None),
    ("Total Current Liabilities",
     "={Accounts Payable}+{Current Portion - Debt}+{Overdraft / Revolving Debt}+{Other Current Liabilities}"),
    ("Long Term Debt", None),
    ("Loan Notes / Preference Shares", None),
    ("Other Long Term Liabilities", None),
    ("Provisions", None),
    ("Total Long Term Liabilities",
     "={Long Term Debt}+{Loan Notes / Preference Shares}+{Other Long Term Liabilities}+{Provisions}"),
    ("Total Liabilities", "={Total Current Liabilities}+{Total Long Term Liabilities}"),
    ("Share Capital", None),
    ("Retained Profit", None),
    ("Total Equity", "={Share Capital}+{Retained Profit}"),
]

# Gross Leverage moves here (written after the Balance Sheet section) because
# it needs the interest-bearing debt rows above, which don't exist yet inside
# RATIO_ROWS -- putting it there would be a forward reference and fail
# validate_row_formulas().
KEY_METRIC_ROWS = [
    ("Tangible Net Worth (TNW)", "={Total Equity}-{Intangible Fixed Assets}"),
    ("TNW + Loan Notes / Preference Shares",
     "={Tangible Net Worth (TNW)}+{Loan Notes / Preference Shares}"),
    ("Gearing % (Interest-Bearing Debt / Equity)",
     '=IFERROR(({Current Portion - Debt}+{Overdraft / Revolving Debt}+{Long Term Debt}+{Loan Notes / Preference Shares})/{Total Equity},"N/A")'),
    ("Current Ratio", '=IFERROR({Total Current Assets}/{Total Current Liabilities},"N/A")'),
    ("Gross Leverage",
     '=IFERROR(({Current Portion - Debt}+{Overdraft / Revolving Debt}+{Long Term Debt}+{Loan Notes / Preference Shares})/{EBITDA},"N/A")'),
    # Net Debt / EBITDA: the same interest-bearing debt aggregate as Gross
    # Leverage above, netted against Cash -- a standard institutional
    # leverage metric Gross Leverage alone doesn't capture (a cash-rich
    # borrower can look more levered on a gross basis than its net cash
    # position actually implies). "N/A" fallback matches every other ratio's
    # own convention here -- see FCF Conversion % above for the same choice.
    ("Net Debt / EBITDA",
     '=IFERROR(({Current Portion - Debt}+{Overdraft / Revolving Debt}+{Long Term Debt}+{Loan Notes / Preference Shares}-{Cash})/{EBITDA},"N/A")'),
]

WORKING_CAPITAL_ROWS = [
    ("Trade Debtor Days", None),
    ("Trade Creditor Days", None),
    ("Stock Days", None),
    ("Working Capital Cycle (days)", "={Trade Debtor Days}+{Stock Days}-{Trade Creditor Days}"),
]

SECTIONS = [
    ("Profit & Loss", PNL_ROWS),
    ("Key Ratios", RATIO_ROWS),
    ("Balance Sheet", BALANCE_SHEET_ROWS),
    ("Key Credit Metrics", KEY_METRIC_ROWS),
    ("Working Capital", WORKING_CAPITAL_ROWS),
]

COLLATERAL_HEADERS = [
    "Asset Class", "Exposure (Rental + RV)", "Number of Units",
    "Cap / Model Value", "Non-Recovery", "Costs", "Collateral Value", "CV % of Exposure",
    "Perfection Status",
]

# Raw multi-period schema field -> the row label it populates. Shared by
# evaluate_financial_model() (computes subtotals/ratios from these same raw
# fields) and _write_financial_spreading() (writes the raw values themselves
# into the workbook so the Excel formulas above have something to evaluate).
#
# "capex" (-> "Capital Expenditure") is optional, like every other raw
# field: a period's input dict simply omitting the key defaults it to 0 via
# evaluate_financial_model()'s own g()/`.get(field, 0)` convention, so every
# existing fixture/caller that predates this field stays fully compatible.
FIELD_LABELS = {
    "revenue": "Revenue",
    "cost_of_sales": "Cost of Goods Sold",
    "admin_expenses": "Admin Expenses",
    "depreciation": "Depreciation",
    "amortisation": "Amortisation",
    "other_income": "Other Income",
    "interest_paid": "Interest Paid",
    "interest_received": "Interest Received",
    "scheduled_principal": "Scheduled Principal Repayment",
    "capex": "Capital Expenditure",
    "exceptional_costs": "Exceptional Costs / (Income)",
    "tax_paid": "Tax",
    "tangible_assets": "Tangible Fixed Assets",
    "intangible_assets": "Intangible Fixed Assets",
    "other_fixed_assets": "Investments",
    "cash": "Cash",
    "trade_debtors": "Accounts Receivable",
    "stock": "Stock",
    "other_current_assets": "Other Current Assets",
    "trade_creditors": "Accounts Payable",
    "current_debt": "Current Portion - Debt",
    "overdraft": "Overdraft / Revolving Debt",
    "other_current_liabilities": "Other Current Liabilities",
    "long_term_debt": "Long Term Debt",
    "loan_notes": "Loan Notes / Preference Shares",
    "share_capital": "Share Capital",
    "retained_profit": "Retained Profit",
}

def _build_label_to_field(field_labels):
    """Reverse of FIELD_LABELS (label -> field), used by
    _write_financial_spreading() to look up which raw field populates a
    given workbook row. Built with an explicit uniqueness check rather than
    a plain dict comprehension: if a future edit to FIELD_LABELS ever gives
    two different fields the same label (a typo, or two fields meant to
    share one), a plain `{label: field for field, label in ...}` would
    silently collapse to whichever entry comes last in iteration order --
    no error, no test failure, just one field silently never populating its
    row. Fail loudly here instead, at the one place this mapping is built.
    """
    label_to_field = {}
    for field, label in field_labels.items():
        if label in label_to_field:
            raise ValueError(
                f"FIELD_LABELS has two fields mapping to the same label {label!r}: "
                f"{label_to_field[label]!r} and {field!r}. Give one of them a distinct label."
            )
        label_to_field[label] = field
    return label_to_field


LABEL_REF_RE = re.compile(r"\{([^{}]+)\}")


def _row_layout(sections):
    """Compute each row's absolute sheet position from `sections` itself.

    Returns (row_of, ordered) where `row_of` maps label -> row number and
    `ordered` is the [(row, label, formula_template), ...] write order.
    Row 1 is the header; each section then contributes one title row
    followed by one row per (label, formula_template) entry -- this must
    match the layout _write_financial_spreading() actually produces.
    """
    row_of = {}
    ordered = []
    row = 1  # header row
    for _section_title, rows in sections:
        row += 1  # section title row
        for label, formula_template in rows:
            row += 1
            row_of[label] = row
            ordered.append((row, label, formula_template))
    return row_of, ordered


def _referenced_labels(formula_template):
    return [label for label in LABEL_REF_RE.findall(formula_template) if label != "col"]


def _resolve_formula(formula_template, row_of, col):
    """Substitute {col} with the column letter and every other {Label} with
    that label's own column+row (e.g. {Revenue} -> "B3")."""
    def repl(match):
        label = match.group(1)
        if label == "col":
            return col
        return f"{col}{row_of[label]}"
    return LABEL_REF_RE.sub(repl, formula_template)


def validate_row_formulas(sections=None):
    """Confirm every formula in `sections` only references a label that's
    already been written -- never itself or a row that comes later --
    matching the ordering _write_financial_spreading() will actually
    produce.

    Because references are by label (not hardcoded row numbers), inserting,
    removing, or reordering a row can never silently point a formula at the
    wrong cell -- either the label still resolves and the position check
    still holds, or resolution/ordering fails loudly here.

    Raises ValueError naming the offending row/label on the first invalid or
    unknown reference found. `sections` defaults to the module-level
    SECTIONS; tests pass a deliberately broken list to exercise the failure
    path.
    """
    sections = SECTIONS if sections is None else sections
    row_of, ordered = _row_layout(sections)
    for row, label, formula_template in ordered:
        if not formula_template:
            continue
        for ref_label in _referenced_labels(formula_template):
            if ref_label not in row_of:
                raise ValueError(
                    f"spreading_builder: formula for '{label}' (row {row}) references "
                    f"unknown label '{ref_label}' -- no row with that label exists in "
                    "the given sections."
                )
            ref_row = row_of[ref_label]
            if ref_row >= row:
                raise ValueError(
                    f"spreading_builder: formula for '{label}' (row {row}) references "
                    f"'{ref_label}' (row {ref_row}), which is not an already-written row "
                    f"(must be < {row}). A row was likely inserted, removed, or reordered "
                    "in SECTIONS without updating this formula's dependency."
                )


def evaluate_financial_model(multi_period_data):
    """Programmatically evaluate the same row chains spreading_builder.py
    writes as Excel formulas, for each period in `multi_period_data` --
    historical ("FY-2", "FY-1", "FY-Current") and forward ("FY+1", "FY+2",
    "FY+3") periods alike, any subset of either. There is nothing period-
    specific about the row chains below: a forward year's raw figures are
    just as directly-supplied (grounded, user/management input -- e.g. a
    forecast) as a historical year's, so they're evaluated exactly the same
    way. Nothing here forecasts, extrapolates, or invents a forward year's
    starting figures -- see apply_stress_shocks()/evaluate_downside_case()
    for the one deterministic transformation this module does apply, and
    only on top of an already-supplied forward-year base case.

    This is the single source of truth for computed figures outside of the
    Excel workbook itself (e.g. for grounding LLM prompts and for
    `state.json`'s `financials`/`ratios` keys) -- it must stay consistent
    with the formula templates above, which is why every raw field name and
    subtotal here also appears in FIELD_LABELS.

    Returns {"financials": {period: {...raw, ...subtotals}}, "ratios": {period: {...}}}.
    """
    financials = {}
    ratios = {}

    for period, raw in (multi_period_data or {}).items():
        raw = raw or {}

        def g(key):
            return raw.get(key, 0) or 0

        revenue = g("revenue")
        cost_of_sales = g("cost_of_sales")
        admin_expenses = g("admin_expenses")
        depreciation = g("depreciation")
        amortisation = g("amortisation")
        other_income = g("other_income")
        interest_paid = g("interest_paid")
        interest_received = g("interest_received")
        scheduled_principal = g("scheduled_principal")
        capex = g("capex")
        exceptional_costs = g("exceptional_costs")
        tax_paid = g("tax_paid")

        gross_profit = revenue - cost_of_sales
        operating_profit = gross_profit - admin_expenses - depreciation - amortisation + other_income
        ebitda = operating_profit + depreciation + amortisation
        profit_before_tax = operating_profit - interest_paid + interest_received - exceptional_costs
        net_profit = profit_before_tax - tax_paid

        # FCF = cash generated after tax, net interest, and capex.
        # Deliberately does NOT net off scheduled_principal -- that's a
        # financing (balance-sheet) outflow, not an operating/FCF concept,
        # and DSCR already covers debt-service coverage on its own; see the
        # matching comment on the "FCF" Excel row in RATIO_ROWS above.
        fcf = ebitda - capex - tax_paid - interest_paid + interest_received

        cash = g("cash")
        trade_debtors = g("trade_debtors")
        stock = g("stock")
        other_current_assets = g("other_current_assets")
        tangible_assets = g("tangible_assets")
        intangible_assets = g("intangible_assets")
        other_fixed_assets = g("other_fixed_assets")
        trade_creditors = g("trade_creditors")
        other_current_liabilities = g("other_current_liabilities")
        overdraft = g("overdraft")
        current_debt = g("current_debt")
        long_term_debt = g("long_term_debt")
        loan_notes = g("loan_notes")
        share_capital = g("share_capital")
        retained_profit = g("retained_profit")

        current_assets = cash + trade_debtors + stock + other_current_assets
        total_fixed_assets = tangible_assets + intangible_assets + other_fixed_assets
        total_assets = current_assets + total_fixed_assets
        current_liabilities = trade_creditors + current_debt + overdraft + other_current_liabilities
        long_term_liabilities = long_term_debt + loan_notes
        total_liabilities = current_liabilities + long_term_liabilities
        total_equity = share_capital + retained_profit
        tangible_net_worth = total_equity - intangible_assets
        total_debt = current_debt + overdraft + long_term_debt + loan_notes

        def safe_div(numerator, denominator):
            """A zero denominator here means the ratio is genuinely
            undefined (e.g. no interest expense at all -- infinite
            coverage, not zero coverage; no equity -- not "0% geared").
            Returning 0 in that case would misrepresent a debt-free or
            distressed company as if it were failing the ratio outright;
            None lets a covenant check (policy_engine._evaluate_covenant)
            correctly treat it as UNRESOLVABLE instead of a false FAIL.
            """
            return numerator / denominator if denominator else None

        dscr = safe_div(ebitda, interest_paid + scheduled_principal)
        gross_leverage = safe_div(total_debt, ebitda)
        # Same interest-bearing debt aggregate as gross_leverage, netted
        # against cash -- see the matching comment on the "Net Debt / EBITDA"
        # Excel row in KEY_METRIC_ROWS above.
        net_debt_to_ebitda = safe_div(total_debt - cash, ebitda)
        current_ratio = safe_div(current_assets, current_liabilities)
        gearing = safe_div(total_debt, total_equity)
        ebit_interest_cover = safe_div(operating_profit, interest_paid)
        ebitda_interest_cover = safe_div(ebitda, interest_paid)
        fcf_conversion_pct = safe_div(fcf, ebitda)

        financials[period] = {
            "raw": dict(raw),  # a copy -- never share a mutable reference to the caller's dict
            "gross_profit": gross_profit,
            "operating_profit": operating_profit,
            "ebitda": ebitda,
            "profit_before_tax": profit_before_tax,
            "net_profit": net_profit,
            "fcf": fcf,
            "current_assets": current_assets,
            "current_liabilities": current_liabilities,
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "total_debt": total_debt,
            "tangible_net_worth": tangible_net_worth,
        }
        ratios[period] = {
            "dscr": dscr,
            "gross_leverage": gross_leverage,
            "net_debt_to_ebitda": net_debt_to_ebitda,
            "current_ratio": current_ratio,
            "gearing": gearing,
            "ebit_interest_cover": ebit_interest_cover,
            "ebitda_interest_cover": ebitda_interest_cover,
            "fcf_conversion_pct": fcf_conversion_pct,
            # Aliases matching the Excel row labels, so anything reading
            # `ratios` off state.json can look figures up either way.
            "EBIT/Interest": ebit_interest_cover,
            "EBITDA/Interest": ebitda_interest_cover,
        }

    return {"financials": financials, "ratios": ratios}


def apply_stress_shocks(base_case_raw, stress_assumptions):
    """Derive one forward year's shocked raw-input dict from its
    already-supplied base-case raw figures and a set of explicit,
    deterministic stress assumptions -- never a Python-invented or
    -extrapolated figure; the base case itself is untouched input, only
    the three shocks below are applied to it.

    `stress_assumptions` (all optional, default to no shock):
    - "revenue_haircut_pct": shocked revenue = base_revenue * (1 - pct/100)
    - "opex_increase_pct": shocked admin_expenses = base_admin_expenses *
      (1 + pct/100) -- applies to `admin_expenses` only, never
      `cost_of_sales`; a cost-of-sales stress is a different, unmodeled
      scenario (e.g. a supply-cost shock), not "opex".
    - "interest_rate_bump_bps": shocked interest_paid = base_interest_paid
      + (total_interest_bearing_debt * bps / 10000), where
      total_interest_bearing_debt is that same year's base-case
      current_debt + overdraft + long_term_debt + loan_notes -- the same
      debt aggregate evaluate_financial_model() uses for total_debt/
      gross_leverage/gearing, just computed directly from the raw dict here
      since evaluate_financial_model() hasn't run yet at this point.

    Every other raw field (balance sheet, tax, etc.) is carried through
    unchanged -- the shocks model a P&L/financing stress, not a full
    re-forecast of the balance sheet.

    Returns a new dict; `base_case_raw` is never mutated.
    """
    stress_assumptions = stress_assumptions or {}
    revenue_haircut_pct = stress_assumptions.get("revenue_haircut_pct") or 0
    opex_increase_pct = stress_assumptions.get("opex_increase_pct") or 0
    interest_rate_bump_bps = stress_assumptions.get("interest_rate_bump_bps") or 0

    base_case_raw = base_case_raw or {}
    shocked = dict(base_case_raw)

    base_revenue = base_case_raw.get("revenue", 0) or 0
    shocked["revenue"] = base_revenue * (1 - revenue_haircut_pct / 100)

    base_admin_expenses = base_case_raw.get("admin_expenses", 0) or 0
    shocked["admin_expenses"] = base_admin_expenses * (1 + opex_increase_pct / 100)

    base_interest_paid = base_case_raw.get("interest_paid", 0) or 0
    total_interest_bearing_debt = (
        (base_case_raw.get("current_debt", 0) or 0)
        + (base_case_raw.get("overdraft", 0) or 0)
        + (base_case_raw.get("long_term_debt", 0) or 0)
        + (base_case_raw.get("loan_notes", 0) or 0)
    )
    shocked["interest_paid"] = base_interest_paid + (
        total_interest_bearing_debt * interest_rate_bump_bps / 10000
    )

    return shocked


def evaluate_downside_case(multi_period_data, stress_assumptions, forward_periods=DEFAULT_FORWARD_PERIODS):
    """Build the downside (stressed) case for every forward period present
    in `multi_period_data`, by applying apply_stress_shocks() to that
    period's already-supplied base-case raw figures and then re-running
    evaluate_financial_model() on the shocked inputs -- the exact same
    row-chain evaluator the base case uses, never a separate, hand-derived
    formula set, so a covenant check against downside ratios is comparing
    like for like against the base case.

    Historical periods are never shocked and never appear in the result --
    only entries in `forward_periods` that are actually present in
    `multi_period_data` are included. Returns {} (both "financials" and
    "ratios" empty) if no forward periods are present at all.

    Returns the same shape as evaluate_financial_model():
    {"financials": {period: {...}}, "ratios": {period: {...}}}.
    """
    multi_period_data = multi_period_data or {}
    shocked_inputs = {
        period: apply_stress_shocks(multi_period_data[period], stress_assumptions)
        for period in forward_periods
        if period in multi_period_data
    }
    return evaluate_financial_model(shocked_inputs)


def _write_financial_spreading(wb, row_of, financial_data=None, downside_financial_data=None):
    ws = wb.active
    ws.title = "Financial Spreading"
    ws.append(PERIOD_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    label_to_field = _build_label_to_field(FIELD_LABELS)

    for section_title, rows in SECTIONS:
        ws.append([section_title] + [None] * len(PERIOD_COLS))
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, italic=True)
        for label, formula_template in rows:
            row_values = [label]
            for col in PERIOD_COLS:
                if formula_template:
                    row_values.append(_resolve_formula(formula_template, row_of, col))
                else:
                    value = None
                    field = label_to_field.get(label)
                    if field:
                        if col in DOWNSIDE_COL_TO_PERIOD_KEY:
                            source, period_key = downside_financial_data, DOWNSIDE_COL_TO_PERIOD_KEY[col]
                        else:
                            source, period_key = financial_data, COL_TO_PERIOD_KEY[col]
                        period_raw = (source or {}).get(period_key, {}) or {}
                        if field in period_raw:
                            value = period_raw[field]
                    row_values.append(value)
            ws.append(row_values)

    ws.column_dimensions["A"].width = 34
    for col in PERIOD_COLS:
        ws.column_dimensions[col].width = 14


def _write_collateral_sheet(wb, collateral_data=None):
    ws = wb.create_sheet("Collateral & Exposure")
    ws.append(COLLATERAL_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    assets = collateral_data or [{}]
    first_row = 2
    last_row = first_row + len(assets) - 1

    for i, asset in enumerate(assets):
        row = first_row + i
        ws.append([
            asset.get("asset_class"),
            asset.get("exposure"),
            asset.get("number_of_units"),
            asset.get("cap_value"),
            asset.get("non_recovery"),
            asset.get("costs"),
            asset.get("collateral_value"),
            f'=IFERROR(G{row}/B{row},"N/A")',
            asset.get("perfection_status"),
        ])

    total_row = last_row + 1
    ws.append([
        "Total",
        f"=SUM(B{first_row}:B{last_row})",
        f"=SUM(C{first_row}:C{last_row})",
        f"=SUM(D{first_row}:D{last_row})",
        f"=SUM(E{first_row}:E{last_row})",
        f"=SUM(F{first_row}:F{last_row})",
        f"=SUM(G{first_row}:G{last_row})",
        f'=IFERROR(G{total_row}/B{total_row},"N/A")',
        None,
    ])

    ws.column_dimensions["A"].width = 22
    for col in "BCDEFGHI":
        ws.column_dimensions[col].width = 16


def export_to_xlsx(company, output_path, financial_data=None, collateral_data=None,
                    downside_financial_data=None):
    """Build the financial spreading + collateral workbook.

    `financial_data` is the same multi-period raw-figure shape consumed by
    evaluate_financial_model() (e.g. {"FY-2": {"revenue": ..., ...}, "FY+1":
    {...}, ...}): its values are written into the historical (FY-2/FY-1/
    FY-Current) and forward-year base-case (FY+1/FY+2/FY+3) raw-input cells
    so the Excel formulas above actually have something to evaluate.
    `downside_financial_data` is the same shape again, but sourced from
    evaluate_downside_case()'s own `financials` output -- its FY+1/FY+2/FY+3
    entries populate the three "(Downside)" columns instead. Either can be
    omitted (e.g. no forward years supplied at all, or no stress_assumptions
    given so no downside case exists) -- the corresponding columns are then
    just left blank, exactly like a historical period with no financial_data
    at all already is. `collateral_data` is a flat list of asset dicts (see
    COLLATERAL_HEADERS / FIELD_LABELS for the expected keys); a single blank
    row is written if omitted, matching the prior blank-template behaviour.
    """
    row_of, _ = _row_layout(SECTIONS)
    validate_row_formulas()
    wb = openpyxl.Workbook()
    _write_financial_spreading(wb, row_of, financial_data=financial_data,
                                downside_financial_data=downside_financial_data)
    _write_collateral_sheet(wb, collateral_data=collateral_data)
    wb.save(output_path)
