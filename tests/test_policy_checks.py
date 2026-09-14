"""Tests for scripts/policy_checks.py: the dependency-free draft-compliance
checks shared between scripts/orchestrator.py and scripts/policy_check.py
(the CLI callable from the /assemble and /review slash commands).

scripts/test_orchestrator.py already exercises this logic thoroughly via
orchestrator.py's re-exported names (_normalize_category,
parse_underwriter_output, _ground_truth_figures, _values_match,
_check_reported_figures, _apply_deterministic_policy_checks) -- these tests
focus on what's new or not covered there: importing directly from this
module, and check_draft_compliance()'s draft_text=None "structural only"
mode, which orchestrator.py never exercises (its own draft is always a
real string).
"""
import pytest

from policy_checks import (
    REQUIRED_RISK_TAXONOMY,
    check_draft_compliance,
    check_reported_figures,
    compute_collateral_cover_pct,
    ground_truth_figures,
    normalize_category,
    parse_underwriter_output,
    values_match,
)


ALL_CATEGORIES_COVERED = {category: {"status": "covered"} for category in REQUIRED_RISK_TAXONOMY}


def _compliant_draft(cp_ids=("KYC-AML",), reported_figures=None, downside_breaches_acknowledged=None,
                      sources=None):
    import json
    payload = {
        "cp_ids_included": list(cp_ids),
        "risk_categories_covered": ALL_CATEGORIES_COVERED,
        "reported_figures": reported_figures or {},
        "downside_breaches_acknowledged": list(downside_breaches_acknowledged or []),
        "sources": list(sources) if sources is not None else ["Test Source"],
    }
    return "# Draft\n\n```json\n" + json.dumps(payload) + "\n```"


def _policy_state(required_cps=None, covenant_results=None, security_gaps=None,
                   downside_covenant_breaches=None):
    return {
        "required_conditions_precedent": required_cps or [{"cp_id": "KYC-AML", "text": "KYC/AML clearance."}],
        "covenant_results": covenant_results or [],
        "security_gaps": security_gaps or [],
        "downside_covenant_breaches": downside_covenant_breaches or [],
    }


# ---------------------------------------------------------------------------
# Basic re-exports / importability directly from this module
# ---------------------------------------------------------------------------

def test_normalize_category_importable_and_correct():
    assert normalize_category("Market Risk") == normalize_category("Market") == "market"


def test_parse_underwriter_output_importable_and_correct():
    draft = _compliant_draft(cp_ids=["KYC-AML", "SEC-PERFECT-AST-001"])
    result = parse_underwriter_output(draft)
    assert result["cp_ids_included"] == ["KYC-AML", "SEC-PERFECT-AST-001"]


def test_parse_underwriter_output_extracts_sources():
    draft = _compliant_draft(cp_ids=["KYC-AML"], sources=["Companies House filing", "Directors' Report"])
    result = parse_underwriter_output(draft)
    assert result["sources"] == ["Companies House filing", "Directors' Report"]


def test_parse_underwriter_output_sources_degrades_to_empty_list_on_wrong_type():
    draft = '# Draft\n\n```json\n{"cp_ids_included": ["KYC-AML"], "sources": "not a list"}\n```'
    result = parse_underwriter_output(draft)
    assert result["sources"] == []


def test_values_match_importable_and_correct():
    assert values_match(1.0501, 1.05) is True
    assert values_match(1.45, 1.05) is False


def test_ground_truth_figures_and_collateral_cover_pct_importable():
    financials = {"FY-Current": {"raw": {"revenue": 1000}, "ebitda": 510}}
    ratios = {"FY-Current": {"dscr": 1.5}}
    collateral = [{"exposure": 100, "collateral_value": 80}]
    truth = ground_truth_figures(financials, ratios, collateral)
    assert truth["ebitda"] == 510
    assert truth["dscr"] == 1.5
    assert truth["collateral_cover_pct"] == pytest.approx(80.0)
    assert compute_collateral_cover_pct([]) is None


def test_check_reported_figures_importable_and_correct():
    reasons = check_reported_figures({"dscr": 1.45}, {"dscr": 1.05})
    assert any("Narrative/Ground-Truth Mismatch" in r for r in reasons)


