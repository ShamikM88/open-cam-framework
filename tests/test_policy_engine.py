"""Tests for scripts/policy_engine.py: deterministic covenant, security/
collateral cross-referencing, and Conditions Precedent generation.

evaluate_deal_policy() is meant to be a pure function of its input -- the
same state_dict must always produce the same result, including the same set
of `cp_id`s, so scripts/orchestrator.py can enforce it as a hard governance
gate rather than a suggestion. Most tests below assert on that determinism
directly, not just on the individual rule outcomes.
"""
import json

import pytest

from policy_engine import (
    STANDARD_CONDITIONS_PRECEDENT,
    STANDARD_CONDITIONS_SUBSEQUENT,
    _evaluate_downside_covenants,
    _evaluate_forward_covenants,
    _period_dict,
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


@pytest.mark.parametrize(
    "actual, threshold, bad_value",
    [(87, 1.0, "actual"), (0.87, 80, "threshold")],
    ids=["actual-written-as-percent", "threshold-written-as-percent"],
)
def test_percent_type_ratio_unit_mismatch_is_unresolvable(actual, threshold, bad_value):
    state = {
        "ratios": {"FY-Current": {"gearing": actual}},
        "covenants": [{"metric": "gearing", "type": "maximum", "threshold": threshold}],
    }

    result = evaluate_deal_policy(state)["covenant_results"][0]

    assert result["status"] == "UNRESOLVABLE"
    assert result["actual"] is None
    assert result["headroom_pct"] is None
    assert bad_value in result["reason"]
    assert "percent" in result["reason"].lower()
    assert "fraction" in result["reason"].lower()


@pytest.mark.parametrize(
    "threshold, expected",
    [(1.0, "PASS"), (0.8, "FAIL")],
    ids=["fraction-passes", "fraction-fails"],
)
def test_genuine_fractional_gearing_keeps_existing_covenant_result(threshold, expected):
    state = {
        "ratios": {"FY-Current": {"gearing": 0.87}},
        "covenants": [{"metric": "gearing", "type": "maximum", "threshold": threshold}],
    }

    result = evaluate_deal_policy(state)["covenant_results"][0]

    assert result["status"] == expected
    assert result["actual"] == 0.87


def test_nonpositive_gearing_denominator_keeps_existing_unresolvable_reason_before_unit_check():
    state = {
        "ratios": {"FY-Current": {"gearing": 0.87}},
        "financials": {"FY-Current": {"total_equity": 0}},
        "covenants": [{"metric": "gearing", "type": "maximum", "threshold": 80}],
    }

    result = evaluate_deal_policy(state)["covenant_results"][0]

    assert result["status"] == "UNRESOLVABLE"
    assert "total equity is zero" in result["reason"]
    assert "fraction" not in result["reason"].lower()


def test_percent_type_threshold_mismatch_precedes_missing_actual():
    state = {
        "ratios": {"FY-Current": {"gearing": None}},
        "covenants": [{"metric": "gearing", "type": "maximum", "threshold": 80}],
    }

    result = evaluate_deal_policy(state)["covenant_results"][0]

    assert result["status"] == "UNRESOLVABLE"
    assert result["actual"] is None
    assert "threshold" in result["reason"].lower()
    assert "percent" in result["reason"].lower()
    assert "fraction" in result["reason"].lower()


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


def test_guarantee_cp_missing_amount_reads_amount_to_be_confirmed():
    """An absent amount must read as an amount to be confirmed, never as
    unlimited cover (issue #505; wording recorded by the maintainer on the issue)."""
    state = {"guarantees": [{"provider": "Jane Smith", "type": "Personal"}]}
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert cps["GUARANTEE-JANE-SMITH"] == "Execution of Personal Guarantee by Jane Smith for an amount to be confirmed."
    assert "Unlimited" not in cps["GUARANTEE-JANE-SMITH"]


def test_guarantee_cp_missing_amount_without_type_reads_amount_to_be_confirmed():
    state = {"guarantees": [{"provider": "Jane Smith"}]}
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert cps["GUARANTEE-JANE-SMITH"] == "Execution of Guarantee by Jane Smith for an amount to be confirmed."


def test_guarantee_cp_preserves_a_legitimate_zero_amount():
    """A falsy-but-real amount (0) must not be silently rewritten to
    "an amount to be confirmed" -- that would materially misstate the CP."""
    state = {"guarantees": [{"provider": "Jane Smith", "type": "Personal", "amount": 0}]}
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert cps["GUARANTEE-JANE-SMITH"] == "Execution of Personal Guarantee by Jane Smith for 0."
    assert "an amount to be confirmed" not in cps["GUARANTEE-JANE-SMITH"]

    missing_provider = evaluate_deal_policy({"guarantees": [{"amount": 0}]})
    assert [
        cp for cp in missing_provider["required_conditions_precedent"] if cp["cp_id"].startswith("GUARANTEE-")
    ] == [{
        "cp_id": "GUARANTEE-E3B0C44298FC",
        "text": "Execution of Guarantee by  for 0.",
    }]


def test_guarantee_cp_treats_blank_string_amount_as_missing():
    state = {"guarantees": [{"provider": "Jane Smith", "type": "Personal", "amount": "   "}]}
    result = evaluate_deal_policy(state)
    cps = {cp["cp_id"]: cp["text"] for cp in result["required_conditions_precedent"]}
    assert cps["GUARANTEE-JANE-SMITH"] == "Execution of Personal Guarantee by Jane Smith for an amount to be confirmed."


_ABSENT_AMOUNT = object()  # sentinel: the "amount" key is left out entirely


def _guarantee_cp_text(amount, guarantee_type):
    guarantee = {"provider": "Synthetic Guarantor"}
    if guarantee_type is not None:
        guarantee["type"] = guarantee_type
    if amount is not _ABSENT_AMOUNT:
        guarantee["amount"] = amount
    result = evaluate_deal_policy({"guarantees": [guarantee]})
    [text] = [cp["text"] for cp in result["required_conditions_precedent"] if cp["cp_id"].startswith("GUARANTEE-")]
    return text


# Exact CP sentences for every absent-amount form, typed and untyped (review findings CG516-1 / PX516-2;
# wording recorded by the maintainer on #505).
@pytest.mark.parametrize("amount", [_ABSENT_AMOUNT, None, "", "   ", " \t "],
                         ids=["key-absent", "none", "empty", "spaces", "tab-whitespace"])
@pytest.mark.parametrize(("guarantee_type", "expected"), [
    ("Personal", "Execution of Personal Guarantee by Synthetic Guarantor for an amount to be confirmed."),
    (None, "Execution of Guarantee by Synthetic Guarantor for an amount to be confirmed."),
], ids=["typed", "untyped"])
def test_guarantee_cp_absent_amount_exact_text(amount, guarantee_type, expected):
    assert _guarantee_cp_text(amount, guarantee_type) == expected


# A recorded amount, including a zero in any form, is kept exactly as written.
@pytest.mark.parametrize(("amount", "written"), [
    (0, "0"), (0.0, "0.0"), ("0", "0"), ("£500,000", "£500,000"), (250000, "250000"),
], ids=["int-zero", "float-zero", "string-zero", "non-blank-string", "positive-number"])
@pytest.mark.parametrize(("guarantee_type", "prefix"), [
    ("Personal", "Execution of Personal Guarantee by Synthetic Guarantor for "),
    (None, "Execution of Guarantee by Synthetic Guarantor for "),
], ids=["typed", "untyped"])
def test_guarantee_cp_recorded_amount_kept_as_written(amount, written, guarantee_type, prefix):
    assert _guarantee_cp_text(amount, guarantee_type) == f"{prefix}{written}."


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

    repeated = evaluate_deal_policy({"guarantees": [
        {"provider": "Repeated Synthetic Provider", "amount": amount} for amount in (10, 20, 30, 40)
    ]})
    assert [
        cp["cp_id"] for cp in repeated["required_conditions_precedent"] if cp["cp_id"].startswith("GUARANTEE-")
    ] == [
        "GUARANTEE-REPEATED-SYNTHETIC-PROVIDER",
        "GUARANTEE-REPEATED-SYNTHETIC-PROVIDER-2",
        "GUARANTEE-REPEATED-SYNTHETIC-PROVIDER-3",
        "GUARANTEE-REPEATED-SYNTHETIC-PROVIDER-4",
    ]
    assert [
        cs["cs_id"] for cs in repeated["required_conditions_subsequent"] if cs["cs_id"].startswith("CS-GUARANTEE-")
    ] == [
        "CS-GUARANTEE-REPEATED-SYNTHETIC-PROVIDER",
        "CS-GUARANTEE-REPEATED-SYNTHETIC-PROVIDER-2",
        "CS-GUARANTEE-REPEATED-SYNTHETIC-PROVIDER-3",
        "CS-GUARANTEE-REPEATED-SYNTHETIC-PROVIDER-4",
    ]


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

    single_character_slug = evaluate_deal_policy({"guarantees": [{"provider": "X"}]})
    assert [
        cp["cp_id"] for cp in single_character_slug["required_conditions_precedent"]
        if cp["cp_id"].startswith("GUARANTEE-")
    ] == ["GUARANTEE-X"]


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

    # Pin the digest's input, case and exact twelve-character width, not only uniqueness.
    symbol_hashes = evaluate_deal_policy({
        "collateral": [{"asset_id": "!!!"}, {"asset_id": "???"}],
        "security_package": [],
    })
    assert [
        cp["cp_id"] for cp in symbol_hashes["required_conditions_precedent"]
        if cp["cp_id"].startswith("SEC-MAPPING-")
    ] == ["SEC-MAPPING-E84C538E7FE2", "SEC-MAPPING-A03B221C6C6E"]


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


@pytest.mark.parametrize(
    ("basis", "group_basis"),
    [
        ("borrower", None),
        ("consolidated", {"basis": "supplied_consolidated"}),
    ],
)
def test_downside_breach_carries_an_explicit_matching_basis(basis, group_basis):
    state = {
        "ratios": {"FY+2": {"dscr": 1.25}},
        "covenants": [{
            "metric": "dscr", "type": "minimum", "threshold": 1.10, "basis": basis,
        }],
        "downside_case": {"ratios": {"FY+2": {"dscr": 1.05}}},
    }
    if group_basis is not None:
        state["group_basis"] = group_basis

    (breach,) = evaluate_deal_policy(state)["downside_covenant_breaches"]

    assert breach["basis"] == basis


def test_downside_breach_omits_basis_for_a_plain_borrower_covenant():
    state = {
        "ratios": {"FY+2": {"dscr": 1.25}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.10}],
        "downside_case": {"ratios": {"FY+2": {"dscr": 1.05}}},
    }

    (breach,) = evaluate_deal_policy(state)["downside_covenant_breaches"]

    assert "basis" not in breach


