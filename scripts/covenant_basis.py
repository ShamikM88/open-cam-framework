"""Deterministic resolution of each covenant's recorded financial basis.

Case C routes one guarantor covenant to one explicitly named, freshly spread
entity. This module deliberately does not aggregate entities or validate the
whole registry: the selected record is checked here so a broken link produces
a covenant-level UNRESOLVABLE result rather than aborting an unrelated policy
read. Registration and write-time validation remain strict.
"""
from collections.abc import Mapping
from typing import Any

from spreading_check import entity_spread_view


def _ordinary_view(state: Mapping[str, Any]) -> dict[str, Any]:
    """The existing deal-level borrower or case-A view, without changing it."""
    recorded_basis = "consolidated" if isinstance(state.get("group_basis"), Mapping) else "borrower"
    financials = state.get("financials")
    ratios = state.get("ratios")
    return {
        "basis": recorded_basis,
        "financials": financials if isinstance(financials, Mapping) else {},
        "ratios": ratios if isinstance(ratios, Mapping) else {},
        "eligible_raw_periods": [],
        "recorded_periods": [],
        "period_reasons": {},
        "unresolvable_reason": None,
    }


def resolve_covenant_view(
    state: Mapping[str, Any], covenant: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one covenant to its deal-level or explicitly named guarantor view.

    Guarantor identity and legal-guarantee linkage are exact-ID joins. Provider
    names are never used as fallback keys. Only periods returned as current by
    entity_spread_view may supply values; stale and analyst-supplied-only
    periods are retained as explicit per-period reasons.
    """
    if covenant.get("basis") != "guarantor_group":
        return _ordinary_view(state)

    raw_id = covenant.get("basis_entity_id")
    entity_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else None

    def unresolved(reason: str) -> dict[str, Any]:
        return {
            "basis": "guarantor_group",
            "basis_entity_id": entity_id,
            "financials": {},
            "ratios": {},
            "eligible_raw_periods": [],
            "recorded_periods": [],
            "period_reasons": {},
            "unresolvable_reason": reason,
        }

    if entity_id is None:
        return unresolved("guarantor_group covenant has no non-blank basis_entity_id")
    registry = state.get("entity_records")
    if not isinstance(registry, Mapping):
        return unresolved("entity_records is absent or is not an object")
    record = registry.get(entity_id)
    if not isinstance(record, dict):
        return unresolved(f"guarantor entity {entity_id!r} is not recorded")
    if record.get("entity_id") != entity_id:
        return unresolved(f"guarantor entity record key does not match entity_id {entity_id!r}")
    if record.get("role") != "guarantor":
        return unresolved(f"entity {entity_id!r} has role {record.get('role')!r}; role must be 'guarantor'")

    guarantee_ids = record.get("guarantee_ids")
    if not isinstance(guarantee_ids, list) or not guarantee_ids:
        return unresolved(f"guarantor entity {entity_id!r} has no explicit guarantee_ids link")
    if any(not isinstance(value, str) or not value.strip() for value in guarantee_ids):
        return unresolved(f"guarantor entity {entity_id!r} has a blank or invalid guarantee_id link")
    if len(set(guarantee_ids)) != len(guarantee_ids):
        return unresolved(f"guarantor entity {entity_id!r} has duplicate guarantee_ids links")
    guarantees = state.get("guarantees")
    if not isinstance(guarantees, list):
        return unresolved("the deal's guarantees list is absent or invalid")
    for guarantee_id in guarantee_ids:
        matches = [
            item for item in guarantees
            if isinstance(item, Mapping) and item.get("guarantee_id") == guarantee_id
        ]
        if len(matches) != 1:
            qualifier = "missing" if not matches else "ambiguous"
            return unresolved(
                f"guarantor entity {entity_id!r} guarantee link {guarantee_id!r} is {qualifier}; "
                "exactly one explicit guarantee_id is required"
            )

    if record.get("spread_status") != "spread":
        return unresolved(
            f"guarantor entity {entity_id!r} has no completed framework spread "
            f"(spread_status={record.get('spread_status')!r})"
        )

    spread = entity_spread_view(record)
    raw = record.get("multi_period_financials")
    supplied = record.get("analyst_supplied_financials")
    raw_periods = set(raw) if isinstance(raw, Mapping) else set()
    supplied_periods = set(supplied) if isinstance(supplied, Mapping) else set()
    financial_periods = set(spread["financials"])
    ratio_periods = set(spread["ratios"])
    recorded_periods = sorted(
        raw_periods | supplied_periods | financial_periods | ratio_periods | set(spread["stale_periods"])
    )
    fresh_periods = financial_periods & ratio_periods & raw_periods
    period_reasons: dict[str, str] = {}
    stale_periods = set(spread["stale_periods"])
    for period in recorded_periods:
        if period in fresh_periods:
            continue
        if period in supplied_periods and period not in raw_periods:
            period_reasons[period] = (
                f"guarantor period {period} is analyst-supplied and is not a framework-computed spread"
            )
        elif period in stale_periods:
            period_reasons[period] = (
                f"guarantor period {period} has a stale or invalid spread fingerprint/evaluator output"
            )
        else:
            period_reasons[period] = (
                f"guarantor period {period} has no fresh framework-computed spread"
            )

    return {
        "basis": "guarantor_group",
        "basis_entity_id": entity_id,
        "financials": dict(spread["financials"]),
        "ratios": dict(spread["ratios"]),
        "eligible_raw_periods": sorted(fresh_periods),
        "recorded_periods": recorded_periods,
        "period_reasons": period_reasons,
        "unresolvable_reason": None,
    }


def resolve_covenant_views(
    state: Mapping[str, Any], covenants: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve covenants in order and reuse one view object per selected entity."""
    cache: dict[str | None, dict[str, Any]] = {}
    views: list[dict[str, Any]] = []
    for covenant in covenants:
        if covenant.get("basis") != "guarantor_group":
            views.append(_ordinary_view(state))
            continue
        raw_id = covenant.get("basis_entity_id")
        entity_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else None
        if entity_id not in cache:
            cache[entity_id] = resolve_covenant_view(state, covenant)
        views.append(cache[entity_id])
    return views
