"""Ground truth for the per-asset collateral figures (issue #266, PR D code part).

``ground_truth_figures()`` maps the *stored* ``collateral_analysis`` results to the flat keys
``collateral_<asset_id>_<metric>`` plus ``collateral_max_lgd_pct`` and ``collateral_avg_lgd_pct``
(decisions 3, 4 and 10 on issue #266). It never recalculates a formula, omits (never defaults) a
result that is stale, malformed or N/A, and leaves every existing key and tolerance alone
(F-045, F-046). The Underwriter prompt does not declare these figures yet, so adding them to
ground truth must not change how any current draft is checked.
"""
import copy
import json

import pytest
from collateral_check import calculate_collateral_analysis, canonical_input_digest
from policy_check import compute
from policy_checks import (
    check_draft_compliance,
    check_reported_figures,
    ground_truth_figures,
)
from state_manager import write_state

METRICS = (
    "non_rv_funded_exposure", "rv_exposure", "total_exposure", "net_exposure",
    "collateral_value", "coverage_pct", "uncovered_risk", "lgd_pct", "net_uncovered_risk",
)

FINANCIALS = {"FY-Current": {"raw": {"revenue": 1000}, "ebitda": 510}}
RATIOS = {"FY-Current": {"dscr": 1.5}}
COLLATERAL = [{"exposure": 100, "collateral_value": 80}]


def _asset(asset_id, **overrides):
    asset = {
        "asset_id": asset_id, "asset_class": "Synthetic excavator", "valuation": 100000,
        "down_payment_pct": 0.2, "est_rv_pct": 0.4, "cap_or_model_value": 60000,
        "number_of_units": 1, "perfection_status": "Pending", "lgd_pct": 0.45,
    }
    asset.update(overrides)
    return asset


def _analysis(assets=None, mitigants=None):
    return calculate_collateral_analysis({
        "assets": assets if assets is not None else [_asset("AST-001")],
        "mitigants": mitigants if mitigants is not None else [],
    })


def _redigest(analysis):
    """Re-sign a hand-edited analysis so only the id rules (not the digest check) can reject it."""
    analysis["input_digest"] = canonical_input_digest({
        "assets": [dict(asset["inputs"], asset_id=asset["asset_id"]) for asset in analysis["assets"]],
        "mitigants": analysis["mitigants"],
    })
    return analysis


def _truth(analysis):
    return ground_truth_figures(FINANCIALS, RATIOS, COLLATERAL, None, analysis)


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------

def test_every_metric_of_an_asset_is_mapped_under_its_exact_key():
    analysis = _analysis()
    truth = _truth(analysis)
    results = analysis["assets"][0]["results"]
    for metric in METRICS:
        assert truth[f"collateral_AST-001_{metric}"] == results[metric]
        assert isinstance(truth[f"collateral_AST-001_{metric}"], (int, float))


def test_the_worked_example_figures_are_the_calculators_not_a_recalculation():
    truth = _truth(_analysis())
    assert truth["collateral_AST-001_total_exposure"] == pytest.approx(80000)
    assert truth["collateral_AST-001_uncovered_risk"] == pytest.approx(20000)
    assert truth["collateral_AST-001_lgd_pct"] == pytest.approx(0.45)


def test_two_assets_do_not_collide():
    analysis = _analysis([_asset("AST-001"), _asset("AST-002", valuation=50000, lgd_pct=0.6)])
    truth = _truth(analysis)
    assert truth["collateral_AST-001_total_exposure"] == pytest.approx(80000)
    assert truth["collateral_AST-002_total_exposure"] == pytest.approx(40000)
    assert truth["collateral_AST-002_lgd_pct"] == pytest.approx(0.6)


def test_the_deal_level_lgd_aggregates_are_mapped():
    analysis = _analysis([_asset("AST-001"), _asset("AST-002", valuation=50000, lgd_pct=0.6)])
    truth = _truth(analysis)
    assert truth["collateral_max_lgd_pct"] == pytest.approx(0.6)
    assert truth["collateral_avg_lgd_pct"] == pytest.approx((80000 * 0.45 + 40000 * 0.6) / 120000)


def test_a_missing_lgd_is_omitted_never_defaulted():
    asset = _asset("AST-001")
    del asset["lgd_pct"]
    truth = _truth(_analysis([asset]))
    assert "collateral_AST-001_lgd_pct" not in truth
    assert "collateral_max_lgd_pct" not in truth
    assert "collateral_avg_lgd_pct" not in truth
    assert truth["collateral_AST-001_total_exposure"] == pytest.approx(80000)


def test_an_na_coverage_is_omitted_never_zero():
    truth = _truth(_analysis([_asset("AST-001", valuation=0)]))
    assert "collateral_AST-001_coverage_pct" not in truth
    assert truth["collateral_AST-001_uncovered_risk"] == 0


# ---------------------------------------------------------------------------
# Stale, malformed or ambiguous analyses yield nothing
# ---------------------------------------------------------------------------

def _per_asset_keys(truth):
    return {key for key in truth if key.startswith("collateral_") and key != "collateral_cover_pct"}


