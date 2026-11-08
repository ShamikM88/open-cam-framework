"""Strict, deterministic fixtures for v2 per-asset collateral evaluation cases (#266).

A fixture is data, not executable mutation instructions. The only supported
states are the closed enum below; stale variants change one result or the
calculator version after the normal calculator and legacy projection run.
No model, SDK, filesystem or network dependency.
"""
from __future__ import annotations

from typing import Any

from collateral_check import (
    CollateralInputError,
    calculate_collateral_analysis,
    collateral_projection,
    validate_collateral_payload,
)

VALID_FIXTURE_STATES = ("current", "result-tampered", "old-version")
_FIXTURE_KEYS = frozenset({"payload", "state"})
_PAYLOAD_KEYS = frozenset({"assets", "mitigants"})
_ASSET_KEYS = frozenset({
    "asset_id", "valuation", "down_payment_pct", "est_rv_pct", "lgd_pct",
    "cap_or_model_value", "non_recovery", "costs", "number_of_units",
    "asset_class", "perfection_status",
})
_MITIGANT_KEYS = frozenset({"mitigant_id", "amount", "eligible_for_netting"})


class CollateralFixtureError(ValueError):
    """A v2 fixture is malformed or asks for a state outside the closed enum."""


def validate_collateral_fixture(fixture: Any) -> None:
    """Reject unknown fields and open-ended mutation instructions before calculation."""
    if not isinstance(fixture, dict):
        raise CollateralFixtureError("fixture must be an object")
    unknown = set(fixture) - _FIXTURE_KEYS
    missing = _FIXTURE_KEYS - set(fixture)
    if unknown:
        raise CollateralFixtureError(f"unknown fixture keys {sorted(unknown)}")
    if missing:
        raise CollateralFixtureError(f"fixture requires keys {sorted(missing)}")
    state = fixture["state"]
    if not isinstance(state, str) or state not in VALID_FIXTURE_STATES:
        raise CollateralFixtureError(f"state must be one of {VALID_FIXTURE_STATES}")

    payload = fixture["payload"]
    if not isinstance(payload, dict):
        raise CollateralFixtureError("payload must be an object")
    unknown_payload = set(payload) - _PAYLOAD_KEYS
    if unknown_payload:
        raise CollateralFixtureError(f"unknown payload keys {sorted(unknown_payload)}")
    if "assets" not in payload:
        raise CollateralFixtureError("payload.assets is required")
    assets = payload["assets"]
    if not isinstance(assets, list) or not assets:
        raise CollateralFixtureError("payload.assets must be a non-empty list")
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise CollateralFixtureError(f"payload.assets[{index}] must be an object")
        unknown_asset = set(asset) - _ASSET_KEYS
        if unknown_asset:
            raise CollateralFixtureError(f"unknown payload.assets[{index}] keys {sorted(unknown_asset)}")

    mitigants = payload.get("mitigants", [])
    if not isinstance(mitigants, list):
        raise CollateralFixtureError("payload.mitigants must be a list")
    for index, mitigant in enumerate(mitigants):
        if not isinstance(mitigant, dict):
            raise CollateralFixtureError(f"payload.mitigants[{index}] must be an object")
        unknown_mitigant = set(mitigant) - _MITIGANT_KEYS
        if unknown_mitigant:
            raise CollateralFixtureError(
                f"unknown payload.mitigants[{index}] keys {sorted(unknown_mitigant)}"
            )
    try:
        validate_collateral_payload(payload)
    except CollateralInputError as exc:
        raise CollateralFixtureError(f"invalid calculator payload: {exc}") from None


def build_collateral_fixture(fixture: Any) -> dict[str, Any]:
    """Return the analysis and legacy projection represented by one closed-state fixture.

    Projection is deliberately calculated before a stale mutation. Thus the
    stale cases seed the original backward-compatible row alongside the altered
    stored analysis; the freshness guard, not a synthetic rewrite of the row,
    is what removes per-asset ground truth.
    """
    validate_collateral_fixture(fixture)
    analysis = calculate_collateral_analysis(fixture["payload"])
    projection = collateral_projection(analysis)
    state = fixture["state"]
    if state == "result-tampered":
        # Result-only mutation: inputs, input_digest and calculator version stay intact.
        analysis["assets"][0]["results"]["total_exposure"] += 1.0
    elif state == "old-version":
        # A deterministic older-version marker; never inferred from arbitrary fixture text.
        analysis["calculator_version"] = "0.0.0"
    return {"analysis": analysis, "collateral": projection}
