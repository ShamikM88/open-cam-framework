"""The "Collateral & Exposure" sheet's persisted-analysis columns (issue #266, PR C).

Contract (from the PR C design note and decisions 2, 5, 6 and 8 on issue #266):
`workbook_inputs_from_state()` routes `collateral_analysis` into `export_to_xlsx()`
alongside the legacy `collateral` projection, and `_write_collateral_sheet()`
joins the stored results to the sheet rows by `asset_id`. The existing nine
columns and the CV % formula are unchanged; the new columns are deterministic
persisted values (no second Excel implementation of the calculator formulas);
a result is used only when the stored `input_digest` matches the stored inputs
(decision 5), otherwise the cell is "N/A". Deal-level LGD figures stay out of
the workbook (decision 8): the LGD % column is per-asset only and its total
cell stays blank.
"""

import openpyxl

from collateral_check import calculate_collateral_analysis, collateral_projection
from deal_export import workbook_inputs_from_state
from spreading_builder import export_to_xlsx

NINE_HEADERS = [
    "Asset Class", "Exposure (Rental + RV)", "Number of Units", "Cap / Model Value",
    "Non-Recovery", "Costs", "Collateral Value", "CV % of Exposure", "Perfection Status",
]
RESULT_HEADERS = [
    "Non-RV Funded Exposure", "RV Exposure", "Total Exposure (Inc. RV)", "Net Exposure",
    "Coverage %", "Uncovered Risk (Gross)", "Net Uncovered Risk", "LGD %",
]
CV_FORMULA = '=IFERROR(IF(B2>0,G2/B2,"N/A"),"N/A")'


def _payload():
    """The worked example from issue #549: 100k value, 20% down, 40% RV."""
    return {
        "assets": [{
            "asset_id": "AST-001",
            "asset_class": "Synthetic excavator",
            "valuation": 100000,
            "down_payment_pct": 0.2,
            "est_rv_pct": 0.4,
            "cap_or_model_value": 60000,
            "number_of_units": 1,
            "perfection_status": "Pending",
        }],
        "mitigants": [{"mitigant_id": "M-1", "amount": 5000, "eligible_for_netting": True}],
    }


def _analysis(payload=None):
    return calculate_collateral_analysis(payload or _payload())


def _write_sheet(tmp_path, analysis=None, rows=None):
    out_path = tmp_path / "workbook.xlsx"
    if rows is None and analysis is not None:
        rows = collateral_projection(analysis)
    export_to_xlsx(
        "Test Co", str(out_path),
        collateral_data=rows,
        collateral_analysis=analysis,
    )
    return openpyxl.load_workbook(out_path)["Collateral & Exposure"]


def _total_formula(letter, first, last):
    """A total is the SUM only when at least one row carries a number; otherwise N/A, never 0."""
    return f'=IF(COUNT({letter}{first}:{letter}{last})=0,"N/A",SUM({letter}{first}:{letter}{last}))'


def _cells(ws, row):
    return [ws.cell(row=row, column=column).value for column in range(10, 18)]


# ---------------------------------------------------------------------------
# Headers and the join key
# ---------------------------------------------------------------------------

def test_result_columns_follow_the_nine_existing_columns(tmp_path):
    ws = _write_sheet(tmp_path, _analysis())
    assert [ws.cell(row=1, column=column).value for column in range(1, 10)] == NINE_HEADERS
    assert [ws.cell(row=1, column=column).value for column in range(10, 18)] == RESULT_HEADERS


def test_the_legacy_projection_carries_the_asset_id_join_key():
    assert collateral_projection(_analysis())[0]["asset_id"] == "AST-001"


# ---------------------------------------------------------------------------
# Values: persisted results joined by asset_id
# ---------------------------------------------------------------------------

def test_result_columns_carry_persisted_values_joined_by_asset_id(tmp_path):
    ws = _write_sheet(tmp_path, _analysis())
    # non_rv 40k, rv 40k, total 80k; net = 80k - 5k eligible mitigant = 75k;
    # coverage 60k/80k = 0.75; gross uncovered 20k; net uncovered 75k - 60k = 15k;
    # no LGD supplied -> N/A.
    assert _cells(ws, 2) == [40000, 40000, 80000, 75000, 0.75, 20000, 15000, "N/A"]


def test_existing_columns_and_formulas_are_unchanged_by_the_analysis(tmp_path):
    analysis = _analysis()
    with_analysis = _write_sheet(tmp_path, analysis)
    without_analysis = _write_sheet(tmp_path, rows=collateral_projection(analysis))
    expected = ["Synthetic excavator", 80000, 1, 60000, 0, 0, 60000, CV_FORMULA, "Pending"]
    assert [with_analysis.cell(row=2, column=c).value for c in range(1, 10)] == expected
    assert [without_analysis.cell(row=2, column=c).value for c in range(1, 10)] == expected


def test_a_row_without_asset_id_gets_na_cells(tmp_path):
    rows = [{
        "asset_class": "HGV", "exposure": 100, "number_of_units": 2, "cap_value": 80,
        "non_recovery": 0, "costs": 0, "collateral_value": 80, "perfection_status": "Registered",
    }]
    ws = _write_sheet(tmp_path, _analysis(), rows=rows)
    assert _cells(ws, 2) == ["N/A"] * 8


def test_an_analysis_asset_missing_from_the_rows_is_simply_not_shown(tmp_path):
    rows = [collateral_projection(_analysis())[0], {
        "asset_class": "Unmatched", "exposure": 10, "number_of_units": 1, "cap_value": 5,
        "non_recovery": 0, "costs": 0, "collateral_value": 5, "perfection_status": "Pending",
    }]
    ws = _write_sheet(tmp_path, _analysis(), rows=rows)
    assert _cells(ws, 2) == [40000, 40000, 80000, 75000, 0.75, 20000, 15000, "N/A"]
    assert _cells(ws, 3) == ["N/A"] * 8


