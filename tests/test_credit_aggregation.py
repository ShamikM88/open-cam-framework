"""Issue #472 case B, step B2a: validate and resolve credit-aggregation inputs.

The aggregation itself (eliminations and calculation) is the accounting-gated
B2b step and is deliberately absent: `record_credit_aggregation()` persists
only the manifest (participants, period-end evidence, confirmed scope) and one
SHA-256 input fingerprint per period under the new top-level
`credit_aggregation` key. Every fixture here is synthetic.

The contract B2b will later fill: each period's entry may carry an optional
`computed` object (`{"financials": {...}, "ratios": {...}}` in the same shape
as the borrower's top-level `financials[period]` / `ratios[period]`). Until
B2b exists no period has `computed`, and every consumer treats a missing or
stale `computed` as UNRESOLVABLE with a reason (see `aggregation_period_status`).
"""
import copy
import json
import sys
from pathlib import Path

import pytest

import credit_aggregation
import state_manager
from credit_aggregation import (
    CREDIT_AGGREGATION_CONTRACT,
    AggregationError,
    aggregation_period_status,
    record_credit_aggregation,
    resolve_request,
)
from state_manager import (
    StateError,
    StateShapeError,
    find_deal,
    read_state,
    record_entity_record,
    state_path,
    write_state,
)

COMPANY = "Synthetic Aggregation Co"
PROPOSAL = "Synthetic Facility"
DATE = "2026-01-01"

BORROWER_RAW = {
    "FY-Current": {
        "revenue": 900, "cost_of_sales": 300, "admin_expenses": 100,
        "cash": 50, "trade_debtors": 60, "stock": 40, "trade_creditors": 45,
        "current_debt": 30, "long_term_debt": 200, "share_capital": 80, "retained_profit": 150,
    },
    "FY+1": {
        "revenue": 950, "cost_of_sales": 310, "admin_expenses": 105,
        "cash": 55, "trade_debtors": 62, "stock": 42, "trade_creditors": 47,
        "current_debt": 28, "long_term_debt": 190, "share_capital": 80, "retained_profit": 160,
    },
}

OPCO_ID = "synthetic-opco"
HOLDCO_ID = "synthetic-holdco"


def _entity_record(entity_id=OPCO_ID, *, role="subsidiary", included=True, raw=None, period_ends=None,
                   intercompany_lines=None, analyst_supplied=None):
    return {
        "entity_id": entity_id,
        "legal_name": f"Synthetic {entity_id} Ltd",
        "role": role,
        "source": {
            "document": f"Synthetic standalone accounts for {entity_id}",
            "accounting_basis": "UK GAAP",
            "date": "2026-02-01",
        },
        "period_ends": period_ends if period_ends is not None else {
            "FY-Current": "2025-12-31", "FY+1": "2026-12-31",
        },
        "perimeter": {
            "included": included,
            "control_basis": "analyst-confirmed",
            "scope_rationale": "Synthetic perimeter, explicitly confirmed",
            "confirmed": True,
            "confirmed_on": "2026-10-27",
        },
        "raw_figures_source": "Synthetic source document",
        "multi_period_financials": raw if raw is not None else {
            "FY-Current": {"revenue": 300, "cost_of_sales": 100, "cash": 20, "trade_debtors": 30,
                           "trade_creditors": 25, "share_capital": 40, "retained_profit": 60},
            "FY+1": {"revenue": 320, "cost_of_sales": 105, "cash": 22, "trade_debtors": 31,
                     "trade_creditors": 26, "share_capital": 40, "retained_profit": 65},
        },
        "analyst_supplied_financials": analyst_supplied or {},
        "spread_status": "not-spread",
        "intercompany_lines": intercompany_lines if intercompany_lines is not None else [],
        "guarantee_ids": [],
        "future_metadata": {"preserve": True},
    }


def _seed_deal(tmp_path, **fields):
    defaults = {
        "financials_source": "framework-computed",
        "financials": {period: {"raw": copy.deepcopy(raw)} for period, raw in BORROWER_RAW.items()},
        "ratios": {"FY-Current": {"dscr": 1.4}},
        "guarantees": [],
    }
    defaults.update(fields)
    write_state(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path), **defaults)
    return Path(state_path(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path)))


def _register_entity(tmp_path, record):
    record_file = tmp_path / "entity-record.json"
    record_file.write_text(json.dumps(record, allow_nan=True), encoding="utf-8")
    record_entity_record(COMPANY, PROPOSAL, str(record_file), perimeter_confirmed=True,
                         date_str="2026-10-27", base_dir=str(tmp_path))