def test_ground_truth_figures_ignores_non_dict_period_values():
    """Malformed/legacy state.json (financials or ratios period value isn't
    a dict) must not crash -- it degrades to no figures for that source
    rather than raising."""
    truth = ground_truth_figures({"FY-Current": "not a dict"}, {"FY-Current": "also not a dict"}, [])
    assert truth == {}


def test_ground_truth_figures_handles_one_malformed_source_alongside_a_valid_one():
    truth = ground_truth_figures(
        {"FY-Current": "not a dict"}, {"FY-Current": {"dscr": 1.5}}, [],
    )
    assert truth == {"dscr": 1.5}


def test_ground_truth_figures_excludes_undefined_ratios_and_subtotals():
    """A ratio or subtotal left as None (e.g. a debt-free company's DSCR --
    evaluate_financial_model() deliberately leaves it undefined rather than
    0) must not appear in ground truth at all -- if it did, any reported
    figure for it would fail values_match() against None every time,
    an unconditional and unfixable "mismatch" for a legitimately undefined
    metric, defeating the None-vs-zero distinction in the first place."""
    truth = ground_truth_figures(
        {"FY-Current": {"raw": {}, "ebitda": None, "tangible_net_worth": 250}},
        {"FY-Current": {"dscr": None, "gross_leverage": 0.5}},
        [],
    )
    assert truth == {"gross_leverage": 0.5, "tangible_net_worth": 250}
    assert "dscr" not in truth
    assert "ebitda" not in truth


def test_check_reported_figures_flags_unresolvable_rather_than_unfixable_mismatch_for_undefined_metric():
    """Reporting a figure for a metric ground truth excluded (because it's
    undefined) must be UNRESOLVABLE_REPORTED_FIGURE, not a permanent
    Narrative/Ground-Truth Mismatch the Underwriter could never satisfy by
    revising -- correctly omitting the key (per the Structured Output
    guideline) must pass with zero reasons."""
    truth = ground_truth_figures({}, {"FY-Current": {"dscr": None}}, [])

    reasons_when_reported_anyway = check_reported_figures({"dscr": 0}, truth)
    assert any("UNRESOLVABLE_REPORTED_FIGURE" in r for r in reasons_when_reported_anyway)
    assert not any("Mismatch" in r for r in reasons_when_reported_anyway)

    reasons_when_correctly_omitted = check_reported_figures({}, truth)
    assert reasons_when_correctly_omitted == []


# ---------------------------------------------------------------------------
# check_draft_compliance(): the unified entry point both orchestrator.py and
# scripts/policy_check.py's CLI use.
# ---------------------------------------------------------------------------

def test_check_draft_compliance_passes_a_fully_compliant_draft():
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    reasons = check_draft_compliance(draft, _policy_state(), {})
    assert reasons == []


def test_check_draft_compliance_flags_missing_cp():
    draft = _compliant_draft(cp_ids=[])
    reasons = check_draft_compliance(draft, _policy_state(), {})
    assert any("Missing Required CP KYC-AML" in r for r in reasons)


def test_check_draft_compliance_flags_missing_narrative_sources():
    draft = _compliant_draft(cp_ids=["KYC-AML"], sources=[])
    reasons = check_draft_compliance(draft, _policy_state(), {})
    assert any("Missing Narrative Sources" in r for r in reasons)


def test_check_draft_compliance_flags_narrative_sources_that_are_only_blank_strings():
    """A declared-but-empty citation (whitespace, or an empty string) is
    the same as not declaring one at all -- must not satisfy the check."""
    draft = _compliant_draft(cp_ids=["KYC-AML"], sources=["", "   "])
    reasons = check_draft_compliance(draft, _policy_state(), {})
    assert any("Missing Narrative Sources" in r for r in reasons)


def test_check_draft_compliance_passes_with_at_least_one_real_source():
    draft = _compliant_draft(cp_ids=["KYC-AML"], sources=["Companies House filing, FY2025"])
    reasons = check_draft_compliance(draft, _policy_state(), {})
    assert reasons == []


def test_check_draft_compliance_skips_narrative_sources_check_when_no_draft_yet():
    """draft_text=None (e.g. /assemble's pre-draft policy_state call) must
    not flag a missing-sources reason against a draft that doesn't exist yet."""
    reasons = check_draft_compliance(None, _policy_state(), {})
    assert not any("Missing Narrative Sources" in r for r in reasons)


