"""Tests for scripts/policy_engine.py: deterministic covenant, security/
collateral cross-referencing, and Conditions Precedent generation.

evaluate_deal_policy() is meant to be a pure function of its input -- the
same state_dict must always produce the same result, including the same set
of `cp_id`s, so scripts/orchestrator.py can enforce it as a hard governance
gate rather than a suggestion. Most tests below assert on that determinism
directly, not just on the individual rule outcomes.
"""
import pytest

from policy_engine import (
    STANDARD_CONDITIONS_PRECEDENT,
    STANDARD_CONDITIONS_SUBSEQUENT,
    evaluate_deal_policy,
)


RATIOS = {"FY-Current": {"dscr": 1.5, "gross_leverage": 3.2, "current_ratio": 2.0}}


def _cp_ids(result):
    return [cp["cp_id"] for cp in result["required_conditions_precedent"]]


def _cs_ids(result):
    return [cs["cs_id"] for cs in result["required_conditions_subsequent"]]


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


def test_zero_threshold_maximum_covenant_reports_headroom_as_none_not_a_misleading_zero():
    """A maximum covenant of 0 with a real breach (actual=5) must still
    FAIL via direct comparison; headroom as a % of a zero threshold is
    undefined, not 0 -- 0 would misleadingly read as "right at the edge"
    rather than "not computable"."""
    state = {
        "ratios": {"FY-Current": {"net_debt_ratio": 5}},
        "covenants": [{"metric": "net_debt_ratio", "type": "maximum", "threshold": 0}],
    }
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "FAIL"
    assert covenant["headroom_pct"] is None


def test_zero_threshold_minimum_covenant_reports_headroom_as_none():
    state = {
        "ratios": {"FY-Current": {"some_ratio": 5}},
        "covenants": [{"metric": "some_ratio", "type": "minimum", "threshold": 0}],
    }
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "PASS"  # 5 >= 0, correctly computed via direct comparison
    assert covenant["headroom_pct"] is None


def test_missing_threshold_is_unresolvable_not_a_crash():
    """A covenant with no `threshold` key at all (threshold=None) must be
    UNRESOLVABLE, not raise -- '>=' between a float and None otherwise
    crashes the comparison below the zero-threshold guard, taking down the
    whole evaluate_deal_policy() call for every other covenant/asset in it."""
    state = {
        "ratios": {"FY-Current": {"dscr": 1.5}},
        "covenants": [{"metric": "dscr", "type": "minimum"}],
    }
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "UNRESOLVABLE"
    assert covenant["actual"] is None
    assert covenant["headroom_pct"] is None