def _seed_two_entity_deal(tmp_path, **record_kwargs):
    """A deal whose borrower and one subsidiary can both be selected for FY-Current."""
    path = _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(**record_kwargs))
    return path


def _period_request(**overrides):
    request = {
        "participants": ["@borrower", OPCO_ID],
        "borrower_period_end": "2025-12-31",
        "borrower_period_end_evidence": "Synthetic audited accounts FY2025",
        "scope_confirmed": True,
    }
    request.update(overrides)
    return request


def _request(periods=None):
    return {"periods": periods if periods is not None else {"FY-Current": _period_request()}}


def _write_request(tmp_path, request, name="aggregation-request.json"):
    request_file = tmp_path / name
    request_file.write_text(json.dumps(request), encoding="utf-8")
    return str(request_file)


def _record(tmp_path, request=None, name="aggregation-request.json", **kwargs):
    return record_credit_aggregation(
        COMPANY, PROPOSAL, _write_request(tmp_path, request or _request(), name=name),
        base_dir=str(tmp_path), **kwargs)


def _state_file(tmp_path):
    return Path(state_path(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path)))


def _read_full(tmp_path):
    return json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))


def _recorded_period(tmp_path, period="FY-Current"):
    return _read_full(tmp_path)["credit_aggregation"][period]


# ---------------------------------------------------------------------------
# Recording the manifest and the fingerprint
# ---------------------------------------------------------------------------

def test_recording_writes_the_manifest_and_fingerprint_under_the_new_key(tmp_path):
    _seed_two_entity_deal(tmp_path)
    result = _record(tmp_path)

    assert result["recorded"] is True
    assert result["periods"] == ["FY-Current"]
    period = _recorded_period(tmp_path)
    assert set(period) == {"manifest", "input_fingerprint"}
    assert period["manifest"] == {
        "participants": ["@borrower", OPCO_ID],
        "period_end": "2025-12-31",
        "period_end_evidence": "Synthetic audited accounts FY2025",
        "scope_confirmations": {OPCO_ID: _entity_record()["perimeter"]},
    }
    assert len(period["input_fingerprint"]) == 64
    assert period["input_fingerprint"] == period["input_fingerprint"].lower()
    assert int(period["input_fingerprint"], 16) >= 0          # lowercase hex, nothing else


def test_recording_stamps_the_new_schema_version_and_touches_no_other_key(tmp_path):
    path = _seed_two_entity_deal(tmp_path)
    before = json.loads(path.read_text(encoding="utf-8"))
    _record(tmp_path)
    after = _read_full(tmp_path)

    assert after["schema_version"] == "1.3.0"
    for key in ("financials", "ratios", "multi_period_financials", "analyst_supplied_financials",
                "entity_records", "group_basis", "financials_source", "guarantees"):
        assert after.get(key) == before.get(key), f"the recording must not touch {key}"
    assert set(after["credit_aggregation"]) == {"FY-Current"}


def test_recording_preserves_periods_not_in_the_request_and_drops_recomputed_computed(tmp_path):
    _seed_two_entity_deal(tmp_path)
    # An earlier recording of FY+1, plus a B2b-era computed object, must survive/flip correctly.
    _record(tmp_path, {"periods": {
        "FY+1": _period_request(participants=["@borrower", OPCO_ID], borrower_period_end="2026-12-31"),
    }})
    state = _read_full(tmp_path)
    state["credit_aggregation"]["FY+1"]["computed"] = {"financials": {"FY+1": {"ebitda": 99}},
                                                      "ratios": {"FY+1": {"dscr": 1.1}}}
    _state_file(tmp_path).write_text(json.dumps(state, indent=2), encoding="utf-8")

    _record(tmp_path)      # FY-Current only: FY+1 is preserved untouched

    after = _read_full(tmp_path)
    assert set(after["credit_aggregation"]) == {"FY-Current", "FY+1"}
    assert after["credit_aggregation"]["FY+1"]["computed"] == {"financials": {"FY+1": {"ebitda": 99}},
                                                              "ratios": {"FY+1": {"dscr": 1.1}}}


def test_re_recording_a_period_replaces_its_own_computed(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)
    state["credit_aggregation"]["FY-Current"]["computed"] = {"financials": {"ebitda": 99},
                                                            "ratios": {"dscr": 1.1}}
    _state_file(tmp_path).write_text(json.dumps(state, indent=2), encoding="utf-8")

    _record(tmp_path)      # same inputs again: the stale computed is dropped

    assert "computed" not in _recorded_period(tmp_path)


