"""Tests-first contract for case C3's standalone guarantor spread."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

import spreading_check
import state_manager
from spreading_builder import evaluate_financial_model
from state_manager import read_state, state_path, write_state


COMPANY = "Synthetic Group Ltd"
PROPOSAL = "Synthetic Group Facility"
DATE = "2026-01-01"
REVIEW_LABEL = "not yet reviewed by an analyst"
GUARANTEE = {"guarantee_id": "GUARANTEE-SYN-001", "provider": "Synthetic GUARANTEE-SYN-001"}
SOURCE = {
    "document": "Synthetic guarantor accounts – period set",
    "accounting_basis": "UK GAAP",
    "date": "2026-02-01",
}
RAW_PERIODS = {
    "FY-1": {
        "revenue": 1000, "cost_of_sales": 400, "admin_expenses": 100,
        "interest_paid": 30, "scheduled_principal": 70, "tax_paid": 40,
        "cash": 60, "trade_debtors": 80, "stock": 90, "trade_creditors": 70,
        "current_debt": 15, "long_term_debt": 300, "share_capital": 100,
        "retained_profit": 200,
    },
    "FY-Current": {
        "revenue": 1200, "cost_of_sales": 500, "admin_expenses": 110,
        "interest_paid": 35, "scheduled_principal": 75, "tax_paid": 45,
        "cash": 65, "trade_debtors": 95, "stock": 100, "trade_creditors": 75,
        "current_debt": 20, "long_term_debt": 310, "share_capital": 100,
        "retained_profit": 220,
    },
}


def _entity(entity_id="synthetic-guarantor", guarantee_id="GUARANTEE-SYN-001",
            *, raw=None, supplied=None, role="guarantor"):
    periods = copy.deepcopy(RAW_PERIODS if raw is None else raw)
    supplied_periods = {} if supplied is None else copy.deepcopy(supplied)
    period_ends = {period: ("2025-12-31" if period == "FY-Current" else "2024-12-31")
                   for period in set(periods) | set(supplied_periods)}
    return {
        "entity_id": entity_id,
        "legal_name": f"Synthetic {entity_id} Ltd",
        "role": role,
        "source": copy.deepcopy(SOURCE),
        "period_ends": period_ends,
        "perimeter": {
            "included": True,
            "control_basis": "analyst-confirmed",
            "scope_rationale": "Synthetic standalone guarantor perimeter",
            "confirmed": True,
            "confirmed_on": "2026-10-27",
        },
        "raw_figures_source": "Synthetic source document",
        "multi_period_financials": periods,
        "analyst_supplied_financials": supplied_periods,
        "spread_status": "not-spread",
        "intercompany_lines": [],
        "guarantee_ids": [] if guarantee_id is None else [guarantee_id],
        "future_metadata": {"preserve": True},
    }


def _register(tmp_path, entity):
    record_file = tmp_path / f"{entity['entity_id']}.json"
    record_file.write_text(json.dumps(entity, ensure_ascii=False), encoding="utf-8")
    return state_manager.record_entity_record(
        COMPANY, PROPOSAL, str(record_file), perimeter_confirmed=True,
        date_str="2026-10-27", base_dir=str(tmp_path),
    )


def _seed_deal(tmp_path, entities=None, guarantee_ids=("GUARANTEE-SYN-001",)):
    borrower_fields = {
        "financials": {"FY-Current": {"ebitda": 777}},
        "ratios": {"FY-Current": {"dscr": 7.7}},
        "multi_period_financials": {"FY-Current": {"revenue": 888}},
        "analyst_supplied_financials": {"FY-1": {"revenue": 999}},
        "financials_source": "analyst-supplied",
    }
    write_state(
        COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path),
        guarantees=[{"guarantee_id": value, "provider": f"Synthetic {value}"}
                    for value in guarantee_ids],
        marker="keep", **copy.deepcopy(borrower_fields),
    )
    for entity in entities or [_entity()]:
        _register(tmp_path, entity)
    path = Path(state_path(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path)))
    return path, borrower_fields


def _fingerprint_payload(entity_id, period, record):
    return {
        "contract": "open-cam-entity-spread-input/v1",
        "entity_id": entity_id,
        "period": period,
        "period_end": record["period_ends"][period],
        "source": record["source"],
        "raw_figures_source": record["raw_figures_source"],
        "store": "multi_period_financials",
        "raw_lines": record["multi_period_financials"][period],
    }


def test_entity_period_fingerprint_is_the_approved_canonical_sha256():
    record = _entity()
    payload = _fingerprint_payload(record["entity_id"], "FY-Current", record)
    expected = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()

    actual = state_manager.entity_period_input_fingerprint(
        record["entity_id"], "FY-Current", record,
    )

    assert actual == expected
    assert len(actual) == 64 and set(actual) <= set("0123456789abcdef")


def test_entity_period_fingerprint_is_stable_under_json_object_key_order():
    record = _entity()
    reordered = copy.deepcopy(record)
    reordered["source"] = dict(reversed(list(reordered["source"].items())))
    reordered["multi_period_financials"]["FY-Current"] = dict(
        reversed(list(reordered["multi_period_financials"]["FY-Current"].items()))
    )

    assert state_manager.entity_period_input_fingerprint(
        record["entity_id"], "FY-Current", record,
    ) == state_manager.entity_period_input_fingerprint(
        reordered["entity_id"], "FY-Current", reordered,
    )


@pytest.mark.parametrize("change", ["raw", "accounting_basis", "source_date", "period_end", "raw_source"])
def test_entity_period_fingerprint_changes_for_each_approved_input_dimension(change):
    record = _entity()
    changed = copy.deepcopy(record)
    if change == "raw":
        changed["multi_period_financials"]["FY-Current"]["revenue"] += 1
    elif change == "accounting_basis":
        changed["source"]["accounting_basis"] = "IFRS"
    elif change == "source_date":
        changed["source"]["date"] = "2026-02-02"
    elif change == "period_end":
        changed["period_ends"]["FY-Current"] = "2026-11-30"
    else:
        changed["raw_figures_source"] = "A different synthetic source"
    assert state_manager.entity_period_input_fingerprint(
        record["entity_id"], "FY-Current", record,
    ) != state_manager.entity_period_input_fingerprint(
        changed["entity_id"], "FY-Current", changed,
    )


def test_entity_spread_persists_only_selected_guarantor_and_preserves_borrower_fields(tmp_path, monkeypatch):
    first = _entity("synthetic-guarantor-a", "GUARANTEE-SYN-001")
    second = _entity("synthetic-guarantor-b", "GUARANTEE-SYN-002")
    second["multi_period_financials"]["FY-Current"]["revenue"] = 9000
    path, borrower_fields = _seed_deal(tmp_path, [first, second],
                                       ("GUARANTEE-SYN-001", "GUARANTEE-SYN-002"))
    before = read_state(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path))
    before_borrower = {key: copy.deepcopy(before[key]) for key in borrower_fields}
    monkeypatch.chdir(tmp_path)

    output = spreading_check.compute_entity(COMPANY, PROPOSAL, "synthetic-guarantor-a")

    after = read_state(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path))
    record = after["entity_records"]["synthetic-guarantor-a"]
    expected = evaluate_financial_model(first["multi_period_financials"])
    assert record["financials"] == expected["financials"]
    assert record["ratios"] == expected["ratios"]
    assert record["figures_basis"] == "recomputed"
    assert record["analyst_review_status"] == REVIEW_LABEL
    assert record["spread_status"] == "spread"
    assert set(record["spread_fingerprints"]) == set(first["multi_period_financials"])
    assert record["future_metadata"] == {"preserve": True}
    assert after["entity_records"]["synthetic-guarantor-b"] == second
    assert {key: after[key] for key in borrower_fields} == before_borrower
    assert after["schema_version"] == "1.2.0"
    assert set(after) == set(before)
    assert output["analyst_review_status"] == REVIEW_LABEL
    assert output["financials"] == expected["financials"]
    assert output["financials"]["FY-Current"]["raw"]["revenue"] == 1200
    assert output["financials"]["FY-Current"]["raw"]["revenue"] != 1200 + 9000
    assert path.exists()


def test_spread_view_exposes_only_periods_with_a_current_hash_and_current_evaluator_output(tmp_path, monkeypatch):
    path, _ = _seed_deal(tmp_path)
    monkeypatch.chdir(tmp_path)
    spreading_check.compute_entity(COMPANY, PROPOSAL, "synthetic-guarantor")
    record = read_state(COMPANY, PROPOSAL)["entity_records"]["synthetic-guarantor"]

    view = spreading_check.entity_spread_view(record)
    assert set(view["financials"]) == {"FY-1", "FY-Current"}
    assert set(view["ratios"]) == {"FY-1", "FY-Current"}
    assert view["stale_periods"] == []
    assert view["analyst_review_status"] == REVIEW_LABEL

    changed = copy.deepcopy(record)
    changed["multi_period_financials"]["FY-1"]["revenue"] += 1
    partial = spreading_check.entity_spread_view(changed)
    assert set(partial["financials"]) == {"FY-Current"}
    assert partial["stale_periods"] == ["FY-1"]

    missing_hash = copy.deepcopy(record)
    del missing_hash["spread_fingerprints"]["FY-1"]
    assert state_manager.validate_state(
        {"entity_records": {"synthetic-guarantor": missing_hash}, "guarantees": [GUARANTEE]},
        keys=("entity_records",),
    )["entity_records"]["synthetic-guarantor"] is missing_hash
    missing_hash_view = spreading_check.entity_spread_view(missing_hash)
    assert set(missing_hash_view["financials"]) == {"FY-Current"}
    assert missing_hash_view["stale_periods"] == ["FY-1"]

    removed_period = copy.deepcopy(record)
    del removed_period["multi_period_financials"]["FY-1"]
    assert state_manager.validate_state(
        {"entity_records": {"synthetic-guarantor": removed_period}, "guarantees": [GUARANTEE]},
        keys=("entity_records",),
    )["entity_records"]["synthetic-guarantor"] is removed_period
    removed_view = spreading_check.entity_spread_view(removed_period)
    # FY-Current's cash-flow figures read the prior period's working capital (see the next test), so
    # removing FY-1 changes FY-Current's evaluated output too: neither period is current.
    assert removed_view["financials"] == {}
    assert removed_view["stale_periods"] == ["FY-1", "FY-Current"]

    all_removed = copy.deepcopy(record)
    all_removed["multi_period_financials"] = {}
    assert state_manager.validate_state(
        {"entity_records": {"synthetic-guarantor": all_removed}, "guarantees": [GUARANTEE]},
        keys=("entity_records",),
    )["entity_records"]["synthetic-guarantor"] is all_removed
    all_removed_view = spreading_check.entity_spread_view(all_removed)
    assert all_removed_view["financials"] == {}
    assert all_removed_view["stale_periods"] == ["FY-1", "FY-Current"]


def test_spread_view_detects_a_later_period_result_changed_by_prior_period_working_capital(tmp_path, monkeypatch):
    _seed_deal(tmp_path)
    monkeypatch.chdir(tmp_path)
    spreading_check.compute_entity(COMPANY, PROPOSAL, "synthetic-guarantor")
    record = read_state(COMPANY, PROPOSAL)["entity_records"]["synthetic-guarantor"]
    changed = copy.deepcopy(record)
    changed["multi_period_financials"]["FY-1"]["trade_debtors"] += 50

    # FY-Current's own input hash is unchanged, but FCF-after-working-capital depends on
    # FY-1 working capital; comparing evaluator output prevents a false-current display.
    assert changed["spread_fingerprints"]["FY-Current"] == record["spread_fingerprints"]["FY-Current"]
    view = spreading_check.entity_spread_view(changed)
    assert view["stale_periods"] == ["FY-1", "FY-Current"]
    assert view["financials"] == {}
    assert view["ratios"] == {}


def test_entity_spread_refuses_to_fall_back_to_borrower_or_analyst_supplied_store(tmp_path, monkeypatch):
    supplied_only = _entity(
        raw={}, supplied={"FY-Current": {"revenue": 500}},
    )
    empty_raw = _entity("synthetic-no-raw", "GUARANTEE-SYN-002", raw={})
    path, _ = _seed_deal(
        tmp_path, [supplied_only, empty_raw],
        ("GUARANTEE-SYN-001", "GUARANTEE-SYN-002"),
    )
    before = path.read_bytes()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="analyst-supplied.*not spread"):
        spreading_check.compute_entity(COMPANY, PROPOSAL, supplied_only["entity_id"])
    with pytest.raises(ValueError, match="no framework-computed"):
        spreading_check.compute_entity(COMPANY, PROPOSAL, empty_raw["entity_id"])

    assert path.read_bytes() == before


@pytest.mark.parametrize("invalid_kind", ["period-key", "boolean"])
def test_entity_spread_refuses_invalid_period_key_or_boolean_raw_value_without_writing(
    tmp_path, monkeypatch, invalid_kind,
):
    path, _ = _seed_deal(tmp_path)
    state = read_state(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path))
    record = state["entity_records"]["synthetic-guarantor"]
    if invalid_kind == "period-key":
        record["multi_period_financials"] = {"Borrower Entity": {"revenue": 10}}
    else:
        record["multi_period_financials"]["FY-Current"]["revenue"] = True
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(state_manager.StateShapeError) as caught:
        spreading_check.compute_entity(COMPANY, PROPOSAL, "synthetic-guarantor")

    expected_fragment = "unexpected period key" if invalid_kind == "period-key" else "finite JSON number or null"
    assert expected_fragment in str(caught.value)
    assert path.read_bytes() == before


def test_entity_record_refuses_same_period_in_both_raw_stores(tmp_path):
    record = _entity()
    record["multi_period_financials"] = {"FY-Current": {"revenue": 1200}}
    record["analyst_supplied_financials"] = {"FY-Current": {"revenue": 1100}}
    with pytest.raises(ValueError, match="both raw stores"):
        state_manager.validate_state(
            {"entity_records": {record["entity_id"]: record}},
            keys=("entity_records",),
        )


def test_entity_spread_refuses_missing_entity_and_non_guarantor_without_writing(tmp_path, monkeypatch):
    parent = _entity("synthetic-parent", "GUARANTEE-SYN-001", role="parent")
    path, _ = _seed_deal(tmp_path, [parent])
    before = path.read_bytes()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="not found"):
        spreading_check.compute_entity(COMPANY, PROPOSAL, "unknown-entity")
    with pytest.raises(ValueError, match="role.*guarantor"):
        spreading_check.compute_entity(COMPANY, PROPOSAL, "synthetic-parent")
    assert path.read_bytes() == before


def test_spread_payload_validator_requires_complete_labelled_payload():
    record = _entity()
    record.update({
        "spread_status": "spread",
        "figures_basis": "recomputed",
        "spread_timestamp": "2026-11-05T20:00:00+00:00",
        "spread_source": "scripts/spreading_check.py --entity-id",
        "analyst_review_status": REVIEW_LABEL,
        "financials": {"FY-Current": {"raw": {"revenue": 1}, "gross_profit": 1}},
        "ratios": {"FY-Current": {"dscr": None}},
        "spread_fingerprints": {"FY-Current": "a" * 64},
    })
    record["multi_period_financials"] = {"FY-Current": {"revenue": 1}}
    state = {"entity_records": {record["entity_id"]: record}, "guarantees": [
        {"guarantee_id": "GUARANTEE-SYN-001"},
    ]}
    assert state_manager.validate_state(state, keys=("entity_records",)) is state

    malformed = copy.deepcopy(state)
    del malformed["entity_records"][record["entity_id"]]["spread_fingerprints"]
    with pytest.raises(state_manager.StateShapeError, match="spread_fingerprints"):
        state_manager.validate_state(malformed, keys=("entity_records",))

    wrong_label = copy.deepcopy(state)
    wrong_label["entity_records"][record["entity_id"]]["analyst_review_status"] = "reviewed"
    with pytest.raises(state_manager.StateShapeError, match="not yet reviewed by an analyst"):
        state_manager.validate_state(wrong_label, keys=("entity_records",))


def test_refused_entity_spread_write_is_atomic_when_the_input_fingerprint_is_stale(tmp_path):
    record = _entity()
    path, _ = _seed_deal(tmp_path, [record])
    before = path.read_bytes()
    calculated = evaluate_financial_model(record["multi_period_financials"])
    expected_fingerprints = {
        period: state_manager.entity_period_input_fingerprint(record["entity_id"], period, record)
        for period in record["multi_period_financials"]
    }
    current = read_state(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path))
    current["entity_records"][record["entity_id"]]["multi_period_financials"]["FY-Current"]["revenue"] += 1
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    before_refusal = path.read_bytes()

    with pytest.raises(ValueError, match="input changed"):
        state_manager.record_entity_spread(
            COMPANY, PROPOSAL, record["entity_id"],
            calculated["financials"], calculated["ratios"], expected_fingerprints,
            "2026-11-05T20:00:00+00:00", "scripts/spreading_check.py --entity-id",
            base_dir=str(tmp_path),
        )

    assert path.read_bytes() == before_refusal
    assert before != before_refusal


def test_record_entity_spread_does_not_write_an_analyst_supplied_period(tmp_path):
    record = _entity(
        raw={"FY-1": RAW_PERIODS["FY-1"]},
        supplied={"FY-Current": {"revenue": 500}},
    )
    path, _ = _seed_deal(tmp_path, [record])
    before = path.read_bytes()
    calculated = evaluate_financial_model(record["multi_period_financials"])
    fingerprints = {
        period: state_manager.entity_period_input_fingerprint(record["entity_id"], period, record)
        for period in record["multi_period_financials"]
    }

    saved = state_manager.record_entity_spread(
        COMPANY, PROPOSAL, record["entity_id"],
        calculated["financials"], calculated["ratios"], fingerprints,
        "2026-11-05T20:00:00+00:00", "scripts/spreading_check.py --entity-id",
        base_dir=str(tmp_path),
    )

    spread = saved["entity_records"][record["entity_id"]]
    assert set(spread["financials"]) == {"FY-1"}
    assert "FY-Current" not in spread["financials"]
    assert spread["analyst_supplied_financials"]["FY-Current"] == {"revenue": 500}
    assert spread["spread_status"] == "spread"
    assert path.read_bytes() != before
