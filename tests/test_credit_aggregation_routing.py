"""B3 (issue #472): covenant and ground-truth routing for a credit aggregation.

Synthetic data only. `computed` is filled by hand because the accounting-gated B2b step does not exist yet.
"""
import copy

import pytest

import policy_checks
import policy_engine
from covenant_basis import resolve_covenant_view, resolve_covenant_views
from credit_aggregation import aggregation_period_status

PERIOD = "FY-Current"
COMPUTED = {"financials": {"ebitda": 500.0, "total_debt": 900.0}, "ratios": {"gross_leverage": 1.8, "dscr": 1.25}}
COVENANT = {"metric": "gross_leverage", "type": "maximum", "threshold": 3.0, "basis": "consolidated"}


def _manifest():
    return {
        "participants": ["@borrower", "sub_a"],
        "period_end": "2025-12-31",
        "period_end_evidence": "Synthetic accounts",
        "scope_confirmations": {"sub_a": {"confirmed": True}},
    }


@pytest.fixture
def current_state(monkeypatch):
    """A deal whose FY-Current aggregation is fresh and calculated (status is stubbed; B2 tests cover it)."""
    state = {
        "ratios": {PERIOD: {"gross_leverage": 5.0, "dscr": 0.9}},
        "financials": {PERIOD: {"ebitda": 100.0}},
        "covenants": [dict(COVENANT)],
        "credit_aggregation": {PERIOD: {"manifest": _manifest(), "input_fingerprint": "a" * 64,
                                        "computed": copy.deepcopy(COMPUTED)}},
    }
    import covenant_basis

    def status(st, period):
        entry = st["credit_aggregation"].get(period, {})
        if entry.get("computed") is None:
            return {"status": "not_calculated", "reason": f"{period} not calculated", "input_fingerprint": "a" * 64}
        return {"status": st.get("_status", "current"), "reason": st.get("_reason"), "input_fingerprint": "a" * 64}

    monkeypatch.setattr(covenant_basis, "aggregation_period_status", status)
    return state


def test_a_deal_without_aggregation_keeps_the_ordinary_view_byte_for_byte():
    state = {"ratios": {PERIOD: {"gross_leverage": 5.0}}, "financials": {PERIOD: {"ebitda": 1.0}}}
    view = resolve_covenant_view(state, COVENANT)
    assert view == {"basis": "borrower", "financials": state["financials"], "ratios": state["ratios"],
                    "eligible_raw_periods": [], "recorded_periods": [], "period_reasons": {},
                    "unresolvable_reason": None}


def test_a_consolidated_covenant_reads_the_fresh_aggregation_not_the_borrower(current_state):
    view = resolve_covenant_view(current_state, COVENANT)
    assert view["view_source"] == "credit_aggregation"
    assert view["ratios"][PERIOD]["gross_leverage"] == 1.8
    assert view["input_fingerprints"] == {PERIOD: "a" * 64}
    assert view["unresolvable_reason"] is None


def test_a_borrower_covenant_never_reads_aggregation_values(current_state):
    view = resolve_covenant_view(current_state, {**COVENANT, "basis": "borrower"})
    assert view["basis"] == "borrower" and "view_source" not in view
    assert view["ratios"][PERIOD]["gross_leverage"] == 5.0
    view = resolve_covenant_view(current_state, {k: v for k, v in COVENANT.items() if k != "basis"})
    assert view["ratios"][PERIOD]["gross_leverage"] == 5.0


def test_group_basis_and_aggregation_together_fail_closed(current_state):
    current_state["group_basis"] = {"basis": "consolidated"}
    view = resolve_covenant_view(current_state, COVENANT)
    assert view["ratios"] == {} and view["financials"] == {}
    assert "both" in view["unresolvable_reason"] and "group_basis" in view["unresolvable_reason"]


@pytest.mark.parametrize("status", ["stale", "invalid", "absent"])
def test_a_stale_or_invalid_period_is_unresolvable_with_its_reason(current_state, status):
    current_state["_status"], current_state["_reason"] = status, f"period is {status}"
    view = resolve_covenant_view(current_state, COVENANT)
    assert PERIOD not in view["ratios"]
    assert view["period_reasons"][PERIOD] == f"period is {status}"


def test_a_recorded_but_not_calculated_period_is_unresolvable(current_state):
    del current_state["credit_aggregation"][PERIOD]["computed"]
    view = resolve_covenant_view(current_state, COVENANT)
    assert PERIOD not in view["ratios"] and "not calculated" in view["period_reasons"][PERIOD]


def test_resolve_covenant_views_routes_each_covenant_to_its_own_view(current_state):
    covenants = [dict(COVENANT), {**COVENANT, "basis": "borrower"}]
    views = resolve_covenant_views(current_state, covenants)
    assert views[0]["view_source"] == "credit_aggregation"
    assert "view_source" not in views[1]


