import re

import openpyxl
from openpyxl.styles import Font

CELL_REF_RE = re.compile(r"[A-Z]+(\d+)")

PERIOD_HEADERS = ["Metric", "FY-2", "FY-1", "FY-Current"]
PERIOD_COLS = ["B", "C", "D"]

# (label, formula_template or None for a raw input row). {col} is substituted with B/C/D.
# Row numbers below are absolute sheet rows and must stay in sync with the layout
# _write_financial_spreading() produces (header row, then one section-title row
# before each block below). Row 1 = header; row 2 = "Profit & Loss" title; row 3 = Revenue; etc.
PNL_ROWS = [
    ("Revenue", None),                                          # row 3
    ("Cost of Goods Sold", None),                                # row 4
    ("Gross Profit", "={col}3-{col}4"),                          # row 5
    ("Gross Profit Margin %", "=IFERROR({col}5/{col}3,0)"),      # row 6
    ("Admin Expenses", None),                                    # row 7
    ("Depreciation", None),                                      # row 8
    ("Amortisation", None),                                      # row 9
    ("Other Income", None),                                      # row 10
    ("Operating Profit", "={col}5-{col}7-{col}8-{col}9+{col}10"),  # row 11
    ("Operating Margin %", "=IFERROR({col}11/{col}3,0)"),        # row 12
    ("EBITDA", "={col}11+{col}8+{col}9"),                        # row 13
    ("Interest Paid", None),                                     # row 14
    ("Interest Received", None),                                 # row 15
    ("Exceptional Costs / (Income)", None),                      # row 16
    ("Profit Before Tax", "={col}11-{col}14+{col}15-{col}16"),   # row 17
    ("Tax", None),                                                # row 18
    ("Net Profit", "={col}17-{col}18"),                          # row 19
]

RATIO_ROWS = [
    ("DSCR", None),                                              # row 21
    ("EBIT/Interest", "=IFERROR({col}11/{col}14,0)"),            # row 22
    ("EBITDA/Interest", "=IFERROR({col}13/{col}14,0)"),          # row 23
    ("Gross Leverage", None),                                    # row 24
]

BALANCE_SHEET_ROWS = [
    ("Tangible Fixed Assets", None),                             # row 26
    ("Intangible Fixed Assets", None),                           # row 27
    ("Investments", None),                                       # row 28
    ("Total Fixed Assets", "=SUM({col}26:{col}28)"),             # row 29
    ("Cash", None),                                              # row 30
    ("Accounts Receivable", None),                               # row 31
    ("Stock", None),                                             # row 32
    ("Other Current Assets", None),                              # row 33
    ("Total Current Assets", "=SUM({col}30:{col}33)"),           # row 34
    ("Total Assets", "={col}29+{col}34"),                        # row 35
    ("Accounts Payable", None),                                  # row 36
    ("Current Portion - Debt", None),                            # row 37
    ("Overdraft / Revolving Debt", None),                        # row 38
    ("Other Current Liabilities", None),                         # row 39
    ("Total Current Liabilities", "=SUM({col}36:{col}39)"),      # row 40
    ("Long Term Debt", None),                                    # row 41
    ("Loan Notes / Preference Shares", None),                    # row 42
    ("Other Long Term Liabilities", None),                       # row 43
    ("Provisions", None),                                        # row 44
    ("Total Long Term Liabilities", "=SUM({col}41:{col}44)"),    # row 45
    ("Total Liabilities", "={col}40+{col}45"),                   # row 46
    ("Share Capital", None),                                     # row 47
    ("Retained Profit", None),                                   # row 48
    ("Total Equity", "=SUM({col}47:{col}48)"),                   # row 49
]

KEY_METRIC_ROWS = [
    ("Tangible Net Worth (TNW)", "={col}49-{col}27"),                       # row 51
    ("TNW + Loan Notes / Preference Shares", "={col}51+{col}42"),           # row 52
    ("Gearing % (Interest-Bearing Debt / Equity)",
     "=IFERROR(({col}37+{col}38+{col}41+{col}42)/{col}49,0)"),              # row 53
    ("Current Ratio", "=IFERROR({col}34/{col}40,0)"),                       # row 54
]