def test_an_identical_request_reproduces_the_same_fingerprint(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    first = _recorded_period(tmp_path)["input_fingerprint"]
    _record(tmp_path, name="aggregation-request-2.json")
    second = _recorded_period(tmp_path)["input_fingerprint"]
    assert first == second


def test_the_fingerprint_is_reproducible_from_the_state_and_the_request(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)
    records = resolve_request(state, _request())
    assert records["FY-Current"]["input_fingerprint"] == _recorded_period(tmp_path)["input_fingerprint"]


# ---------------------------------------------------------------------------
# The fingerprint covers every input the plan names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mutate", [
    lambda state: state["entity_records"][OPCO_ID]["multi_period_financials"]["FY-Current"].update(
        {"revenue": 999}),
    lambda state: state["entity_records"][OPCO_ID]["source"].update({"document": "Different document"}),
    lambda state: state["entity_records"][OPCO_ID]["perimeter"].update(
        {"scope_rationale": "A different confirmed scope"}),
    lambda state: state["financials"]["FY-Current"]["raw"].update({"revenue": 123}),
    lambda state: state["entity_records"][OPCO_ID].update({"raw_figures_source": "Different source document"}),
])
def test_the_fingerprint_changes_when_any_covered_input_changes(tmp_path, mutate):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    baseline = _recorded_period(tmp_path)["input_fingerprint"]

    state = _read_full(tmp_path)
    mutate(state)
    records = resolve_request(state, _request())

    assert records["FY-Current"]["input_fingerprint"] != baseline


