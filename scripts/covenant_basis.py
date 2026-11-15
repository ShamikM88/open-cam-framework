"""Deterministic resolution of each covenant's recorded financial basis.

Case C routes one guarantor covenant to one explicitly named, freshly spread
entity. This module deliberately does not aggregate entities or validate the
whole registry: the selected record is checked here so a broken link produces
a covenant-level UNRESOLVABLE result rather than aborting an unrelated policy
read. Registration and write-time validation remain strict.
"""
from collections.abc import Mapping
from typing import Any

from credit_aggregation import aggregation_period_status
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


def _consolidated_view(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Case B (issue #472): the credit-aggregation view for a ``consolidated`` covenant, or None.

    None means "no aggregation is recorded", so the ordinary (borrower or case-A) view applies unchanged. A
    consolidated covenant selects exactly one eligible source: case-A ``group_basis`` or a fresh, calculated
    credit-aggregation period. Both recorded is a fail-closed collision (D22). A missing, stale, invalid or
    uncalculated period is reported per period and never replaced by the borrower's or any other view.
    """
    recorded = state.get("credit_aggregation")
    if recorded is None or (isinstance(recorded, Mapping) and not recorded):
        return None
    view: dict[str, Any] = {
        "basis": "consolidated",
        "view_source": "credit_aggregation",
        "financials": {},
        "ratios": {},
        "eligible_raw_periods": [],
        "recorded_periods": sorted(str(period) for period in recorded) if isinstance(recorded, Mapping) else [],
        "period_reasons": {},
        "input_fingerprints": {},
        "unresolvable_reason": None,
    }
    if not isinstance(recorded, Mapping):
        # A corrupt case-B record is never read as "none recorded": that would let a valid case-A group_basis
        # (or the borrower view) answer a covenant the corrupt record may contradict (CG587-5).
        view["unresolvable_reason"] = (
            "the recorded credit_aggregation is malformed (it must be an object keyed by period), so a "
            "consolidated covenant is not tested against any view"
        )
        return view
    if isinstance(state.get("group_basis"), Mapping):
        view["unresolvable_reason"] = (
            "both a case-A group_basis and a credit aggregation are recorded; a consolidated covenant "
            "needs exactly one source, so it is not tested against either"
        )
        return view
    for period in view["recorded_periods"]:
        status = aggregation_period_status(dict(state), period)
        if isinstance(status.get("input_fingerprint"), str):
            view["input_fingerprints"][period] = status["input_fingerprint"]
        entry = recorded[period]
        computed = entry.get("computed") if isinstance(entry, Mapping) else None
        if status.get("status") != "current" or not isinstance(computed, Mapping):
            view["period_reasons"][period] = status.get("reason") or f"credit aggregation {period} is not current"
            continue
        view["financials"][period] = dict(computed["financials"])
        view["ratios"][period] = dict(computed["ratios"])
        view["eligible_raw_periods"].append(period)
    return view


def _recorded_period_labels(record: Mapping[str, Any]) -> list[str]:
    """The period labels a record holds in any figure store (labels only, never values)."""
    labels: set[str] = set()
    for field in ("multi_period_financials", "analyst_supplied_financials", "financials", "ratios"):
        value = record.get(field)
        if isinstance(value, Mapping):
            labels.update(str(period) for period in value)
    return sorted(labels)


def resolve_covenant_view(
    state: Mapping[str, Any], covenant: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one covenant to its deal-level or explicitly named guarantor view.

    Guarantor identity and legal-guarantee linkage are exact-ID joins. Provider
    names are never used as fallback keys. Only periods returned as current by
    entity_spread_view may supply values; stale and analyst-supplied-only
    periods are retained as explicit per-period reasons.
    """
    if covenant.get("basis") == "consolidated":
        routed = _consolidated_view(state)
        return routed if routed is not None else _ordinary_view(state)
    if covenant.get("basis") != "guarantor_group":
        return _ordinary_view(state)

    raw_id = covenant.get("basis_entity_id")
    entity_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else None

    def unresolved(reason: str, recorded_periods: list[str] | None = None) -> dict[str, Any]:
        return {
            "basis": "guarantor_group",
            "basis_entity_id": entity_id,
            "financials": {},
            "ratios": {},
            "eligible_raw_periods": [],
            "recorded_periods": list(recorded_periods or []),
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
    # The record is now identified, so its recorded period labels (never its figures) are kept: a period the
    # selected entity records but cannot supply is reported UNRESOLVABLE, not silently dropped (CG578-1).
    inventory = _recorded_period_labels(record)
    if record.get("role") != "guarantor":
        return unresolved(
            f"entity {entity_id!r} has role {record.get('role')!r}; role must be 'guarantor'", inventory,
        )

    guarantee_ids = record.get("guarantee_ids")
    if not isinstance(guarantee_ids, list) or not guarantee_ids:
        return unresolved(f"guarantor entity {entity_id!r} has no explicit guarantee_ids link", inventory)
    if any(not isinstance(value, str) or not value.strip() for value in guarantee_ids):
        return unresolved(f"guarantor entity {entity_id!r} has a blank or invalid guarantee_id link", inventory)
    if len(set(guarantee_ids)) != len(guarantee_ids):
        return unresolved(f"guarantor entity {entity_id!r} has duplicate guarantee_ids links", inventory)
    guarantees = state.get("guarantees")
    if not isinstance(guarantees, list):
        return unresolved("the deal's guarantees list is absent or invalid", inventory)
    for guarantee_id in guarantee_ids:
        matches = [
            item for item in guarantees
            if isinstance(item, Mapping) and item.get("guarantee_id") == guarantee_id
        ]
        if len(matches) != 1:
            qualifier = "missing" if not matches else "ambiguous"
            return unresolved(
                f"guarantor entity {entity_id!r} guarantee link {guarantee_id!r} is {qualifier}; "
                "exactly one explicit guarantee_id is required",
                inventory,
            )

    if record.get("spread_status") != "spread":
        return unresolved(
            f"guarantor entity {entity_id!r} has no completed framework spread "
            f"(spread_status={record.get('spread_status')!r})",
            inventory,
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
    consolidated: dict[str, Any] | None = None     # its own slot: "consolidated" is also a legal entity ID
    views: list[dict[str, Any]] = []
    for covenant in covenants:
        if isinstance(covenant, Mapping) and covenant.get("basis") == "consolidated":
            if consolidated is None:
                consolidated = resolve_covenant_view(state, covenant)
            views.append(consolidated)
            continue
        if not isinstance(covenant, Mapping) or covenant.get("basis") != "guarantor_group":
            views.append(_ordinary_view(state))
            continue
        raw_id = covenant.get("basis_entity_id")
        entity_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else None
        if entity_id not in cache:
            cache[entity_id] = resolve_covenant_view(state, covenant)
        views.append(cache[entity_id])
    return views