@pytest.mark.parametrize(
    ("basis_fields", "expected_basis"),
    [
        ({"basis": "borrower"}, "borrower"),
        ({}, None),
    ],
)
def test_unavailable_downside_breach_copies_only_a_present_base_basis(basis_fields, expected_basis):
    covenant = {
        "metric": "dscr", "type": "minimum", "threshold": 1.10, **basis_fields,
    }
    state = {
        "ratios": {"FY+2": {"dscr": 1.25}},
        "covenants": [covenant],
        "downside_case": {"unavailable": {"FY+2": "no downside scenario was supplied"}},
    }

    (breach,) = evaluate_deal_policy(state)["downside_covenant_breaches"]

    if expected_basis is None:
        assert "basis" not in breach
    else:
        assert breach["basis"] == expected_basis


def test_unstated_basis_on_consolidated_view_is_unresolvable_not_a_downside_breach():
    state = {
        "group_basis": {"basis": "supplied_consolidated"},
        "ratios": {
            "FY-Current": {"dscr": 1.5},
            "FY+2": {"dscr": 1.25},
        },
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.10}],
        "downside_case": {"ratios": {"FY+2": {"dscr": 1.05}}},
    }

    result = evaluate_deal_policy(state)

    assert result["covenant_results"][0]["status"] == "UNRESOLVABLE"
    assert result["downside_covenant_breaches"] == []


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
         "status": "PASS", "headroom_pct": pytest.approx((1.5 - 1.25) / 1.25), "reason": None},
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

    missing_provider = evaluate_deal_policy({"guarantees": [{"amount": 0}]})
    assert [
        cs for cs in missing_provider["required_conditions_subsequent"] if cs["cs_id"].startswith("CS-GUARANTEE-")
    ] == [{
        "cs_id": "CS-GUARANTEE-E3B0C44298FC",
        "text": "Ongoing (e.g. annual) reconfirmation of the continued standing of guarantor .",
    }]


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


# ---------------------------------------------------------------------------
# Downside breaches: which years are compared (issue #219)
# ---------------------------------------------------------------------------

def test_a_downside_year_with_no_base_case_ratios_is_not_compared_and_cannot_crash_the_check():
    """A breach is a base PASS that fails under stress, so a stressed year with no base-case ratios has nothing to
    compare with: it is skipped, never a breach and never a KeyError, while a year that has both is still compared."""
    covenants = [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}]
    base = {"FY+1": {"gross_leverage": 2.0}}
    downside = {"FY+1": {"gross_leverage": 5.0}, "FY+2": {"gross_leverage": 5.0}}      # FY+2 has no base case
    breaches = _evaluate_downside_covenants(covenants, base, downside)
    assert [(b["year"], b["metric"], b["downside_status"], b["breach_id"]) for b in breaches] == [
        ("FY+1", "gross_leverage", "FAIL", "DOWNSIDE-FY-1-GROSS-LEVERAGE")]
    assert _evaluate_downside_covenants(covenants, {}, downside) == []
    assert _evaluate_downside_covenants(covenants, None, downside) == []


# ---------------------------------------------------------------------------
# Downside breaches: the full record, the stressed-but-unresolvable case and the unavailable-downside case (issue #183)
#
# A downside breach is a covenant that PASSes in a year's base case but is FAIL or UNRESOLVABLE under stress, or whose stress
# could not be derived at all (`downside_case["unavailable"]`). It is enforced when a draft is checked, so a missing or
# renamed field, a dropped status or a silently skipped year is a governance defect, not cosmetics. The deal-level tests of
# the unavailable case (tests/test_supplied_forecast.py) run the same code; these pin it at the function.
# ---------------------------------------------------------------------------

