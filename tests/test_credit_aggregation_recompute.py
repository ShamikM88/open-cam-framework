"""B2b persistence and freshness regressions; no mocked calculation."""

import copy

import pytest

import credit_aggregation as ca
from state_manager import StateError, validate_state
from test_credit_aggregation import _read_full
from test_credit_aggregation import _state_file
from test_credit_aggregation_calc import B, H, O, P, calculate, run, seed


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


@pytest.mark.parametrize(("change", "message"), [
    (lambda e: e.pop("input"), "confirmed input"),
    (lambda e: e.pop("computed"), "calculated computed"),
    (lambda e: e["computed"].pop("cash"), "cash is missing"),
    (lambda e: e["computed"].update(debt_before=True), "debt_before"),
])
def test_state_validator_recognises_the_calculated_shape(tmp_path, change, message):
    run(tmp_path)
    state = _read_full(tmp_path)
    change(state["credit_aggregation"][P])
    with pytest.raises(StateError, match=message):
        validate_state(state, keys=("credit_aggregation",))


def test_an_invalid_period_on_a_live_intercompany_line_never_looks_current(tmp_path):
    run(tmp_path)
    state = _read_full(tmp_path)
    state["entity_records"][H]["intercompany_lines"].append({"period": "typo"})
    status = ca.aggregation_period_status(state, P)
    assert status["status"] == "stale"
    assert "intercompany" in status["reason"]


def test_an_ordinary_read_withholds_a_hand_edited_number_that_binary_floating_point_would_round(tmp_path):
    # PX628-124: the calculation refuses such a number; a reader must not silently round it and report "current".
    from state_manager import read_state
    from test_credit_aggregation import COMPANY, PROPOSAL
    file = seed(tmp_path)
    calculate(tmp_path, file)
    assert ca.aggregation_period_status(read_state(COMPANY, PROPOSAL, base_dir=tmp_path), P)["status"] == "current"
    path = _state_file(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert '"amount": 20,' in text
    path.write_text(text.replace('"amount": 20,', '"amount": 20.000000000000001,', 1), encoding="utf-8")

    status = ca.aggregation_period_status(read_state(COMPANY, PROPOSAL, base_dir=tmp_path), P)

    assert status["status"] == "stale" and "exceeds the precision" in status["reason"]


def test_an_unrelated_write_cannot_persist_a_rounded_number_or_the_private_marker(tmp_path):
    # CG628-16: the read-time marker is private and a number binary floating point would round is never reserialized.
    from state_manager import StateShapeError, write_state
    from test_credit_aggregation import COMPANY, PROPOSAL
    file = seed(tmp_path)
    calculate(tmp_path, file)
    write_state(COMPANY, PROPOSAL, base_dir=tmp_path, note="clean write")
    assert "_numeric_precision_problem" not in _state_file(tmp_path).read_text(encoding="utf-8")
    path = _state_file(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"amount": 20,', '"amount": 20.000000000000001,', 1), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(StateShapeError, match="exceeds the precision"):
        write_state(COMPANY, PROPOSAL, base_dir=tmp_path, note="unrelated")

    assert path.read_bytes() == before
