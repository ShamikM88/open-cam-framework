import re

import openpyxl
from openpyxl.styles import Font

PERIOD_HEADERS = ["Metric", "FY-2", "FY-1", "FY-Current"]
PERIOD_COLS = ["B", "C", "D"]
PERIOD_KEYS = ["FY-2", "FY-1", "FY-Current"]

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
    ("Gross Profit Margin %", "=IFERROR({Gross Profit}/{Revenue},0)"),
    ("Admin Expenses", None),
    ("Depreciation", None),
    ("Amortisation", None),
    ("Other Income", None),
    ("Operating Profit",
     "={Gross Profit}-{Admin Expenses}-{Depreciation}-{Amortisation}+{Other Income}"),
    ("Operating Margin %", "=IFERROR({Operating Profit}/{Revenue},0)"),
    ("EBITDA", "={Operating Profit}+{Depreciation}+{Amortisation}"),
    ("Interest Paid", None),
    ("Interest Received", None),
    ("Scheduled Principal Repayment", None),
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
    ("DSCR", "=IFERROR({EBITDA}/({Interest Paid}+{Scheduled Principal Repayment}),0)"),
    ("EBIT/Interest", "=IFERROR({Operating Profit}/{Interest Paid},0)"),
    ("EBITDA/Interest", "=IFERROR({EBITDA}/{Interest Paid},0)"),
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
     "=IFERROR(({Current Portion - Debt}+{Overdraft / Revolving Debt}+{Long Term Debt}+{Loan Notes / Preference Shares})/{Total Equity},0)"),
    ("Current Ratio", "=IFERROR({Total Current Assets}/{Total Current Liabilities},0)"),
    ("Gross Leverage",
     "=IFERROR(({Current Portion - Debt}+{Overdraft / Revolving Debt}+{Long Term Debt}+{Loan Notes / Preference Shares})/{EBITDA},0)"),
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
    writes as Excel formulas, for each period in `multi_period_data`
    (expected keys: "FY-2", "FY-1", "FY-Current" -- any subset is fine).

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
        exceptional_costs = g("exceptional_costs")
        tax_paid = g("tax_paid")

        gross_profit = revenue - cost_of_sales
        operating_profit = gross_profit - admin_expenses - depreciation - amortisation + other_income
        ebitda = operating_profit + depreciation + amortisation
        profit_before_tax = operating_profit - interest_paid + interest_received - exceptional_costs
        net_profit = profit_before_tax - tax_paid

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
            return numerator / denominator if denominator else 0

        dscr = safe_div(ebitda, interest_paid + scheduled_principal)
        gross_leverage = safe_div(total_debt, ebitda)
        current_ratio = safe_div(current_assets, current_liabilities)
        gearing = safe_div(total_debt, total_equity)
        ebit_interest_cover = safe_div(operating_profit, interest_paid)
        ebitda_interest_cover = safe_div(ebitda, interest_paid)

        financials[period] = {
            "raw": raw,
            "gross_profit": gross_profit,
            "operating_profit": operating_profit,
            "ebitda": ebitda,
            "profit_before_tax": profit_before_tax,
            "net_profit": net_profit,
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
            "current_ratio": current_ratio,
            "gearing": gearing,
            "ebit_interest_cover": ebit_interest_cover,
            "ebitda_interest_cover": ebitda_interest_cover,
            # Aliases matching the Excel row labels, so anything reading
            # `ratios` off state.json can look figures up either way.
            "EBIT/Interest": ebit_interest_cover,
            "EBITDA/Interest": ebitda_interest_cover,
        }

    return {"financials": financials, "ratios": ratios}


def _write_financial_spreading(wb, row_of, financial_data=None):
    ws = wb.active
    ws.title = "Financial Spreading"
    ws.append(PERIOD_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    label_to_field = {label: field for field, label in FIELD_LABELS.items()}

    for section_title, rows in SECTIONS:
        ws.append([section_title, None, None, None])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, italic=True)
        for label, formula_template in rows:
            row_values = [label]
            for i, col in enumerate(PERIOD_COLS):
                if formula_template:
                    row_values.append(_resolve_formula(formula_template, row_of, col))
                else:
                    value = None
                    field = label_to_field.get(label)
                    if financial_data and field:
                        period_raw = financial_data.get(PERIOD_KEYS[i], {}) or {}
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
            f"=IFERROR(G{row}/B{row},0)",
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
        f"=IFERROR(G{total_row}/B{total_row},0)",
        None,
    ])

    ws.column_dimensions["A"].width = 22
    for col in "BCDEFGHI":
        ws.column_dimensions[col].width = 16


def export_to_xlsx(company, output_path, financial_data=None, collateral_data=None):
    """Build the financial spreading + collateral workbook.

    `financial_data` is the same multi-period raw-figure shape consumed by
    evaluate_financial_model() (e.g. {"FY-2": {"revenue": ..., ...}, ...}):
    its values are written into the raw-input cells so the Excel formulas
    above actually have something to evaluate. `collateral_data` is a flat
    list of asset dicts (see COLLATERAL_HEADERS / FIELD_LABELS for the
    expected keys); a single blank row is written if omitted, matching the
    prior blank-template behaviour.
    """
    row_of, _ = _row_layout(SECTIONS)
    validate_row_formulas()
    wb = openpyxl.Workbook()
    _write_financial_spreading(wb, row_of, financial_data=financial_data)
    _write_collateral_sheet(wb, collateral_data=collateral_data)
    wb.save(output_path)