def test_non_numeric_threshold_is_unresolvable_not_a_crash():
    state = {
        "ratios": {"FY-Current": {"dscr": 1.5}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": "1.25"}],
    }
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "UNRESOLVABLE"


def test_missing_threshold_does_not_abort_other_covenants_in_the_same_call():
    state = {
        "ratios": RATIOS,
        "covenants": [
            {"metric": "dscr", "type": "minimum"},  # missing threshold
            {"metric": "gross_leverage", "type": "maximum", "threshold": 3.0},
        ],
    }
    results = evaluate_deal_policy(state)["covenant_results"]
    assert results[0]["status"] == "UNRESOLVABLE"
    assert results[1]["status"] == "FAIL"


def test_undefined_ratio_value_is_unresolvable_not_a_crash():
    """A ratio spreading_builder.py left undefined (None, e.g. a debt-free
    company's DSCR -- see test_spreading_builder.py's
    test_zero_denominator_ratios_are_undefined_not_zero) must be
    UNRESOLVABLE, not crash the comparison -- the metric key is present in
    ratios (unlike the "unrecognized metric" case), but its value can't be
    compared against a threshold."""
    state = {
        "ratios": {"FY-Current": {"dscr": None}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
    }
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "UNRESOLVABLE"
    assert covenant["headroom_pct"] is None


def test_non_numeric_ratio_value_is_unresolvable_not_a_crash():
    state = {
        "ratios": {"FY-Current": {"dscr": "1.5"}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
    }
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] == "UNRESOLVABLE"


def test_debt_free_company_does_not_falsely_fail_a_dscr_covenant():
    """End-to-end regression for the audit's headline finding: a debt-free
    company (undefined DSCR, not a coverage ratio of 0) must not FAIL a
    DSCR covenant -- it's UNRESOLVABLE (the covenant doesn't meaningfully
    apply), never a false breach."""
    state = {
        "ratios": {"FY-Current": {"dscr": None}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
    }
    covenant = evaluate_deal_policy(state)["covenant_results"][0]
    assert covenant["status"] != "FAIL"
    assert covenant["status"] == "UNRESOLVABLE"


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


def test_multiple_charges_over_the_same_asset_are_all_evaluated_not_just_the_last():
    """A senior (compliant) and a subordinate (non-compliant) charge over
    the same asset is a realistic structure -- the non-compliant one must
    never be silently dropped just because another charge for the same
    asset also exists."""
    state = {
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [
            {"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "First"},
            {"secures_asset_id": "AST-001", "perfection_status": "Pending", "ranking": "Second"},
        ],
    }
    result = evaluate_deal_policy(state)
    assert any("Unperfected Security" in gap for gap in result["security_gaps"])
    assert any("Subordinate Ranking" in gap for gap in result["security_gaps"])


def test_multiple_charges_over_the_same_asset_get_distinct_cp_ids():
    state = {
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [
            {"secures_asset_id": "AST-001", "perfection_status": "Pending", "ranking": "First"},
            {"secures_asset_id": "AST-001", "perfection_status": "Pending", "ranking": "First"},
        ],
    }
    result = evaluate_deal_policy(state)
    perfect_cp_ids = [cp_id for cp_id in _cp_ids(result) if cp_id.startswith("SEC-PERFECT-")]
    assert len(perfect_cp_ids) == 2
    assert len(set(perfect_cp_ids)) == 2  # distinct, not both "SEC-PERFECT-AST-001"


def test_single_charge_per_asset_keeps_the_simple_cp_id_unchanged():
    """The common case (one charge per asset) must not gain a suffix just
    because the multi-charge code path now exists."""
    state = {
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [{"secures_asset_id": "AST-001", "perfection_status": "Pending", "ranking": "First"}],
    }
    result = evaluate_deal_policy(state)
    assert "SEC-PERFECT-AST-001" in _cp_ids(result)


def test_security_cps_avoid_cross_collision_between_suffixed_and_naturally_unique_asset_slug():
    """A second asset's own id can naturally slugify to the exact string a
    different, multi-charge asset's disambiguation suffix would produce --
    collision detection has to check every cp_id already assigned in this
    call, not just other charges on the same asset."""
    state = {
        "collateral": [{"asset_id": "AST-1"}, {"asset_id": "AST-1-2"}],
        "security_package": [
            {"secures_asset_id": "AST-1", "perfection_status": "Pending", "ranking": "First"},
            {"secures_asset_id": "AST-1", "perfection_status": "Pending", "ranking": "First"},
            {"secures_asset_id": "AST-1-2", "perfection_status": "Pending", "ranking": "First"},
        ],
    }
    result = evaluate_deal_policy(state)
    perfect_cp_ids = [cp_id for cp_id in _cp_ids(result) if cp_id.startswith("SEC-PERFECT-")]
    assert len(perfect_cp_ids) == 3
    assert len(set(perfect_cp_ids)) == 3


def test_dangling_reference_reported_once_even_with_multiple_charges_on_the_missing_asset():
    state = {
        "collateral": [],
        "security_package": [
            {"secures_asset_id": "AST-999", "perfection_status": "Perfected", "ranking": "First"},
            {"secures_asset_id": "AST-999", "perfection_status": "Pending", "ranking": "Second"},
        ],
    }
    result = evaluate_deal_policy(state)
    assert result["security_gaps"] == ["Dangling Reference: AST-999 does not exist in collateral records."]


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


def test_guarantee_cp_preserves_a_legitimate_zero_amount():
    """A falsy-but-real amount (0) must not be silently rewritten to
    "Unlimited Facility" -- that would materially misstate the CP."""
    state = {"guarantees": [{"provider": "Jane Smith", "type": "Personal", "amount": 0}]}
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert cps["GUARANTEE-JANE-SMITH"] == "Execution of Personal Guarantee by Jane Smith for 0."


def test_guarantee_cp_treats_blank_string_amount_as_missing():
    state = {"guarantees": [{"provider": "Jane Smith", "type": "Personal", "amount": "   "}]}
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert "Unlimited Facility" in cps["GUARANTEE-JANE-SMITH"]


def test_guarantee_cps_use_distinct_ids_when_same_provider_guarantees_two_facilities():
    """The same person/entity guaranteeing two different amounts is a
    realistic deal structure -- both must get a distinct, addressable CP,
    not silently collide on one shared cp_id."""
    state = {"guarantees": [
        {"provider": "Jane Smith", "type": "Personal", "amount": "£100,000"},
        {"provider": "Jane Smith", "type": "Personal", "amount": "£250,000"},
    ]}
    result = evaluate_deal_policy(state)
    guarantee_cps = [cp for cp in result["required_conditions_precedent"] if cp["cp_id"].startswith("GUARANTEE-")]
    cp_ids = [cp["cp_id"] for cp in guarantee_cps]

    assert len(cp_ids) == 2
    assert len(set(cp_ids)) == 2  # distinct, not both "GUARANTEE-JANE-SMITH"
    texts = {cp["cp_id"]: cp["text"] for cp in guarantee_cps}
    assert any("£100,000" in text for text in texts.values())
    assert any("£250,000" in text for text in texts.values())


def test_guarantee_cp_id_unchanged_for_the_common_single_guarantee_case():
    """A guarantee that doesn't collide with anything else must keep the
    same simple cp_id as before -- the disambiguation suffix only appears
    when actually needed."""
    state = {"guarantees": [{"provider": "Acme Holdings", "type": "Corporate", "amount": "£500,000"}]}
    result = evaluate_deal_policy(state)
    cp_ids = [cp["cp_id"] for cp in result["required_conditions_precedent"]]
    assert "GUARANTEE-ACME-HOLDINGS" in cp_ids


def test_guarantee_id_field_overrides_provider_as_the_cp_id_source_when_given():
    state = {"guarantees": [
        {"guarantee_id": "GRT-001", "provider": "Jane Smith", "type": "Personal", "amount": "£100,000"},
        {"guarantee_id": "GRT-002", "provider": "Jane Smith", "type": "Personal", "amount": "£250,000"},
    ]}
    result = evaluate_deal_policy(state)
    cp_ids = {cp["cp_id"] for cp in result["required_conditions_precedent"]}
    assert "GUARANTEE-GRT-001" in cp_ids
    assert "GUARANTEE-GRT-002" in cp_ids


def test_guarantee_cps_are_stable_and_collision_free_across_repeated_calls():
    state = {"guarantees": [
        {"provider": "Jane Smith", "amount": "£100,000"},
        {"provider": "Jane Smith", "amount": "£250,000"},
        {"provider": "Acme Holdings", "amount": None},
    ]}
    first = evaluate_deal_policy(state)
    second = evaluate_deal_policy(state)
    assert first == second


def test_guarantee_cps_avoid_cross_collision_between_suffixed_and_naturally_unique_slug():
    """A third guarantee whose provider naturally slugifies to the exact
    string a colliding pair's disambiguation suffix would produce must
    still end up with a distinct id -- collision detection has to check
    every cp_id already assigned, not just other guarantees sharing the
    same pre-suffix base slug."""
    state = {"guarantees": [
        {"provider": "Acme Corp"},
        {"provider": "Acme Corp"},
        {"provider": "Acme Corp 1"},
    ]}
    result = evaluate_deal_policy(state)
    cp_ids = [cp["cp_id"] for cp in result["required_conditions_precedent"] if cp["cp_id"].startswith("GUARANTEE-")]
    assert len(cp_ids) == 3
    assert len(set(cp_ids)) == 3


def test_guarantee_id_of_zero_is_honored_not_treated_as_missing():
    """A falsy-but-real guarantee_id (0) must be used as given, the same
    way a falsy-but-real amount (0) is -- not silently discarded in favor
    of the provider-based fallback."""
    state = {"guarantees": [
        {"guarantee_id": 0, "provider": "Jane Smith", "amount": "£1"},
        {"provider": "Jane Smith", "amount": "£2"},
    ]}
    result = evaluate_deal_policy(state)
    cp_ids = {cp["cp_id"] for cp in result["required_conditions_precedent"]}
    assert "GUARANTEE-0" in cp_ids


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


def test_slugify_falls_back_to_a_stable_hash_for_symbol_only_input():
    """An asset_id with no alphanumeric characters at all must not collapse
    to an empty slug -- that would make two different such assets collide
    on the same cp_id (e.g. both "SEC-MAPPING-")."""
    state = {
        "collateral": [{"asset_id": "***"}, {"asset_id": "---"}],
        "security_package": [],
    }
    result = evaluate_deal_policy(state)
    cp_ids = [cp_id for cp_id in _cp_ids(result) if cp_id.startswith("SEC-MAPPING-")]
    assert len(cp_ids) == 2
    assert len(set(cp_ids)) == 2  # distinct, not both "SEC-MAPPING-"
    assert all(cp_id != "SEC-MAPPING-" for cp_id in cp_ids)

    # Stability: the same symbol-only input always slugifies to the same id.
    again = evaluate_deal_policy(state)
    assert _cp_ids(result) == _cp_ids(again)


# ---------------------------------------------------------------------------
# Downside covenant breaches: purely additive on top of _evaluate_covenant().
# covenant_results (the FY-Current gate) must be completely unaffected.
# ---------------------------------------------------------------------------

def test_base_pass_downside_fail_populates_downside_covenant_breaches():
    state = {
        "ratios": {
            "FY-Current": {"dscr": 1.5},
            "FY+2": {"dscr": 1.25},
        },
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.10}],
        "downside_case": {"ratios": {"FY+2": {"dscr": 1.05}}},
    }
    result = evaluate_deal_policy(state)
    breaches = result["downside_covenant_breaches"]

    assert len(breaches) == 1
    breach = breaches[0]
    assert breach["year"] == "FY+2"
    assert breach["metric"] == "dscr"
    assert breach["base_actual"] == 1.25
    assert breach["downside_actual"] == 1.05
    assert breach["threshold"] == 1.10
    assert breach["breach_id"]


def test_downside_breach_id_is_stable_and_repeatable_across_calls():
    state = {
        "ratios": {"FY+2": {"dscr": 1.25}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.10}],
        "downside_case": {"ratios": {"FY+2": {"dscr": 1.05}}},
    }
    first = evaluate_deal_policy(state)["downside_covenant_breaches"]
    second = evaluate_deal_policy(state)["downside_covenant_breaches"]
    assert first == second
    assert first[0]["breach_id"] == second[0]["breach_id"]


def test_covenant_results_unaffected_by_downside_covenant_breach_logic():
    """The existing FY-Current gate must be byte-for-byte the same whether
    or not a downside_case is present -- this is purely additive."""
    covenants = [{"metric": "dscr", "type": "minimum", "threshold": 1.10}]
    without_downside = evaluate_deal_policy({
        "ratios": {"FY-Current": {"dscr": 1.5}},
        "covenants": covenants,
    })
    with_downside = evaluate_deal_policy({
        "ratios": {"FY-Current": {"dscr": 1.5}, "FY+2": {"dscr": 1.25}},
        "covenants": covenants,
        "downside_case": {"ratios": {"FY+2": {"dscr": 1.05}}},
    })
    assert without_downside["covenant_results"] == with_downside["covenant_results"]


def test_no_downside_breach_when_covenant_passes_in_both_cases():
    state = {
        "ratios": {"FY+2": {"dscr": 1.5}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.10}],
        "downside_case": {"ratios": {"FY+2": {"dscr": 1.20}}},
    }
    assert evaluate_deal_policy(state)["downside_covenant_breaches"] == []


