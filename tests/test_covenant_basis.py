"""C4 contract: a guarantor covenant resolves only to its named, fresh, linked entity."""

import copy

import pytest

from covenant_basis import resolve_covenant_view, resolve_covenant_views
from spreading_builder import evaluate_financial_model
from state_manager import entity_period_input_fingerprint

REVIEW_LABEL = "not yet reviewed by an analyst"
RAW = {
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


def _record(entity_id, guarantee_id, *, role="guarantor", raw=None, supplied=None):
    raw_periods = copy.deepcopy(RAW if raw is None else raw)
    supplied_periods = copy.deepcopy(supplied or {})
    period_ends = {
        period: "2025-12-31" if period == "FY-Current" else "2024-12-31"
        for period in set(raw_periods) | set(supplied_periods)
    }
    model = evaluate_financial_model(raw_periods) if raw_periods else {"financials": {}, "ratios": {}}
    record = {
        "entity_id": entity_id,
        "legal_name": f"Synthetic {entity_id} Ltd",
        "role": role,
        "source": {
            "document": "Synthetic standalone accounts",
            "accounting_basis": "UK GAAP",
            "date": "2026-02-01",
        },
        "period_ends": period_ends,
        "perimeter": {
            "included": True,
            "control_basis": "analyst-confirmed",
            "scope_rationale": "Synthetic standalone guarantor perimeter",
            "confirmed": True,
            "confirmed_on": "2026-10-27",
        },
        "raw_figures_source": "Synthetic source document",
        "multi_period_financials": raw_periods,
        "analyst_supplied_financials": supplied_periods,
        "spread_status": "spread",
        "figures_basis": "recomputed",
        "spread_timestamp": "2026-11-06T12:00:00+00:00",
        "spread_source": "scripts/spreading_check.py --entity-id",
        "analyst_review_status": REVIEW_LABEL,
        "financials": model["financials"],
        "ratios": model["ratios"],
        "spread_fingerprints": {},
        "intercompany_lines": [],
        "guarantee_ids": [guarantee_id] if guarantee_id else [],
    }
    record["spread_fingerprints"] = {
        period: entity_period_input_fingerprint(entity_id, period, record)
        for period in raw_periods
    }
    return record


def _state(*records, guarantees=None, borrower_dscr=7.7):
    guarantee_rows = guarantees if guarantees is not None else [
        {"guarantee_id": record["guarantee_ids"][0], "provider": record["legal_name"]}
        for record in records if record["guarantee_ids"]
    ]
    return {
        "financials": {"FY-Current": {"ebitda": 777}},
        "ratios": {"FY-Current": {"dscr": borrower_dscr}, "FY+1": {"dscr": borrower_dscr - 0.2}},
        "entity_records": {record["entity_id"]: record for record in records},
        "guarantees": guarantee_rows,
    }


def _covenant(entity_id="synthetic-guarantor"):
    return {
        "metric": "dscr",
        "type": "minimum",
        "threshold": 1.25,
        "basis": "guarantor_group",
        "basis_entity_id": entity_id,
    }


def test_borrower_and_case_a_resolution_preserve_the_legacy_views_and_shape():
    state = _state(_record("synthetic-guarantor", "G-1"))
    borrower = resolve_covenant_view(state, {"metric": "dscr", "type": "minimum", "threshold": 1.0})
    consolidated = resolve_covenant_view(
        {**state, "group_basis": {"basis": "supplied_consolidated"}},
        {"metric": "dscr", "type": "minimum", "threshold": 1.0, "basis": "consolidated"},
    )

    assert borrower["basis"] == "borrower"
    assert borrower["ratios"] is state["ratios"]
    assert "basis_entity_id" not in borrower
    assert consolidated["basis"] == "consolidated"
    assert consolidated["ratios"] is state["ratios"]
    assert "basis_entity_id" not in consolidated


def test_a_valid_named_guarantor_uses_only_its_own_fresh_periods():
    record = _record("synthetic-guarantor-a", "G-A")
    state = _state(record, borrower_dscr=77.0)
    view = resolve_covenant_view(state, _covenant("synthetic-guarantor-a"))

    assert view["basis"] == "guarantor_group"
    assert view["basis_entity_id"] == "synthetic-guarantor-a"
    assert view["ratios"]["FY-Current"] == record["ratios"]["FY-Current"]
    assert view["ratios"]["FY-Current"]["dscr"] != state["ratios"]["FY-Current"]["dscr"]
    assert set(view["eligible_raw_periods"]) == {"FY-1", "FY-Current"}
    assert view["unresolvable_reason"] is None
    assert view["period_reasons"] == {}


def test_two_named_guarantors_cannot_cross_route_or_collide():
    first = _record("synthetic-guarantor-a", "G-A")
    second_raw = copy.deepcopy(RAW)
    second_raw["FY-Current"]["revenue"] = 5000
    second = _record("synthetic-guarantor-b", "G-B", raw=second_raw)
    state = _state(first, second, borrower_dscr=88.0)

    views = resolve_covenant_views(state, [_covenant(first["entity_id"]), _covenant(second["entity_id"])])

    assert [view["basis_entity_id"] for view in views] == [
        "synthetic-guarantor-a", "synthetic-guarantor-b",
    ]
    assert views[0]["ratios"]["FY-Current"]["dscr"] == first["ratios"]["FY-Current"]["dscr"]
    assert views[1]["ratios"]["FY-Current"]["dscr"] == second["ratios"]["FY-Current"]["dscr"]
    assert views[0]["ratios"]["FY-Current"]["dscr"] != views[1]["ratios"]["FY-Current"]["dscr"]


@pytest.mark.parametrize("entity_id", [None, "", "   ", "unknown-entity"])
def test_missing_blank_or_unknown_entity_id_fails_closed_without_borrower_fallback(entity_id):
    record = _record("synthetic-guarantor", "G-1")
    state = _state(record, borrower_dscr=99.0)
    covenant = _covenant(entity_id)
    if entity_id is None:
        covenant.pop("basis_entity_id")

    view = resolve_covenant_view(state, covenant)

    assert view["basis"] == "guarantor_group"
    assert view["basis_entity_id"] == (entity_id if isinstance(entity_id, str) and entity_id.strip() else None)
    assert view["unresolvable_reason"]
    assert view["ratios"] == {}
    assert view["financials"] == {}


def test_only_a_record_with_role_guarantor_is_eligible():
    parent = _record("synthetic-parent", "G-1", role="parent")
    view = resolve_covenant_view(_state(parent), _covenant("synthetic-parent"))

    assert "role" in view["unresolvable_reason"].lower()
    assert view["ratios"] == {}


def test_missing_spread_is_unresolvable_even_when_raw_accounts_exist():
    record = _record("synthetic-guarantor", "G-1")
    record["spread_status"] = "not-spread"
    view = resolve_covenant_view(_state(record), _covenant())

    assert view["unresolvable_reason"]
    assert view["ratios"] == {}


def test_stale_fingerprint_withholds_only_the_affected_period():
    record = _record("synthetic-guarantor", "G-1")
    record["multi_period_financials"]["FY-1"]["revenue"] += 1
    view = resolve_covenant_view(_state(record), _covenant())

    assert "FY-1" in view["period_reasons"]
    assert "FY-1" not in view["eligible_raw_periods"]
    assert "FY-Current" in view["eligible_raw_periods"]


def test_evaluator_output_staleness_withholds_a_period_even_when_its_input_hash_matches():
    record = _record("synthetic-guarantor", "G-1")
    record["financials"]["FY-Current"]["ebitda"] += 1
    view = resolve_covenant_view(_state(record), _covenant())

    assert "FY-Current" in view["period_reasons"]
    assert "FY-Current" not in view["ratios"]


def test_analyst_supplied_only_period_is_never_used_as_a_computed_spread():
    record = _record(
        "synthetic-guarantor", "G-1", raw={},
        supplied={"FY-Current": {"revenue": 500}},
    )
    view = resolve_covenant_view(_state(record), _covenant())

    assert "FY-Current" in view["period_reasons"]
    assert "analyst-supplied" in view["period_reasons"]["FY-Current"]
    assert "FY-Current" not in view["ratios"]


@pytest.mark.parametrize(
    "guarantees",
    [
        [],
        [{"guarantee_id": "OTHER", "provider": "Synthetic guarantor"}],
        [{"guarantee_id": "G-1"}, {"guarantee_id": "G-1"}],
    ],
    ids=["missing", "wrong-id-name-coincidence", "duplicate-id"],
)
def test_missing_wrong_or_ambiguous_explicit_guarantee_link_is_unresolvable(guarantees):
    record = _record("synthetic-guarantor", "G-1")
    view = resolve_covenant_view(_state(record, guarantees=guarantees), _covenant())

    assert view["unresolvable_reason"]
    assert "guarantee" in view["unresolvable_reason"].lower()
    assert view["ratios"] == {}


def test_a_guarantor_record_without_any_explicit_guarantee_id_is_unresolvable():
    record = _record("synthetic-guarantor", None)
    view = resolve_covenant_view(_state(record, guarantees=[]), _covenant())

    assert view["unresolvable_reason"]
    assert "guarantee" in view["unresolvable_reason"].lower()



def test_non_object_entity_registry_is_unresolvable():
    record = _record("synthetic-guarantor", "G-1")
    state = _state(record)
    state["entity_records"] = None

    view = resolve_covenant_view(state, _covenant())

    assert view["unresolvable_reason"] == "entity_records is absent or is not an object"
    assert view["ratios"] == {}


def test_registry_key_must_match_the_record_entity_id():
    record = _record("synthetic-guarantor", "G-1")
    state = _state(record)
    state["entity_records"]["synthetic-guarantor"]["entity_id"] = "another-entity"

    view = resolve_covenant_view(state, _covenant())

    assert "key does not match entity_id" in view["unresolvable_reason"]


def test_non_object_entity_record_is_unresolvable():
    view = resolve_covenant_view(
        {
            "entity_records": {"synthetic-guarantor": []},
            "guarantees": [{"guarantee_id": "G-1"}],
        },
        _covenant(),
    )

    assert "is not recorded" in view["unresolvable_reason"]


@pytest.mark.parametrize("guarantee_ids", [[None], ["  "]], ids=["non-string", "blank"])
def test_invalid_guarantee_id_members_are_unresolvable(guarantee_ids):
    record = _record("synthetic-guarantor", "G-1")
    record["guarantee_ids"] = guarantee_ids

    view = resolve_covenant_view(_state(record), _covenant())

    assert "blank or invalid guarantee_id" in view["unresolvable_reason"]


def test_duplicate_guarantee_ids_on_the_entity_record_are_unresolvable():
    record = _record("synthetic-guarantor", "G-1")
    record["guarantee_ids"] = ["G-1", "G-1"]

    view = resolve_covenant_view(_state(record), _covenant())

    assert "duplicate guarantee_ids" in view["unresolvable_reason"]


def test_non_list_deal_guarantees_are_unresolvable():
    record = _record("synthetic-guarantor", "G-1")
    state = _state(record)
    state["guarantees"] = None

    view = resolve_covenant_view(state, _covenant())

    assert view["unresolvable_reason"] == "the deal's guarantees list is absent or invalid"


def test_recorded_period_without_a_current_spread_has_an_explicit_fallback_reason(monkeypatch):
    import covenant_basis

    record = _record("synthetic-guarantor", "G-1")
    monkeypatch.setattr(
        covenant_basis,
        "entity_spread_view",
        lambda _: {"financials": {}, "ratios": {}, "stale_periods": []},
    )

    view = resolve_covenant_view(_state(record), _covenant())

    assert view["period_reasons"]["FY-Current"] == (
        "guarantor period FY-Current has no fresh framework-computed spread"
    )

def test_resolve_covenant_views_reuses_the_same_fresh_view_for_duplicate_entity_ids(monkeypatch):
    import covenant_basis

    record = _record("synthetic-guarantor", "G-1")
    state = _state(record)
    original = covenant_basis.resolve_covenant_view
    calls = []

    def counted_resolve(selected_state, covenant):
        calls.append(covenant)
        return original(selected_state, covenant)

    monkeypatch.setattr(covenant_basis, "resolve_covenant_view", counted_resolve)
    views = resolve_covenant_views(state, [_covenant(), _covenant()])

    assert len(calls) == 1
    assert views[0] is views[1]
    assert views[0]["basis_entity_id"] == "synthetic-guarantor"


_GLOBAL_FAILURES = ["not-spread", "wrong-role", "missing-link", "no-link"]


def _ineligible_guarantor(failure):
    """A recorded guarantor that fails one global eligibility check but still holds raw FY-Current and FY+1 periods."""
    from guarantor_test_support import make_guarantor_record

    record = make_guarantor_record("synthetic-guarantor")
    if failure == "not-spread":
        record["spread_status"] = "not-spread"
    elif failure == "wrong-role":
        record["role"] = "parent"
    elif failure == "missing-link":
        record["guarantee_ids"] = ["G-MISSING"]
    else:
        record["guarantee_ids"] = []
    return record


@pytest.mark.parametrize("failure", _GLOBAL_FAILURES)
def test_a_recorded_guarantor_failing_a_global_check_still_discloses_its_recorded_periods(failure):
    """CG578-1: the period inventory (keys only, never figures) survives a global eligibility failure."""
    record = _ineligible_guarantor(failure)
    state = _state(record, guarantees=[{"guarantee_id": "G-C4-01", "provider": "Synthetic Guarantor"}])

    view = resolve_covenant_view(state, _covenant())

    assert view["unresolvable_reason"]
    assert view["recorded_periods"] == ["FY+1", "FY-Current"]
    assert view["financials"] == {} and view["ratios"] == {}
    assert view["eligible_raw_periods"] == []


def test_an_entity_whose_identity_is_not_established_discloses_no_periods():
    from guarantor_test_support import make_guarantor_record

    record = make_guarantor_record("synthetic-guarantor")
    assert resolve_covenant_view(_state(record), _covenant("unknown-entity"))["recorded_periods"] == []

    mismatched = _state(record)
    mismatched["entity_records"]["synthetic-guarantor"]["entity_id"] = "another-entity"
    assert resolve_covenant_view(mismatched, _covenant())["recorded_periods"] == []
