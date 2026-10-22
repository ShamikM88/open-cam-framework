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

import pytest
import policy_check
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

# ---------------------------------------------------------------------------
# Mutation-survivor coverage for policy_check.compute(): state projection,
# default normalization, draft-path handling, and result contract.
# ---------------------------------------------------------------------------

def pc_stub_compute_collaborators(monkeypatch, loaded_state, *, policy_state=None, truth=None, reasons=None):
    """Stub compute's deterministic collaborators and record its public orchestration."""
    calls = {
        "read_state": [],
        "evaluate_deal_policy": [],
        "ground_truth_figures": [],
        "check_draft_compliance": [],
    }
    result_policy_state = {"synthetic": "policy state"} if policy_state is None else policy_state
    result_truth = {"synthetic_figure": 12.5} if truth is None else truth
    result_reasons = [] if reasons is None else reasons

    def fake_read_state(company, proposal, keys=None):
        calls["read_state"].append((company, proposal, keys))
        return loaded_state

    def fake_evaluate_deal_policy(policy_input):
        calls["evaluate_deal_policy"].append(policy_input)
        return result_policy_state

    def fake_ground_truth_figures(financials, ratios, collateral, downside_case):
        calls["ground_truth_figures"].append((financials, ratios, collateral, downside_case))
        return result_truth

    def fake_check_draft_compliance(
        draft_text, actual_policy_state, actual_truth, *,
        financials_source=None, credit_policy_present=False,
    ):
        calls["check_draft_compliance"].append((
            draft_text, actual_policy_state, actual_truth,
            financials_source, credit_policy_present,
        ))
        return result_reasons

    monkeypatch.setattr(policy_check, "read_state", fake_read_state)
    monkeypatch.setattr(policy_check, "evaluate_deal_policy", fake_evaluate_deal_policy)
    monkeypatch.setattr(policy_check, "ground_truth_figures", fake_ground_truth_figures)
    monkeypatch.setattr(policy_check, "check_draft_compliance", fake_check_draft_compliance)
    return calls, result_policy_state, result_truth, result_reasons


