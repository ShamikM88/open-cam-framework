"""Tests for scripts/covenant_cushion.py (issue #196, built under issue #470).

Every formula here is checked against a hand-computed number, and the shock search against
an analytically known crossing. The break-even formulas are register entries F-059..F-063.
"""
import json
import copy
import math

import pytest

import covenant_cushion
from covenant_cushion import (
    STATE_KEYS_READ,
    bracket_and_bisect,
    compute_from_state,
    ebitda_break_even,
    generate,
    main,
)
from policy_engine import evaluate_single_covenant
from numeric_validation import InvalidFinancialInputError, StressAssumptionsError
from spreading_builder import evaluate_financial_model


def _raw(**overrides):
    """A small framework-computed raw period: EBITDA 100, debt service 25, debt 100, cash 30."""
    raw = {
        "revenue": 1000.0, "cost_of_sales": 600.0, "admin_expenses": 250.0,
        "depreciation": 40.0, "amortisation": 10.0, "other_income": 0.0,
        "interest_paid": 20.0, "interest_received": 0.0, "scheduled_principal": 5.0,
        "capex": 0.0, "exceptional_costs": 0.0, "tax_paid": 0.0,
        "cash": 30.0, "trade_debtors": 0.0, "stock": 0.0, "other_current_assets": 0.0,
        "trade_creditors": 0.0, "current_debt": 40.0, "overdraft": 10.0,
        "other_current_liabilities": 0.0, "long_term_debt": 50.0, "loan_notes": 0.0,
        "other_long_term_liabilities": 0.0, "provisions": 0.0, "share_capital": 0.0,
        "retained_profit": 0.0,
    }
    raw.update(overrides)
    return raw


def _state(covenants, raws=None, ratios=None, **extra):
    raws = raws if raws is not None else {"FY-Current": _raw()}
    state = {
        "multi_period_financials": raws,
        "financials_source": "framework-computed",
        "covenants": covenants,
    }
    if ratios is None:
        model = evaluate_financial_model(raws)
        ratios = model["ratios"]
        state["financials"] = model["financials"]
    if ratios is not None:
        state["ratios"] = ratios
    state.update(extra)
    return state


# ---------------------------------------------------------------------------
# Exact break-even formulas (register entries F-059..F-062)
# ---------------------------------------------------------------------------

