"""B2b persistence and freshness regressions; no mocked calculation."""

import copy

import pytest

import credit_aggregation as ca
from state_manager import validate_state
from test_credit_aggregation import _read_full
from test_credit_aggregation_calc import B, O, P, calculate, run, seed


def test_recompute_equals_stored_and_does_not_mutate_state(tmp_path):
    file = seed(tmp_path)
    before = _read_full(tmp_path)
    calculate(tmp_path, file)
    after = _read_full(tmp_path)
    entry = after["credit_aggregation"][P]
    snapshot = copy.deepcopy(after)
    assert ca.recompute_period(after, P) == entry["computed"]
    assert after == snapshot
    assert ca.aggregation_period_status(after, P)["status"] == "current"
    assert entry["input"] == file["periods"][P]
    assert entry["journals"] == file["periods"][P]["journals"]
    assert {k: v for k, v in after.items() if k not in {"credit_aggregation", "last_updated"}} == {
        k: v for k, v in before.items() if k != "last_updated"}
    validate_state(after, keys=("credit_aggregation",))


@pytest.mark.parametrize("change", [
    lambda p: p["attestations"][O].update(ownership_pct=81),
    lambda p: p["declarations"][0]["evidence"].update(page_or_note="8"),
    lambda p: p["journals"][0].update(journal_id="J2"),
    lambda p: p["left_out"].update({"synthetic-out": {"reason": "Not selected", "external_debt": 20, "support": []}}),
])
def test_input_digest_changes_with_any_confirmed_input(tmp_path, change):
    file = seed(tmp_path)
    calculate(tmp_path, file)
    before = _read_full(tmp_path)["credit_aggregation"][P]
    change(file["periods"][P])
    calculate(tmp_path, file)
    after = _read_full(tmp_path)["credit_aggregation"][P]
    assert before["input_digest"] != after["input_digest"]
    assert before["computed"]["output_digest"] != after["computed"]["output_digest"]


@pytest.mark.parametrize("change", [
    lambda e: e["computed"]["ratios"].update(gross_leverage=0.01),
    lambda e: e["computed"]["adjusted_raw"].update(current_debt=True),
    lambda e: e["computed"].update(limitations=["synthetic invented disclosure"]),
    lambda e: e["computed"]["participant_table"][0].update(total_debt=0),
    lambda e: e["computed"]["refused_journals"].append({"journal_id": "invented"}),
    lambda e: e["journals"].clear(),
    lambda e: e["input"]["attestations"][B].update(ownership_pct=52),
])
def test_edited_output_is_never_current_even_with_a_recomputed_digest(tmp_path, change):
    run(tmp_path)
    state = _read_full(tmp_path)
    e = state["credit_aggregation"][P]
    change(e)
    c = e["computed"]
    c["output_digest"] = ca.computed_output_digest(e["input_fingerprint"], c["financials"], c["ratios"])
    status = ca.aggregation_period_status(state, P)
    assert status["status"] != "current"
    if not (e["journals"] == e["input"]["journals"] and e["input"]["attestations"][B]["ownership_pct"] == 100):
        assert status["status"] == "stale"
    else:
        assert "deterministic recomputation" in status["reason"]
