"""B4 (issue #472): the committee summary shows a credit aggregation per period, output only.

Synthetic data. `computed` is hand-built because the accounting-gated B2b step does not exist yet.
"""
import copy

import pytest

import committee_summary as cs

PERIOD = "FY-Current"
COMPUTED = {"financials": {"ebitda": 500.0, "total_debt": 900.0}, "ratios": {"gross_leverage": 1.8}}


def _state():
    return {
        "financials": {PERIOD: {"ebitda": 100.0}},
        "ratios": {PERIOD: {"gross_leverage": 5.0}},
        "credit_aggregation": {PERIOD: {
            "manifest": {
                "participants": ["@borrower", "sub_a"], "period_end": "2025-12-31",
                "period_end_evidence": "Synthetic accounts",
                "scope_confirmations": {"sub_a": {"confirmed": True}},
            },
            "input_fingerprint": "ab" * 32,
            "computed": copy.deepcopy(COMPUTED),
        }},
    }


def _render(state):
    data = {"company": "Co", "proposal": "P", "date": "2026-01-01", "state": state,
            "policy": {"covenant_results": [], "security_gaps": [], "required_conditions_precedent": [],
                       "required_conditions_subsequent": [], "downside_covenant_breaches": [],
                       "forward_covenant_results": []},
            "manifest_count": 0, "sources_declared": "none", "sources_missing": False}
    return cs.render_markdown(data)


@pytest.fixture
def status(monkeypatch):
    def set_status(value, reason=None):
        monkeypatch.setattr(cs, "aggregation_period_status", lambda st, period: {
            "status": value, "reason": reason, "input_fingerprint": "ab" * 32})
    return set_status


def test_no_aggregation_section_for_a_deal_without_one():
    assert "Credit aggregation" not in _render({"financials": {}, "ratios": {}})


def test_a_current_period_shows_scope_period_end_fingerprint_and_computed_figures(status):
    status("current")
    text = _render(_state())
    assert "## Credit aggregation (not a consolidation)" in text
    assert "@borrower" in text and "sub_a" in text
    assert "2025-12-31" in text and "Synthetic accounts" in text
    assert "abababab" in text
    assert "current" in text and "gross_leverage" in text and "1.8" in text


def test_the_limitations_are_always_stated(status):
    status("current")
    text = _render(_state())
    assert "never a statutory consolidation" in text
    for item in ("equity", "goodwill", "non-controlling interests"):
        assert item in text


@pytest.mark.parametrize("value", ["not_calculated", "stale", "invalid", "absent"])
def test_a_period_that_is_not_current_shows_its_status_and_reason_but_no_figures(status, value):
    status(value, f"reason for {value}")
    text = _render(_state())
    assert value.replace("_", " ") in text and f"reason for {value}" in text
    assert "1.8" not in text and "500" not in text


def test_the_borrower_ratios_are_never_summed_with_entity_figures(status):
    status("current")
    text = _render(_state())
    assert "5.0" in text or "5" in text  # the borrower's own figure stays in its own table
    assert "6.8" not in text and "600" not in text


# --- workbook rows and sheet ------------------------------------------------------------------------------

import openpyxl  # noqa: E402

import credit_aggregation as ca  # noqa: E402
from deal_export import workbook_inputs_from_state  # noqa: E402
from spreading_builder import export_to_xlsx  # noqa: E402


@pytest.fixture
def sheet_status(monkeypatch):
    def set_status(value, reason=None):
        monkeypatch.setattr(ca, "aggregation_period_status", lambda st, period: {
            "status": value, "reason": reason, "input_fingerprint": "ab" * 32})
    return set_status


def _flat(rows):
    return [str(cell) for row in rows for cell in row]


def test_no_workbook_rows_for_a_deal_without_an_aggregation():
    assert ca.aggregation_sheet_rows({"financials": {}}) is None
    assert workbook_inputs_from_state({"financials": {}})["credit_aggregation"] is None


def test_workbook_rows_carry_scope_period_end_status_fingerprint_limits_and_figures(sheet_status):
    sheet_status("current")
    cells = _flat(ca.aggregation_sheet_rows(_state()))
    joined = " | ".join(cells)
    assert "never a statutory consolidation" in joined
    assert "goodwill" in joined and "non-controlling interests" in joined
    assert "@borrower, sub_a" in joined and "2025-12-31" in joined and "current" in joined
    assert "ab" * 8 in joined and "gross_leverage" in joined and "1.8" in joined


@pytest.mark.parametrize("value", ["not_calculated", "stale", "invalid", "absent"])
def test_workbook_rows_for_a_period_that_is_not_current_show_no_figures(sheet_status, value):
    sheet_status(value, f"why {value}")
    joined = " | ".join(_flat(ca.aggregation_sheet_rows(_state())))
    assert f"why {value}" in joined and "gross_leverage" not in joined and "500" not in joined


def test_the_workbook_gets_a_credit_aggregation_sheet_last_and_only_when_rows_are_given(tmp_path, sheet_status):
    sheet_status("current")
    plain = tmp_path / "a.xlsx"
    export_to_xlsx("Co", plain)
    assert "Credit aggregation" not in openpyxl.load_workbook(plain).sheetnames
    path = tmp_path / "b.xlsx"
    export_to_xlsx("Co", path, credit_aggregation=ca.aggregation_sheet_rows(_state()))
    workbook = openpyxl.load_workbook(path)
    assert workbook.sheetnames[-1] == "Credit aggregation"
    values = [cell for row in workbook["Credit aggregation"].iter_rows(values_only=True) for cell in row]
    assert any("statutory consolidation" in str(v) for v in values)