def test_dscr_break_even_is_threshold_times_debt_service():
    raw = _raw(interest_paid=10.0, scheduled_principal=15.0)
    break_even = ebitda_break_even({"metric": "dscr", "type": "minimum", "threshold": 1.25}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(1.25 * 25.0)
    assert break_even["pass_side"] == "at_or_above"


def test_gross_leverage_break_even_is_total_debt_over_threshold():
    raw = _raw()  # current_debt 40 + overdraft 10 + long_term_debt 50 = 100
    break_even = ebitda_break_even(
        {"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(100.0 / 3.5)
    assert break_even["pass_side"] == "at_or_above"


def test_net_debt_break_even_is_net_debt_over_threshold():
    raw = _raw()  # total debt 100, cash 30 -> net debt 70
    break_even = ebitda_break_even(
        {"metric": "net_debt_to_ebitda", "type": "maximum", "threshold": 3.5}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(70.0 / 3.5)
    assert break_even["pass_side"] == "at_or_above"


def test_ebitda_interest_cover_break_even_is_threshold_times_interest():
    raw = _raw(interest_paid=8.0)
    break_even = ebitda_break_even(
        {"metric": "ebitda_interest_cover", "type": "minimum", "threshold": 6.0}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(48.0)
    assert break_even["pass_side"] == "at_or_above"


def test_ebitda_interest_cover_accepts_the_workbook_alias_metric():
    raw = _raw(interest_paid=8.0)
    break_even = ebitda_break_even(
        {"metric": "EBITDA/Interest", "type": "minimum", "threshold": 6.0}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(48.0)


def test_a_maximum_covenant_with_a_negative_threshold_passes_at_or_below():
    # A negative threshold on a maximum covenant inverts the pass side (the ratio is
    # non-negative in the meaningful region, so only a negative numerator can satisfy it).
    raw = _raw(current_debt=-100.0, overdraft=0.0, long_term_debt=0.0)
    break_even = ebitda_break_even(
        {"metric": "gross_leverage", "type": "maximum", "threshold": -2.0}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx((-100.0) / (-2.0))
    assert break_even["pass_side"] == "at_or_below"


def test_break_even_notes_when_the_boundary_is_outside_the_meaningful_region():
    # Net cash (total debt 100, cash 200): the boundary is negative, so every positive-EBITDA
    # point passes and the covenant is UNRESOLVABLE at EBITDA <= 0 before it can FAIL.
    raw = _raw(cash=200.0)
    break_even = ebitda_break_even(
        {"metric": "net_debt_to_ebitda", "type": "maximum", "threshold": 3.5}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(-100.0 / 3.5)
    assert break_even["pass_side"] == "at_or_above"
    assert break_even["note"]


# ---------------------------------------------------------------------------
# The pass side follows the covenant's recorded type (review finding CG526-1)
# ---------------------------------------------------------------------------

def test_the_pass_side_follows_the_covenants_recorded_type_dscr():
    # The engine evaluates a maximum DSCR as DSCR <= 1.25 being PASS, so the pass side is
    # at-or-below the boundary, whatever the metric's usual direction (CG526-1).
    raw = _raw(interest_paid=10.0, scheduled_principal=15.0)  # debt service 25
    break_even = ebitda_break_even({"metric": "dscr", "type": "maximum", "threshold": 1.25}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(1.25 * 25.0)
    assert break_even["pass_side"] == "at_or_below"


def test_the_pass_side_follows_the_covenants_recorded_type_ebitda_interest_cover():
    raw = _raw(interest_paid=8.0)
    break_even = ebitda_break_even(
        {"metric": "ebitda_interest_cover", "type": "maximum", "threshold": 6.0}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(48.0)
    assert break_even["pass_side"] == "at_or_below"


def test_the_pass_side_follows_the_covenants_recorded_type_gross_leverage():
    # A gross leverage covenant recorded as a minimum: the ratio falls as EBITDA rises over
    # positive debt, so the engine passes it at-or-below the boundary (CG526-1).
    raw = _raw()  # total debt 100
    break_even = ebitda_break_even(
        {"metric": "gross_leverage", "type": "minimum", "threshold": 3.5}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(100.0 / 3.5)
    assert break_even["pass_side"] == "at_or_below"


def test_the_pass_side_follows_the_covenants_recorded_type_over_net_cash():
    # Net cash (negative numerator): the ratio rises as EBITDA rises, so a minimum covenant
    # passes at-or-above the boundary (CG526-1).
    raw = _raw(cash=200.0)  # net debt -100
    break_even = ebitda_break_even(
        {"metric": "net_debt_to_ebitda", "type": "minimum", "threshold": -2.0}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx((-100.0) / (-2.0))  # 50
    assert break_even["pass_side"] == "at_or_above"


def test_a_minimum_leverage_covenant_with_a_negative_threshold_passes_everywhere():
    # A minimum covenant over positive debt with a negative threshold: the ratio is positive at
    # every testable EBITDA, so the covenant passes at every testable point (CG526-1).
    raw = _raw()  # total debt 100
    break_even = ebitda_break_even(
        {"metric": "gross_leverage", "type": "minimum", "threshold": -2.0}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(100.0 / -2.0)  # -50
    assert break_even["pass_side"] == "at_or_above"
    assert "passes at every testable EBITDA" in break_even["note"]


def test_a_minimum_leverage_covenant_can_fail_to_pass_at_any_testable_ebitda():
    # Net cash with a positive threshold on a minimum covenant: the ratio is negative at every
    # testable EBITDA, so no testable point passes (CG526-1).
    raw = _raw(cash=200.0)  # net debt -100
    break_even = ebitda_break_even(
        {"metric": "net_debt_to_ebitda", "type": "minimum", "threshold": 2.0}, raw)
    assert break_even["break_even_ebitda"] == pytest.approx(-100.0 / 2.0)  # -50
    assert break_even["pass_side"] == "at_or_below"
    assert "cannot pass at any testable EBITDA" in break_even["note"]


def test_a_zero_numerator_leverage_boundary_follows_the_covenants_recorded_type():
    # No debt at all: the ratio is 0 at every EBITDA, so which side passes depends only on the
    # recorded type and the threshold's sign, never on a fixed direction (CG526-1).
    raw = _raw(current_debt=0.0, overdraft=0.0, long_term_debt=0.0, loan_notes=0.0)
    passes = ebitda_break_even(
        {"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}, raw)
    assert passes["break_even_ebitda"] == 0.0
    assert passes["pass_side"] == "at_or_above"
    assert "numerator is zero" in passes["note"]
    fails = ebitda_break_even(
        {"metric": "gross_leverage", "type": "minimum", "threshold": 3.5}, raw)
    assert fails["break_even_ebitda"] == 0.0
    assert fails["pass_side"] == "at_or_below"
    assert "numerator is zero" in fails["note"]


def test_the_reported_side_matches_the_policy_engine_across_the_boundary():
    # The report must never contradict the engine's own result: evaluate the covenant just
    # below and just above the reported boundary and check the reported side against it.
    raw = _raw(interest_paid=10.0, scheduled_principal=15.0)  # debt service 25
    cases = (
        {"metric": "dscr", "type": "maximum", "threshold": 1.25},
        {"metric": "gross_leverage", "type": "minimum", "threshold": 3.5},
    )
    for covenant in cases:
        break_even = ebitda_break_even(covenant, raw)
        boundary = float(break_even["break_even_ebitda"])
        for factor, side in ((0.9, "below"), (1.1, "above")):
            ebitda = boundary * factor
            shocked_raw = _raw(revenue=850.0 + ebitda,  # EBITDA = revenue - 600 - 250 = ebitda
                               interest_paid=10.0, scheduled_principal=15.0)
            model = evaluate_financial_model({"FY-Current": shocked_raw})
            result = evaluate_single_covenant(
                dict(covenant), model["ratios"]["FY-Current"], model["financials"]["FY-Current"],
                "borrower")
            assert (result["status"] == "PASS") == (break_even["pass_side"] == f"at_or_{side}")


# ---------------------------------------------------------------------------
# Zero and negative denominators, threshold zero, unsupported metrics
# ---------------------------------------------------------------------------

def test_dscr_break_even_refuses_a_zero_or_negative_denominator():
    for interest, principal in ((0.0, 0.0), (-20.0, 10.0)):
        raw = _raw(interest_paid=interest, scheduled_principal=principal)
        break_even = ebitda_break_even(
            {"metric": "dscr", "type": "minimum", "threshold": 1.25}, raw)
        assert break_even["break_even_ebitda"] is None
        assert "debt service" in break_even["reason"]


def test_ebitda_interest_cover_break_even_refuses_a_zero_or_negative_denominator():
    for interest in (0.0, -5.0):
        raw = _raw(interest_paid=interest)
        break_even = ebitda_break_even(
            {"metric": "ebitda_interest_cover", "type": "minimum", "threshold": 6.0}, raw)
        assert break_even["break_even_ebitda"] is None
        assert "interest" in break_even["reason"]


def test_leverage_break_even_refuses_a_zero_threshold():
    raw = _raw()
    break_even = ebitda_break_even(
        {"metric": "gross_leverage", "type": "maximum", "threshold": 0.0}, raw)
    assert break_even["break_even_ebitda"] is None
    assert "threshold is zero" in break_even["reason"]


def test_dscr_break_even_with_a_zero_threshold_is_the_zero_boundary():
    raw = _raw()
    break_even = ebitda_break_even(
        {"metric": "dscr", "type": "minimum", "threshold": 0.0}, raw)
    assert break_even["break_even_ebitda"] == 0.0
    assert break_even["pass_side"] == "at_or_above"


def test_break_even_names_metrics_outside_the_four_supported_families():
    raw = _raw()
    for metric in ("gearing", "EBIT/Interest", "current_ratio"):
        break_even = ebitda_break_even(
            {"metric": metric, "type": "minimum", "threshold": 1.0}, raw)
        assert break_even["break_even_ebitda"] is None
        assert "not one of the four" in break_even["reason"]


def test_break_even_names_an_unsupported_covenant_type():
    raw = _raw()
    break_even = ebitda_break_even(
        {"metric": "dscr", "type": "at-least", "threshold": 1.25}, raw)
    assert break_even["break_even_ebitda"] is None
    assert "type" in break_even["reason"]


def test_break_even_needs_the_period_raw_lines():
    break_even = ebitda_break_even(
        {"metric": "dscr", "type": "minimum", "threshold": 1.25}, None)
    assert break_even["break_even_ebitda"] is None
    assert "raw line items" in break_even["reason"]


# ---------------------------------------------------------------------------
# compute_from_state: results, periods, disclosure-only forward years
# ---------------------------------------------------------------------------

def _find(report, metric, period="FY-Current"):
    for covenant in report["covenants"]:
        if covenant["metric"] == metric:
            for entry in covenant["periods"]:
                if entry["period"] == period:
                    return entry
    raise AssertionError(f"no {metric} entry for {period}")


def test_report_carries_the_verbatim_policy_engine_result():
    report = compute_from_state(_state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}]))
    entry = _find(report, "dscr")
    assert entry["result"]["status"] == "PASS"
    assert entry["result"]["actual"] == pytest.approx(150.0 / 25.0)
    assert entry["result"]["threshold"] == 1.25
    assert "headroom_pct" in entry["result"]


def test_forward_years_are_reported_disclosure_only_and_history_is_not():
    raws = {"FY-Current": _raw(), "FY+1": _raw(revenue=1200.0), "FY-1": _raw(revenue=900.0)}
    report = compute_from_state(_state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}], raws=raws))
    assert _find(report, "dscr", "FY-Current")["disclosure_only"] is False
    assert _find(report, "dscr", "FY+1")["disclosure_only"] is True
    with pytest.raises(AssertionError):
        _find(report, "dscr", "FY-1")


def test_a_period_with_ratios_but_no_raw_lines_reports_unavailable_searches():
    raws = {"FY-Current": _raw()}
    state = _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}], raws=raws)
    state["ratios"]["FY+1"] = dict(state["ratios"]["FY-Current"])
    report = compute_from_state(state)
    entry = _find(report, "dscr", "FY+1")
    assert entry["shocks"] is None
    assert "FY+1" in entry["shock_search_unavailable_reason"]
    assert "raw line items" in entry["shock_search_unavailable_reason"]


def test_analyst_supplied_figures_make_the_search_unavailable():
    state = _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}])
    state["financials_source"] = "analyst-supplied"
    report = compute_from_state(state)
    entry = _find(report, "dscr")
    assert entry["shocks"] is None
    assert "analyst-supplied" in entry["shock_search_unavailable_reason"]


def test_analyst_supplied_forecast_makes_forward_searches_unavailable():
    raws = {"FY-Current": _raw(), "FY+1": _raw(revenue=1200.0)}
    state = _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
                   raws=raws, forecast_source="analyst-supplied")
    report = compute_from_state(state)
    assert _find(report, "dscr", "FY-Current")["shocks"] is not None
    assert _find(report, "dscr", "FY+1")["shocks"] is None
    assert "analyst-supplied" in _find(report, "dscr", "FY+1")["shock_search_unavailable_reason"]


def test_no_covenants_recorded_is_reported_not_crashed():
    report = compute_from_state(_state([]))
    assert report["covenants"] == []
    assert any("no covenants" in caveat for caveat in report["caveats"])


def test_group_basis_sets_the_recorded_view():
    state = _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}])
    state["group_basis"] = {"supplied_by": "borrower"}
    report = compute_from_state(state)
    assert report["recorded_view"] == "consolidated"
    entry = _find(report, "dscr")
    assert entry["result"]["status"] == "UNRESOLVABLE"  # borrower covenant on consolidated figures