WORKING_CAPITAL_ROWS = [
    ("Trade Debtor Days", None),                                 # row 56
    ("Trade Creditor Days", None),                               # row 57
    ("Stock Days", None),                                        # row 58
    ("Working Capital Cycle (days)", "={col}56+{col}58-{col}57"),  # row 59
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
]


def _row_layout(sections):
    """Yield (row_number, label, formula_template) in exactly the order and
    position _write_financial_spreading() writes them: row 1 is the header,
    then each section contributes one title row followed by one row per
    (label, formula_template) entry.
    """
    row = 1  # header row
    for section_title, rows in sections:
        row += 1  # section title row
        for label, formula_template in rows:
            row += 1
            yield row, label, formula_template


def _referenced_rows(formula_template):
    """Row numbers a formula template references, e.g. '={col}5-{col}4' -> [5, 4].

    Substitutes a concrete column letter for {col} first so the regex is a
    plain, ordinary cell reference (e.g. B5) rather than needing to know
    about the {col} placeholder itself.
    """
    formatted = formula_template.format(col="B")
    return [int(n) for n in CELL_REF_RE.findall(formatted)]


def validate_row_formulas(sections=None):
    """Confirm every formula in `sections` only references an already-written
    row -- never itself or a row that comes later -- matching the ordering
    and row numbers _write_financial_spreading() will actually produce.

    This is a runtime version of what the hand-maintained '# row N' comments
    next to each SECTIONS entry are meant to guarantee: that a formula's
    cell references still point at the row they were written for. Those
    comments (and tests/test_spreading_builder.py's formula-correctness
    tests) can drift silently if a row is inserted, removed, or reordered
    without updating every formula below it -- this check catches that at
    export time too, not just in CI, by deriving each row's real position
    from `sections` itself rather than trusting the comments.

    Raises ValueError naming the offending row/label on the first invalid
    reference found. `sections` defaults to the module-level SECTIONS;
    tests pass a deliberately broken list to exercise the failure path.
    """
    sections = SECTIONS if sections is None else sections
    for row, label, formula_template in _row_layout(sections):
        if not formula_template:
            continue
        for ref_row in _referenced_rows(formula_template):
            if ref_row >= row:
                raise ValueError(
                    f"spreading_builder: formula for '{label}' (row {row}) references "
                    f"row {ref_row}, which is not an already-written row (must be < {row}). "
                    "A row was likely inserted, removed, or reordered in SECTIONS without "
                    "updating this formula's cell references."
                )


def _write_financial_spreading(wb):
    ws = wb.active
    ws.title = "Financial Spreading"
    ws.append(PERIOD_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for section_title, rows in SECTIONS:
        ws.append([section_title, None, None, None])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, italic=True)
        for label, formula_template in rows:
            row_values = [label]
            for col in PERIOD_COLS:
                if formula_template:
                    row_values.append(formula_template.format(col=col))
                else:
                    row_values.append(None)
            ws.append(row_values)

    ws.column_dimensions["A"].width = 34
    for col in PERIOD_COLS:
        ws.column_dimensions[col].width = 14


def _write_collateral_sheet(wb):
    ws = wb.create_sheet("Collateral & Exposure")
    ws.append(COLLATERAL_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    # One blank asset-class row plus a totals row the user extends as needed.
    ws.append(["[Asset class]", None, None, None, None, None, None, "=IFERROR(G2/B2,0)"])
    ws.append(["Total", "=SUM(B2:B2)", "=SUM(C2:C2)", "=SUM(D2:D2)",
               "=SUM(E2:E2)", "=SUM(F2:F2)", "=SUM(G2:G2)", "=IFERROR(G3/B3,0)"])
    ws.column_dimensions["A"].width = 22
    for col in "BCDEFGH":
        ws.column_dimensions[col].width = 16


def export_to_xlsx(company, output_path):
    validate_row_formulas()
    wb = openpyxl.Workbook()
    _write_financial_spreading(wb)
    _write_collateral_sheet(wb)
    wb.save(output_path)
