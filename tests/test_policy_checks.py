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


def _compliant_draft(cp_ids=("KYC-AML",), cs_ids=("MI-REPORTING",), reported_figures=None,
                      downside_breaches_acknowledged=None, sources=None,
                      financials_source_disclosed=None, credit_policy_considered=None):
    import json
    payload = {
        "cp_ids_included": list(cp_ids),
        "cs_ids_included": list(cs_ids),
        "risk_categories_covered": ALL_CATEGORIES_COVERED,
        "reported_figures": reported_figures or {},
        "downside_breaches_acknowledged": list(downside_breaches_acknowledged or []),
        "sources": list(sources) if sources is not None else ["Test Source"],
    }
    if financials_source_disclosed is not None:
        payload["financials_source_disclosed"] = financials_source_disclosed
    if credit_policy_considered is not None:
        payload["credit_policy_considered"] = credit_policy_considered
    return "# Draft\n\n```json\n" + json.dumps(payload) + "\n```"


def _policy_state(required_cps=None, required_css=None, covenant_results=None, security_gaps=None,
                   downside_covenant_breaches=None):
    return {
        "required_conditions_precedent": required_cps or [{"cp_id": "KYC-AML", "text": "KYC/AML clearance."}],
        "required_conditions_subsequent": (
            required_css if required_css is not None
            else [{"cs_id": "MI-REPORTING", "text": "Periodic MI submission."}]
        ),
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


def test_parse_underwriter_output_extracts_cs_ids_included():
    draft = _compliant_draft(cs_ids=["MI-REPORTING", "CS-COVENANT-COMPLIANCE"])
    result = parse_underwriter_output(draft)
    assert result["cs_ids_included"] == ["MI-REPORTING", "CS-COVENANT-COMPLIANCE"]


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
    truth = ground_truth_figures(
        {"FY-Current": "not a dict", "FY-1": "not a dict", "FY+1": "not a dict"},
        {"FY-Current": "also not a dict", "FY-1": "not a dict", "FY+1": "not a dict"},
        [],
    )
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


def test_check_draft_compliance_flags_undisclosed_analyst_supplied_financials():
    """See agents/underwriter_agent.md's Guideline 9 -- an analyst-supplied
    deal whose draft never set financials_source_disclosed must be flagged
    regardless of how compliant the rest of the draft is."""
    draft = _compliant_draft(cp_ids=["KYC-AML"])  # financials_source_disclosed omitted
    reasons = check_draft_compliance(draft, _policy_state(), {}, financials_source="analyst-supplied")
    assert any("Missing Analyst-Supplied Spreading Disclosure" in r for r in reasons)


def test_check_draft_compliance_passes_disclosed_analyst_supplied_financials():
    draft = _compliant_draft(cp_ids=["KYC-AML"], financials_source_disclosed=True)
    reasons = check_draft_compliance(draft, _policy_state(), {}, financials_source="analyst-supplied")
    assert reasons == []


def test_check_draft_compliance_ignores_disclosure_flag_for_framework_computed():
    """The caveat check is specific to financials_source == "analyst-supplied"
    -- framework-computed (the default) and unspecified must never require it,
    even though financials_source_disclosed is omitted from the draft."""
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    assert check_draft_compliance(draft, _policy_state(), {}, financials_source="framework-computed") == []
    assert check_draft_compliance(draft, _policy_state(), {}, financials_source=None) == []


def test_check_draft_compliance_flags_undeclared_credit_policy_consideration():
    """See agents/underwriter_agent.md's Guideline 10 -- when this fork has a
    calibrated credit policy, a draft that never declared
    credit_policy_considered must be flagged regardless of how compliant the
    rest of the draft is."""
    draft = _compliant_draft(cp_ids=["KYC-AML"])  # credit_policy_considered omitted
    reasons = check_draft_compliance(draft, _policy_state(), {}, credit_policy_present=True)
    assert any("Missing Credit Policy Consideration" in r for r in reasons)


def test_check_draft_compliance_passes_declared_credit_policy_consideration():
    draft = _compliant_draft(cp_ids=["KYC-AML"], credit_policy_considered=True)
    reasons = check_draft_compliance(draft, _policy_state(), {}, credit_policy_present=True)
    assert reasons == []


def test_check_draft_compliance_ignores_credit_policy_flag_when_no_policy_calibrated():
    """The consideration check only applies when this fork actually has a
    calibrated credit policy -- credit_policy_present=False/None must never
    require the declaration, even though it's omitted from the draft."""
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    assert check_draft_compliance(draft, _policy_state(), {}, credit_policy_present=False) == []
    assert check_draft_compliance(draft, _policy_state(), {}, credit_policy_present=None) == []


def test_check_draft_compliance_flags_missing_cs():
    draft = _compliant_draft(cs_ids=[])
    reasons = check_draft_compliance(draft, _policy_state(), {})
    assert any("Missing Required Condition Subsequent MI-REPORTING" in r for r in reasons)


def test_check_draft_compliance_passes_when_every_required_cs_is_included():
    required_css = [
        {"cs_id": "MI-REPORTING", "text": "Periodic MI submission."},
        {"cs_id": "CS-COVENANT-COMPLIANCE", "text": "Ongoing covenant certification."},
    ]
    draft = _compliant_draft(cs_ids=["MI-REPORTING", "CS-COVENANT-COMPLIANCE"])
    reasons = check_draft_compliance(draft, _policy_state(required_css=required_css), {})
    assert reasons == []


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


def test_acknowledging_one_of_two_same_metric_downside_breaches_leaves_the_other_undisclosed():
    first_breach = {
        **DOWNSIDE_BREACH,
        "year": "FY+1",
        "breach_id": "DOWNSIDE-FY-1-DSCR",
    }
    second_breach = {
        **DOWNSIDE_BREACH,
        "year": "FY+1",
        "breach_id": "DOWNSIDE-FY-1-DSCR-2",
    }
    draft = _compliant_draft(
        cp_ids=["KYC-AML"], downside_breaches_acknowledged=["DOWNSIDE-FY-1-DSCR"])
    policy_state = _policy_state(
        downside_covenant_breaches=[first_breach, second_breach])

    reasons = check_draft_compliance(draft, policy_state, {})

    assert not any("Undisclosed Downside Breach DOWNSIDE-FY-1-DSCR " in reason for reason in reasons)
    assert any(
        "Undisclosed Downside Breach DOWNSIDE-FY-1-DSCR-2" in reason
        and "FY+1" in reason and "dscr" in reason
        for reason in reasons
    )


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


def test_ground_truth_figures_checks_historical_and_forward_base_case_figures():
    financials = {
        "FY-1": {"raw": {"revenue": 4200}, "ebitda": 850},
        "FY-Current": {"raw": {"revenue": 5000}, "ebitda": 1000},
        "FY+1": {"raw": {"revenue": 4800}, "ebitda": 550},
    }
    ratios = {
        "FY-1": {"dscr": 1.3},
        "FY-Current": {"dscr": 1.5},
        "FY+1": {"dscr": 1.1},
    }

    truth = ground_truth_figures(financials, ratios, [])

    assert truth["ebitda"] == 1000
    assert truth["dscr"] == 1.5
    assert truth["ebitda_FY-1"] == 850
    assert truth["dscr_FY-1"] == 1.3
    assert truth["ebitda_FY+1"] == 550
    assert truth["dscr_FY+1"] == 1.1
    assert "ebitda_FY-1_downside" not in truth
    assert "dscr_FY+1_downside" not in truth
    assert "raw_FY-1" not in truth
    assert check_reported_figures({
        "ebitda_FY-1": 850,
        "dscr_FY+1": 1.1,
        "ebitda": 1000,
        "dscr": 1.5,
    }, truth) == []


def test_check_reported_figures_flags_wrong_historical_and_forward_base_case_values():
    truth = ground_truth_figures(
        {"FY-1": {"raw": {}, "ebitda": 850}, "FY+1": {"raw": {}, "ebitda": 550}},
        {"FY-1": {"dscr": 1.3}, "FY+1": {"dscr": 1.1}},
        [],
    )

    reasons = check_reported_figures({"ebitda_FY-1": 900, "dscr_FY+1": 1.4}, truth)

    assert any(
        "Narrative/Ground-Truth Mismatch: reported ebitda_FY-1 900 vs computed 850" in r
        for r in reasons
    )
    assert any(
        "Narrative/Ground-Truth Mismatch: reported dscr_FY+1 1.4 vs computed 1.1" in r
        for r in reasons
    )


def test_ground_truth_figures_excludes_undefined_non_current_base_case_figures():
    truth = ground_truth_figures(
        {
            "FY-1": {"raw": {}, "ebitda": None},
            "FY-Current": {"raw": {}, "ebitda": 1000},
            "FY+1": {"raw": {}, "ebitda": None},
        },
        {
            "FY-1": {"dscr": None},
            "FY-Current": {"dscr": 1.5},
            "FY+1": {"dscr": None},
        },
        [],
    )

    assert "ebitda_FY-1" not in truth
    assert "dscr_FY-1" not in truth
    assert "ebitda_FY+1" not in truth
    assert "dscr_FY+1" not in truth
    assert truth["ebitda"] == 1000
    assert truth["dscr"] == 1.5

# ---------------------------------------------------------------------------
# Mutation-survivor coverage for check_draft_compliance(): pin the combined
# rejection contract, boundaries, and the ordering the revision prompt uses.
# ---------------------------------------------------------------------------

def _structured_draft(payload):
    import json

    return "# Synthetic Draft\n\n```json\n" + json.dumps(payload) + "\n```"


def test_check_draft_compliance_pins_verbatim_reason_text_and_global_order():
    """A rejected draft's reasons are sent to the Underwriter for revision.
    Keep exact wording and ordering across CPs, CSs, taxonomy, downside,
    disclosures, covenant/security facts, and reported figures."""
    draft = _structured_draft({
        "cp_ids_included": [],
        "cs_ids_included": [],
        "risk_categories_covered": {},
        "reported_figures": {"unlisted_metric": 123, "dscr": 2.0},
        "downside_breaches_acknowledged": [],
        "sources": [],
    })
    policy_state = _policy_state(
        required_cps=[
            {"cp_id": "CP-ALPHA", "text": "Register the all-assets charge."},
            {"cp_id": "CP-BETA", "text": "Deliver the insurance certificate."},
        ],
        required_css=[
            {"cs_id": "CS-REPORTING", "text": "Provide quarterly management accounts."},
        ],
        covenant_results=[
            {
                "metric": "dscr", "type": "minimum", "threshold": 1.25,
                "actual": 1.0, "status": "FAIL", "reason": "below minimum",
            },
            {
                "metric": "net_leverage", "type": "maximum", "threshold": 4.0,
                "actual": None, "status": "UNRESOLVABLE", "reason": "no debt figure",
            },
        ],
        security_gaps=["Uncharged Asset: AST-001 lacks a registered charge.", "Guarantee gap: guarantor signature missing."],
        downside_covenant_breaches=[{
            "breach_id": "DOWNSIDE-FY-1-DSCR",
            "metric": "dscr",
            "year": "FY+1",
            "downside_status": "UNRESOLVABLE",
            "reason": "forward ratios missing",
        }],
    )

    assert check_draft_compliance(
        draft, policy_state, {"dscr": 1.0},
        financials_source="analyst-supplied", credit_policy_present=True,
    ) == [
        "Missing Required CP CP-ALPHA: Register the all-assets charge.",
        "Missing Required CP CP-BETA: Deliver the insurance certificate.",
        "Missing Required Condition Subsequent CS-REPORTING: Provide quarterly management accounts.",
        "Missing or malformed Risk Category: Market",
        "Missing or malformed Risk Category: Refinance",
        "Missing or malformed Risk Category: Operational",
        "Missing or malformed Risk Category: Concentration",
        "Missing or malformed Risk Category: Key Man",
        "Missing or malformed Risk Category: Financial",
        "Missing or malformed Risk Category: Legal",
        "Undisclosed Downside Breach DOWNSIDE-FY-1-DSCR: dscr cannot be tested in FY+1 under stress (forward ratios missing) and is not addressed in the draft.",
        "Missing Narrative Sources: no citation sources were declared for this draft's narrative claims (company history, management, market/competitive) -- see agents/underwriter_agent.md's Structured Output guideline.",
        "Missing Analyst-Supplied Spreading Disclosure: this deal's financials_source is 'analyst-supplied', but the draft did not declare financials_source_disclosed=true -- see agents/underwriter_agent.md's Guideline 9 (the CAM's Financial Analysis section must carry an explicit caveat when spreading wasn't independently recomputed by the framework).",
        "Missing Credit Policy Consideration: this fork has a calibrated institutional credit policy (config/credit_policy.md), but the draft did not declare credit_policy_considered=true -- see agents/underwriter_agent.md's Guideline 10.",
        "Covenant FAIL: dscr (minimum 1.25, actual 1.0) -- below minimum",
        "Covenant UNRESOLVABLE: net_leverage (maximum 4.0, actual None) -- no debt figure",
        "Uncharged Asset: AST-001 lacks a registered charge.",
        "Guarantee gap: guarantor signature missing.",
        "UNRESOLVABLE_REPORTED_FIGURE: 'unlisted_metric' is not a recognized computed figure (reported value: 123).",
        "Narrative/Ground-Truth Mismatch: reported dscr 2.0 vs computed 1.0",
    ]


@pytest.mark.parametrize(
    ("market_entry", "should_be_missing"),
    [
        ({"status": " COVERED "}, False),
        ({"status": "not_applicable", "justification": "outside this facility's scope"}, False),
        ({"status": "not_applicable", "justification": "  \t "}, True),
        ({"status": "rejected", "justification": "not accepted"}, True),
        ("covered", True),
        (None, True),
    ],
)
def test_check_draft_compliance_pins_risk_taxonomy_status_and_justification(
    market_entry, should_be_missing,
):
    categories = {category: {"status": "covered"} for category in REQUIRED_RISK_TAXONOMY}
    categories.pop("Market")
    categories["Market Risk"] = market_entry
    payload = {
        "cp_ids_included": ["KYC-AML"],
        "cs_ids_included": ["MI-REPORTING"],
        "risk_categories_covered": categories,
        "reported_figures": {},
        "downside_breaches_acknowledged": [],
        "sources": ["Synthetic filing"],
    }
    reasons = check_draft_compliance(_structured_draft(payload), _policy_state(), {})
    assert ("Missing or malformed Risk Category: Market" in reasons) is should_be_missing
    assert not any(reason != "Missing or malformed Risk Category: Market" for reason in reasons)


@pytest.mark.parametrize(
    ("actual", "reported", "should_mismatch"),
    [
        (100.0, 100.5, False),       # exactly +0.5% is accepted
        (100.0, 100.5001, True),     # just outside the +0.5% edge
        (100.0, 99.5, False),        # exactly -0.5% is accepted
        (100.0, 99.4999, True),      # just outside the -0.5% edge
        (0.0, 0.0, False),
        (0.0, 0.000000000999, False),  # strictly below the zero guard
        (0.0, 0.000000001, True),      # the zero guard is strict
    ],
)
def test_check_draft_compliance_pins_reported_figure_tolerance_edges(
    actual, reported, should_mismatch,
):
    draft = _compliant_draft(
        cp_ids=["KYC-AML"], reported_figures={"dscr": reported},
    )
    reasons = check_draft_compliance(draft, _policy_state(), {"dscr": actual})
    mismatch_reasons = [reason for reason in reasons if "Narrative/Ground-Truth Mismatch" in reason]
    assert bool(mismatch_reasons) is should_mismatch
    if should_mismatch:
        assert mismatch_reasons == [
            f"Narrative/Ground-Truth Mismatch: reported dscr {reported} vs computed {actual}"
        ]
    else:
        assert reasons == []


@pytest.mark.parametrize("status", ["PASS", "pass", "MET", "NOT_APPLICABLE"])
def test_check_draft_compliance_does_not_reject_non_failure_covenant_statuses(status):
    policy_state = _policy_state(covenant_results=[{
        "metric": "dscr", "type": "minimum", "threshold": 1.25,
        "actual": 1.4, "status": status,
    }])
    assert check_draft_compliance(None, policy_state, {}) == []


@pytest.mark.parametrize("financials_source", ["Analyst-Supplied", "analyst-supplied ", "framework-computed"])
def test_check_draft_compliance_requires_analyst_disclosure_only_for_exact_source_value(
    financials_source,
):
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    assert check_draft_compliance(
        draft, _policy_state(), {}, financials_source=financials_source,
    ) == []