LEVERAGE_MAX = {"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}
DSCR_MIN = {"metric": "dscr", "type": "minimum", "threshold": 1.25}


def test_a_failing_downside_year_is_recorded_with_every_field_the_draft_check_and_the_model_read():
    breaches = _evaluate_downside_covenants(
        [LEVERAGE_MAX], {"FY+1": {"gross_leverage": 2.0}}, {"FY+1": {"gross_leverage": 5.0}})
    assert breaches == [{
        "year": "FY+1", "metric": "gross_leverage", "base_actual": 2.0, "downside_actual": 5.0, "threshold": 3.5,
        "breach_id": "DOWNSIDE-FY-1-GROSS-LEVERAGE", "downside_status": "FAIL", "reason": None}]


def test_two_downside_covenants_on_one_metric_get_distinct_stable_ids():
    covenants = [
        {"metric": "dscr", "type": "minimum", "threshold": 1.2},
        {"metric": "dscr", "type": "minimum", "threshold": 1.5},
    ]
    base = {"FY+1": {"dscr": 1.6}}
    downside = {"FY+1": {"dscr": 1.0}}

    first = _evaluate_downside_covenants(covenants, base, downside)
    second = _evaluate_downside_covenants(covenants, base, downside)

    assert [breach["breach_id"] for breach in first] == [
        "DOWNSIDE-FY-1-DSCR", "DOWNSIDE-FY-1-DSCR-2"]
    assert first == second


def test_two_covenants_on_one_metric_get_distinct_ids_when_downside_is_unavailable():
    covenants = [
        {"metric": "dscr", "type": "minimum", "threshold": 1.2},
        {"metric": "dscr", "type": "minimum", "threshold": 1.5},
    ]
    breaches = _evaluate_downside_covenants(
        covenants,
        {"FY+1": {"dscr": 1.6}},
        {},
        unavailable={"FY+1": "no downside scenario was supplied for this year"},
    )

    assert [breach["breach_id"] for breach in breaches] == [
        "DOWNSIDE-FY-1-DSCR", "DOWNSIDE-FY-1-DSCR-2"]
    assert [breach["downside_status"] for breach in breaches] == ["UNRESOLVABLE", "UNRESOLVABLE"]


def test_a_downside_ratio_that_cannot_be_computed_is_an_unresolvable_breach_not_a_pass():
    """Base PASS -> stressed UNRESOLVABLE is enforced exactly like FAIL, with the reason naming why the ratio is N/A."""
    breaches = _evaluate_downside_covenants(
        [LEVERAGE_MAX], {"FY+1": {"gross_leverage": 2.0}}, {"FY+1": {"gross_leverage": None}},
        base_financials={"FY+1": {"ebitda": 400}}, downside_financials={"FY+1": {"ebitda": -50}})
    (breach,) = breaches
    assert breach["downside_status"] == "UNRESOLVABLE" and breach["downside_actual"] is None
    assert breach["base_actual"] == 2.0 and breach["threshold"] == 3.5
    assert "EBITDA is negative" in breach["reason"]


def test_a_stressed_pass_and_a_base_failure_are_not_breaches():
    """Only base PASS -> stressed non-PASS is a downside breach: a stressed pass is not, and a base failure is disclosed once
    in the forward results, not again here."""
    covenants = [LEVERAGE_MAX, DSCR_MIN]
    base = {"FY+1": {"gross_leverage": 2.0, "dscr": 0.9}}                       # leverage passes, DSCR already fails
    downside = {"FY+1": {"gross_leverage": 3.0, "dscr": 0.5}}                   # leverage still passes under stress
    assert _evaluate_downside_covenants(covenants, base, downside) == []


def test_a_year_without_base_ratios_is_skipped_but_the_years_after_it_are_still_compared():
    """The skip is per year (a `continue`), not the end of the check: a stressed FY+1 with no base case sits before a
    FY+2 that does have one, and FY+2's breach must still be found."""
    base = {"FY+2": {"gross_leverage": 2.0}}
    downside = {"FY+1": {"gross_leverage": 9.0}, "FY+2": {"gross_leverage": 5.0}}
    breaches = _evaluate_downside_covenants([LEVERAGE_MAX], base, downside)
    assert [(b["year"], b["downside_status"]) for b in breaches] == [("FY+2", "FAIL")]


def test_an_unavailable_downside_is_an_unresolvable_breach_for_every_covenant_that_passes_in_the_base_case():
    """The stress could not be derived for FY+2, so a covenant that passes there cannot be shown to survive it: it is
    recorded UNRESOLVABLE with no downside figure and a reason that names the year and why, never silently absent."""
    base = {"FY+2": {"gross_leverage": 2.0, "dscr": 2.0}}
    breaches = _evaluate_downside_covenants(
        [LEVERAGE_MAX, DSCR_MIN], base, {}, unavailable={"FY+2": "no downside scenario was supplied for this year"})
    assert breaches == [
        {"year": "FY+2", "metric": "gross_leverage", "base_actual": 2.0, "downside_actual": None, "threshold": 3.5,
         "breach_id": "DOWNSIDE-FY-2-GROSS-LEVERAGE", "downside_status": "UNRESOLVABLE",
         "reason": "downside analysis unavailable for FY+2: no downside scenario was supplied for this year"},
        {"year": "FY+2", "metric": "dscr", "base_actual": 2.0, "downside_actual": None, "threshold": 1.25,
         "breach_id": "DOWNSIDE-FY-2-DSCR", "downside_status": "UNRESOLVABLE",
         "reason": "downside analysis unavailable for FY+2: no downside scenario was supplied for this year"}]

    # The recorded view must reach the unavailable-year pass as well as the ordinary covenant and forward paths.
    consolidated = evaluate_deal_policy({
        "group_basis": GROUP_BASIS,
        "ratios": {"FY-Current": {"dscr": 2.0}, "FY+1": {"dscr": 2.0}},
        "covenants": [{
            "metric": "dscr", "type": "minimum", "threshold": 1.25, "basis": "consolidated",
        }],
        "downside_case": {"unavailable": {"FY+1": "no scenario was supplied for this year"}},
    })
    assert consolidated["downside_covenant_breaches"] == [{
        "year": "FY+1",
        "metric": "dscr",
        "base_actual": 2.0,
        "downside_actual": None,
        "threshold": 1.25,
        "breach_id": "DOWNSIDE-FY-1-DSCR",
        "downside_status": "UNRESOLVABLE",
        "reason": "downside analysis unavailable for FY+1: no scenario was supplied for this year",
        "basis": "consolidated",
    }]


@pytest.mark.parametrize("label, base, unavailable", [
    ("the base case already fails", {"FY+2": {"gross_leverage": 4.0}}, {"FY+2": "x"}),
    ("the base ratios are empty", {"FY+2": {}}, {"FY+2": "x"}),
    ("the base ratios are not an object", {"FY+2": [1, 2]}, {"FY+2": "x"}),
    ("the year has no base ratios", {"FY+1": {"gross_leverage": 2.0}}, {"FY+2": "x"}),
    ("no base ratios at all", None, {"FY+2": "x"}),
    ("unavailable is not a mapping", {"FY+2": {"gross_leverage": 2.0}}, ["FY+2"]),
    ("nothing is unavailable", {"FY+2": {"gross_leverage": 2.0}}, None),
])
def test_an_unavailable_downside_adds_no_breach_where_there_is_nothing_to_survive(label, base, unavailable):
    assert _evaluate_downside_covenants([LEVERAGE_MAX], base, {}, unavailable=unavailable) == []


def test_a_non_mapping_base_period_adds_no_unavailable_downside_breach():
    """A truthy non-mapping is malformed base data, not a usable ratio map; it is skipped before the metric lookup."""
    # A truthy non-mapping is malformed base data, not a usable ratio map; skip it before metric lookup.
    assert _evaluate_downside_covenants(
        [DSCR_MIN], {"FY+1": "dscr"}, {}, unavailable={"FY+1": "no ratio object"}
    ) == []


def test_a_year_with_a_derived_downside_is_not_also_reported_as_unavailable():
    """If the year has downside ratios the first loop has judged it; `unavailable` must not produce a second record."""
    base = {"FY+1": {"gross_leverage": 2.0}}
    breaches = _evaluate_downside_covenants(
        [LEVERAGE_MAX], base, {"FY+1": {"gross_leverage": 3.0}}, unavailable={"FY+1": "stale note"})
    assert breaches == []
    failing = _evaluate_downside_covenants(
        [LEVERAGE_MAX], base, {"FY+1": {"gross_leverage": 5.0}}, unavailable={"FY+1": "stale note"})
    assert [b["downside_status"] for b in failing] == ["FAIL"], "one record, from the derived downside"


def test_evaluate_deal_policy_passes_the_downside_figures_and_the_unavailable_years_through():
    state = {
        "ratios": {"FY-Current": {"gross_leverage": 2.0}, "FY+1": {"gross_leverage": 2.0}, "FY+2": {"gross_leverage": 2.0}},
        "financials": {"FY+1": {"ebitda": 400}},
        "covenants": [LEVERAGE_MAX],
        "downside_case": {"ratios": {"FY+1": {"gross_leverage": None}}, "financials": {"FY+1": {"ebitda": -50}},
                          "unavailable": {"FY+2": "no scenario"}},
    }
    breaches = evaluate_deal_policy(state)["downside_covenant_breaches"]
    assert [(b["year"], b["downside_status"]) for b in breaches] == [("FY+1", "UNRESOLVABLE"), ("FY+2", "UNRESOLVABLE")]
    assert "EBITDA is negative" in breaches[0]["reason"], "the downside financials reach the explanation"
    assert "downside analysis unavailable for FY+2: no scenario" in breaches[1]["reason"]


def test_a_skipped_unavailable_year_does_not_end_the_check_for_the_years_after_it():
    """FY+1 has nothing to survive (its base ratios are empty) and is skipped; FY+2, later in the order, must still be found."""
    base = {"FY+1": {}, "FY+2": {"gross_leverage": 2.0}}
    breaches = _evaluate_downside_covenants([LEVERAGE_MAX], base, {}, unavailable={"FY+1": "a", "FY+2": "b"})
    assert [(b["year"], b["reason"]) for b in breaches] == [("FY+2", "downside analysis unavailable for FY+2: b")]


# ---------------------------------------------------------------------------
# _evaluate_covenant's UNRESOLVABLE reasons, and the forward record (issue #183)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("covenant, ratios, reason", [
    ({"metric": "dscr", "type": "floor", "threshold": 1.25}, {"dscr": 2.0},
     "unrecognized covenant type 'floor' (expected 'minimum' or 'maximum')"),
    ({"metric": "dscr", "type": "minimum"}, {"dscr": 2.0}, "the covenant threshold is missing or not a number"),
    ({"metric": "dscr", "type": "minimum", "threshold": "1.25"}, {"dscr": 2.0}, "the covenant threshold is missing or not a number"),
    ({"metric": "dscr", "type": "minimum", "threshold": True}, {"dscr": 2.0}, "the covenant threshold is missing or not a number"),
    ({"metric": "icr", "type": "minimum", "threshold": 1.25}, {"dscr": 2.0},
     "metric 'icr' is not among this period's computed ratios"),
    ({"metric": "dscr", "type": "minimum", "threshold": 1.25}, {"dscr": None}, "dscr has no computed value for this period"),
    ({"metric": "dscr", "type": "minimum", "threshold": 1.25}, {"dscr": "n/m"}, "dscr has no computed value for this period"),
])
def test_a_covenant_that_cannot_be_tested_is_unresolvable_with_the_reason_that_says_why(covenant, ratios, reason):
    """Never a PASS and never a silent skip: UNRESOLVABLE, no actual and no headroom, with one sentence for the cause."""
    (result,) = evaluate_deal_policy({"covenants": [covenant], "ratios": {"FY-Current": ratios}})["covenant_results"]
    assert (result["status"], result["actual"], result["headroom_pct"], result["reason"]) == ("UNRESOLVABLE", None, None, reason)
    assert (result["metric"], result["type"], result["threshold"]) == (covenant["metric"], covenant["type"], covenant.get("threshold"))