# ---------------------------------------------------------------------------
# The digest gate (decision 5)
# ---------------------------------------------------------------------------

def test_a_stale_digest_makes_the_result_cells_na(tmp_path):
    analysis = _analysis()
    analysis["assets"][0]["inputs"]["valuation"] = 999999
    ws = _write_sheet(tmp_path, analysis)
    assert _cells(ws, 2) == ["N/A"] * 8


def test_a_malformed_analysis_yields_no_results(tmp_path):
    analysis = _analysis()
    analysis["assets"] = "not-a-list"
    rows = collateral_projection(_analysis())
    ws = _write_sheet(tmp_path, analysis, rows=rows)
    assert _cells(ws, 2) == ["N/A"] * 8


def test_an_asset_without_stored_inputs_yields_no_results(tmp_path):
    analysis = _analysis()
    analysis["assets"] = [{"asset_id": "AST-001", "results": {}}]
    rows = collateral_projection(_analysis())
    ws = _write_sheet(tmp_path, analysis, rows=rows)
    assert _cells(ws, 2) == ["N/A"] * 8


def test_an_analysis_with_non_finite_inputs_yields_no_results(tmp_path):
    analysis = _analysis()
    analysis["assets"][0]["inputs"]["valuation"] = float("nan")
    rows = collateral_projection(_analysis())
    ws = _write_sheet(tmp_path, analysis, rows=rows)
    assert _cells(ws, 2) == ["N/A"] * 8


# ---------------------------------------------------------------------------
# N/A semantics: missing LGD and zero-exposure coverage
# ---------------------------------------------------------------------------

def test_zero_exposure_coverage_and_missing_lgd_are_na_never_zero(tmp_path):
    payload = {
        "assets": [{
            "asset_id": "AST-Z", "asset_class": "Zero-exposure", "valuation": 1000,
            "down_payment_pct": 1.0, "est_rv_pct": 0.0, "cap_or_model_value": 500,
        }],
        "mitigants": [],
    }
    ws = _write_sheet(tmp_path, _analysis(payload))
    assert _cells(ws, 2) == [0, 0, 0, 0, "N/A", 0, 0, "N/A"]


# ---------------------------------------------------------------------------
# The total row
# ---------------------------------------------------------------------------

def test_totals_sum_the_monetary_columns_and_aggregate_coverage(tmp_path):
    ws = _write_sheet(tmp_path, _analysis())
    for column, letter in ((10, "J"), (11, "K"), (12, "L"), (13, "M"), (15, "O"), (16, "P")):
        assert ws.cell(row=3, column=column).value == _total_formula(letter, 2, 2)
    assert ws.cell(row=3, column=14).value == '=IFERROR(IF(L3>0,G3/L3,"N/A"),"N/A")'


def test_a_deal_without_results_gets_na_totals_not_zero(tmp_path):
    rows = [{
        "asset_class": "HGV", "exposure": 100, "number_of_units": 2, "cap_value": 80,
        "non_recovery": 0, "costs": 0, "collateral_value": 80, "perfection_status": "Registered",
    }]
    ws = _write_sheet(tmp_path, analysis=None, rows=rows)
    for column in (10, 11, 12, 13, 15, 16):
        formula = ws.cell(row=3, column=column).value
        assert formula.startswith("=IF(COUNT(") and '"N/A"' in formula


def test_the_deal_level_lgd_stays_out_of_the_workbook(tmp_path):
    ws = _write_sheet(tmp_path, _analysis())
    assert ws.cell(row=3, column=17).value is None


def test_totals_span_all_asset_rows(tmp_path):
    payload = {
        "assets": [
            dict(_payload()["assets"][0]),
            {
                "asset_id": "AST-002", "asset_class": "Trailer", "valuation": 50000,
                "down_payment_pct": 0.1, "est_rv_pct": 0.1, "cap_or_model_value": 30000,
                "number_of_units": 2, "perfection_status": "Registered",
            },
        ],
        "mitigants": [],
    }
    ws = _write_sheet(tmp_path, _analysis(payload))
    assert ws.cell(row=4, column=10).value == _total_formula("J", 2, 3)
    assert ws.cell(row=4, column=14).value == '=IFERROR(IF(L4>0,G4/L4,"N/A"),"N/A")'


# ---------------------------------------------------------------------------
# Old deals: no collateral_analysis in state
# ---------------------------------------------------------------------------

def test_old_deals_without_an_analysis_export_as_before(tmp_path):
    rows = [{
        "asset_class": "HGV", "exposure": 100, "number_of_units": 2, "cap_value": 80,
        "non_recovery": 0, "costs": 0, "collateral_value": 80, "perfection_status": "Registered",
    }]
    ws = _write_sheet(tmp_path, analysis=None, rows=rows)
    expected = ["HGV", 100, 2, 80, 0, 0, 80, CV_FORMULA, "Registered"]
    assert [ws.cell(row=2, column=c).value for c in range(1, 10)] == expected
    assert _cells(ws, 2) == ["N/A"] * 8


def test_workbook_inputs_from_state_routes_the_analysis(tmp_path):
    analysis = _analysis()
    state = {"collateral": collateral_projection(analysis), "collateral_analysis": analysis}
    inputs = workbook_inputs_from_state(state)
    assert inputs["collateral_analysis"] is analysis
    assert inputs["collateral_data"] == state["collateral"]


def test_workbook_inputs_from_state_without_an_analysis_is_none():
    inputs = workbook_inputs_from_state({"collateral": []})
    assert inputs["collateral_analysis"] is None