def test_the_fingerprint_changes_when_the_period_end_changes(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    baseline = _recorded_period(tmp_path)["input_fingerprint"]

    state = _read_full(tmp_path)
    state["entity_records"][OPCO_ID]["period_ends"]["FY-Current"] = "2025-11-30"
    request = _request()
    request["periods"]["FY-Current"]["borrower_period_end"] = "2025-11-30"
    records = resolve_request(state, request)

    assert records["FY-Current"]["input_fingerprint"] != baseline


def test_the_fingerprint_changes_when_a_participant_is_added(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    baseline = _recorded_period(tmp_path)["input_fingerprint"]

    _register_entity(tmp_path, _entity_record(HOLDCO_ID))
    records = resolve_request(_read_full(tmp_path), {"periods": {"FY-Current": _period_request(
        participants=["@borrower", OPCO_ID, HOLDCO_ID])}})

    assert records["FY-Current"]["input_fingerprint"] != baseline


def test_the_fingerprint_covers_the_contract_tag_and_version(tmp_path, monkeypatch):
    _seed_two_entity_deal(tmp_path)
    state = _read_full(tmp_path)
    records = resolve_request(state, _request())
    monkeypatch.setattr(credit_aggregation, "CREDIT_AGGREGATION_CONTRACT", "open-cam-credit-aggregation-input/v2")
    changed = resolve_request(state, _request())
    assert changed["FY-Current"]["input_fingerprint"] != records["FY-Current"]["input_fingerprint"]


def test_the_payload_is_tagged_with_the_contract_version(tmp_path):
    _seed_two_entity_deal(tmp_path)
    state = _read_full(tmp_path)
    payload = credit_aggregation._period_payload(state, "FY-Current", _period_request())
    assert payload["contract"] == CREDIT_AGGREGATION_CONTRACT
    assert payload["contract_version"] == 1
    assert payload["period"] == "FY-Current"
    assert payload["period_end"] == "2025-12-31"
    assert payload["participants"] == ["@borrower", OPCO_ID]
    assert set(payload["inputs"]) == {"@borrower", OPCO_ID}


# ---------------------------------------------------------------------------
# Read-side status: the contract B3/B4 consume
# ---------------------------------------------------------------------------

def test_a_recorded_period_without_computed_is_not_calculated(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    status = aggregation_period_status(_read_full(tmp_path), "FY-Current")
    assert status["status"] == "not_calculated"
    assert status["reason"]
    assert status["input_fingerprint"] == _recorded_period(tmp_path)["input_fingerprint"]


def test_a_recorded_period_with_computed_is_current(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)
    state["credit_aggregation"]["FY-Current"]["computed"] = {"financials": {"ebitda": 99},
                                                            "ratios": {"dscr": 1.1}}
    status = aggregation_period_status(state, "FY-Current")
    assert status["status"] == "current"
    assert status["reason"] is None


def test_a_period_whose_inputs_changed_is_stale(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)
    state["entity_records"][OPCO_ID]["multi_period_financials"]["FY-Current"]["revenue"] = 4242
    status = aggregation_period_status(state, "FY-Current")
    assert status["status"] == "stale"
    assert "stale" in status["reason"] or "fingerprint" in status["reason"]


def test_a_period_whose_inputs_disappeared_is_stale_not_silently_current(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)
    state["financials"] = {}                  # the borrower's store no longer holds the period
    status = aggregation_period_status(state, "FY-Current")
    assert status["status"] == "stale"


def test_a_period_with_no_manifest_is_absent(tmp_path):
    _seed_deal(tmp_path)
    status = aggregation_period_status(_read_full(tmp_path), "FY-Current")
    assert status["status"] == "absent"
    assert status["reason"]


def test_a_malformed_computed_object_is_invalid(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)
    state["credit_aggregation"]["FY-Current"]["computed"] = {"financials": "not an object"}
    status = aggregation_period_status(state, "FY-Current")
    assert status["status"] == "invalid"
    assert status["reason"]


# ---------------------------------------------------------------------------
# Participants and the reserved "@borrower" token
# ---------------------------------------------------------------------------

def test_at_borrower_maps_to_the_legacy_borrower_store_and_creates_no_entity(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)

    assert "@borrower" not in state["entity_records"]
    assert set(state["entity_records"]) == {OPCO_ID}
    payload = credit_aggregation._period_payload(state, "FY-Current", _period_request())
    assert payload["inputs"]["@borrower"]["store"] == "financials"
    assert payload["inputs"]["@borrower"]["raw_period"] == BORROWER_RAW["FY-Current"]


def test_a_request_without_at_borrower_is_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    with pytest.raises(AggregationError, match="@borrower"):
        _record(tmp_path, {"periods": {"FY-Current": _period_request(participants=[OPCO_ID])}})


def test_an_at_borrower_entity_record_key_is_refused_by_the_state_manager(tmp_path):
    _seed_deal(tmp_path)
    record = _entity_record("@borrower")
    record_file = tmp_path / "entity-record.json"
    record_file.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(StateError, match="@borrower"):
        record_entity_record(COMPANY, PROPOSAL, str(record_file), perimeter_confirmed=True,
                             date_str="2026-10-27", base_dir=str(tmp_path))

    registry = {"@borrower": record}
    problems = state_manager._entity_records_problems(registry, [])
    assert any("@borrower" in problem for problem in problems)


def test_validate_state_rejects_an_at_borrower_entity_key(tmp_path):
    record = _entity_record("@borrower")
    with pytest.raises(StateShapeError, match="@borrower"):
        state_manager.validate_state({"entity_records": {"@borrower": record}})


def test_duplicate_unknown_and_unselected_participants_are_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(HOLDCO_ID))
    with pytest.raises(AggregationError, match="duplicate"):
        _record(tmp_path, {"periods": {"FY-Current": _period_request(
            participants=["@borrower", OPCO_ID, OPCO_ID])}})
    with pytest.raises(AggregationError, match="ghost-entity"):
        _record(tmp_path, {"periods": {"FY-Current": _period_request(
            participants=["@borrower", "ghost-entity"])}})
    # A registered entity that is simply not selected is not an error by itself (the analyst
    # selects the perimeter); the refusal for unselected references is the counterparty check below.


def test_a_participant_outside_the_perimeter_is_refused(tmp_path):
    path = _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(included=False))
    with pytest.raises(AggregationError, match="perimeter"):
        _record(tmp_path)


def test_participants_must_be_non_empty_strings(tmp_path):
    _seed_two_entity_deal(tmp_path)
    with pytest.raises(AggregationError, match="participants"):
        _record(tmp_path, {"periods": {"FY-Current": _period_request(participants=[])}})


# ---------------------------------------------------------------------------
# Period-ends: same label, same date, no rolling or inferring
# ---------------------------------------------------------------------------

def test_a_missing_borrower_period_end_is_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    request = _request()
    del request["periods"]["FY-Current"]["borrower_period_end"]
    with pytest.raises(AggregationError, match="borrower_period_end"):
        _record(tmp_path, request)


def test_a_missing_borrower_period_end_evidence_is_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    request = _request()
    del request["periods"]["FY-Current"]["borrower_period_end_evidence"]
    with pytest.raises(AggregationError, match="borrower_period_end_evidence"):
        _record(tmp_path, request)


def test_a_period_end_mismatch_between_participants_is_refused(tmp_path):
    _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(period_ends={"FY-Current": "2025-11-30", "FY+1": "2026-11-30"}))
    with pytest.raises(AggregationError, match="2025-11-30"):
        _record(tmp_path)


def test_an_entity_without_the_requested_period_is_refused(tmp_path):
    _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(period_ends={"FY+1": "2026-12-31"},
                                             raw={"FY+1": {"revenue": 300}}))
    with pytest.raises(AggregationError, match="FY-Current"):
        _record(tmp_path)


def test_an_unknown_period_label_is_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    with pytest.raises(AggregationError, match="FY-9"):
        _record(tmp_path, {"periods": {"FY-9": _period_request()}})