def test_a_recorded_denominator_that_makes_a_ratio_meaningless_overrides_a_stored_number_and_says_so():
    """Issue #169: a number stored next to a negative recorded EBITDA is ignored, and the sentence says it was."""
    state = {"covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}],
             "ratios": {"FY-Current": {"gross_leverage": 2.0}}, "financials": {"FY-Current": {"ebitda": -10}}}
    (result,) = evaluate_deal_policy(state)["covenant_results"]
    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None
    assert result["reason"] == "gross leverage not meaningful: EBITDA is negative (the recorded value 2.0 is ignored)"

    # The ignored-value suffix is only accurate for a numeric stored ratio.
    nonnumeric_state = {
        **state,
        "ratios": {"FY-Current": {"gross_leverage": "n/m"}},
    }
    (nonnumeric,) = evaluate_deal_policy(nonnumeric_state)["covenant_results"]
    assert (nonnumeric["status"], nonnumeric["actual"], nonnumeric["headroom_pct"], nonnumeric["reason"]) == (
        "UNRESOLVABLE", None, None, "gross leverage not meaningful: EBITDA is negative",
    )


def test_the_forward_record_lists_each_projected_year_then_each_covenant_with_a_stable_id():
    """Disclosure only: one entry per projected year and covenant, in year order then covenant order, each the covenant
    result plus `year` and `forward_id`; a year with nothing recorded, or a null or non-object ratio set, is not listed."""
    covenants = [LEVERAGE_MAX, DSCR_MIN, LEVERAGE_MAX]                          # the same metric twice: a numeric suffix
    ratios = {"FY-Current": {"gross_leverage": 2.0, "dscr": 2.0},               # not a forward year
              "FY+1": {"gross_leverage": 2.0, "dscr": 2.0}, "FY+2": {}, "FY+3": {"gross_leverage": 4.0, "dscr": 1.0}}
    forward = evaluate_deal_policy({"covenants": covenants, "ratios": ratios})["forward_covenant_results"]
    assert [(r["year"], r["metric"], r["status"], r["forward_id"]) for r in forward] == [
        ("FY+1", "gross_leverage", "PASS", "FORWARD-FY-1-GROSS-LEVERAGE"),
        ("FY+1", "dscr", "PASS", "FORWARD-FY-1-DSCR"),
        ("FY+1", "gross_leverage", "PASS", "FORWARD-FY-1-GROSS-LEVERAGE-2"),
        ("FY+3", "gross_leverage", "FAIL", "FORWARD-FY-3-GROSS-LEVERAGE"),
        ("FY+3", "dscr", "FAIL", "FORWARD-FY-3-DSCR"),
        ("FY+3", "gross_leverage", "FAIL", "FORWARD-FY-3-GROSS-LEVERAGE-2")]

    assert _period_dict({"FY+1": ["not", "a", "period", "object"]}, "FY+1") is None
    assert _period_dict({"FY+1": {"ebitda": 400}}, "FY+1") == {"ebitda": 400}


@pytest.mark.parametrize("ratios", [None, {}, {"FY+1": None}, {"FY+1": [1]}, {"FY+1": {}}])
def test_the_forward_record_is_empty_when_no_year_has_a_usable_ratio_set(ratios):
    state = {"covenants": [LEVERAGE_MAX], "ratios": ratios}
    assert evaluate_deal_policy(state)["forward_covenant_results"] == []


@pytest.mark.parametrize("ratios", ["text", ["FY+1"], 5])
def test_the_forward_evaluator_itself_returns_nothing_for_ratios_that_are_not_an_object(ratios):
    """evaluate_deal_policy's callers validate the state first; the helper still must not raise on a malformed mapping."""
    assert _evaluate_forward_covenants([LEVERAGE_MAX], ratios) == []


# ---------------------------------------------------------------------------
# Non-finite ratios or thresholds must be refused, never compared into PASS
# or FAIL (issue #244).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "positive-infinity", "negative-infinity"])
def test_non_finite_ratio_is_rejected_before_covenant_evaluation(value):
    state = {
        "ratios": {"FY-Current": {"gross_leverage": value}},
        "covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}],
    }
    with pytest.raises(ValueError, match="non-finite numeric value"):
        evaluate_deal_policy(state)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "positive-infinity", "negative-infinity"])
def test_non_finite_covenant_threshold_is_rejected_before_comparison(value):
    state = {
        "ratios": {"FY-Current": {"gross_leverage": 3.2}},
        "covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": value}],
    }
    with pytest.raises(ValueError, match="non-finite numeric value"):
        evaluate_deal_policy(state)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), json.loads("1e999")],
                         ids=["nan", "positive-infinity", "negative-infinity", "overflow-1e999"])
@pytest.mark.parametrize("key", ["collateral", "security_package"])
def test_non_finite_collateral_or_security_is_rejected_before_policy_evaluation(key, value):
    """Decision D4 (#244): the policy engine's guard covers the collateral figures as well."""
    state = {
        "ratios": {"FY-Current": {"gross_leverage": 3.2}},
        "covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}],
        key: [{"asset_id": "A1", "secures_asset_id": "A1", "exposure": value, "collateral_value": 80, "value": value}],
    }
    with pytest.raises(ValueError, match="non-finite numeric value"):
        evaluate_deal_policy(state)

# ---------------------------------------------------------------------------
# Mutation-survivor contracts for policy output, ordering and boundary guards.
# These exercise the public policy entry point so they pin behaviour rather
# than the shape of a particular private helper.
# ---------------------------------------------------------------------------

def test_pe_empty_policy_output_has_the_complete_ordered_return_shape():
    result = evaluate_deal_policy({})

    assert list(result) == [
        "covenant_results",
        "security_gaps",
        "required_conditions_precedent",
        "required_conditions_subsequent",
        "downside_covenant_breaches",
        "forward_covenant_results",
    ]
    assert result == {
        "covenant_results": [],
        "security_gaps": [],
        "required_conditions_precedent": STANDARD_CONDITIONS_PRECEDENT,
        "required_conditions_subsequent": STANDARD_CONDITIONS_SUBSEQUENT,
        "downside_covenant_breaches": [],
        "forward_covenant_results": [],
    }


