"""Tests for scripts/policy_check.py: the standalone CLI wrapper that lets
the /assemble and /review slash commands' Bash steps run the same
deterministic checks orchestrator.py's headless pipeline does.

Tests call compute() directly rather than invoking the script as a
subprocess -- the __main__ block is a thin argparse+print wrapper around
compute(), so exercising compute() covers the actual logic; a subprocess
test would only additionally verify argparse wiring, which is simple enough
to trust by inspection.
"""
import json
import os

from policy_check import compute
from state_manager import write_state


def _compliant_draft(cp_ids=("KYC-AML", "FACILITY-EXECUTION"), cs_ids=("MI-REPORTING",),
                      financials_source_disclosed=None, credit_policy_considered=None):
    payload = {
        "cp_ids_included": list(cp_ids),
        "cs_ids_included": list(cs_ids),
        "risk_categories_covered": {
            category: {"status": "covered"}
            for category in ["Market", "Refinance", "Operational", "Concentration",
                              "Key Man", "Financial", "Legal"]
        },
        "reported_figures": {},
        "sources": ["Test Source"],
    }
    if financials_source_disclosed is not None:
        payload["financials_source_disclosed"] = financials_source_disclosed
    if credit_policy_considered is not None:
        payload["credit_policy_considered"] = credit_policy_considered
    return "# Draft CAM\n\n```json\n" + json.dumps(payload) + "\n```"


def test_compute_with_no_state_and_no_draft_returns_only_standard_cps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = compute("Acme Corp", "Fleet Loan")

    assert result["compliant"] is True
    assert result["reasons"] == []
    cp_ids = [cp["cp_id"] for cp in result["policy_state"]["required_conditions_precedent"]]
    assert cp_ids == ["KYC-AML", "FACILITY-EXECUTION"]


def test_compute_with_no_draft_still_surfaces_structural_covenant_and_security_issues(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state(
        "Acme Corp", "Fleet Loan",
        ratios={"FY-Current": {"dscr": 1.0}},
        covenants=[{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
        collateral=[{"asset_id": "AST-001"}],
        security_package=[],
    )

    result = compute("Acme Corp", "Fleet Loan")

    assert result["compliant"] is False
    assert any("Covenant FAIL: dscr" in r for r in result["reasons"])
    assert any("Uncharged Asset: AST-001" in r for r in result["reasons"])
    # No draft was given -- CP/taxonomy checks must not fire even though
    # cp_ids_included is empty (there's no draft to have included them in).
    assert not any("Missing Required CP" in r for r in result["reasons"])


def test_compute_with_a_compliant_draft_reports_no_reasons(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(_compliant_draft(), encoding="utf-8")

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft_path))
    assert result["compliant"] is True
    assert result["reasons"] == []


def test_compute_with_an_incomplete_draft_flags_missing_cp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(_compliant_draft(cp_ids=[]), encoding="utf-8")

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft_path))
    assert result["compliant"] is False
    assert any("Missing Required CP KYC-AML" in r for r in result["reasons"])


def test_compute_with_an_incomplete_draft_flags_missing_cs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(_compliant_draft(cs_ids=[]), encoding="utf-8")

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft_path))
    assert result["compliant"] is False
    assert any("Missing Required Condition Subsequent MI-REPORTING" in r for r in result["reasons"])


def test_compute_checks_reported_figures_against_state_when_draft_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", ratios={"FY-Current": {"dscr": 1.05}})

    payload = {
        "cp_ids_included": ["KYC-AML", "FACILITY-EXECUTION"],
        "cs_ids_included": ["MI-REPORTING"],
        "risk_categories_covered": {
            category: {"status": "covered"}
            for category in ["Market", "Refinance", "Operational", "Concentration",
                              "Key Man", "Financial", "Legal"]
        },
        "reported_figures": {"dscr": 5.0},  # wildly wrong vs. computed 1.05
    }
    draft_path = tmp_path / "draft.md"
    draft_path.write_text("# Draft\n\n```json\n" + json.dumps(payload) + "\n```", encoding="utf-8")

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft_path))
    assert result["compliant"] is False
    assert any("Narrative/Ground-Truth Mismatch: reported dscr 5.0 vs computed 1.05" in r
               for r in result["reasons"])


def test_compute_flags_undisclosed_analyst_supplied_financials(tmp_path, monkeypatch):
    """See issue #62 / agents/underwriter_agent.md's Guideline 9 -- compute()
    must read state.json's financials_source and reject a draft that never
    declared financials_source_disclosed for an analyst-supplied deal."""
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", financials_source="analyst-supplied")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(_compliant_draft(), encoding="utf-8")  # disclosure omitted

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft_path))
    assert result["compliant"] is False
    assert any("Missing Analyst-Supplied Spreading Disclosure" in r for r in result["reasons"])


def test_compute_passes_disclosed_analyst_supplied_financials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", financials_source="analyst-supplied")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(_compliant_draft(financials_source_disclosed=True), encoding="utf-8")

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft_path))
    assert result["compliant"] is True
    assert result["reasons"] == []


def test_compute_reports_cam_data_absent_for_a_research_only_deal(tmp_path, monkeypatch):
    """Issue #87: a deal that only ran /research (triage/commercial, never
    /spread) has no financials/ratios/covenants/security_package at all --
    cam_data_present must be False, so a caller can't mistake an empty
    "reasons" list for real, verified compliance."""
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", triage={"go_no_go": "Go"}, commercial={"sources": []})

    result = compute("Acme Corp", "Fleet Loan")
    assert result["cam_data_present"] is False
    assert result["compliant"] is True  # nothing applicable was found to flag -- not "verified sound"


def test_compute_reports_cam_data_present_once_financials_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", financials={"FY-Current": {"revenue": 1000}})

    result = compute("Acme Corp", "Fleet Loan")
    assert result["cam_data_present"] is True


def test_compute_reports_cam_data_present_once_covenants_exist_even_without_financials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan",
                covenants=[{"metric": "dscr", "type": "minimum", "threshold": 1.25}])

    result = compute("Acme Corp", "Fleet Loan")
    assert result["cam_data_present"] is True


def test_compute_ignores_credit_policy_consideration_when_none_calibrated(tmp_path, monkeypatch):
    """No config/credit_policy.md exists in this fork (tmp_path has none) --
    compute() must not require credit_policy_considered even though it's
    omitted from the draft."""
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(_compliant_draft(), encoding="utf-8")

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft_path))
    assert result["compliant"] is True
    assert result["reasons"] == []


def test_compute_flags_undeclared_credit_policy_consideration_when_calibrated(tmp_path, monkeypatch):
    """See issue #57 / agents/underwriter_agent.md's Guideline 10 -- compute()
    must detect config/credit_policy.md's presence and reject a draft that
    never declared credit_policy_considered."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("config", exist_ok=True)
    with open("config/credit_policy.md", "w", encoding="utf-8") as f:
        f.write("# Institutional Credit Policy (Calibrated)\n")
    write_state("Acme Corp", "Fleet Loan")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(_compliant_draft(), encoding="utf-8")  # consideration omitted

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft_path))
    assert result["compliant"] is False
    assert any("Missing Credit Policy Consideration" in r for r in result["reasons"])


def test_compute_passes_declared_credit_policy_consideration_when_calibrated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("config", exist_ok=True)
    with open("config/credit_policy.md", "w", encoding="utf-8") as f:
        f.write("# Institutional Credit Policy (Calibrated)\n")
    write_state("Acme Corp", "Fleet Loan")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(_compliant_draft(credit_policy_considered=True), encoding="utf-8")

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft_path))
    assert result["compliant"] is True
    assert result["reasons"] == []
