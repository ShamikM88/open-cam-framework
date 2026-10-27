"""The policy engine's result payload, pinned byte for byte (issue #483).

`evaluate_deal_policy()` returns six lists of small records, and `policy_check.py` prints them inside a four-key envelope.
Typing those records as TypedDicts (issue #483) must change no key, no key order, no value and no list order, because the
payload is stored in state, compared between reviews and rendered into documents. The goldens in
`tests/fixtures/policy_payloads/` were captured from the implementation *before* any typing change, so they are independent
of the code they check: one `*.engine.json` per case for `evaluate_deal_policy()` (`json.dumps(..., indent=2)`), and one
`*.cli.json` per case for the exact standard output of `python scripts/policy_check.py`. Both are compared as bytes. The one
normalisation is that CRLF becomes LF on both sides: a Windows pipe writes CRLF for every line the script prints and a Windows
checkout may turn a golden's LF into CRLF, and neither says anything about the payload.

The cases are the six synthetic legacy fixtures plus two synthetic states built here that between them reach every branch
that shapes the payload: a covenant with and without `basis` (the key sits before `actual` in a covenant result and last in
a downside breach), every status, a unit mismatch, a zero threshold, a duplicate metric, forward years, downside FAIL and
UNRESOLVABLE breaches and an unavailable downside year, every kind of security gap, and colliding guarantee and asset ids.
Only invented names and figures appear here.
"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from policy_engine import evaluate_deal_policy

TESTS = Path(__file__).resolve().parent
GOLDEN_DIR = TESTS / "fixtures" / "policy_payloads"
STATE_FIXTURES = TESTS / "fixtures" / "state"
SCRIPTS = TESTS.parent / "scripts"
LEGACY = ["01_pre_schema_version", "02_pre_spreading_check", "03_pre_merge_fix", "04_pre_analyst_store",
          "05_future_unknown_fields", "06_analyst_terms"]
PAYLOAD_COMPANY, PAYLOAD_PROPOSAL, PAYLOAD_DATE = "Synthetic Payload Co", "Synthetic Payload Loan", "2026-01-01"

RICH_STATE = {
    "company": PAYLOAD_COMPANY, "proposal": PAYLOAD_PROPOSAL, "date": PAYLOAD_DATE, "deal_type": "corporate_credit",
    "ratios": {
        "FY-Current": {"dscr": 1.8, "gross_leverage": 2.5, "gearing": 0.4, "current_ratio": 1.1,
                       "fcf_conversion_pct": 0.9, "ebit_interest_cover": None, "net_debt_to_ebitda": -0.5},
        "FY+1": {"dscr": 1.7, "gross_leverage": 2.8, "gearing": 0.4, "current_ratio": 1.2, "fcf_conversion_pct": 0.8,
                 "ebit_interest_cover": 4.0, "net_debt_to_ebitda": -0.3},
        "FY+2": {"dscr": 1.6, "gross_leverage": 2.9, "gearing": 0.4, "current_ratio": 1.2, "fcf_conversion_pct": 0.8,
                 "ebit_interest_cover": 4.0, "net_debt_to_ebitda": 0.1},
        "FY+3": {"dscr": 1.6, "gross_leverage": 2.6, "gearing": 0.5, "current_ratio": 1.3, "fcf_conversion_pct": 0.8,
                 "ebit_interest_cover": 4.0, "net_debt_to_ebitda": 0.2},
    },
    "financials": {
        "FY-Current": {"ebitda": 100, "total_equity": 400, "current_liabilities": 50,
                       "raw": {"interest_paid": 0, "scheduled_principal": 10, "revenue": 1000, "cost_of_sales": 600}},
        "FY+1": {"ebitda": 110, "total_equity": 420, "current_liabilities": 50,
                 "raw": {"interest_paid": 5, "scheduled_principal": 10, "revenue": 1100, "cost_of_sales": 650}},
    },
    "covenants": [
        {"metric": "dscr", "type": "minimum", "threshold": 1.5, "basis": "borrower"},
        {"metric": "gross_leverage", "type": "maximum", "threshold": 3.0},
        {"metric": "gross_leverage", "type": "maximum", "threshold": 2.6},
        {"metric": "gearing", "type": "maximum", "threshold": 0.45},
        {"metric": "gearing", "type": "maximum", "threshold": 0.3},
        {"metric": "ebit_interest_cover", "type": "minimum", "threshold": 2.0},
        {"metric": "unknown_metric", "type": "minimum", "threshold": 1},
        {"metric": "dscr", "type": "sideways", "threshold": 1.5},
        {"metric": "dscr", "type": "maximum"},
        {"metric": "current_ratio", "type": "minimum", "threshold": 0},
        {"metric": "fcf_conversion_pct", "type": "minimum", "threshold": 87},
        {"metric": "dscr", "type": "minimum", "threshold": 1.5, "basis": "consolidated"},
        {"metric": "dscr", "type": "minimum", "threshold": 1.5, "basis": "weird"},
        {"metric": "net_debt_to_ebitda", "type": "maximum", "threshold": -0.5},
    ],
    "downside_case": {
        "financials": {
            "FY+1": {"ebitda": 80, "total_equity": 300, "current_liabilities": 50,
                      "raw": {"interest_paid": 6, "scheduled_principal": 10, "revenue": 900, "cost_of_sales": 650}},
            "FY+2": {"ebitda": -5, "total_equity": -20, "current_liabilities": 50,
                     "raw": {"interest_paid": 6, "scheduled_principal": 10, "revenue": 700, "cost_of_sales": 650}},
        },
        "ratios": {
            "FY+1": {"dscr": 1.4, "gross_leverage": 3.4, "gearing": 0.6, "current_ratio": 1.0,
                     "fcf_conversion_pct": 0.5, "ebit_interest_cover": 3.0, "net_debt_to_ebitda": 0.6},
            "FY+2": {"dscr": 1.2, "gross_leverage": None, "gearing": None, "current_ratio": 0.9,
                     "fcf_conversion_pct": None, "ebit_interest_cover": 2.0, "net_debt_to_ebitda": None},
        },
        "unavailable": {"FY+3": "the supplied forecast cannot be stressed"},
    },
    "collateral": [
        {"asset_id": "AST-001", "asset_class": "Vehicles"},
        {"asset_id": "AST-002", "asset_class": "Plant"},
        {"asset_id": "AST-003", "asset_class": "Property"},
        {"asset_id": "AST-004", "asset_class": "Stock"},
        {"asset_id": "ast 004", "asset_class": "Stock"},
    ],
    "security_package": [
        {"secures_asset_id": "AST-001", "perfection_status": "Perfected", "ranking": "First"},
        {"secures_asset_id": "AST-003", "perfection_status": "Pending", "ranking": "Second"},
        {"secures_asset_id": "AST-004", "perfection_status": "Perfected", "ranking": "First"},
        {"secures_asset_id": "AST-004", "perfection_status": "Perfected", "ranking": "Second"},
        {"secures_asset_id": "ast 004", "perfection_status": "Pending", "ranking": "First"},
        {"secures_asset_id": "GHOST-9", "perfection_status": "Perfected", "ranking": "First"},
    ],
    "guarantees": [
        {"provider": "Parent Holdings", "type": "Corporate", "amount": 500000},
        {"provider": "Parent Holdings", "type": "Corporate Guarantee", "amount": None},
        {"provider": "Director X", "guarantee_id": "G-1", "type": "  ", "amount": 0},
        {"provider": "Parent-Holdings", "type": "Personal", "amount": "  "},
        {"provider": "", "type": None, "amount": 25},
    ],
}
CONSOLIDATED_STATE = {**copy.deepcopy(RICH_STATE), "financials_source": "analyst-supplied",
                      "group_basis": {
                          "basis": "supplied_consolidated", "reporting_entity": "Synthetic Parent Holdings",
                          "entities_covered": ["Synthetic Parent Holdings", "Synthetic Subsidiary Ltd"],
                          "source": {"document": "Synthetic group accounts", "accounting_basis": "Synthetic GAAP",
                                     "date": "2026-01-01"},
                          "period_end": "2025-12-31", "periods": ["FY-Current"], "figures_basis": "supplied",
                          "perimeter_confirmed": True, "perimeter_confirmed_on": "2026-01-01"}}


def load_legacy(name):
    return json.loads((STATE_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def all_cases():
    cases = {name: load_legacy(name) for name in LEGACY}
    cases["rich"] = copy.deepcopy(RICH_STATE)
    cases["rich_consolidated"] = copy.deepcopy(CONSOLIDATED_STATE)
    return cases


CASE_NAMES = [*LEGACY, "rich", "rich_consolidated"]


def golden(name, kind):
    return (GOLDEN_DIR / f"{name}.{kind}.json").read_bytes().replace(b"\r\n", b"\n")


def engine_bytes(state):
    return (json.dumps(evaluate_deal_policy(state), indent=2) + "\n").encode("utf-8")


@pytest.mark.parametrize("name", CASE_NAMES)
def test_the_engine_payload_is_byte_for_byte_the_recorded_one(name):
    assert engine_bytes(all_cases()[name]) == golden(name, "engine")


def test_the_empty_and_missing_state_payloads_are_the_recorded_ones():
    assert engine_bytes({}) == golden("empty", "engine")
    assert engine_bytes(None) == golden("empty", "engine")


def child_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("ANTHROPIC")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@pytest.mark.parametrize("name", CASE_NAMES)
def test_the_policy_check_cli_output_is_byte_for_byte_the_recorded_one(tmp_path, name):
    state = all_cases()[name]
    folder = tmp_path / "deals" / state["company"] / f"{state['proposal']}_{state['date']}"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "policy_check.py"), "--company", state["company"], "--proposal", state["proposal"]],
        cwd=tmp_path, env=child_env(), capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace(b"\r\n", b"\n") == golden(name, "cli")


def test_the_rich_cases_reach_every_shape_the_payload_can_take():
    """A golden that never produced a status or a key would pin nothing: check the cases cover the branches."""
    rich = evaluate_deal_policy(all_cases()["rich"])
    results = rich["covenant_results"]
    assert {r["status"] for r in results} == {"PASS", "FAIL", "UNRESOLVABLE"}
    assert any("basis" in r for r in results) and any("basis" not in r for r in results)
    assert any(r["headroom_pct"] is None and r["status"] == "PASS" for r in results)            # a zero threshold
    assert any(r["reason"] is None for r in results) and any(isinstance(r["reason"], str) for r in results)
    assert any(list(r)[:5] == ["metric", "type", "threshold", "basis", "actual"] for r in results)
    assert any(list(r) == ["metric", "type", "threshold", "actual", "status", "headroom_pct", "reason"] for r in results)
    assert len(rich["security_gaps"]) >= 5
    cp_ids = [cp["cp_id"] for cp in rich["required_conditions_precedent"]]
    assert len(cp_ids) == len(set(cp_ids)) and any(cp_id.endswith("-2") for cp_id in cp_ids)
    assert list(rich) == ["covenant_results", "security_gaps", "required_conditions_precedent",
                          "required_conditions_subsequent", "downside_covenant_breaches", "forward_covenant_results"]

    consolidated = evaluate_deal_policy(all_cases()["rich_consolidated"])
    assert all("basis" in r for r in consolidated["covenant_results"])      # the default basis is named when it matters
    assert any(r["status"] == "PASS" and r["basis"] == "consolidated" for r in consolidated["covenant_results"])

    for payload in (rich, consolidated):
        forward = payload["forward_covenant_results"]
        assert {r["year"] for r in forward} == {"FY+1", "FY+2", "FY+3"}
        assert [list(r)[-2:] for r in forward] == [["year", "forward_id"]] * len(forward)
        breaches = payload["downside_covenant_breaches"]
        assert {b["downside_status"] for b in breaches} == {"FAIL", "UNRESOLVABLE"}
        assert any(b["downside_actual"] is None and b["year"] == "FY+3" for b in breaches)      # the unavailable year
        assert any("basis" in b and list(b)[-1] == "basis" for b in breaches)
    assert any(b["downside_status"] == "FAIL" and b["reason"] is None for b in rich["downside_covenant_breaches"])
    assert any("basis" not in b for b in rich["downside_covenant_breaches"])
