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

from policy_check import compute
from state_manager import write_state


def _compliant_draft(cp_ids=("KYC-AML", "FACILITY-EXECUTION")):
    payload = {
        "cp_ids_included": list(cp_ids),
        "risk_categories_covered": {
            category: {"status": "covered"}
            for category in ["Market", "Refinance", "Operational", "Concentration",
                              "Key Man", "Financial", "Legal"]
        },
        "reported_figures": {},
        "sources": ["Test Source"],
    }
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


def test_compute_checks_reported_figures_against_state_when_draft_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", ratios={"FY-Current": {"dscr": 1.05}})

    payload = {
        "cp_ids_included": ["KYC-AML", "FACILITY-EXECUTION"],
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