# ---------------------------------------------------------------------------
# Baseline FAIL / UNRESOLVABLE: zero remaining cushion
# ---------------------------------------------------------------------------

def test_a_baseline_fail_reports_zero_cushion_and_no_search():
    raw = _raw(revenue=200.0)  # EBITDA 20, debt service 25 -> dscr 0.8 < 1.25 FAIL
    report = compute_from_state(_state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
                                       raws={"FY-Current": raw}))
    entry = _find(report, "dscr")
    assert entry["result"]["status"] == "FAIL"
    for shock in entry["shocks"]:
        for scenario in ("base_scenario", "downside_scenario"):
            assert shock[scenario]["outcome"] == "baseline_non_pass"
            assert shock[scenario]["baseline_status"] == "FAIL"
            assert "bracket" not in shock[scenario]


def test_a_baseline_unresolvable_reports_zero_cushion():
    raw = _raw(interest_paid=0.0, scheduled_principal=0.0)  # debt service 0 -> dscr N/A
    report = compute_from_state(_state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
                                       raws={"FY-Current": raw}))
    entry = _find(report, "dscr")
    assert entry["result"]["status"] == "UNRESOLVABLE"
    for shock in entry["shocks"]:
        assert shock["base_scenario"]["outcome"] == "baseline_non_pass"
        assert shock["base_scenario"]["baseline_status"] == "UNRESOLVABLE"


