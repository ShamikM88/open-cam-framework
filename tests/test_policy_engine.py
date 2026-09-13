"""Tests for scripts/policy_engine.py: deterministic covenant, security/
collateral cross-referencing, and Conditions Precedent generation.

evaluate_deal_policy() is meant to be a pure function of its input -- the
same state_dict must always produce the same result, including the same set
of `cp_id`s, so scripts/orchestrator.py can enforce it as a hard governance
gate rather than a suggestion. Most tests below assert on that determinism
directly, not just on the individual rule outcomes.
"""
import pytest

from policy_engine import STANDARD_CONDITIONS_PRECEDENT, evaluate_deal_policy


RATIOS = {"FY-Current": {"dscr": 1.5, "gross_leverage": 3.2, "current_ratio": 2.0}}


def _cp_ids(result):
    return [cp["cp_id"] for cp in result["required_conditions_precedent"]]


# ---------------------------------------------------------------------------
# Standard CPs: always present regardless of deal-specific structure.
# ---------------------------------------------------------------------------

def test_standard_cps_always_included_even_with_no_deal_structure():
    result = evaluate_deal_policy({})
    assert result["required_conditions_precedent"] == STANDARD_CONDITIONS_PRECEDENT
    assert result["covenant_results"] == []
    assert result["security_gaps"] == []


def test_evaluate_deal_policy_handles_none_input():
    result = evaluate_deal_policy(None)
    assert result["required_conditions_precedent"] == STANDARD_CONDITIONS_PRECEDENT


# ---------------------------------------------------------------------------
# Covenant headroom: minimum/maximum, boundary rule, and the UNRESOLVABLE
# fallback for a metric or type that doesn't resolve.
# ---------------------------------------------------------------------------

