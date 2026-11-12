"""Review round 3 for PR #587 (ChatGPT CG587-8 to -14, Cubic): one red test per confirmed finding.

Synthetic data only. Written before the fixes; see the commit history.
"""
import copy

import openpyxl
import pytest

import covenant_basis
import credit_aggregation as ca
import policy_engine
from credit_aggregation import AggregationError, aggregation_period_status, resolve_request
from spreading_builder import export_to_xlsx

from test_credit_aggregation import (
    BORROWER_RAW, OPCO_ID, _entity_record, _hand_state, _line, _period_request, _read_full, _record, _request,
    _seed_two_entity_deal,
)

PERIOD = "FY-Current"
COMPUTED = {"financials": {"ebitda": 500.0}, "ratios": {"gross_leverage": 1.8}}
COVENANT = {"metric": "gross_leverage", "type": "maximum", "threshold": 3.0, "basis": "consolidated"}
FORMULA = '=HYPERLINK("http://x","y")'


def _with_computed(tmp_path, computed=None):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)
    entry = state["credit_aggregation"][PERIOD]
    figures = copy.deepcopy(computed or COMPUTED)
    entry["computed"] = {**figures, "output_digest": ca.computed_output_digest(
        entry["input_fingerprint"], figures["financials"], figures["ratios"])}
    return state


# CG587-8 / Cubic: cache namespace
@pytest.mark.parametrize("order", ["consolidated_first", "guarantor_first"])
def test_a_guarantor_named_consolidated_does_not_share_the_consolidated_cache_slot(order):
    guarantor = {"metric": "dscr", "type": "minimum", "threshold": 1.0, "basis": "guarantor_group",
                 "basis_entity_id": "consolidated"}
    covenants = [dict(COVENANT), guarantor] if order == "consolidated_first" else [guarantor, dict(COVENANT)]
    state = {"ratios": {PERIOD: {"gross_leverage": 1.0}}, "financials": {}, "entity_records": {}}
    views = covenant_basis.resolve_covenant_views(state, covenants)
    by_basis = {covenant["basis"]: view for covenant, view in zip(covenants, views, strict=True)}
    assert by_basis["guarantor_group"]["basis"] == "guarantor_group"
    assert by_basis["consolidated"]["basis"] != "guarantor_group"


# CG587-9 / Cubic: forward and downside on a source collision
def test_forward_and_downside_fail_closed_when_group_basis_and_aggregation_are_both_recorded():
    state = {
        "ratios": {PERIOD: {"gross_leverage": 1.0}, "FY+1": {"gross_leverage": 1.0}},
        "financials": {}, "group_basis": {"basis": "supplied_consolidated"},
        "credit_aggregation": {PERIOD: {"manifest": {}, "input_fingerprint": "a" * 64}},
        "covenants": [dict(COVENANT)],
        "downside_case": {"ratios": {"FY+1": {"gross_leverage": 9.0}}, "financials": {}},
    }
    result = policy_engine.evaluate_deal_policy(state)
    assert result["covenant_results"][0]["status"] == "UNRESOLVABLE"
    assert all(item["status"] == "UNRESOLVABLE" for item in result["forward_covenant_results"])
    assert all(item.get("downside_status") != "FAIL" for item in result["downside_covenant_breaches"])
    assert "both" in result["covenant_results"][0]["reason"]


# CG587-10 / Cubic: formula injection
def test_an_evidence_string_starting_with_equals_is_written_as_text_not_a_formula(tmp_path):
    path = tmp_path / "x.xlsx"
    export_to_xlsx("Co", path, credit_aggregation=[["Period end evidence", FORMULA]])
    cell = openpyxl.load_workbook(path)["Credit aggregation"]["B1"]
    assert cell.data_type != "f" and cell.value == FORMULA


# CG587-11 / Cubic: computed output integrity
def test_a_computed_object_with_the_right_digest_is_current(tmp_path):
    assert aggregation_period_status(_with_computed(tmp_path), PERIOD)["status"] == "current"


def test_an_edited_computed_ratio_is_not_current(tmp_path):
    state = _with_computed(tmp_path)
    state["credit_aggregation"][PERIOD]["computed"]["ratios"]["gross_leverage"] = 0.1
    status = aggregation_period_status(state, PERIOD)
    assert status["status"] == "invalid" and "digest" in status["reason"]