# ---------------------------------------------------------------------------
# Shock search: crossing, not-moved, no-crossing, refusal paths
# ---------------------------------------------------------------------------

def _linear_state():
    """EBITDA = revenue (no other lines): the DSCR crossing is analytically known."""
    raw = _raw(revenue=1000.0, cost_of_sales=0.0, admin_expenses=0.0,
               interest_paid=100.0, scheduled_principal=0.0)
    return _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
                  raws={"FY-Current": raw})


def _base_scenario(entry, shock_key):
    for shock in entry["shocks"]:
        if shock["shock"] == shock_key:
            return shock["base_scenario"]
    raise AssertionError(f"no {shock_key} shock row")


def test_revenue_haircut_crossing_brackets_the_analytic_value():
    report = compute_from_state(_linear_state())
    scenario = _base_scenario(_find(report, "dscr"), "revenue_haircut_pct")
    assert scenario["outcome"] == "crossing"
    # dscr = ebitda/100 >= 1.25 while ebitda >= 125, i.e. haircut <= 87.5% of revenue
    lo, hi = scenario["bracket"]
    assert lo <= 87.5 < hi
    assert hi - lo <= scenario["tolerance"] + 1e-9
    assert scenario["status_beyond"] == "FAIL"


def test_opex_crossing_brackets_the_analytic_value():
    raw = _raw(admin_expenses=400.0, interest_paid=100.0, scheduled_principal=0.0)
    # ebitda = 1000 - 600 - 400 = 0 at zero shock... use cost_of_sales 0 instead:
    raw = _raw(revenue=1000.0, cost_of_sales=0.0, admin_expenses=400.0,
               interest_paid=100.0, scheduled_principal=0.0)
    report = compute_from_state(_state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
                                      raws={"FY-Current": raw}))
    scenario = _base_scenario(_find(report, "dscr"), "opex_increase_pct")
    assert scenario["outcome"] == "crossing"
    # ebitda = 1000 - 400*(1+x/100) = 600 - 4x >= 125 (dscr 1.25 x debt service 100) -> x <= 118.75
    lo, hi = scenario["bracket"]
    assert lo <= 118.75 < hi
    assert hi - lo <= scenario["tolerance"] + 1e-9