def test_a_stale_digest_omits_every_collateral_key():
    analysis = _analysis()
    analysis["assets"][0]["inputs"]["valuation"] = 999999
    assert _per_asset_keys(_truth(analysis)) == set()


def test_a_wrong_stored_digest_omits_every_collateral_key():
    analysis = _analysis()
    analysis["input_digest"] = "0" * 64
    assert _per_asset_keys(_truth(analysis)) == set()


def test_duplicate_asset_ids_in_a_stored_analysis_omit_every_collateral_key():
    analysis = _analysis([_asset("AST-001"), _asset("AST-002")])
    analysis["assets"][1]["asset_id"] = "AST-001"
    assert _per_asset_keys(_truth(_redigest(analysis))) == set()


@pytest.mark.parametrize("broken", [
    "not an object", 7, [], {"assets": "x"}, {"assets": [1]}, {"assets": [{"asset_id": "A"}]},
    {"assets": [{"asset_id": "A", "inputs": {}, "results": "x"}]},
], ids=["string", "number", "list", "assets-not-list", "asset-not-object", "no-inputs", "results-not-object"])
def test_a_malformed_analysis_omits_every_collateral_key(broken):
    assert _per_asset_keys(_truth(broken)) == set()


def test_a_non_finite_stored_input_omits_every_collateral_key():
    analysis = _analysis()
    analysis["assets"][0]["inputs"]["valuation"] = float("nan")
    assert _per_asset_keys(_truth(analysis)) == set()


def test_a_result_that_is_not_a_plain_number_is_omitted():
    analysis = _analysis()
    analysis["assets"][0]["results"]["rv_exposure"] = True
    analysis["assets"][0]["results"]["net_exposure"] = "80000"
    truth = _truth(analysis)
    assert "collateral_AST-001_rv_exposure" not in truth
    assert "collateral_AST-001_net_exposure" not in truth
    assert "collateral_AST-001_total_exposure" in truth


def test_an_asset_id_that_breaks_the_grammar_is_omitted():
    analysis = _analysis()
    analysis["assets"][0]["asset_id"] = "AST_001"
    assert _per_asset_keys(_truth(_redigest(analysis))) == set()


def test_an_asset_whose_results_are_not_an_object_adds_no_keys_for_that_asset():
    analysis = _analysis([_asset("AST-001"), _asset("AST-002")])
    analysis["assets"][1]["results"] = "x"
    truth = _truth(analysis)
    assert "collateral_AST-001_total_exposure" in truth
    assert _per_asset_keys(truth) == {key for key in truth if key.startswith("collateral_AST-001_")} | {
        "collateral_max_lgd_pct", "collateral_avg_lgd_pct"}


def test_deal_results_that_are_not_an_object_add_no_lgd_aggregates():
    analysis = _analysis()
    analysis["results"] = "x"
    truth = _truth(analysis)
    assert "collateral_max_lgd_pct" not in truth and "collateral_avg_lgd_pct" not in truth
    assert "collateral_AST-001_total_exposure" in truth


def test_the_inputs_passed_in_are_not_modified():
    analysis = _analysis()
    before = copy.deepcopy(analysis)
    _truth(analysis)
    assert analysis == before


# ---------------------------------------------------------------------------
# Nothing else changes
# ---------------------------------------------------------------------------

def test_without_an_analysis_the_ground_truth_is_exactly_what_it_was():
    legacy = ground_truth_figures(FINANCIALS, RATIOS, COLLATERAL)
    assert ground_truth_figures(FINANCIALS, RATIOS, COLLATERAL, None, None) == legacy
    assert _per_asset_keys(legacy) == set()


def test_the_new_keys_are_additive_and_the_aggregate_cover_is_unchanged():
    legacy = ground_truth_figures(FINANCIALS, RATIOS, COLLATERAL)
    truth = _truth(_analysis())
    assert {key: truth[key] for key in legacy} == legacy
    assert truth["collateral_cover_pct"] == pytest.approx(80.0)


def test_a_draft_that_declares_none_of_the_new_figures_is_checked_exactly_as_before():
    payload = {
        "cp_ids_included": ["KYC-AML"], "cs_ids_included": ["MI-REPORTING"],
        "risk_categories_covered": {"Market": {"status": "covered"}},
        "reported_figures": {"dscr": 1.5, "ebitda": 510, "collateral_cover_pct": 80.0},
    }
    draft = "# Draft\n\n```json\n" + json.dumps(payload) + "\n```"
    policy_state = {"cam_data_present": True, "covenant_results": [], "security_checks": []}
    legacy = ground_truth_figures(FINANCIALS, RATIOS, COLLATERAL)
    with_analysis = _truth(_analysis())
    assert check_draft_compliance(draft, policy_state, with_analysis) == \
        check_draft_compliance(draft, policy_state, legacy)


# ---------------------------------------------------------------------------
# F-046 still decides the comparison
# ---------------------------------------------------------------------------