def test_check_draft_compliance_flags_covenant_failure_regardless_of_draft():
    policy_state = _policy_state(covenant_results=[
        {"metric": "dscr", "type": "minimum", "threshold": 1.25, "actual": 1.0, "status": "FAIL", "headroom_pct": -0.2},
    ])
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    reasons = check_draft_compliance(draft, policy_state, {})
    assert any("Covenant FAIL: dscr" in r for r in reasons)


def test_check_draft_compliance_flags_security_gaps():
    policy_state = _policy_state(security_gaps=["Uncharged Asset: AST-002 has no corresponding security charge registered."])
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    reasons = check_draft_compliance(draft, policy_state, {})
    assert "Uncharged Asset: AST-002 has no corresponding security charge registered." in reasons


def test_check_draft_compliance_flags_narrative_mismatch():
    draft = _compliant_draft(cp_ids=["KYC-AML"], reported_figures={"dscr": 1.45})
    reasons = check_draft_compliance(draft, _policy_state(), {"dscr": 1.05})
    assert any("Narrative/Ground-Truth Mismatch" in r for r in reasons)


# ---------------------------------------------------------------------------
# draft_text=None: "no draft yet" mode -- used by /assemble (via
# policy_check.py) to obtain policy_state *before* drafting, where reporting
# every required CP as "missing" against a nonexistent draft would be noise.
# ---------------------------------------------------------------------------

def test_check_draft_compliance_with_no_draft_skips_cp_and_taxonomy_checks():
    """draft_text=None must not report CP/taxonomy reasons -- there's no
    draft yet for the Underwriter to have included them in."""
    policy_state = _policy_state(required_cps=[
        {"cp_id": "KYC-AML", "text": "KYC/AML clearance."},
        {"cp_id": "SEC-PERFECT-AST-001", "text": "Perfect the charge over AST-001."},
    ])
    reasons = check_draft_compliance(None, policy_state, {})
    assert reasons == []  # no covenant/security issues either in this policy_state


def test_check_draft_compliance_with_no_draft_still_reports_covenant_and_security_issues():
    """Covenant/security reasons depend only on the deal's own recorded
    structure, not on any draft -- these must still surface even before a
    draft exists, since /assemble needs to know about them to inject into
    its own drafting context."""
    policy_state = _policy_state(
        covenant_results=[
            {"metric": "dscr", "type": "minimum", "threshold": 1.25, "actual": 1.0, "status": "FAIL", "headroom_pct": -0.2},
        ],
        security_gaps=["Uncharged Asset: AST-002 has no corresponding security charge registered."],
    )
    reasons = check_draft_compliance(None, policy_state, {})
    assert any("Covenant FAIL: dscr" in r for r in reasons)
    assert "Uncharged Asset: AST-002 has no corresponding security charge registered." in reasons


def test_check_draft_compliance_empty_string_draft_is_treated_as_a_real_draft():
    """Unlike None, an empty string IS a draft (just an empty one) -- it
    should report every required CP as missing, not be treated as
    "no draft yet"."""
    reasons = check_draft_compliance("", _policy_state(), {})
    assert any("Missing Required CP KYC-AML" in r for r in reasons)
    assert any("Missing or malformed Risk Category" in r for r in reasons)


# ---------------------------------------------------------------------------
# Downside breach disclosure: a breach itself never forces a reason (a deal
# can be sound with disclosed downside risk) -- only silently omitting it
# from downside_breaches_acknowledged does.
# ---------------------------------------------------------------------------

DOWNSIDE_BREACH = {
    "year": "FY+2", "metric": "dscr", "base_actual": 1.25,
    "downside_actual": 1.05, "threshold": 1.10, "breach_id": "DOWNSIDE-FY-2-DSCR",
}


def test_check_draft_compliance_flags_undisclosed_downside_breach():
    draft = _compliant_draft(cp_ids=["KYC-AML"], downside_breaches_acknowledged=[])
    policy_state = _policy_state(downside_covenant_breaches=[DOWNSIDE_BREACH])

    reasons = check_draft_compliance(draft, policy_state, {})
    assert any(
        "Undisclosed Downside Breach DOWNSIDE-FY-2-DSCR" in r and "FY+2" in r and "dscr" in r
        for r in reasons
    )