def test_a_borrower_period_missing_from_the_store_is_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    with pytest.raises(AggregationError, match="FY-2"):
        _record(tmp_path, {"periods": {"FY-2": _period_request(
            borrower_period_end="2023-12-31", participants=["@borrower"])}})


# ---------------------------------------------------------------------------
# Counterparties: exact-ID joins, never matched by name
# ---------------------------------------------------------------------------

def _line(counterparty=HOLDCO_ID, **overrides):
    line = {"period": "FY-Current", "line_key": "trade_debtors", "counterparty_entity_id": counterparty,
            "direction": "payable", "amount": 25, "confirmed": True}
    line.update(overrides)
    return line


def test_a_dangling_counterparty_is_a_named_error(tmp_path):
    _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(intercompany_lines=[_line("ghost-entity")]))
    with pytest.raises(AggregationError, match="ghost-entity"):
        _record(tmp_path)


def test_an_unselected_counterparty_is_a_named_error(tmp_path):
    path = _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(intercompany_lines=[_line(HOLDCO_ID)]))
    _register_entity(tmp_path, _entity_record(HOLDCO_ID))
    with pytest.raises(AggregationError, match=HOLDCO_ID):
        _record(tmp_path)


def test_an_out_of_perimeter_counterparty_is_a_named_error(tmp_path):
    _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(intercompany_lines=[_line(HOLDCO_ID)]))
    _register_entity(tmp_path, _entity_record(HOLDCO_ID, included=False))
    with pytest.raises(AggregationError, match=HOLDCO_ID):
        _record(tmp_path)


def test_an_at_borrower_counterparty_resolves_to_the_borrower_view(tmp_path):
    _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(intercompany_lines=[_line("@borrower")]))
    _record(tmp_path)       # must not raise


def test_an_unconfirmed_intercompany_line_is_refused(tmp_path):
    _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(intercompany_lines=[_line(HOLDCO_ID, confirmed=False)]))
    with pytest.raises(AggregationError, match="confirmed"):
        _record(tmp_path)


def test_a_counterparty_is_checked_against_its_own_period_s_participants(tmp_path):
    """A counterparty selected for FY-Current but not for FY+1 must fail the FY+1 request."""
    _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(intercompany_lines=[
        _line(HOLDCO_ID), _line(HOLDCO_ID, period="FY+1"),
    ]))
    _register_entity(tmp_path, _entity_record(HOLDCO_ID))
    with pytest.raises(AggregationError, match=HOLDCO_ID):
        _record(tmp_path, {"periods": {
            "FY-Current": _period_request(participants=["@borrower", OPCO_ID, HOLDCO_ID]),
            "FY+1": _period_request(participants=["@borrower", OPCO_ID], borrower_period_end="2026-12-31"),
        }})


def test_a_line_outside_the_requested_periods_is_not_checked(tmp_path):
    _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(intercompany_lines=[_line("ghost-entity", period="FY+1")]))
    _record(tmp_path)       # the FY-2 line is irrelevant to an FY-Current aggregation


# ---------------------------------------------------------------------------
# Refusals the plan names explicitly
# ---------------------------------------------------------------------------

def test_missing_or_false_scope_confirmation_is_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    request = _request()
    del request["periods"]["FY-Current"]["scope_confirmed"]
    with pytest.raises(AggregationError, match="scope_confirmed"):
        _record(tmp_path, request)
    with pytest.raises(AggregationError, match="scope_confirmed"):
        _record(tmp_path, {"periods": {"FY-Current": _period_request(scope_confirmed=False)}})


def test_analyst_supplied_only_borrower_figures_are_refused(tmp_path):
    _seed_deal(tmp_path, financials={}, financials_source="analyst-supplied",
               analyst_supplied_financials={"FY-Current": {"revenue": 900}})
    _register_entity(tmp_path, _entity_record())
    with pytest.raises(AggregationError, match="analyst-supplied"):
        _record(tmp_path)


def test_analyst_supplied_only_entity_figures_are_refused(tmp_path):
    _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(HOLDCO_ID, raw={},
                                              analyst_supplied={"FY-Current": {"revenue": 300}}))
    with pytest.raises(AggregationError, match="analyst-supplied"):
        _record(tmp_path, {"periods": {"FY-Current": _period_request(
            participants=["@borrower", HOLDCO_ID])}})


def test_an_analyst_supplied_forecast_is_refused_for_a_forward_period(tmp_path):
    _seed_deal(tmp_path, forecast_source="analyst-supplied")
    _register_entity(tmp_path, _entity_record())
    with pytest.raises(AggregationError, match="analyst-supplied"):
        _record(tmp_path, {"periods": {"FY+1": _period_request(borrower_period_end="2026-12-31")}})