def test_no_downside_breach_when_base_already_fails():
    """Base already FAILs -- covered by the ordinary covenant_results gate,
    not a "downside-specific" breach (the whole point of this list is
    "passes today, fails only under stress")."""
    state = {
        "ratios": {"FY+2": {"dscr": 1.0}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.10}],
        "downside_case": {"ratios": {"FY+2": {"dscr": 0.8}}},
    }
    assert evaluate_deal_policy(state)["downside_covenant_breaches"] == []


def test_downside_covenant_breaches_empty_when_no_downside_case_present():
    state = {
        "ratios": {"FY-Current": {"dscr": 1.5}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.10}],
    }
    assert evaluate_deal_policy(state)["downside_covenant_breaches"] == []


def test_downside_covenant_breaches_across_multiple_forward_years():
    state = {
        "ratios": {"FY+1": {"dscr": 1.5}, "FY+2": {"dscr": 1.25}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.10}],
        "downside_case": {"ratios": {
            "FY+1": {"dscr": 1.20},  # still passes downside
            "FY+2": {"dscr": 1.05},  # breaches downside
        }},
    }
    breaches = evaluate_deal_policy(state)["downside_covenant_breaches"]
    assert len(breaches) == 1
    assert breaches[0]["year"] == "FY+2"


# ---------------------------------------------------------------------------
# Conditions Subsequent: purely additive on top of everything above --
# covenant_results/security_gaps/required_conditions_precedent/
# downside_covenant_breaches must be byte-for-byte unaffected by this
# module's introduction (mirrors this file's own precedent for
# downside_covenant_breaches when it was added).
# ---------------------------------------------------------------------------