def test_policy_engine_tests_the_covenant_on_the_aggregation_and_names_the_view(current_state):
    state = policy_engine.evaluate_deal_policy(current_state)
    result = state["covenant_results"][0]
    assert result["status"] == "PASS" and result["actual"] == 1.8
    assert result["basis"] == "consolidated"
    assert result["view_source"] == "credit_aggregation"
    assert result["input_fingerprint"] == "a" * 64


def test_policy_engine_fails_closed_on_a_stale_aggregation_never_the_borrower(current_state):
    current_state["_status"], current_state["_reason"] = "stale", "inputs changed"
    result = policy_engine.evaluate_deal_policy(current_state)["covenant_results"][0]
    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None
    assert "inputs changed" in result["reason"]


def test_policy_engine_leaves_a_borrower_covenant_on_the_borrower_figures(current_state):
    current_state["covenants"] = [{**COVENANT, "basis": "borrower"}]
    result = policy_engine.evaluate_deal_policy(current_state)["covenant_results"][0]
    assert result["actual"] == 5.0 and result["status"] == "FAIL" and "view_source" not in result


def test_a_nonpositive_denominator_stays_unresolvable_on_the_aggregation(current_state):
    current_state["credit_aggregation"][PERIOD]["computed"]["ratios"]["gross_leverage"] = None
    current_state["credit_aggregation"][PERIOD]["computed"]["financials"]["ebitda"] = -5.0
    result = policy_engine.evaluate_deal_policy(current_state)["covenant_results"][0]
    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None


def test_ground_truth_keeps_the_view_identity_and_never_flattens_into_borrower_names(current_state):
    views = resolve_covenant_views(current_state, current_state["covenants"])
    truth = policy_checks.ground_truth_figures(current_state["financials"], current_state["ratios"], [],
                                               resolved_views=views)
    assert truth["credit_aggregation::gross_leverage"] == 1.8
    assert truth["credit_aggregation::ebitda"] == 500.0
    assert truth["gross_leverage"] == 5.0  # the borrower's own figure is untouched


def test_ground_truth_has_no_aggregation_keys_for_a_stale_period(current_state):
    current_state["_status"], current_state["_reason"] = "stale", "x"
    views = resolve_covenant_views(current_state, current_state["covenants"])
    truth = policy_checks.ground_truth_figures(current_state["financials"], current_state["ratios"], [],
                                               resolved_views=views)
    assert not [key for key in truth if key.startswith("credit_aggregation::")]


def test_status_function_is_the_one_imported_by_the_resolver():
    import covenant_basis
    assert covenant_basis.aggregation_period_status is aggregation_period_status


def test_forward_and_downside_stay_unresolvable_for_an_aggregation_covenant(current_state):
    current_state["ratios"]["FY+1"] = {"gross_leverage": 1.0}
    current_state["downside_case"] = {"ratios": {"FY+1": {"gross_leverage": 9.0}}, "financials": {}}
    result = policy_engine.evaluate_deal_policy(current_state)
    assert result["forward_covenant_results"]
    assert all(item["status"] == "UNRESOLVABLE" for item in result["forward_covenant_results"])
    assert all(item.get("downside_status") != "FAIL" or item.get("basis") for item in
               result["downside_covenant_breaches"])


def test_ground_truth_ignores_malformed_aggregation_view_stores():
    views = [{"view_source": "credit_aggregation", "basis": "consolidated",
              "ratios": "not-a-mapping", "financials": {"FY-Current": "not-a-mapping", "FY+1": {"ebitda": 7.0, "raw": {}}}}]
    truth = policy_checks.ground_truth_figures({}, {}, [], resolved_views=views)
    assert truth == {"credit_aggregation::ebitda_FY+1": 7.0}


@pytest.mark.parametrize("corrupt", [[], "corrupt", 7, ["x"]])
def test_a_malformed_credit_aggregation_fails_closed_even_beside_a_valid_group_basis(corrupt):
    """CG587-5: a non-mapping case-B key must never fall back to the case-A view."""
    state = {"ratios": {PERIOD: {"gross_leverage": 1.0}}, "financials": {PERIOD: {"ebitda": 1.0}},
             "group_basis": {"basis": "supplied_consolidated"}, "credit_aggregation": corrupt}
    view = resolve_covenant_view(state, COVENANT)
    assert view["ratios"] == {} and view["financials"] == {}
    assert "credit_aggregation" in view["unresolvable_reason"] and "malformed" in view["unresolvable_reason"]
    result = policy_engine.evaluate_deal_policy({**state, "covenants": [dict(COVENANT)]})["covenant_results"][0]
    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None


def test_an_absent_null_or_empty_credit_aggregation_still_leaves_the_ordinary_view():
    for value in (None, {}):
        state = {"ratios": {PERIOD: {"gross_leverage": 1.0}}, "financials": {}, "credit_aggregation": value}
        assert resolve_covenant_view(state, COVENANT)["ratios"] == state["ratios"]