def test_compute_forwards_the_loaded_state_utf8_draft_and_policy_flags_exactly(
    tmp_path, monkeypatch, cp1252_default_open,
):
    """A UTF-8 draft and every state component reach their intended policy
    boundary; the public result preserves reason order and exact result keys."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "credit_policy.md").write_text(
        "Synthetic calibrated policy\n", encoding="utf-8",
    )
    loaded_state = {
        "financials": {"FY-Current": {"revenue": 1200.0}},
        "ratios": {"FY-Current": {"dscr": 1.35}},
        "collateral": [{"asset_id": "SYN-AST-001", "value": 500.0}],
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.2}],
        "security_package": [{
            "secures_asset_id": "SYN-AST-001",
            "perfection_status": "Perfected",
            "ranking": "First",
        }],
        "guarantees": [{"provider": "Synthetic Guarantor", "type": "corporate", "amount": 300.0}],
        "downside_case": {
            "financials": {"FY+1": {"ebitda": 100.0}},
            "ratios": {"FY+1": {"dscr": 0.9}},
        },
        "financials_source": "analyst-supplied",
    }
    policy_state = {"covenant_results": [{"metric": "dscr", "status": "UNRESOLVABLE"}]}
    truth = {"dscr": 1.35, "dscr_FY+1": 0.9}
    reasons = ["Synthetic first rejection reason.", "Synthetic second rejection reason."]
    calls, _, _, _ = pc_stub_compute_collaborators(
        monkeypatch, loaded_state, policy_state=policy_state, truth=truth, reasons=reasons,
    )
    draft_text = "# Synthetic Draft — café\nSources: Synthetic filing.\n"
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(draft_text, encoding="utf-8")

    result = compute("Synthetic Co", "Synthetic Facility", draft_path=str(draft_path))

    assert result == {
        "policy_state": policy_state,
        "reasons": reasons,
        "compliant": False,
        "cam_data_present": True,
    }
    assert len(calls["read_state"]) == 1
    read_company, read_proposal, read_keys = calls["read_state"][0]
    assert (read_company, read_proposal) == ("Synthetic Co", "Synthetic Facility")
    assert set(read_keys) == {
        "financials", "ratios", "collateral", "covenants",
        "security_package", "guarantees", "downside_case", "financials_source",
    }
    assert calls["evaluate_deal_policy"] == [{
        "financials": loaded_state["financials"],
        "ratios": loaded_state["ratios"],
        "collateral": loaded_state["collateral"],
        "covenants": loaded_state["covenants"],
        "security_package": loaded_state["security_package"],
        "guarantees": loaded_state["guarantees"],
        "downside_case": loaded_state["downside_case"],
        "group_basis": None,
    }]
    assert calls["ground_truth_figures"] == [(
        loaded_state["financials"], loaded_state["ratios"],
        loaded_state["collateral"], loaded_state["downside_case"],
    )]
    assert calls["check_draft_compliance"] == [(
        draft_text, policy_state, truth, "analyst-supplied", True,
    )]


@pytest.mark.parametrize(
    "loaded_state",
    [
        None,
        {},
        {
            "financials": None,
            "ratios": None,
            "collateral": None,
            "covenants": None,
            "security_package": None,
            "guarantees": None,
            "downside_case": None,
            "financials_source": None,
        },
    ],
    ids=["state-file-missing", "empty-state", "nullable-state-fields"],
)
def test_compute_normalizes_missing_or_null_state_to_the_empty_policy_shape(
    tmp_path, monkeypatch, loaded_state,
):
    monkeypatch.chdir(tmp_path)
    policy_state = {"synthetic": "empty policy result"}
    truth = {"synthetic": "empty ground truth"}
    calls, _, _, _ = pc_stub_compute_collaborators(
        monkeypatch, loaded_state, policy_state=policy_state, truth=truth, reasons=[],
    )

    result = compute("Synthetic Co", "Synthetic Facility")

    empty_policy_input = {
        "financials": {},
        "ratios": {},
        "collateral": [],
        "covenants": [],
        "security_package": [],
        "guarantees": [],
        "downside_case": {},
        "group_basis": None,
    }
    assert result == {
        "policy_state": policy_state,
        "reasons": [],
        "compliant": True,
        "cam_data_present": False,
    }
    assert len(calls["read_state"]) == 1
    read_company, read_proposal, read_keys = calls["read_state"][0]
    assert (read_company, read_proposal) == ("Synthetic Co", "Synthetic Facility")
    assert set(read_keys) == {
        "financials", "ratios", "collateral", "covenants",
        "security_package", "guarantees", "downside_case", "financials_source",
    }
    assert calls["evaluate_deal_policy"] == [empty_policy_input]
    assert calls["ground_truth_figures"] == [({}, {}, [], {})]
    assert calls["check_draft_compliance"] == [(None, policy_state, truth, None, False)]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("ratios", {"FY-Current": {"dscr": 1.1}}, True),
        ("security_package", [{
            "secures_asset_id": "SYN-AST-001",
            "perfection_status": "Perfected",
            "ranking": "First",
        }], True),
        ("collateral", [{"asset_id": "SYN-AST-001"}], False),
        ("downside_case", {
            "financials": {"FY+1": {"ebitda": 100.0}},
            "ratios": {"FY+1": {"dscr": 0.9}},
        }, False),
        ("guarantees", [{"provider": "Synthetic Guarantor", "amount": 300.0}], False),
    ],
)
def test_compute_cam_data_present_tracks_only_spreading_and_security_inputs(
    tmp_path, monkeypatch, field, value, expected,
):
    """Ratios and the security package each qualify; collateral, downside
    and guarantees alone do not make a research-only deal a spread CAM."""
    monkeypatch.chdir(tmp_path)
    calls, _, _, _ = pc_stub_compute_collaborators(
        monkeypatch, {field: value}, reasons=[],
    )

    result = compute("Synthetic Co", "Synthetic Facility")

    assert result["cam_data_present"] is expected
    assert calls["evaluate_deal_policy"][0].get(field) == value


def test_compute_treats_an_empty_string_draft_path_as_no_draft(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls, policy_state, truth, reasons = pc_stub_compute_collaborators(
        monkeypatch, {}, reasons=[],
    )

    result = compute("Synthetic Co", "Synthetic Facility", draft_path="")

    assert result == {
        "policy_state": policy_state,
        "reasons": reasons,
        "compliant": True,
        "cam_data_present": False,
    }
    assert calls["check_draft_compliance"] == [(None, policy_state, truth, None, False)]


def test_compute_propagates_a_missing_draft_file_before_ground_truth_or_compliance(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    calls, *_ = pc_stub_compute_collaborators(monkeypatch, {})

    with pytest.raises(FileNotFoundError):
        compute("Synthetic Co", "Synthetic Facility", draft_path=str(tmp_path / "missing.md"))

    assert calls["ground_truth_figures"] == []
    assert calls["check_draft_compliance"] == []



# ---------------------------------------------------------------------------
# Covenant basis (issue #48): the state's group_basis reaches the engine, and a malformed one is refused.
# ---------------------------------------------------------------------------

def _group_basis_for_policy_tests():
    return {
        "basis": "supplied_consolidated", "reporting_entity": "Synthetic Group Ltd",
        "entities_covered": ["Synthetic Group Ltd", "Synthetic Group OpCo"],
        "source": {"document": "Borrower-supplied consolidated accounts", "accounting_basis": "UK GAAP",
                   "date": "2026-08-31"},
        "period_end": "2026-03-31", "periods": ["FY-Current"], "figures_basis": "supplied",
        "perimeter_confirmed": True, "perimeter_confirmed_on": "2026-10-21",
    }


def test_compute_tests_a_covenant_on_its_basis_against_the_states_recorded_group_basis(tmp_path, monkeypatch):
    from policy_check import compute
    from state_manager import write_state

    monkeypatch.chdir(tmp_path)
    write_state("Synthetic Group Ltd", "Group Facility", date_str="2026-10-21",
                financials_source="analyst-supplied", ratios={"FY-Current": {"dscr": 1.4}},
                group_basis=_group_basis_for_policy_tests(),
                covenants=[{"metric": "dscr", "type": "minimum", "threshold": 1.25, "basis": "consolidated"},
                           {"metric": "dscr", "type": "minimum", "threshold": 1.25}])

    results = compute("Synthetic Group Ltd", "Group Facility")["policy_state"]["covenant_results"]

    assert [(r["basis"], r["status"]) for r in results] == [("consolidated", "PASS"), ("borrower", "UNRESOLVABLE")]
    assert "recorded figures are the consolidated view" in results[1]["reason"]


def test_compute_without_a_group_basis_leaves_a_legacy_covenant_exactly_as_before(tmp_path, monkeypatch):
    from policy_check import compute
    from state_manager import write_state

    monkeypatch.chdir(tmp_path)
    write_state("Synthetic Co", "Facility", date_str="2026-10-21", ratios={"FY-Current": {"dscr": 1.4}},
                covenants=[{"metric": "dscr", "type": "minimum", "threshold": 1.25}])

    result, = compute("Synthetic Co", "Facility")["policy_state"]["covenant_results"]

    assert "basis" not in result and result["status"] == "PASS"


def test_compute_refuses_a_malformed_group_basis_naming_the_key(tmp_path, monkeypatch):
    from policy_check import compute
    from state_manager import StateShapeError, write_state

    monkeypatch.chdir(tmp_path)
    write_state("Synthetic Co", "Facility", date_str="2026-10-21", group_basis={"basis": "nonsense"},
                covenants=[{"metric": "dscr", "type": "minimum", "threshold": 1.25, "basis": "consolidated"}])

    with pytest.raises(StateShapeError, match="group_basis"):
        compute("Synthetic Co", "Facility")


def test_compute_from_state_gives_the_same_answer_as_compute_for_the_same_state(tmp_path, monkeypatch):
    from policy_check import STATE_KEYS_READ, compute, compute_from_state
    from state_manager import read_state, write_state

    monkeypatch.chdir(tmp_path)
    write_state("Synthetic Co", "Synthetic Facility", date_str="2026-01-05",
                financials_source="analyst-supplied", ratios={"FY-Current": {"dscr": 1.1}},
                covenants=[{"metric": "dscr", "type": "minimum", "threshold": 1.25}])
    state = read_state("Synthetic Co", "Synthetic Facility", keys=STATE_KEYS_READ)

    assert compute_from_state(state) == compute("Synthetic Co", "Synthetic Facility")
    assert compute_from_state({}) == {
        "policy_state": compute_from_state({})["policy_state"], "reasons": [], "compliant": True,
        "cam_data_present": False}