def test_interest_bump_crossing_brackets_the_analytic_value():
    raw = _raw(revenue=1000.0, cost_of_sales=0.0, admin_expenses=0.0,
               interest_paid=700.0, scheduled_principal=0.0, current_debt=900.0,
               overdraft=0.0, long_term_debt=0.0)
    report = compute_from_state(_state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
                                      raws={"FY-Current": raw}))
    scenario = _base_scenario(_find(report, "dscr"), "interest_rate_bump_bps")
    assert scenario["outcome"] == "crossing"
    # dscr = 1000/(700 + 900*bump/10000) >= 1.25 while bump <= (800 - 700)*10000/900 = 1111.1...
    lo, hi = scenario["bracket"]
    analytic = (1000.0 / 1.25 - 700.0) * 10000.0 / 900.0
    assert lo <= analytic < hi
    assert hi - lo <= scenario["tolerance"] + 1e-9


def test_downside_scenario_starts_from_the_recorded_shock_vector():
    state = _linear_state()
    state["stress_assumptions"] = {"revenue_haircut_pct": 50.0}
    report = compute_from_state(state)
    for shock in _find(report, "dscr")["shocks"]:
        if shock["shock"] == "revenue_haircut_pct":
            assert shock["downside_scenario"]["start"] == 50.0
            assert shock["downside_scenario"]["outcome"] == "crossing"
            lo, hi = shock["downside_scenario"]["bracket"]
            assert lo <= 87.5 < hi
            additional_lo, additional_hi = shock["downside_scenario"]["additional_cushion_bracket"]
            assert additional_lo == pytest.approx(lo - 50.0)
            assert additional_hi == pytest.approx(hi - 50.0)


def test_downside_scenario_other_shocks_stay_at_their_recorded_settings():
    raw = _raw(revenue=1000.0, cost_of_sales=0.0, admin_expenses=0.0,
               interest_paid=100.0, scheduled_principal=0.0, current_debt=900.0,
               overdraft=0.0, long_term_debt=0.0)
    state = _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
                   raws={"FY-Current": raw})
    state["stress_assumptions"] = {"revenue_haircut_pct": 50.0, "opex_increase_pct": 20.0}
    report = compute_from_state(state)
    for shock in _find(report, "dscr")["shocks"]:
        if shock["shock"] == "interest_rate_bump_bps":
            # The bump search runs with the haircut and opex shocks fixed at their recorded
            # values: ebitda 500, so dscr = 500/(100 + 900*bump/10000) >= 1.25 at bump <= 3333.3...
            assert shock["downside_scenario"]["start"] == 0.0
            assert shock["downside_scenario"]["outcome"] == "crossing"
            lo, hi = shock["downside_scenario"]["bracket"]
            analytic = (500.0 / 1.25 - 100.0) * 10000.0 / 900.0
            assert lo <= analytic < hi


def test_a_leverage_covenant_is_not_moved_by_the_interest_bump():
    report = compute_from_state(_state([{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}]))
    for shock in _find(report, "gross_leverage")["shocks"]:
        if shock["shock"] == "interest_rate_bump_bps":
            assert shock["base_scenario"]["outcome"] == "not_moved"
            assert "do not move" in shock["base_scenario"]["reason"]
        else:
            assert shock["base_scenario"]["outcome"] == "crossing"


def test_no_crossing_within_the_documented_bound_is_reported_unavailable():
    raw = _raw(other_income=1000.0, interest_paid=10.0, scheduled_principal=0.0)
    # Even a 100% revenue haircut leaves EBITDA >= 990, so dscr >= 99: no crossing in 0-100.
    report = compute_from_state(_state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
                                      raws={"FY-Current": raw}))
    scenario = _base_scenario(_find(report, "dscr"), "revenue_haircut_pct")
    assert scenario["outcome"] == "no_crossing_in_bound"
    assert "0" in scenario["reason"] and "100" in scenario["reason"]


def test_bracket_and_bisect_refuses_a_non_monotone_predicate():
    def status_at(value):
        if 40 <= value < 60:
            return "FAIL"
        return "PASS"

    result = bracket_and_bisect(status_at, start=0.0, step=10.0, bound=100.0, tolerance=0.01)
    assert result["outcome"] == "non_monotone"
    assert "non-monotone" in result["reason"]


def test_bracket_and_bisect_reports_a_refused_point():
    def status_at(value):
        if value >= 50:
            raise ValueError("non-finite intermediate result")
        return "PASS"

    result = bracket_and_bisect(status_at, start=0.0, step=10.0, bound=100.0, tolerance=0.01)
    assert result["outcome"] == "refused_point"
    assert "refused" in result["reason"]


def test_bracket_and_bisect_keeps_the_bracket_when_only_the_bound_refuses():
    def status_at(value):
        if value == 100.0:
            raise ValueError("refused at the bound")
        return "FAIL" if value >= 30 else "PASS"

    result = bracket_and_bisect(status_at, start=0.0, step=10.0, bound=100.0, tolerance=1.0)
    assert result["outcome"] == "crossing"
    lo, hi = result["bracket"]
    # lo is the last PASS point, hi the first non-PASS point: the boundary 30 lies between.
    assert lo < 30 <= hi
    assert hi - lo <= 1.0