def test_check_draft_compliance_passes_when_downside_breach_is_acknowledged():
    """The breach itself never forces REJECTED -- only non-disclosure does."""
    draft = _compliant_draft(cp_ids=["KYC-AML"], downside_breaches_acknowledged=["DOWNSIDE-FY-2-DSCR"])
    policy_state = _policy_state(downside_covenant_breaches=[DOWNSIDE_BREACH])

    reasons = check_draft_compliance(draft, policy_state, {})
    assert reasons == []


def test_check_draft_compliance_with_no_draft_never_checks_downside_disclosure():
    """Disclosure is a property of the draft's own narrative -- it can't be
    checked before a draft exists, same as CP/taxonomy."""
    policy_state = _policy_state(downside_covenant_breaches=[DOWNSIDE_BREACH])
    reasons = check_draft_compliance(None, policy_state, {})
    assert not any("Undisclosed Downside Breach" in r for r in reasons)


def test_check_draft_compliance_no_reasons_when_no_downside_breaches_exist():
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    reasons = check_draft_compliance(draft, _policy_state(), {})
    assert reasons == []


# ---------------------------------------------------------------------------
# Downside reported-figures accuracy: the same mismatch/unresolvable
# discipline as base-case reported_figures, extended to downside keys.
# ---------------------------------------------------------------------------

def test_ground_truth_figures_includes_downside_keys_when_downside_case_given():
    downside_case = {
        "ratios": {"FY+2": {"dscr": 1.05}},
        "financials": {"FY+2": {"raw": {"revenue": 900}, "ebitda": 480}},
    }
    truth = ground_truth_figures({}, {}, [], downside_case=downside_case)
    assert truth["dscr_FY+2_downside"] == 1.05
    assert truth["ebitda_FY+2_downside"] == 480
    assert "raw_FY+2_downside" not in truth  # the nested raw dict is never a figure itself


def test_ground_truth_figures_excludes_undefined_downside_ratios():
    downside_case = {"ratios": {"FY+2": {"dscr": None}}, "financials": {}}
    truth = ground_truth_figures({}, {}, [], downside_case=downside_case)
    assert "dscr_FY+2_downside" not in truth


def test_ground_truth_figures_downside_keys_never_collide_with_base_case_keys():
    truth = ground_truth_figures(
        {"FY-Current": {"raw": {}, "ebitda": 510}},
        {"FY-Current": {"dscr": 1.5}},
        [],
        downside_case={"ratios": {"FY+2": {"dscr": 1.05}}, "financials": {}},
    )
    assert truth["dscr"] == 1.5  # base case, untouched
    assert truth["dscr_FY+2_downside"] == 1.05  # downside, distinct key


def test_check_draft_compliance_catches_downside_reported_figure_mismatch():
    """The same narrative-accuracy discipline FY-Current already has, now
    extended to a downside-case figure -- not just whether the breach was
    disclosed, but whether the cited number for it is actually correct."""
    draft = _compliant_draft(
        cp_ids=["KYC-AML"],
        reported_figures={"dscr_FY+2_downside": 5.0},  # wildly wrong
        downside_breaches_acknowledged=["DOWNSIDE-FY-2-DSCR"],
    )
    policy_state = _policy_state(downside_covenant_breaches=[DOWNSIDE_BREACH])
    ground_truth = ground_truth_figures({}, {}, [], downside_case={"ratios": {"FY+2": {"dscr": 1.05}}, "financials": {}})

    reasons = check_draft_compliance(draft, policy_state, ground_truth)
    assert any(
        "Narrative/Ground-Truth Mismatch: reported dscr_FY+2_downside 5.0 vs computed 1.05" in r
        for r in reasons
    )


def test_check_draft_compliance_downside_figure_within_tolerance_passes():
    draft = _compliant_draft(
        cp_ids=["KYC-AML"],
        reported_figures={"dscr_FY+2_downside": 1.0501},
        downside_breaches_acknowledged=["DOWNSIDE-FY-2-DSCR"],
    )
    policy_state = _policy_state(downside_covenant_breaches=[DOWNSIDE_BREACH])
    ground_truth = ground_truth_figures({}, {}, [], downside_case={"ratios": {"FY+2": {"dscr": 1.05}}, "financials": {}})

    reasons = check_draft_compliance(draft, policy_state, ground_truth)
    assert reasons == []