def test_a_case_a_deal_is_refused_fail_closed(tmp_path):
    _seed_deal(tmp_path, financials_source="analyst-supplied", group_basis={
        "basis": "supplied_consolidated", "reporting_entity": "Synthetic Parent",
        "entities_covered": ["Synthetic Parent"], "source": {"document": "Synthetic group accounts",
                                                            "accounting_basis": "UK GAAP", "date": "2026-01-01"},
        "period_end": "2025-12-31", "periods": ["FY-Current"], "figures_basis": "supplied",
        "perimeter_confirmed": True, "perimeter_confirmed_on": "2026-01-01",
    })
    _register_entity(tmp_path, _entity_record())
    with pytest.raises(AggregationError, match="group_basis"):
        _record(tmp_path)


def test_non_finite_and_boolean_numbers_are_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    state["financials"]["FY-Current"]["raw"]["revenue"] = float("nan")
    _state_file(tmp_path).write_text(json.dumps(state, allow_nan=True), encoding="utf-8")
    # The shared read path refuses the non-finite figure before the aggregation is even
    # resolved; nothing is written either way.
    with pytest.raises((AggregationError, StateShapeError), match="non-finite"):
        _record(tmp_path)

    state["financials"]["FY-Current"]["raw"]["revenue"] = True
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(AggregationError, match="boolean"):
        _record(tmp_path)


def test_unknown_keys_in_the_request_are_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    with pytest.raises(AggregationError, match="unexpected key"):
        _record(tmp_path, {"periods": {"FY-Current": _period_request(extra_key=1)}})
    with pytest.raises(AggregationError, match="unexpected key"):
        _record(tmp_path, {"periods": _request()["periods"], "extra": 1})


def test_a_malformed_request_file_is_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    request_file = tmp_path / "request.json"
    request_file.write_text("not json", encoding="utf-8")
    with pytest.raises(AggregationError, match="JSON"):
        record_credit_aggregation(COMPANY, PROPOSAL, str(request_file), base_dir=str(tmp_path))
    request_file.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(AggregationError, match="object"):
        record_credit_aggregation(COMPANY, PROPOSAL, str(request_file), base_dir=str(tmp_path))


def test_a_deal_that_does_not_exist_is_refused_and_nothing_is_created(tmp_path):
    request_file = _write_request(tmp_path, _request())
    with pytest.raises(AggregationError, match="no exact existing"):
        record_credit_aggregation(COMPANY, PROPOSAL, request_file, base_dir=str(tmp_path))
    assert not (tmp_path / "deals").exists()


# ---------------------------------------------------------------------------
# No write on any refusal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", [
    "missing_scope",
    "no_borrower_token",
    "unknown_participant",
    "period_end_mismatch",
    "missing_period",
    "dangling_counterparty",
    "unconfirmed_line",
    "analyst_supplied",
    "case_a_collision",
    "unknown_key",
])
def test_every_refusal_leaves_state_json_byte_for_byte_unchanged(tmp_path, scenario):
    path = _seed_deal(tmp_path)
    _register_entity(tmp_path, _entity_record(intercompany_lines=[_line("ghost-entity")]))
    if scenario == "case_a_collision":
        state = json.loads(path.read_text(encoding="utf-8"))
        state["group_basis"] = {
            "basis": "supplied_consolidated", "reporting_entity": "Synthetic Parent",
            "entities_covered": ["Synthetic Parent"],
            "source": {"document": "Synthetic group accounts", "accounting_basis": "UK GAAP",
                       "date": "2026-01-01"},
            "period_end": "2025-12-31", "periods": ["FY-Current"], "figures_basis": "supplied",
            "perimeter_confirmed": True, "perimeter_confirmed_on": "2026-01-01",
        }
        state["financials_source"] = "analyst-supplied"
        path.write_text(json.dumps(state), encoding="utf-8")
    requests = {
        "missing_scope": {"periods": {"FY-Current": _period_request(scope_confirmed=False)}},
        "no_borrower_token": {"periods": {"FY-Current": _period_request(participants=[OPCO_ID])}},
        "unknown_participant": {"periods": {"FY-Current": _period_request(participants=["@borrower", "ghost"])}},
        "period_end_mismatch": {"periods": {"FY-Current": _period_request(borrower_period_end="2025-11-30")}},
        "missing_period": {"periods": {"FY-2": _period_request(borrower_period_end="2023-12-31")}},
        "dangling_counterparty": _request(),
        "unconfirmed_line": None,       # registered below with an unconfirmed line
        "analyst_supplied": None,       # seeded below
        "case_a_collision": _request(),
        "unknown_key": {"periods": {"FY-Current": _period_request(unexpected=1)}},
    }
    if scenario == "unconfirmed_line":
        _register_entity(tmp_path, _entity_record(entity_id="second-entity",
                                                  intercompany_lines=[]))
        state = json.loads(path.read_text(encoding="utf-8"))
        state["entity_records"]["second-entity"]["intercompany_lines"] = [
            _line(OPCO_ID, confirmed=False)]
        path.write_text(json.dumps(state), encoding="utf-8")
    if scenario == "analyst_supplied":
        state = json.loads(path.read_text(encoding="utf-8"))
        state["financials_source"] = "analyst-supplied"
        path.write_text(json.dumps(state), encoding="utf-8")

    before = path.read_bytes()
    with pytest.raises(AggregationError):
        _record(tmp_path, requests[scenario])
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# The stored shape validates, and legacy fixtures still load
# ---------------------------------------------------------------------------