@pytest.mark.parametrize("actual, covenant_type, expected_status", [
    (True, "maximum", "UNRESOLVABLE"),
    (False, "minimum", "UNRESOLVABLE"),
], ids=["true-is-not-numeric", "false-is-not-numeric"])
def test_pe_boolean_ratio_values_are_not_accepted_as_numeric(actual, covenant_type, expected_status):
    result = evaluate_deal_policy({
        "ratios": {"FY-Current": {"gross_leverage": actual}},
        "covenants": [{"metric": "gross_leverage", "type": covenant_type, "threshold": 3.5}],
    })["covenant_results"][0]

    assert result == {
        "metric": "gross_leverage",
        "type": covenant_type,
        "threshold": 3.5,
        "actual": None,
        "status": expected_status,
        "headroom_pct": None,
        "reason": "gross_leverage has no computed value for this period",
    }


@pytest.mark.parametrize("actual, covenant_type, expected_status", [
    (-10, "minimum", "FAIL"),
    (10, "maximum", "FAIL"),
], ids=["negative-ten-is-a-real-ratio", "positive-ten-is-a-real-ratio"])
def test_pe_percent_ratio_actual_at_absolute_ten_is_not_flagged_as_percent(actual, covenant_type, expected_status):
    result = evaluate_deal_policy({
        "ratios": {"FY-Current": {"gearing": actual}},
        "covenants": [{"metric": "gearing", "type": covenant_type, "threshold": 1}],
    })["covenant_results"][0]

    assert result["actual"] == actual
    assert result["status"] == expected_status
    assert result["reason"] is None


def test_pe_percent_ratio_actual_just_over_ten_is_flagged_as_percent():
    """An actual of 10.5 on a percent-type ratio is flagged; 10 is not (the boundary above)."""
    just_over_ten = evaluate_deal_policy({
        "ratios": {"FY-Current": {"gearing": 10.5}},
        "covenants": [{"metric": "gearing", "type": "maximum", "threshold": 1}],
    })["covenant_results"][0]
    assert just_over_ten == {
        "metric": "gearing",
        "type": "maximum",
        "threshold": 1,
        "actual": None,
        "status": "UNRESOLVABLE",
        "headroom_pct": None,
        "reason": "gearing actual ratio 10.5 looks like a percent; percent-type ratios and their covenant "
                  "thresholds are stored as fractions (0.87 means 87%). Confirm the unit before comparing.",
    }


@pytest.mark.parametrize("threshold, covenant_type, expected_status", [
    (-10, "minimum", "PASS"),
    (10, "maximum", "PASS"),
], ids=["negative-ten-threshold", "positive-ten-threshold"])
def test_pe_percent_ratio_threshold_at_absolute_ten_is_not_flagged_as_percent(
    threshold, covenant_type, expected_status,
):
    result = evaluate_deal_policy({
        "ratios": {"FY-Current": {"gearing": 0}},
        "covenants": [{"metric": "gearing", "type": covenant_type, "threshold": threshold}],
    })["covenant_results"][0]

    assert result["actual"] == 0
    assert result["status"] == expected_status
    assert result["reason"] is None


def test_pe_percent_ratio_threshold_just_over_ten_is_flagged_as_percent():
    """A covenant threshold of 10.5 on a percent-type ratio is flagged; 10 is not (the boundary above)."""
    just_over_ten = evaluate_deal_policy({
        "ratios": {"FY-Current": {"gearing": 0}},
        "covenants": [{"metric": "gearing", "type": "maximum", "threshold": 10.5}],
    })["covenant_results"][0]
    assert just_over_ten == {
        "metric": "gearing",
        "type": "maximum",
        "threshold": 10.5,
        "actual": None,
        "status": "UNRESOLVABLE",
        "headroom_pct": None,
        "reason": "gearing covenant threshold 10.5 looks like a percent; percent-type ratios and their covenant "
                  "thresholds are stored as fractions (0.87 means 87%). Confirm the unit before comparing.",
    }


@pytest.mark.parametrize("ebitda, reason", [
    (0, "gross leverage not defined: EBITDA is zero"),
    (-10, "gross leverage not meaningful: EBITDA is negative"),
], ids=["zero-denominator", "negative-denominator"])
def test_pe_nonpositive_ebitda_makes_a_recorded_gross_leverage_unresolvable(ebitda, reason):
    result = evaluate_deal_policy({
        "ratios": {"FY-Current": {"gross_leverage": 2.0}},
        "financials": {"FY-Current": {"ebitda": ebitda}},
        "covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}],
    })["covenant_results"][0]

    assert result["status"] == "UNRESOLVABLE"
    assert result["actual"] is None
    assert result["headroom_pct"] is None
    assert result["reason"] == f"{reason} (the recorded value 2.0 is ignored)"


def test_pe_a_negative_base_ebitda_is_neither_a_forward_pass_nor_a_downside_breach():
    """The negative denominator keeps both forward disclosure and downside comparison from treating the stored ratio as a base-case PASS."""
    # The same negative denominator must keep both forward disclosure and downside comparison from
    # treating the stored ratio as a base-case PASS.
    forward_and_stress = evaluate_deal_policy({
        "ratios": {"FY-Current": {"gross_leverage": 2.0}, "FY+1": {"gross_leverage": 2.0}},
        "financials": {"FY-Current": {"ebitda": 400}, "FY+1": {"ebitda": -10}},
        "covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}],
        "downside_case": {
            "ratios": {"FY+1": {"gross_leverage": 5.0}},
            "financials": {"FY+1": {"ebitda": 400}},
        },
    })
    assert forward_and_stress["downside_covenant_breaches"] == []
    assert forward_and_stress["forward_covenant_results"] == [{
        "metric": "gross_leverage",
        "type": "maximum",
        "threshold": 3.5,
        "actual": None,
        "status": "UNRESOLVABLE",
        "headroom_pct": None,
        "reason": "gross leverage not meaningful: EBITDA is negative (the recorded value 2.0 is ignored)",
        "year": "FY+1",
        "forward_id": "FORWARD-FY-1-GROSS-LEVERAGE",
    }]

    # An unavailable stress also must not become a breach when the base ratio is untestable.
    unavailable_stress = evaluate_deal_policy({
        "ratios": {"FY-Current": {"gross_leverage": 2.0}, "FY+1": {"gross_leverage": 2.0}},
        "financials": {"FY-Current": {"ebitda": 400}, "FY+1": {"ebitda": -10}},
        "covenants": [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}],
        "downside_case": {"unavailable": {"FY+1": "no scenario was supplied"}},
    })
    assert unavailable_stress["downside_covenant_breaches"] == []


def test_pe_security_failure_outputs_both_gaps_and_conditions_in_perfection_then_priority_order():
    result = evaluate_deal_policy({
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [{
            "secures_asset_id": "AST-001",
            "perfection_status": "Pending",
            "ranking": "Second",
        }],
    })

    assert result["security_gaps"] == [
        "Unperfected Security: Asset AST-001 charge status is 'Pending', not Perfected.",
        "Subordinate Ranking: Asset AST-001 charge ranking is 'Second', not First.",
    ]
    assert [
        cp for cp in result["required_conditions_precedent"]
        if cp["cp_id"].startswith(("SEC-PERFECT-", "SEC-PRIORITY-"))
    ] == [
        {
            "cp_id": "SEC-PERFECT-AST-001",
            "text": "Execution and completion of registration to perfect charge over asset AST-001 "
                    "(Current Status: Pending).",
        },
        {
            "cp_id": "SEC-PRIORITY-AST-001",
            "text": "Negotiation, execution, and stamping of a formal Intercreditor Deed / Deed of Priority "
                    "with existing chargeholders for asset AST-001 (Current Ranking: Second).",
        },
    ]


def test_pe_security_mapping_conditions_disambiguate_colliding_asset_slugs_across_gap_kinds():
    result = evaluate_deal_policy({
        "collateral": [{"asset_id": "AST+A"}],
        "security_package": [{"secures_asset_id": "AST A", "perfection_status": "Perfected", "ranking": "First"}],
    })

    assert result["security_gaps"] == [
        "Uncharged Asset: AST+A has no corresponding security charge registered.",
        "Dangling Reference: AST A does not exist in collateral records.",
    ]
    assert [
        cp for cp in result["required_conditions_precedent"] if cp["cp_id"].startswith("SEC-MAPPING-")
    ] == [
        {
            "cp_id": "SEC-MAPPING-AST-A",
            "text": "Resolution of collateral security mapping discrepancy for asset AST+A.",
        },
        {
            "cp_id": "SEC-MAPPING-AST-A-2",
            "text": "Resolution of collateral security mapping discrepancy for asset AST A.",
        },
    ]