def test_bracket_and_bisect_reports_an_empty_search_domain():
    result = bracket_and_bisect(lambda value: "PASS", start=100.0, step=5.0,
                               bound=100.0, tolerance=0.01)
    assert result["outcome"] == "no_crossing_in_bound"


def test_scenario_search_reports_not_moved_when_the_ratio_cannot_change():
    def evaluate(value):
        return {"status": "PASS", "actual": 6.0}

    result = covenant_cushion._scenario_search(evaluate, start=0.0, step=5.0,
                                              bound=100.0, tolerance=0.01)
    assert result["outcome"] == "not_moved"


def test_scenario_search_reports_a_refused_bound():
    def evaluate(value):
        if value == 100.0:
            return {"refused": "refused at the bound"}
        return {"status": "PASS", "actual": float(value)}

    result = covenant_cushion._scenario_search(evaluate, start=0.0, step=5.0,
                                               bound=100.0, tolerance=0.01)
    assert result["outcome"] == "refused_point"
    assert "refused" in result["reason"]


def test_a_refused_model_point_makes_the_scenario_unavailable():
    raw = _raw()
    raw["revenue"] = "not a number"  # the model refuses to evaluate this period
    state = {
        "multi_period_financials": {"FY-Current": raw},
        "financials_source": "framework-computed",
        "ratios": {"FY-Current": {"dscr": 6.0}},
        "financials": {"FY-Current": {"ebitda": 150.0}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
    }
    report = compute_from_state(state)
    scenario = _base_scenario(_find(report, "dscr"), "revenue_haircut_pct")
    assert scenario["outcome"] == "refused_point"
    assert "refused" in scenario["reason"]


def test_the_report_flags_ratios_that_disagree_with_the_raw_lines():
    state = _linear_state()
    state["ratios"]["FY-Current"]["dscr"] = 9.9  # stale: the raw lines re-evaluate to 10.0
    report = compute_from_state(state)
    assert any("re-evaluat" in caveat and "dscr" in caveat for caveat in report["caveats"])


def test_every_real_ratio_key_produces_a_break_even_or_a_reason():
    model = evaluate_financial_model({"FY-Current": _raw()})
    covenants = [{"metric": key, "type": "minimum", "threshold": 1.0}
                 for key in model["ratios"]["FY-Current"]]
    report = compute_from_state(_state(covenants))
    supported = {"dscr", "gross_leverage", "net_debt_to_ebitda",
                 "ebitda_interest_cover", "EBITDA/Interest"}
    for covenant in report["covenants"]:
        entry = covenant["periods"][0]
        if covenant["metric"] in supported:
            assert entry["break_even_ebitda"]["break_even_ebitda"] is not None, covenant["metric"]
        else:
            assert entry["break_even_ebitda"]["break_even_ebitda"] is None, covenant["metric"]
            assert entry["break_even_ebitda"]["reason"]


def test_report_carries_the_fixed_limits_and_basis_notes():
    report = compute_from_state(_state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}]))
    assert any("not a credit decision" in limit for limit in report["limits"])
    assert any("disclosure only" in limit for limit in report["limits"])
    assert any("EBITDA-based" in note and "D-d" in note for note in report["basis_notes"])
    assert any("other income" in note and "D-a" in note for note in report["basis_notes"])


# ---------------------------------------------------------------------------
# generate / main: the read-only CLI
# ---------------------------------------------------------------------------

def _write_deal(tmp_path, state):
    folder = tmp_path / "deals" / "Synthetic Co" / "Loan_2026-01-01"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "draft_v1.md").write_text("# Draft\n", encoding="utf-8")
    return folder


def test_generate_reads_the_newest_dated_folder_and_writes_nothing(tmp_path):
    _write_deal(tmp_path, _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}]))
    folder2 = tmp_path / "deals" / "Synthetic Co" / "Loan_2026-02-02"
    folder2.mkdir(parents=True)
    (folder2 / "state.json").write_text(
        json.dumps(_state([{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}])),
        encoding="utf-8")
    before = {path: path.read_bytes() for path in sorted((tmp_path / "deals").rglob("*")) if path.is_file()}
    report = generate("Synthetic Co", "Loan", base_dir=tmp_path)
    after = {path: path.read_bytes() for path in sorted((tmp_path / "deals").rglob("*")) if path.is_file()}
    assert before == after
    assert report["selected_folder"].endswith("Loan_2026-02-02")
    assert report["status"] == "ok"
    assert any(c["metric"] == "gross_leverage" for c in report["covenants"])