def test_a_recorded_state_validates_under_the_full_state_shapes(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)
    assert state_manager.validate_state(state) is state


def test_validate_state_rejects_malformed_credit_aggregation_entries(tmp_path):
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    good = _read_full(tmp_path)

    malformed = copy.deepcopy(good)
    malformed["credit_aggregation"]["FY-9"] = malformed["credit_aggregation"]["FY-Current"]
    with pytest.raises(StateShapeError, match="FY-9"):
        state_manager.validate_state(malformed)

    malformed = copy.deepcopy(good)
    del malformed["credit_aggregation"]["FY-Current"]["manifest"]
    with pytest.raises(StateShapeError, match="manifest"):
        state_manager.validate_state(malformed)

    malformed = copy.deepcopy(good)
    malformed["credit_aggregation"]["FY-Current"]["input_fingerprint"] = "not-a-digest"
    with pytest.raises(StateShapeError, match="input_fingerprint"):
        state_manager.validate_state(malformed)

    malformed = copy.deepcopy(good)
    malformed["credit_aggregation"]["FY-Current"]["unexpected"] = 1
    with pytest.raises(StateShapeError, match="unexpected"):
        state_manager.validate_state(malformed)


def test_legacy_state_fixtures_still_load_at_schema_version_1_3_0():
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "state"
    for fixture in sorted(fixture_dir.glob("*.json")):
        state = json.loads(fixture.read_text(encoding="utf-8"))
        assert state_manager.validate_state(state) is state, fixture.name
    assert state_manager.SCHEMA_VERSION == "1.3.0"


# ---------------------------------------------------------------------------
# resolve_request is pure: it never writes
# ---------------------------------------------------------------------------

def test_resolve_request_does_not_write_the_state(tmp_path):
    path = _seed_two_entity_deal(tmp_path)
    before = path.read_bytes()
    state = _read_full(tmp_path)
    resolve_request(state, _request())
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# Validation branches reached through the pure resolver on hand-built state
# ---------------------------------------------------------------------------

def _hand_state(**overrides):
    """A minimal in-memory state the pure resolver accepts, for branch tests."""
    state = {
        "financials": {"FY-Current": {"raw": dict(BORROWER_RAW["FY-Current"])}},
        "financials_source": "framework-computed",
        "entity_records": {OPCO_ID: _entity_record()},
    }
    state.update(overrides)
    return state


def test_an_invalid_period_end_date_is_refused(tmp_path):
    _seed_two_entity_deal(tmp_path)
    state = _read_full(tmp_path)
    for bad_date in ("2025-13-45", "2025-12-3", "31/12/2025", 20251231, None):
        with pytest.raises(AggregationError, match="calendar date"):
            resolve_request(state, {"periods": {"FY-Current": _period_request(borrower_period_end=bad_date)}})


def test_a_boolean_inside_a_nested_list_of_raw_figures_is_refused(tmp_path):
    with pytest.raises(AggregationError, match="boolean"):
        credit_aggregation._reject_boolean_numbers({"lines": [1, True]}, "raw")


def test_a_non_finite_raw_line_is_refused_by_the_resolver(tmp_path):
    state = _hand_state()
    state["financials"]["FY-Current"]["raw"]["revenue"] = float("nan")
    with pytest.raises(AggregationError, match="non-finite"):
        resolve_request(state, _request())


def test_borrower_figures_in_both_raw_stores_are_refused(tmp_path):
    state = _hand_state(analyst_supplied_financials={"FY-Current": {"revenue": 900}})
    with pytest.raises(AggregationError, match="both raw stores"):
        resolve_request(state, _request())