def test_standard_cs_always_included_even_with_no_deal_structure():
    result = evaluate_deal_policy({})
    assert result["required_conditions_subsequent"] == STANDARD_CONDITIONS_SUBSEQUENT


def test_evaluate_deal_policy_handles_none_input_for_cs_too():
    result = evaluate_deal_policy(None)
    assert result["required_conditions_subsequent"] == STANDARD_CONDITIONS_SUBSEQUENT


def test_existing_keys_are_byte_for_byte_unaffected_by_cs_introduction():
    """Additive-only: every pre-existing return key must be identical to
    what evaluate_deal_policy() produced before required_conditions_subsequent
    existed at all, for a state exercising every CP-driving code path."""
    state = {
        "ratios": RATIOS,
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [
            {"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "First"},
        ],
        "guarantees": [{"provider": "Acme Holdings", "type": "Corporate", "amount": "£500,000"}],
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
    }
    result = evaluate_deal_policy(state)
    assert result["covenant_results"] == [
        {"metric": "dscr", "type": "minimum", "threshold": 1.25, "actual": 1.5,
         "status": "PASS", "headroom_pct": pytest.approx((1.5 - 1.25) / 1.25)},
    ]
    assert result["security_gaps"] == []
    assert _cp_ids(result) == [
        "KYC-AML", "FACILITY-EXECUTION", "GUARANTEE-ACME-HOLDINGS",
    ]
    assert result["downside_covenant_breaches"] == []


def test_covenant_cs_appears_iff_covenants_non_empty():
    with_covenants = evaluate_deal_policy({
        "ratios": RATIOS,
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
    })
    without_covenants = evaluate_deal_policy({"ratios": RATIOS})

    assert "CS-COVENANT-COMPLIANCE" in _cs_ids(with_covenants)
    assert "CS-COVENANT-COMPLIANCE" not in _cs_ids(without_covenants)


def test_covenant_cs_is_one_entry_for_the_whole_deal_not_one_per_covenant():
    state = {
        "ratios": RATIOS,
        "covenants": [
            {"metric": "dscr", "type": "minimum", "threshold": 1.25},
            {"metric": "gross_leverage", "type": "maximum", "threshold": 3.5},
        ],
    }
    result = evaluate_deal_policy(state)
    covenant_cs_ids = [cs_id for cs_id in _cs_ids(result) if cs_id.startswith("CS-COVENANT-")]
    assert covenant_cs_ids == ["CS-COVENANT-COMPLIANCE"]


def test_security_cs_appears_only_for_a_perfected_charge():
    perfected = evaluate_deal_policy({
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [
            {"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "First"},
        ],
    })
    pending = evaluate_deal_policy({
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [
            {"secures_asset_id": "AST-001", "perfection_status": "Pending", "ranking": "First"},
        ],
    })
    no_security = evaluate_deal_policy({})

    assert "CS-SEC-REPERFECT-AST-001" in _cs_ids(perfected)
    assert not any(cs_id.startswith("CS-SEC-REPERFECT-") for cs_id in _cs_ids(pending))
    assert not any(cs_id.startswith("CS-SEC-REPERFECT-") for cs_id in _cs_ids(no_security))


def test_security_cs_gets_distinct_ids_for_multiple_perfected_charges_on_the_same_asset():
    state = {
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [
            {"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "First"},
            {"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "Second"},
        ],
    }
    result = evaluate_deal_policy(state)
    reperfect_ids = [cs_id for cs_id in _cs_ids(result) if cs_id.startswith("CS-SEC-REPERFECT-")]
    assert len(reperfect_ids) == 2
    assert len(set(reperfect_ids)) == 2


def test_guarantee_cs_appears_iff_guarantees_non_empty():
    with_guarantee = evaluate_deal_policy({
        "guarantees": [{"provider": "Acme Holdings", "type": "Corporate", "amount": "£500,000"}],
    })
    without_guarantee = evaluate_deal_policy({})

    assert "CS-GUARANTEE-ACME-HOLDINGS" in _cs_ids(with_guarantee)
    assert not any(cs_id.startswith("CS-GUARANTEE-") for cs_id in _cs_ids(without_guarantee))


def test_guarantee_cs_gets_distinct_ids_for_two_guarantees_from_the_same_provider():
    state = {"guarantees": [
        {"provider": "Jane Smith", "type": "Personal", "amount": "£100,000"},
        {"provider": "Jane Smith", "type": "Personal", "amount": "£250,000"},
    ]}
    result = evaluate_deal_policy(state)
    guarantee_cs_ids = [cs_id for cs_id in _cs_ids(result) if cs_id.startswith("CS-GUARANTEE-")]
    assert len(guarantee_cs_ids) == 2
    assert len(set(guarantee_cs_ids)) == 2


def test_cs_id_generation_is_stable_across_repeated_calls_with_the_same_input():
    state = {
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [
            {"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "First"},
        ],
        "guarantees": [{"provider": "Acme Holdings", "type": "Corporate", "amount": "£1,000,000"}],
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
        "ratios": RATIOS,
    }
    first = evaluate_deal_policy(state)
    second = evaluate_deal_policy(state)
    assert first == second
    assert _cs_ids(first) == _cs_ids(second)


def test_cp_and_cs_id_namespaces_never_collide():
    """A cs_id must never be indistinguishable from a cp_id -- every
    deal-driven CS id carries a "CS-" prefix distinct from every CP prefix
    ("SEC-", "GUARANTEE-") used for the equivalent structure."""
    state = {
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [
            {"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "First"},
        ],
        "guarantees": [{"provider": "Acme Holdings", "amount": "£1,000,000"}],
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
        "ratios": RATIOS,
    }
    result = evaluate_deal_policy(state)
    assert set(_cp_ids(result)).isdisjoint(set(_cs_ids(result)))