def test_a_computed_object_without_a_digest_is_not_current(tmp_path):
    state = _with_computed(tmp_path)
    del state["credit_aggregation"][PERIOD]["computed"]["output_digest"]
    assert aggregation_period_status(state, PERIOD)["status"] == "invalid"


def test_the_digest_is_bound_to_the_input_fingerprint(tmp_path):
    state = _with_computed(tmp_path)
    figures = state["credit_aggregation"][PERIOD]["computed"]
    other = ca.computed_output_digest("b" * 64, figures["financials"], figures["ratios"])
    assert figures["output_digest"] != other


# CG587-12 / Cubic: intercompany lines in the fingerprint
def test_changing_an_intercompany_line_makes_the_fingerprint_stale():
    state = _hand_state(entity_records={OPCO_ID: _entity_record(intercompany_lines=[_line("@borrower")])})
    before = resolve_request(state, _request())[PERIOD]["input_fingerprint"]
    line = state["entity_records"][OPCO_ID]["intercompany_lines"][0]
    line["amount"] = 26
    assert resolve_request(state, _request())[PERIOD]["input_fingerprint"] != before
    line["amount"] = 25
    line["direction"] = "receivable"
    assert resolve_request(state, _request())[PERIOD]["input_fingerprint"] != before


# CG587-13 / Cubic: another period's line must not block this period
def test_a_line_for_a_period_where_the_owner_is_not_selected_does_not_block_the_request():
    state = _hand_state(
        financials={p: {"raw": dict(BORROWER_RAW["FY-Current"])} for p in (PERIOD, "FY+1")},
        entity_records={OPCO_ID: _entity_record(intercompany_lines=[_line("ghost-entity", period="FY+1")])})
    periods = {PERIOD: _period_request(),
               "FY+1": _period_request(participants=["@borrower"], borrower_period_end="2026-12-31")}
    resolve_request(state, {"periods": periods})   # OPCO is selected in FY-Current only


# Cubic: missing or non-list lines list
@pytest.mark.parametrize("bad", ["missing", "not-a-list"])
def test_a_selected_record_without_a_lines_list_is_refused(bad):
    state = _hand_state()
    if bad == "missing":
        del state["entity_records"][OPCO_ID]["intercompany_lines"]
    else:
        state["entity_records"][OPCO_ID]["intercompany_lines"] = "x"
    with pytest.raises(AggregationError, match="intercompany_lines"):
        resolve_request(state, _request())


# Cubic: a legacy state without financials_source behaves as framework-computed
def test_an_absent_financials_source_is_the_same_input_as_framework_computed():
    state = _hand_state()
    with_flag = resolve_request(state, _request())[PERIOD]["input_fingerprint"]
    del state["financials_source"]
    assert resolve_request(state, _request())[PERIOD]["input_fingerprint"] == with_flag


# Cubic: huge integers
@pytest.mark.parametrize("big", [10**400, -(10**400)])
def test_a_computed_integer_outside_float_range_is_not_current(tmp_path, big):
    state = _with_computed(tmp_path)
    state["credit_aggregation"][PERIOD]["computed"]["ratios"]["gross_leverage"] = big
    assert aggregation_period_status(state, PERIOD)["status"] == "invalid"


# Cubic: the recorded scope snapshots must match the live perimeter
def test_an_edited_scope_confirmation_snapshot_is_stale(tmp_path):
    state = _with_computed(tmp_path)
    state["credit_aggregation"][PERIOD]["manifest"]["scope_confirmations"][OPCO_ID] = {}
    assert aggregation_period_status(state, PERIOD)["status"] == "stale"


def test_a_line_to_a_selected_participant_resolves():
    state = _hand_state(entity_records={
        OPCO_ID: _entity_record(intercompany_lines=[_line("holdco-sel")]),
        "holdco-sel": _entity_record("holdco-sel")})
    request = _request({PERIOD: _period_request(participants=["@borrower", OPCO_ID, "holdco-sel"])})
    resolve_request(state, request)       # the counterparty is itself a selected participant


def test_computed_with_an_unexpected_key_or_a_bad_digest_format_is_refused_by_the_validator():
    from state_manager import computed_figures_problem
    assert "unexpected key" in computed_figures_problem({"financials": {}, "ratios": {}, "extra": 1})
    assert "output_digest" in computed_figures_problem({"financials": {}, "ratios": {}, "output_digest": "xyz"})
    assert computed_figures_problem({"financials": {}, "ratios": {}, "output_digest": "a" * 64}) is None