def test_pe_security_reconfirmation_condition_ids_avoid_natural_slug_collisions_and_keep_order():
    result = evaluate_deal_policy({
        "security_package": [
            {"secures_asset_id": "AST-1", "perfection_status": "Perfected", "ranking": "First"},
            {"secures_asset_id": "AST-1", "perfection_status": "Perfected", "ranking": "Second"},
            {"secures_asset_id": "AST-1-2", "perfection_status": "Perfected", "ranking": "First"},
        ],
    })

    assert [
        cs for cs in result["required_conditions_subsequent"] if cs["cs_id"].startswith("CS-SEC-REPERFECT-")
    ] == [
        {
            "cs_id": "CS-SEC-REPERFECT-AST-1",
            "text": "Ongoing (e.g. annual) reconfirmation of the continued perfection status of "
                    "the security charge over asset AST-1.",
        },
        {
            "cs_id": "CS-SEC-REPERFECT-AST-1-2",
            "text": "Ongoing (e.g. annual) reconfirmation of the continued perfection status of "
                    "the security charge over asset AST-1.",
        },
        {
            "cs_id": "CS-SEC-REPERFECT-AST-1-2-2",
            "text": "Ongoing (e.g. annual) reconfirmation of the continued perfection status of "
                    "the security charge over asset AST-1-2.",
        },
    ]

    pending_before_perfected = evaluate_deal_policy({
        "security_package": [
            {"secures_asset_id": "AST-PENDING", "perfection_status": "Pending", "ranking": "First"},
            {"secures_asset_id": "AST-READY", "perfection_status": "Perfected", "ranking": "First"},
        ],
    })
    assert [
        cs for cs in pending_before_perfected["required_conditions_subsequent"]
        if cs["cs_id"].startswith("CS-SEC-REPERFECT-")
    ] == [{
        "cs_id": "CS-SEC-REPERFECT-AST-READY",
        "text": "Ongoing (e.g. annual) reconfirmation of the continued perfection status of the security charge "
                "over asset AST-READY.",
    }]


@pytest.mark.parametrize(("guarantee", "expected_id"), [
    ({"provider": "Synthetic Guarantor"}, "CS-GUARANTEE-SYNTHETIC-GUARANTOR"),
    ({"guarantee_id": None, "provider": "Synthetic Guarantor"}, "CS-GUARANTEE-SYNTHETIC-GUARANTOR"),
    ({"guarantee_id": "   ", "provider": "Synthetic Guarantor"}, "CS-GUARANTEE-SYNTHETIC-GUARANTOR"),
    ({"guarantee_id": 0, "provider": "Synthetic Guarantor"}, "CS-GUARANTEE-0"),
    ({"guarantee_id": "GRT/001", "provider": "Synthetic Guarantor"}, "CS-GUARANTEE-GRT-001"),
], ids=["missing-id-falls-back", "null-id-falls-back", "blank-id-falls-back", "zero-id-is-present", "explicit-id-is-used"])
def test_pe_guarantee_standing_condition_uses_the_correct_identity_source(guarantee, expected_id):
    result = evaluate_deal_policy({"guarantees": [guarantee]})

    assert [
        cs for cs in result["required_conditions_subsequent"] if cs["cs_id"].startswith("CS-GUARANTEE-")
    ] == [{
        "cs_id": expected_id,
        "text": "Ongoing (e.g. annual) reconfirmation of the continued standing of guarantor Synthetic Guarantor.",
    }]


@pytest.mark.parametrize(
    ("has_type", "type_value"),
    [(False, None), (True, ""), (True, "   ")],
    ids=["missing-type", "empty-type", "whitespace-type"],
)
def test_pe_guarantee_cp_default_type_does_not_duplicate_guarantee_in_text(has_type, type_value):
    guarantee = {
        "guarantee_id": "   ",
        "provider": "Synthetic Guarantor",
    }
    if has_type:
        guarantee["type"] = type_value
    result = evaluate_deal_policy({"guarantees": [guarantee]})

    assert [
        cp for cp in result["required_conditions_precedent"] if cp["cp_id"].startswith("GUARANTEE-")
    ] == [{
        "cp_id": "GUARANTEE-SYNTHETIC-GUARANTOR",
        "text": "Execution of Guarantee by Synthetic Guarantor for an amount to be confirmed.",
    }]


@pytest.mark.parametrize(
    ("type_value", "expected_label"),
    [
        ("Corporate Guarantee", "Corporate Guarantee"),
        ("Personal guarantee", "Personal guarantee"),
        ("Bank GUARANTEE", "Bank GUARANTEE"),
        ("Counter-guarantee", "Counter-guarantee"),
        ("Guarantee", "Guarantee"),
        ("Corporate Guarantee ", "Corporate Guarantee "),
        ("Corporate", "Corporate Guarantee"),
        ("Personal", "Personal Guarantee"),
        ("Guarantee Bond", "Guarantee Bond"),
        ("Corporate Guarantee and Indemnity", "Corporate Guarantee and Indemnity"),
        ("Guarantor Support", "Guarantor Support Guarantee"),
    ],
    ids=[
        "ends-with-guarantee",
        "lower-case-guarantee",
        "upper-case-guarantee",
        "hyphenated-guarantee",
        "just-guarantee",
        "trailing-space-kept-as-written",
        "corporate-gets-the-word",
        "personal-gets-the-word",
        "word-at-the-start-is-used-as-written",
        "word-in-the-middle-is-used-as-written",
        "guarantor-is-not-the-word",
    ],
)
def test_pe_guarantee_cp_does_not_repeat_a_type_that_already_contains_guarantee(type_value, expected_label):
    result = evaluate_deal_policy({"guarantees": [{
        "provider": "Synthetic Guarantor",
        "type": type_value,
        "amount": 500,
    }]})

    assert [
        cp for cp in result["required_conditions_precedent"] if cp["cp_id"].startswith("GUARANTEE-")
    ] == [{
        "cp_id": "GUARANTEE-SYNTHETIC-GUARANTOR",
        "text": f"Execution of {expected_label} by Synthetic Guarantor for 500.",
    }]


def test_pe_composed_standing_conditions_have_the_exact_contract_text_and_order():
    result = evaluate_deal_policy({
        "ratios": RATIOS,
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [{
            "secures_asset_id": "AST-001",
            "perfection_status": "Perfected",
            "ranking": "First",
        }],
        "guarantees": [{"provider": "Synthetic Guarantor", "type": "Corporate", "amount": 0}],
    })

    assert result["required_conditions_subsequent"] == STANDARD_CONDITIONS_SUBSEQUENT + [
        {
            "cs_id": "CS-COVENANT-COMPLIANCE",
            "text": "Ongoing certification of continued compliance with all financial covenants "
                    "set out in the Facility Agreement.",
        },
        {
            "cs_id": "CS-SEC-REPERFECT-AST-001",
            "text": "Ongoing (e.g. annual) reconfirmation of the continued perfection status of "
                    "the security charge over asset AST-001.",
        },
        {
            "cs_id": "CS-GUARANTEE-SYNTHETIC-GUARANTOR",
            "text": "Ongoing (e.g. annual) reconfirmation of the continued standing of guarantor Synthetic Guarantor.",
        },
    ]


@pytest.mark.parametrize("state_key", [
    "financials",
    "multi_period_financials",
    "analyst_supplied_financials",
    "stress_assumptions",
    "downside_case",
], ids=["financials", "computed-raw-store", "supplied-raw-store", "stress-assumptions", "downside-case"])
def test_pe_policy_entry_rejects_non_finite_numbers_in_each_financial_input_store(state_key):
    with pytest.raises(ValueError, match="non-finite numeric value"):
        evaluate_deal_policy({state_key: {"synthetic-period": {"synthetic-line": float("inf")}}})

    with pytest.raises(ValueError) as exc_info:
        evaluate_deal_policy({"ratios": {"FY-Current": {"dscr": float("inf")}}})
    assert exc_info.value.args == (
        "non-finite numeric value at policy state['ratios']['FY-Current']['dscr']: inf",
    )

