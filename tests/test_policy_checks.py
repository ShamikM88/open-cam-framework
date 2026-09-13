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


def _compliant_draft(cp_ids=("KYC-AML",), reported_figures=None):
    import json
    payload = {
        "cp_ids_included": list(cp_ids),
        "risk_categories_covered": ALL_CATEGORIES_COVERED,
        "reported_figures": reported_figures or {},
    }
    return "# Draft\n\n```json\n" + json.dumps(payload) + "\n```"


def _policy_state(required_cps=None, covenant_results=None, security_gaps=None):
    return {
        "required_conditions_precedent": required_cps or [{"cp_id": "KYC-AML", "text": "KYC/AML clearance."}],
        "covenant_results": covenant_results or [],
        "security_gaps": security_gaps or [],
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