def test_minimum_covenant_pass_and_headroom():
    state = {"ratios": RATIOS, "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}]}
    result = evaluate_deal_policy(state)
    covenant = result["covenant_results"][0]
    assert covenant["status"] == "PASS"
    assert covenant["actual"] == 1.5
    assert covenant["headroom_pct"] == pytest.approx((1.5 - 1.25) / 1.25)


def test_minimum_covenant_fail():
    state = {"ratios": RATIOS, "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.75}]}
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "FAIL"
    assert covenant["headroom_pct"] == pytest.approx((1.5 - 1.75) / 1.75)


def test_maximum_covenant_pass_and_headroom():
    state = {"ratios": RATIOS, "covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}]}
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "PASS"
    assert covenant["headroom_pct"] == pytest.approx((3.5 - 3.2) / 3.5)


def test_maximum_covenant_fail():
    state = {"ratios": RATIOS, "covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.0}]}
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "FAIL"
    assert covenant["headroom_pct"] == pytest.approx((3.0 - 3.2) / 3.0)


def test_boundary_actual_equals_threshold_is_pass_for_minimum():
    state = {"ratios": RATIOS, "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.5}]}
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "PASS"
    assert covenant["headroom_pct"] == 0


def test_boundary_actual_equals_threshold_is_pass_for_maximum():
    state = {"ratios": RATIOS, "covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.2}]}
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "PASS"
    assert covenant["headroom_pct"] == 0


def test_unrecognized_metric_is_unresolvable_not_silently_skipped():
    state = {"ratios": RATIOS, "covenants": [{"metric": "made_up_metric", "type": "minimum", "threshold": 1.0}]}
    result = evaluate_deal_policy(state)
    assert len(result["covenant_results"]) == 1
    covenant = result["covenant_results"][0]
    assert covenant["status"] == "UNRESOLVABLE"
    assert covenant["actual"] is None
    assert covenant["headroom_pct"] is None
    assert covenant["metric"] == "made_up_metric"  # still identifiable in the output


def test_unrecognized_type_is_unresolvable():
    state = {"ratios": RATIOS, "covenants": [{"metric": "dscr", "type": "at_least", "threshold": 1.0}]}
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "UNRESOLVABLE"


def test_multiple_covenants_evaluated_independently():
    state = {
        "ratios": RATIOS,
        "covenants": [
            {"metric": "dscr", "type": "minimum", "threshold": 1.25},
            {"metric": "gross_leverage", "type": "maximum", "threshold": 3.0},
        ],
    }
    results = evaluate_deal_policy(state)["covenant_results"]
    assert results[0]["status"] == "PASS"
    assert results[1]["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Security & orphan checks: collateral <-> security_package cross-reference
# via asset_id / secures_asset_id.
# ---------------------------------------------------------------------------

def test_fully_compliant_asset_generates_no_gap_or_cp():
    state = {
        "collateral": [{"asset_id": "AST-001", "asset_class": "HGV"}],
        "security_package": [{"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "First"}],
    }
    result = evaluate_deal_policy(state)
    assert result["security_gaps"] == []
    assert _cp_ids(result) == [cp["cp_id"] for cp in STANDARD_CONDITIONS_PRECEDENT]


def test_uncharged_asset_flagged_with_explicit_distinct_text():
    state = {"collateral": [{"asset_id": "AST-002", "asset_class": "Trailer"}], "security_package": []}
    result = evaluate_deal_policy(state)
    assert result["security_gaps"] == [
        "Uncharged Asset: AST-002 has no corresponding security charge registered."
    ]
    assert "SEC-MAPPING-AST-002" in _cp_ids(result)


def test_dangling_reference_flagged_with_explicit_distinct_text():
    state = {
        "collateral": [],
        "security_package": [{"secures_asset_id": "AST-999", "perfection_status": "Perfected", "ranking": "First"}],
    }
    result = evaluate_deal_policy(state)
    assert result["security_gaps"] == [
        "Dangling Reference: AST-999 does not exist in collateral records."
    ]
    assert "SEC-MAPPING-AST-999" in _cp_ids(result)


def test_uncharged_asset_and_dangling_reference_are_both_caught_and_distinct():
    state = {
        "collateral": [{"asset_id": "AST-002"}],
        "security_package": [{"secures_asset_id": "AST-999", "perfection_status": "Perfected", "ranking": "First"}],
    }
    result = evaluate_deal_policy(state)
    assert set(result["security_gaps"]) == {
        "Uncharged Asset: AST-002 has no corresponding security charge registered.",
        "Dangling Reference: AST-999 does not exist in collateral records.",
    }
    assert result["security_gaps"][0] != result["security_gaps"][1]


def test_pending_perfection_status_generates_the_registration_cp():
    state = {
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [{"secures_asset_id": "AST-001", "perfection_status": "Pending", "ranking": "First"}],
    }
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert "SEC-PERFECT-AST-001" in cps
    assert "perfect charge over asset AST-001" in cps["SEC-PERFECT-AST-001"]
    assert "(Current Status: Pending)" in cps["SEC-PERFECT-AST-001"]
    assert "SEC-PRIORITY-AST-001" not in cps  # ranking was fine, no priority CP needed


def test_second_ranking_generates_the_deed_of_priority_cp_with_a_distinct_id():
    state = {
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [{"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "Second"}],
    }
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert "SEC-PRIORITY-AST-001" in cps
    assert "Deed of Priority" in cps["SEC-PRIORITY-AST-001"]
    assert "(Current Ranking: Second)" in cps["SEC-PRIORITY-AST-001"]
    assert "SEC-PERFECT-AST-001" not in cps  # perfection was fine, no registration CP needed


def test_asset_can_fail_both_perfection_and_ranking_independently():
    state = {
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [{"secures_asset_id": "AST-001", "perfection_status": "Pending", "ranking": "Second"}],
    }
    result = evaluate_deal_policy(state)
    cp_ids = _cp_ids(result)
    assert "SEC-PERFECT-AST-001" in cp_ids
    assert "SEC-PRIORITY-AST-001" in cp_ids
    assert len(result["security_gaps"]) == 2


# ---------------------------------------------------------------------------
# Guarantees
# ---------------------------------------------------------------------------

def test_guarantee_cp_uses_amount_when_given():
    state = {"guarantees": [{"provider": "Acme Holdings", "type": "Corporate", "amount": "£500,000"}]}
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert "GUARANTEE-ACME-HOLDINGS" in cps
    assert cps["GUARANTEE-ACME-HOLDINGS"] == "Execution of Corporate Guarantee by Acme Holdings for £500,000."


def test_guarantee_cp_falls_back_to_unlimited_facility_when_amount_missing():
    state = {"guarantees": [{"provider": "Jane Smith", "type": "Personal"}]}
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert "Unlimited Facility" in cps["GUARANTEE-JANE-SMITH"]


# ---------------------------------------------------------------------------
# Determinism: same input must always produce the same cp_ids, in the same
# structure -- this is the property orchestrator.py's governance gate relies on.
# ---------------------------------------------------------------------------

def test_cp_id_generation_is_stable_across_repeated_calls_with_the_same_input():
    state = {
        "collateral": [{"asset_id": "AST-001"}, {"asset_id": "AST-002"}],
        "security_package": [
            {"secures_asset_id": "AST-001", "perfection_status": "Pending", "ranking": "Second"},
        ],
        "guarantees": [{"provider": "Acme Holdings", "type": "Corporate", "amount": "£1,000,000"}],
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
        "ratios": RATIOS,
    }
    first = evaluate_deal_policy(state)
    second = evaluate_deal_policy(state)
    assert first == second
    assert _cp_ids(first) == _cp_ids(second)


def test_slugify_normalizes_special_characters_deterministically():
    state = {"guarantees": [{"provider": "Acme & Sons, Ltd.", "type": "Corporate", "amount": None}]}
    result = evaluate_deal_policy(state)
    cp_ids = _cp_ids(result)
    matching = [cp_id for cp_id in cp_ids if cp_id.startswith("GUARANTEE-")]
    assert matching == ["GUARANTEE-ACME-SONS-LTD"]