def test_pe_percent_unit_error_lists_actual_before_threshold_and_keeps_exact_guidance():
    result = evaluate_deal_policy({
        "ratios": {"FY-Current": {"gearing": -87}},
        "covenants": [{"metric": "gearing", "type": "maximum", "threshold": 80}],
    })["covenant_results"][0]

    assert result["status"] == "UNRESOLVABLE"
    assert result["actual"] is None
    assert result["headroom_pct"] is None
    assert result["reason"] == (
        "gearing actual ratio -87 and covenant threshold 80 looks like a percent; percent-type ratios and their "
        "covenant thresholds are stored as fractions (0.87 means 87%). Confirm the unit before comparing."
    )


def test_pe_guarantee_standing_condition_ids_avoid_natural_slug_collisions():
    result = evaluate_deal_policy({
        "guarantees": [
            {"provider": "Acme Corp"},
            {"provider": "Acme Corp"},
            {"provider": "Acme Corp 2"},
        ],
    })

    assert [
        cs["cs_id"] for cs in result["required_conditions_subsequent"] if cs["cs_id"].startswith("CS-GUARANTEE-")
    ] == [
        "CS-GUARANTEE-ACME-CORP",
        "CS-GUARANTEE-ACME-CORP-2",
        "CS-GUARANTEE-ACME-CORP-2-2",
    ]


def test_pe_precedent_conditions_keep_standard_security_then_guarantee_order_and_exact_text():
    result = evaluate_deal_policy({
        "collateral": [{"asset_id": "AST-001"}],
        "security_package": [{
            "secures_asset_id": "AST-001",
            "perfection_status": "Pending",
            "ranking": "Second",
        }],
        "guarantees": [{"provider": "Synthetic Guarantor", "type": "Corporate", "amount": 0}],
    })

    assert result["required_conditions_precedent"] == STANDARD_CONDITIONS_PRECEDENT + [
        {
            "cp_id": "SEC-PERFECT-AST-001",
            "text": "Execution and completion of registration to perfect charge over asset AST-001 "
                    "(Current Status: Pending).",
        },
        {
            "cp_id": "SEC-PRIORITY-AST-001",
            "text": "Negotiation, execution, and stamping of a formal Intercreditor Deed / Deed of Priority "
                    "with existing chargeholders for asset AST-001 (Current Ranking: Second).",
        },
        {
            "cp_id": "GUARANTEE-SYNTHETIC-GUARANTOR",
            "text": "Execution of Corporate Guarantee by Synthetic Guarantor for 0.",
        },
    ]