def test_generate_with_an_exact_date_selects_that_folder(tmp_path):
    _write_deal(tmp_path, _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}]))
    folder2 = tmp_path / "deals" / "Synthetic Co" / "Loan_2026-02-02"
    folder2.mkdir(parents=True)
    (folder2 / "state.json").write_text("{}", encoding="utf-8")
    report = generate("Synthetic Co", "Loan", date_str="2026-01-01", base_dir=tmp_path)
    assert report["selected_folder"].endswith("Loan_2026-01-01")


def test_generate_refuses_a_date_whose_folder_has_no_state(tmp_path):
    _write_deal(tmp_path, _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}]))
    (tmp_path / "deals" / "Synthetic Co" / "Loan_2026-03-03").mkdir(parents=True)
    with pytest.raises(Exception) as excinfo:
        generate("Synthetic Co", "Loan", date_str="2026-03-03", base_dir=tmp_path)
    assert "2026-03-03" in str(excinfo.value)


def test_generate_refuses_a_non_calendar_date(tmp_path):
    with pytest.raises(Exception) as excinfo:
        generate("Synthetic Co", "Loan", date_str="2026-02-30", base_dir=tmp_path)
    assert "calendar date" in str(excinfo.value)


def test_generate_refuses_a_missing_deal(tmp_path):
    with pytest.raises(Exception) as excinfo:
        generate("Synthetic Co", "No Such Deal", base_dir=tmp_path)
    assert "No Such Deal" in str(excinfo.value)


def test_generate_refuses_a_malformed_state(tmp_path):
    folder = tmp_path / "deals" / "Synthetic Co" / "Loan_2026-01-01"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text('{"ratios": [1]}', encoding="utf-8")
    with pytest.raises(Exception) as excinfo:
        generate("Synthetic Co", "Loan", base_dir=tmp_path)
    assert "ratios" in str(excinfo.value)


def test_main_prints_the_report_and_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_deal(tmp_path, _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}]))
    main(["--company", "Synthetic Co", "--proposal", "Loan"])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["status"] == "ok"
    assert report["selected_folder"].endswith("Loan_2026-01-01")


def test_main_reports_a_missing_deal_with_one_error_line_and_exit_1(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["--company", "Synthetic Co", "--proposal", "No Such Deal"])
    # sys.exit(<message>) prints one "error: ..." line to stderr and exits 1 at top level.
    code = excinfo.value.code
    assert isinstance(code, str) and code.startswith("error: ") and code.count("\n") == 0


def test_state_keys_read_matches_the_documented_list():
    assert STATE_KEYS_READ == (
        "financials", "ratios", "financials_source", "forecast_source",
        "analyst_supplied_financials", "multi_period_financials", "stress_assumptions",
        "downside_case", "covenants", "group_basis",
    )


def test_break_even_refuses_a_non_numeric_threshold():
    for threshold in (None, "1.25x", [1.25]):
        break_even = ebitda_break_even(
            {"metric": "dscr", "type": "minimum", "threshold": threshold}, _raw())
        assert break_even["break_even_ebitda"] is None
        assert "not a recorded number" in break_even["reason"]


def test_a_numeric_string_threshold_gives_no_boundary_and_matches_the_engine_refusal():
    # The policy engine accepts only an int or float threshold (never a boolean or a numeric
    # string), so the break-even must not present a boundary the engine's row cannot use; the
    # engine's own status for the same row is UNRESOLVABLE (review finding CG526-2).
    for threshold in ("1.25", True):
        covenant = {"metric": "dscr", "type": "minimum", "threshold": threshold}
        break_even = ebitda_break_even(covenant, _raw())
        assert break_even["break_even_ebitda"] is None
        assert break_even["pass_side"] is None
        assert "not a recorded number" in break_even["reason"]
        assert "never a boolean or a numeric string" in break_even["reason"]
        model = evaluate_financial_model({"FY-Current": _raw()})
        result = evaluate_single_covenant(
            dict(covenant), model["ratios"]["FY-Current"], model["financials"]["FY-Current"],
            "borrower")
        assert result["status"] == "UNRESOLVABLE"
        assert "threshold is missing or not a number" in result["reason"]