def test_exact_figures_pass_and_a_wrong_one_fails_under_the_existing_tolerance():
    truth = _truth(_analysis())
    exact = {"collateral_AST-001_total_exposure": 80000.0, "collateral_AST-001_lgd_pct": 0.45}
    assert check_reported_figures(exact, truth) == []
    within = {"collateral_AST-001_total_exposure": 80000.0 * 1.004}
    assert check_reported_figures(within, truth) == []
    beyond = {"collateral_AST-001_total_exposure": 80000.0 * 1.006}
    reasons = check_reported_figures(beyond, truth)
    assert len(reasons) == 1 and "collateral_AST-001_total_exposure" in reasons[0]


def test_an_actual_zero_passes_only_for_an_absolute_zero():
    truth = _truth(_analysis([_asset("AST-001", cap_or_model_value=90000)]))
    assert truth["collateral_AST-001_uncovered_risk"] == 0
    assert check_reported_figures({"collateral_AST-001_uncovered_risk": 0}, truth) == []
    assert check_reported_figures({"collateral_AST-001_uncovered_risk": 5}, truth) != []


def test_an_unknown_asset_or_metric_key_is_unresolvable():
    truth = _truth(_analysis())
    for key in ("collateral_AST-999_total_exposure", "collateral_AST-001_made_up"):
        reasons = check_reported_figures({key: 1.0}, truth)
        assert reasons and reasons[0].startswith("UNRESOLVABLE_REPORTED_FIGURE")


def test_a_per_asset_key_is_unresolvable_when_no_analysis_is_stored():
    truth = ground_truth_figures(FINANCIALS, RATIOS, COLLATERAL)
    reasons = check_reported_figures({"collateral_AST-001_total_exposure": 80000.0}, truth)
    assert reasons and reasons[0].startswith("UNRESOLVABLE_REPORTED_FIGURE")


# ---------------------------------------------------------------------------
# The interactive path reads the stored analysis
# ---------------------------------------------------------------------------

def _draft(tmp_path, reported):
    payload = {
        "cp_ids_included": ["KYC-AML"], "cs_ids_included": ["MI-REPORTING"],
        "risk_categories_covered": {"Market": {"status": "covered"}},
        "reported_figures": reported,
    }
    path = tmp_path / "draft.md"
    path.write_text("# Draft\n\n```json\n" + json.dumps(payload) + "\n```", encoding="utf-8")
    return str(path)


def test_policy_check_compute_uses_the_stored_analysis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", ratios={"FY-Current": {"dscr": 1.05}},
                collateral_analysis=_analysis())
    right = compute("Acme Corp", "Fleet Loan",
                    draft_path=_draft(tmp_path, {"collateral_AST-001_total_exposure": 80000.0}))
    assert not any("collateral_AST-001" in reason for reason in right["reasons"])
    wrong = compute("Acme Corp", "Fleet Loan",
                    draft_path=_draft(tmp_path, {"collateral_AST-001_total_exposure": 70000.0}))
    assert any("Narrative/Ground-Truth Mismatch: reported collateral_AST-001_total_exposure" in reason
               for reason in wrong["reasons"])


def test_policy_check_compute_without_an_analysis_keeps_the_legacy_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", ratios={"FY-Current": {"dscr": 1.05}})
    result = compute("Acme Corp", "Fleet Loan",
                     draft_path=_draft(tmp_path, {"collateral_AST-001_total_exposure": 80000.0}))
    assert any(reason.startswith("UNRESOLVABLE_REPORTED_FIGURE") and "collateral_AST-001" in reason
               for reason in result["reasons"])


# ---------------------------------------------------------------------------
# Review findings CG559-1, CG559-2, CG559-4
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("asset_id", ["max", "avg"])
def test_an_asset_id_that_would_collide_with_a_deal_level_key_adds_no_keys(asset_id):
    """CG559-1: collateral_max_lgd_pct is also asset 'max' metric lgd_pct; the aggregate overwrote the asset."""
    analysis = _analysis([_asset("AST-001"), _asset("AST-002", lgd_pct=0.3)])
    analysis["assets"][1]["asset_id"] = asset_id  # as a hand-edited state could hold it
    assert _per_asset_keys(_truth(_redigest(analysis))) == set()


@pytest.mark.parametrize("asset_id", [["x"], {"a": 1}, 5, None, True])
def test_an_unhashable_or_non_string_asset_id_is_omitted_not_a_crash(asset_id):
    """CG559-2: set() over the ids raised TypeError for a list or object id with a valid digest."""
    analysis = _analysis()
    analysis["assets"][0]["asset_id"] = asset_id
    assert _per_asset_keys(_truth(_redigest(analysis))) == set()


@pytest.mark.parametrize("huge", [10**400, -(10**400)], ids=["positive", "negative"])
def test_an_integer_too_large_for_a_float_is_omitted_not_a_crash(huge):
    """CG559-4: math.isfinite() raised OverflowError on a huge int."""
    analysis = _analysis()
    analysis["assets"][0]["results"]["total_exposure"] = huge
    truth = _truth(analysis)
    assert "collateral_AST-001_total_exposure" not in truth
    assert "collateral_AST-001_uncovered_risk" in truth