@pytest.mark.parametrize(("downside_period_ratios", "reason"), [
    ({}, "metric 'dscr' is not among this period's computed ratios"),
    ({"dscr": None}, "dscr has no computed value for this period"),
], ids=["ratio-key-absent", "ratio-value-null"])
def test_pe_downside_base_pass_with_missing_stress_ratio_is_an_unresolvable_breach(downside_period_ratios, reason):
    breaches = _evaluate_downside_covenants(
        [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
        {"FY+1": {"dscr": 2.0}},
        {"FY+1": downside_period_ratios},
    )

    assert breaches == [{
        "year": "FY+1",
        "metric": "dscr",
        "base_actual": 2.0,
        "downside_actual": None,
        "threshold": 1.25,
        "breach_id": "DOWNSIDE-FY-1-DSCR",
        "downside_status": "UNRESOLVABLE",
        "reason": reason,
    }]


def test_pe_downside_breach_results_are_sorted_by_period_not_input_mapping_order():
    breaches = _evaluate_downside_covenants(
        [{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}],
        {
            "FY+2": {"gross_leverage": 2.0},
            "FY+1": {"gross_leverage": 2.0},
        },
        {
            "FY+2": {"gross_leverage": 5.0},
            "FY+1": {"gross_leverage": 5.0},
        },
    )

    assert [(breach["year"], breach["breach_id"]) for breach in breaches] == [
        ("FY+1", "DOWNSIDE-FY-1-GROSS-LEVERAGE"),
        ("FY+2", "DOWNSIDE-FY-2-GROSS-LEVERAGE"),
    ]


def test_pe_guarantee_cps_avoid_natural_slug_collision_with_an_earlier_numeric_suffix():
    result = evaluate_deal_policy({
        "guarantees": [
            {"provider": "Acme Corp", "type": "Corporate", "amount": 10},
            {"provider": "Acme Corp", "type": "Corporate", "amount": 20},
            {"provider": "Acme Corp 2", "type": "Corporate", "amount": 30},
        ],
    })

    assert [
        cp for cp in result["required_conditions_precedent"] if cp["cp_id"].startswith("GUARANTEE-")
    ] == [
        {
            "cp_id": "GUARANTEE-ACME-CORP",
            "text": "Execution of Corporate Guarantee by Acme Corp for 10.",
        },
        {
            "cp_id": "GUARANTEE-ACME-CORP-2",
            "text": "Execution of Corporate Guarantee by Acme Corp for 20.",
        },
        {
            "cp_id": "GUARANTEE-ACME-CORP-2-2",
            "text": "Execution of Corporate Guarantee by Acme Corp 2 for 30.",
        },
    ]



# ---------------------------------------------------------------------------
# Covenant basis (issue #48, maintainer decisions of 2026-10-22): a covenant is tested only on its own basis.
# ---------------------------------------------------------------------------

from policy_engine import COVENANT_BASES, DEFAULT_COVENANT_BASIS, _evaluate_covenant

GROUP_BASIS = {"basis": "supplied_consolidated"}
BORROWER_ON_CONSOLIDATED = (
    "the covenant is on the borrower basis but the recorded figures are the consolidated view, and no borrower "
    "figures are recorded; state the covenant's basis, or record figures for it")
CONSOLIDATED_ON_BORROWER = (
    "the covenant is on the consolidated basis but the recorded figures are the borrower view, and no consolidated "
    "figures are recorded; state the covenant's basis, or record figures for it")


def _basis_state(covenants, group_basis=None, ratios=None, **extra):
    state = {"ratios": ratios or RATIOS, "covenants": covenants, **extra}
    if group_basis is not None:
        state["group_basis"] = group_basis
    return state


def _dscr(basis="<absent>", threshold=1.25):
    covenant = {"metric": "dscr", "type": "minimum", "threshold": threshold}
    if basis != "<absent>":
        covenant["basis"] = basis
    return covenant


def test_the_basis_constants_are_the_three_decided_bases_and_the_default_is_borrower():
    assert COVENANT_BASES == ("borrower", "consolidated", "guarantor_group")
    assert DEFAULT_COVENANT_BASIS == "borrower"


def test_a_covenant_without_a_basis_on_an_ordinary_deal_keeps_exactly_its_earlier_result():
    for covenant in (_dscr(), _dscr(None)):
        result = evaluate_deal_policy(_basis_state([covenant]))["covenant_results"][0]

        assert result == {"metric": "dscr", "type": "minimum", "threshold": 1.25, "actual": 1.5, "status": "PASS",
                          "headroom_pct": pytest.approx(0.2), "reason": None}
        assert list(result) == ["metric", "type", "threshold", "actual", "status", "headroom_pct", "reason"]


def test_an_explicit_borrower_basis_on_an_ordinary_deal_is_tested_and_names_its_basis():
    result = evaluate_deal_policy(_basis_state([_dscr("borrower", threshold=1.6)]))["covenant_results"][0]

    assert (result["basis"], result["status"], result["actual"], result["reason"]) == ("borrower", "FAIL", 1.5, None)
    assert list(result) == ["metric", "type", "threshold", "basis", "actual", "status", "headroom_pct", "reason"]


def test_a_consolidated_covenant_on_an_ordinary_deal_is_unresolvable_with_the_reason_and_no_figures():
    result = evaluate_deal_policy(_basis_state([_dscr("consolidated")]))["covenant_results"][0]

    assert result == {"metric": "dscr", "type": "minimum", "threshold": 1.25, "basis": "consolidated",
                      "actual": None, "status": "UNRESOLVABLE", "headroom_pct": None,
                      "reason": CONSOLIDATED_ON_BORROWER}


def test_a_guarantor_group_covenant_is_unresolvable_on_every_deal_because_no_such_figures_are_recorded():
    ordinary = evaluate_deal_policy(_basis_state([_dscr("guarantor_group")]))["covenant_results"][0]
    grouped = evaluate_deal_policy(_basis_state([_dscr("guarantor_group")], GROUP_BASIS))["covenant_results"][0]

    assert (ordinary["status"], ordinary["actual"]) == ("UNRESOLVABLE", None)
    assert ordinary["reason"] == CONSOLIDATED_ON_BORROWER.replace("consolidated basis", "guarantor_group basis") \
        .replace("no consolidated figures", "no guarantor_group figures")
    assert (grouped["status"], grouped["actual"]) == ("UNRESOLVABLE", None)
    assert "guarantor_group basis but the recorded figures are the consolidated view" in grouped["reason"]


@pytest.mark.parametrize("basis", ["parent", "", "Borrower", " borrower", 5, ["borrower"], {"b": 1}, True])
def test_an_unrecognised_basis_is_unresolvable_and_echoed_never_treated_as_borrower(basis):
    result = evaluate_deal_policy(_basis_state([_dscr(basis)]))["covenant_results"][0]

    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None and result["headroom_pct"] is None
    assert result["basis"] == basis
    assert result["reason"] == (
        f"unrecognized covenant basis {basis!r} (expected 'borrower', 'consolidated' or 'guarantor_group')")


def test_the_basis_is_checked_before_the_other_covenant_faults_so_the_reason_names_the_figures_problem_first():
    bad = {"metric": "nonsense", "type": "sideways", "threshold": "x", "basis": "consolidated"}

    result = evaluate_deal_policy(_basis_state([bad]))["covenant_results"][0]

    assert result["reason"] == CONSOLIDATED_ON_BORROWER


@pytest.mark.parametrize("group_basis", [None, "x", [], 0, False])
def test_only_a_recorded_group_basis_object_makes_the_recorded_view_consolidated(group_basis):
    state = _basis_state([_dscr("consolidated")], ratios=RATIOS)
    state["group_basis"] = group_basis

    result = evaluate_deal_policy(state)["covenant_results"][0]

    assert result["status"] == "UNRESOLVABLE" and result["reason"] == CONSOLIDATED_ON_BORROWER


def test_on_a_group_basis_deal_a_consolidated_covenant_is_tested_and_a_covenant_without_basis_is_unresolvable():
    covenants = [_dscr("consolidated"), _dscr(), {"metric": "gross_leverage", "type": "maximum", "threshold": 3.0,
                                                  "basis": "consolidated"}]

    results = evaluate_deal_policy(_basis_state(covenants, GROUP_BASIS))["covenant_results"]

    assert [r["status"] for r in results] == ["PASS", "UNRESOLVABLE", "FAIL"]
    assert results[0] == {"metric": "dscr", "type": "minimum", "threshold": 1.25, "basis": "consolidated",
                          "actual": 1.5, "status": "PASS", "headroom_pct": pytest.approx(0.2), "reason": None}
    assert results[1] == {"metric": "dscr", "type": "minimum", "threshold": 1.25, "basis": "borrower",
                          "actual": None, "status": "UNRESOLVABLE", "headroom_pct": None,
                          "reason": BORROWER_ON_CONSOLIDATED}
    assert (results[2]["basis"], results[2]["actual"]) == ("consolidated", 3.2)


def test_a_group_basis_deal_with_an_explicit_borrower_covenant_is_unresolvable_too():
    result = evaluate_deal_policy(_basis_state([_dscr("borrower")], GROUP_BASIS))["covenant_results"][0]

    assert (result["basis"], result["status"], result["reason"]) == ("borrower", "UNRESOLVABLE", BORROWER_ON_CONSOLIDATED)


def test_the_evaluator_itself_takes_the_recorded_view_and_defaults_to_borrower():
    covenant = _dscr("consolidated")
    ratios = RATIOS["FY-Current"]

    assert _evaluate_covenant(covenant, ratios)["reason"] == CONSOLIDATED_ON_BORROWER
    assert _evaluate_covenant(covenant, ratios, None, "borrower")["status"] == "UNRESOLVABLE"
    assert _evaluate_covenant(covenant, ratios, None, "consolidated")["status"] == "PASS"
    assert _evaluate_covenant(_dscr(), ratios, None, "consolidated")["status"] == "UNRESOLVABLE"


def test_forward_results_test_each_covenant_on_its_own_basis_and_keep_their_ids():
    ratios = {"FY-Current": {"dscr": 1.5}, "FY+1": {"dscr": 1.1}, "FY+2": {"dscr": 1.4}}
    covenants = [_dscr("consolidated"), _dscr("borrower")]

    forward = evaluate_deal_policy(_basis_state(covenants, GROUP_BASIS, ratios=ratios))["forward_covenant_results"]

    assert [(r["year"], r["basis"], r["status"], r["forward_id"]) for r in forward] == [
        ("FY+1", "consolidated", "FAIL", "FORWARD-FY-1-DSCR"),
        ("FY+1", "borrower", "UNRESOLVABLE", "FORWARD-FY-1-DSCR-2"),
        ("FY+2", "consolidated", "PASS", "FORWARD-FY-2-DSCR"),
        ("FY+2", "borrower", "UNRESOLVABLE", "FORWARD-FY-2-DSCR-2"),
    ]
    assert forward[1]["reason"] == BORROWER_ON_CONSOLIDATED and forward[1]["actual"] is None


def test_forward_results_without_any_basis_are_unchanged_and_carry_no_basis_key():
    ratios = {"FY-Current": {"dscr": 1.5}, "FY+1": {"dscr": 1.1}}

    forward = evaluate_deal_policy(_basis_state([_dscr()], ratios=ratios))["forward_covenant_results"]

    assert forward == [{"metric": "dscr", "type": "minimum", "threshold": 1.25, "actual": 1.1, "status": "FAIL",
                        "headroom_pct": pytest.approx(-0.12), "reason": None, "year": "FY+1",
                        "forward_id": "FORWARD-FY-1-DSCR"}]


def test_a_downside_breach_needs_a_covenant_that_can_be_tested_on_the_recorded_view():
    base = {"FY-Current": {"dscr": 1.5}, "FY+1": {"dscr": 1.4}}
    stressed = {"ratios": {"FY+1": {"dscr": 0.9}}, "financials": {}}
    covenants = [_dscr("consolidated"), _dscr("borrower"), _dscr("guarantor_group")]

    result = evaluate_deal_policy(_basis_state(covenants, GROUP_BASIS, ratios=base, downside_case=stressed))

    assert [(b["year"], b["metric"], b["downside_status"], b["breach_id"])
            for b in result["downside_covenant_breaches"]] == [("FY+1", "dscr", "FAIL", "DOWNSIDE-FY-1-DSCR")]
    assert result["downside_covenant_breaches"][0]["reason"] is None


def test_an_unavailable_downside_year_is_unresolvable_only_for_covenants_that_pass_the_base_case_on_the_view():
    base = {"FY-Current": {"dscr": 1.5}, "FY+1": {"dscr": 1.4}}
    downside = {"unavailable": {"FY+1": "supplied forecast not stressable"}}

    result = evaluate_deal_policy(_basis_state([_dscr("consolidated"), _dscr("borrower")], GROUP_BASIS,
                                               ratios=base, downside_case=downside))

    assert [(b["year"], b["downside_status"], b["breach_id"]) for b in result["downside_covenant_breaches"]] == [
        ("FY+1", "UNRESOLVABLE", "DOWNSIDE-FY-1-DSCR")]
    assert "supplied forecast not stressable" in result["downside_covenant_breaches"][0]["reason"]


def test_basis_results_are_deterministic_and_do_not_change_the_other_policy_outputs():
    state = _basis_state([_dscr("consolidated")], GROUP_BASIS)
    plain = _basis_state([_dscr("consolidated")])
    first, second = evaluate_deal_policy(state), evaluate_deal_policy(state)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    for key in ("security_gaps", "required_conditions_precedent", "required_conditions_subsequent"):
        assert first[key] == evaluate_deal_policy(plain)[key]