def test_break_even_refuses_a_non_numeric_raw_line():
    raw = _raw()
    raw["current_debt"] = "not a number"
    break_even = ebitda_break_even(
        {"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}, raw)
    assert break_even["break_even_ebitda"] is None
    assert "not a number" in break_even["reason"]


def test_a_numeric_string_or_boolean_raw_figure_gives_no_boundary_and_matches_the_model_refusal():
    # The raw figures use the spreading model's own contract (numeric_validation
    # .validate_raw_line_items): a recognised raw line item is a finite JSON number or null,
    # never a numeric string or a boolean, so a figure the model refuses gives no boundary
    # and the reason names the model's contract (review finding CG526-4).
    for figure in ("100", True, float("nan")):
        raw = _raw()
        raw["current_debt"] = figure
        break_even = ebitda_break_even(
            {"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}, raw)
        assert break_even["break_even_ebitda"] is None
        assert break_even["pass_side"] is None
        assert "must be a finite JSON number or null" in break_even["reason"]
        with pytest.raises(InvalidFinancialInputError):
            evaluate_financial_model({"FY-Current": raw})


def test_a_numeric_string_raw_figure_in_the_debt_service_gives_no_boundary():
    # The same contract on the denominator side of the DSCR break-even: the model refuses
    # the period, so no boundary is reported for it (review finding CG526-4).
    raw = _raw()
    raw["scheduled_principal"] = "5"
    break_even = ebitda_break_even(
        {"metric": "dscr", "type": "minimum", "threshold": 1.25}, raw)
    assert break_even["break_even_ebitda"] is None
    assert break_even["pass_side"] is None
    assert "must be a finite JSON number or null" in break_even["reason"]


def test_the_zero_threshold_refusal_wording_covers_both_leverage_covenant_types():
    # The break-even refuses a zero threshold for either leverage type (minimum or maximum),
    # so the module docstring must not limit that refusal to maximum covenants (review
    # finding CG526-5).
    assert "zero threshold on a maximum covenant" not in (covenant_cushion.__doc__ or "")
    assert "zero threshold on either leverage covenant" in (covenant_cushion.__doc__ or "")


def test_a_negative_threshold_with_no_debt_cannot_pass_at_any_testable_ebitda():
    raw = _raw(current_debt=0.0, overdraft=0.0, long_term_debt=0.0)
    break_even = ebitda_break_even(
        {"metric": "gross_leverage", "type": "maximum", "threshold": -2.0}, raw)
    assert break_even["break_even_ebitda"] == 0.0
    assert break_even["pass_side"] == "at_or_below"
    assert "cannot pass at any testable EBITDA" in break_even["note"]


def test_bracket_and_bisect_refuses_a_refused_scenario_start():
    def status_at(value):
        raise ValueError("nope")

    result = bracket_and_bisect(status_at, start=0.0, step=10.0, bound=100.0, tolerance=0.01)
    assert result["outcome"] == "refused_point"
    assert "scenario start" in result["reason"]


def test_bracket_and_bisect_reports_a_non_pass_baseline_directly():
    result = bracket_and_bisect(lambda value: "FAIL", start=0.0, step=10.0,
                                bound=100.0, tolerance=0.01)
    assert result == {"outcome": "baseline_non_pass", "baseline_status": "FAIL"}


def test_scenario_search_refuses_a_bisection_point_that_the_model_refuses():
    def evaluate(value):
        if value == 50.0:
            return {"refused": "refused mid-search"}
        if value >= 100.0:
            return {"status": "FAIL", "actual": 0.0}
        return {"status": "PASS", "actual": 1.0}

    result = covenant_cushion._scenario_search(evaluate, start=0.0, step=100.0,
                                               bound=100.0, tolerance=0.01)
    assert result["outcome"] == "refused_point"
    assert "bisection point" in result["reason"]


def test_a_present_malformed_recorded_shock_refuses_the_report_not_zero():
    # A present but malformed stress assumption is a domain error, never a silent zero shock:
    # the shared write-path validator refuses the vector, so the report refuses with its
    # reason instead of searching as though no haircut had been recorded (review finding
    # CG526-3). Only an absent shock key defaults to zero.
    state = _linear_state()
    state["stress_assumptions"] = {"revenue_haircut_pct": "lots"}
    with pytest.raises(StressAssumptionsError) as excinfo:
        compute_from_state(state)
    assert "revenue_haircut_pct" in str(excinfo.value)


def test_a_present_negative_recorded_shock_refuses_the_report_not_a_reverse_shock():
    state = _linear_state()
    state["stress_assumptions"] = {"opex_increase_pct": -10.0}
    with pytest.raises(StressAssumptionsError):
        compute_from_state(state)


def test_an_absent_recorded_shock_still_defaults_to_zero():
    state = _linear_state()
    report = compute_from_state(state)  # no stress_assumptions key at all
    for shock in _find(report, "dscr")["shocks"]:
        if shock["shock"] == "revenue_haircut_pct":
            assert shock["downside_scenario"]["start"] == 0.0


def test_a_malformed_covenant_entry_is_skipped_not_crashed():
    state = _state([{"metric": "dscr", "type": "minimum", "threshold": 1.25}, "not a covenant"])
    report = compute_from_state(state)
    assert len(report["covenants"]) == 1


def test_evaluate_single_covenant_delegates_to_the_engine():
    raw = _raw()
    model = evaluate_financial_model({"FY-Current": raw})
    covenant = {"metric": "dscr", "type": "minimum", "threshold": 1.25}
    ratios = model["ratios"]["FY-Current"]
    financials = model["financials"]["FY-Current"]
    result = evaluate_single_covenant(covenant, ratios, financials)
    assert result["status"] == "PASS"
    assert result["actual"] == pytest.approx(6.0)
    # The public function must not re-implement the engine's rules: a missing actual
    # stays UNRESOLVABLE through it too.
    unresolved = evaluate_single_covenant(covenant, {}, None)
    assert unresolved["status"] == "UNRESOLVABLE"