def test_an_absent_entity_registry_is_refused_for_an_entity_participant():
    state = _hand_state()
    del state["entity_records"]
    with pytest.raises(AggregationError, match="entity_records is absent"):
        resolve_request(state, _request())


def test_an_entity_participant_whose_record_key_mismatches_is_refused():
    state = _hand_state()
    state["entity_records"][OPCO_ID]["entity_id"] = "someone-else"
    with pytest.raises(AggregationError, match="not a registered entity record"):
        resolve_request(state, _request())


def test_an_entity_participant_with_an_unconfirmed_perimeter_is_refused():
    state = _hand_state()
    state["entity_records"][OPCO_ID]["perimeter"]["confirmed"] = False
    with pytest.raises(AggregationError, match="unconfirmed perimeter"):
        resolve_request(state, _request())


def test_an_entity_participant_with_no_figures_at_all_is_refused():
    state = _hand_state()
    state["entity_records"][OPCO_ID]["multi_period_financials"] = {}
    with pytest.raises(AggregationError, match="no raw figures"):
        resolve_request(state, _request())


def test_an_intercompany_line_without_a_counterparty_id_is_refused():
    state = _hand_state()
    state["entity_records"][OPCO_ID]["intercompany_lines"] = [
        {k: v for k, v in _line(HOLDCO_ID).items() if k != "counterparty_entity_id"}]
    with pytest.raises(AggregationError, match="counterparty_entity_id"):
        resolve_request(state, _request())


def test_a_counterparty_with_an_unconfirmed_perimeter_is_refused():
    state = _hand_state()
    state["entity_records"][HOLDCO_ID] = _entity_record(HOLDCO_ID)
    state["entity_records"][HOLDCO_ID]["perimeter"]["confirmed"] = False
    state["entity_records"][OPCO_ID]["intercompany_lines"] = [_line(HOLDCO_ID)]
    with pytest.raises(AggregationError, match="unconfirmed"):
        resolve_request(state, _request())


def test_a_line_held_by_a_record_without_a_lines_list_is_skipped():
    state = _hand_state()
    del state["entity_records"][OPCO_ID]["intercompany_lines"]
    resolve_request(state, _request())       # must not raise


def test_a_non_canonical_payload_is_refused():
    with pytest.raises(AggregationError, match="canonical JSON"):
        credit_aggregation._canonical_digest({"unserialisable": object()})


def test_a_period_request_that_is_not_an_object_is_refused():
    state = _hand_state()
    with pytest.raises(AggregationError, match="must be a JSON object"):
        resolve_request(state, {"periods": {"FY-Current": "not an object"}})


def test_a_non_string_participant_is_refused():
    state = _hand_state()
    with pytest.raises(AggregationError, match="non-empty strings"):
        resolve_request(state, {"periods": {"FY-Current": _period_request(participants=["@borrower", 5])}})


def test_an_empty_borrower_period_end_evidence_is_refused():
    state = _hand_state()
    with pytest.raises(AggregationError, match="borrower_period_end_evidence"):
        resolve_request(state, {"periods": {"FY-Current": _period_request(
            borrower_period_end_evidence="  ")}})


def test_a_request_that_is_not_an_object_is_refused():
    with pytest.raises(AggregationError, match="must be a JSON object"):
        resolve_request(_hand_state(), ["not", "an", "object"])


def test_an_empty_periods_object_is_refused():
    with pytest.raises(AggregationError, match="periods"):
        resolve_request(_hand_state(), {"periods": {}})


def test_a_status_lookup_on_an_entry_without_a_manifest_is_stale():
    state = _hand_state(credit_aggregation={"FY-Current": {"input_fingerprint": "x" * 64}})
    status = aggregation_period_status(state, "FY-Current")
    assert status["status"] == "stale"
    assert status["input_fingerprint"] == "x" * 64


def test_a_missing_request_file_is_refused_by_the_library_call(tmp_path):
    _seed_deal(tmp_path)
    with pytest.raises(AggregationError, match="nope.json"):
        record_credit_aggregation(COMPANY, PROPOSAL, str(tmp_path / "nope.json"), base_dir=str(tmp_path))


def test_a_state_that_disappears_under_the_lock_is_refused(tmp_path, monkeypatch):
    _seed_two_entity_deal(tmp_path)
    monkeypatch.setattr(credit_aggregation, "read_state", lambda *args, **kwargs: None)
    with pytest.raises(AggregationError, match="no longer exists"):
        _record(tmp_path)


def test_an_unresolvable_state_path_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(credit_aggregation, "find_deal",
                        lambda *args, **kwargs: {"record_found": True, "date": None, "state_path": 123})
    with pytest.raises(AggregationError, match="was not resolved"):
        _record(tmp_path)
