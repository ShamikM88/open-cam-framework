"""Collateral exposure calculator: a pure calculation plus a recording CLI.

Issue #266 PR B. The register entries F-049 to F-057 and F-066 are the
specification; the calculator implements them as pure functions so that the
spreading workbook (PR C) and the policy ground truth (PR D) can consume the
recorded results.

The CLI validates the complete payload before writing anything, then
atomically records the ``collateral_analysis`` object (per-asset inputs and
results, a separate mitigants list, deal-level results, the input digest and
the calculator version) through the state manager, refreshes the
backward-compatible ``collateral`` projection, and marks the ``collateral``
step complete only on success. On any refusal it prints exactly one
``error:`` line, exits 1, and writes nothing.

All figures are synthetic; no real borrower data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any

from state_manager import read_state, write_state

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CALCULATOR_VERSION = "1.0.0"

# Decision 3: the asset identifier grammar.
ASSET_ID_RE = re.compile(r"[A-Za-z0-9-]{1,32}")

# collateral_<asset_id>_lgd_pct must never equal a deal-level ground-truth key (decision 4):
# asset ids "max" and "avg" would produce collateral_max_lgd_pct / collateral_avg_lgd_pct (CG559-1).
RESERVED_ASSET_IDS = frozenset({"max", "avg"})

# The per-asset monetary inputs that must be non-negative when present, and
# their documented defaults (F-049 to F-051: absent means zero, never a guess).
OPTIONAL_NON_NEGATIVE_INPUTS = {
    "cap_or_model_value": 0.0,
    "non_recovery": 0.0,
    "costs": 0.0,
    "number_of_units": 0,
}

# The input fields every asset must carry (decision 4).
REQUIRED_ASSET_INPUTS = ("valuation", "down_payment_pct", "est_rv_pct")

class CollateralInputError(ValueError):
    """A collateral payload violates a register rule; nothing may be written."""


# ---------------------------------------------------------------------------
# Validation (decisions 3, 4 and 6; register F-049 to F-052)
# ---------------------------------------------------------------------------

def _finite_number(value: Any, field: str, context: str) -> float:
    """A number that must be finite; bools and strings are refused, not coerced."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollateralInputError(f"{context}: {field} must be a finite number; found {value!r}")
    try:
        number = float(value)
    except OverflowError:  # an integer too large for a float is not a usable figure
        raise CollateralInputError(f"{context}: {field} must be a finite number; found an integer too large") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise CollateralInputError(f"{context}: {field} must be a finite number; found {value!r}")
    return number


def _asset_context(asset_id: str) -> str:
    return f"collateral[{asset_id}]"


def _validate_asset(asset: Any, index: int, seen_asset_ids: set[str]) -> dict[str, Any]:
    if not isinstance(asset, dict):
        raise CollateralInputError(f"collateral[{index}]: each asset must be an object; found {asset!r}")
    if "asset_id" not in asset:
        raise CollateralInputError(f"collateral[index {index}]: asset_id is required")
    asset_id = asset["asset_id"]
    if not isinstance(asset_id, str) or not ASSET_ID_RE.fullmatch(asset_id):
        raise CollateralInputError(
            f"collateral[index {index}]: asset_id must match [A-Za-z0-9-]{{1,32}}; found {asset_id!r}"
        )
    if asset_id in RESERVED_ASSET_IDS:
        raise CollateralInputError(
            f"collateral[index {index}]: asset_id {asset_id!r} is reserved (it would collide with the deal-level "
            f"collateral_{asset_id}_lgd_pct figure); choose another asset_id"
        )
    if asset_id in seen_asset_ids:
        raise CollateralInputError(f"collateral: duplicate asset_id {asset_id!r}")
    seen_asset_ids.add(asset_id)
    context = _asset_context(asset_id)

    validated: dict[str, Any] = {"asset_id": asset_id}
    for field in REQUIRED_ASSET_INPUTS:
        if field not in asset:
            raise CollateralInputError(f"{context}: {field} is required")
        validated[field] = _finite_number(asset[field], field, context)
    if validated["valuation"] < 0:
        raise CollateralInputError(f"{context}: valuation must be non-negative; found {validated['valuation']}")

    for field in ("down_payment_pct", "est_rv_pct"):
        value = validated[field]
        if not 0.0 <= value <= 1.0:
            raise CollateralInputError(f"{context}: {field} must be within [0, 1]; found {value}")

    # Optional, with no default: absent means N/A, never zero.
    if "lgd_pct" in asset:
        lgd_pct = _finite_number(asset["lgd_pct"], "lgd_pct", context)
        if not 0.0 <= lgd_pct <= 1.0:
            raise CollateralInputError(f"{context}: lgd_pct must be within [0, 1]; found {lgd_pct}")
        validated["lgd_pct"] = lgd_pct
    else:
        validated["lgd_pct"] = None

    # Decision 6 / F-052: a residual above the financed share is refused.
    financed_share = 1.0 - validated["down_payment_pct"]
    if validated["est_rv_pct"] > financed_share:
        raise CollateralInputError(
            f"{context}: est_rv_pct {validated['est_rv_pct']!r} exceeds the financed share "
            f"(1 - down_payment_pct = {financed_share!r}); a residual above the financed share is refused."
        )

    for field, default in OPTIONAL_NON_NEGATIVE_INPUTS.items():
        if field in asset and asset[field] is not None:
            value = _finite_number(asset[field], field, context)
            if value < 0:
                raise CollateralInputError(f"{context}: {field} must be non-negative; found {value}")
            # A whole number of units is stored as an integer, not 3.0 (it reaches the legacy projection).
            validated[field] = int(value) if field == "number_of_units" and value.is_integer() else value
        else:
            validated[field] = default

    for field in ("asset_class", "perfection_status"):
        if field in asset and asset[field] is not None:
            if not isinstance(asset[field], str) or not asset[field].strip():
                raise CollateralInputError(
                    f"{context}: {field} must be a non-empty string when present; found {asset[field]!r}"
                )
            validated[field] = asset[field]

    return validated


def _validate_mitigant(mitigant: Any, index: int, seen_mitigant_ids: set[str]) -> dict[str, Any]:
    if not isinstance(mitigant, dict):
        raise CollateralInputError(f"mitigants[{index}]: each mitigant must be an object; found {mitigant!r}")
    context = f"mitigants[{index}]"
    if "amount" not in mitigant:
        raise CollateralInputError(f"{context}: amount is required")
    amount = _finite_number(mitigant["amount"], "amount", context)
    if amount < 0:
        raise CollateralInputError(f"{context}: amount must be non-negative; found {amount}")
    # Decision 6: the eligibility flag is required; a missing flag is refused.
    if "eligible_for_netting" not in mitigant:
        raise CollateralInputError(
            f"{context}: eligible_for_netting is required (a missing eligibility flag is refused)"
        )
    if not isinstance(mitigant["eligible_for_netting"], bool):
        raise CollateralInputError(
            f"{context}: eligible_for_netting must be a boolean; found {mitigant['eligible_for_netting']!r}"
        )
    validated: dict[str, Any] = {"amount": amount, "eligible_for_netting": mitigant["eligible_for_netting"]}
    if "mitigant_id" in mitigant and mitigant["mitigant_id"] is not None:
        mitigant_id = mitigant["mitigant_id"]
        if not isinstance(mitigant_id, str) or not ASSET_ID_RE.fullmatch(mitigant_id):
            raise CollateralInputError(
                f"{context}: mitigant_id must match [A-Za-z0-9-]{{1,32}}; found {mitigant_id!r}"
            )
        if mitigant_id in seen_mitigant_ids:
            raise CollateralInputError(f"mitigants: duplicate mitigant_id {mitigant_id!r}")
        seen_mitigant_ids.add(mitigant_id)
        validated["mitigant_id"] = mitigant_id
    return validated


def validate_collateral_payload(payload: Any) -> dict:
    """Validate the complete collateral payload before anything is computed or written."""
    if not isinstance(payload, dict):
        raise CollateralInputError(f"collateral payload must be an object; found {payload!r}")
    if "assets" not in payload:
        raise CollateralInputError("collateral payload: assets is required")
    assets = payload["assets"]
    if not isinstance(assets, list):
        raise CollateralInputError(f"collateral payload: assets must be a list; found {assets!r}")

    seen_asset_ids: set = set()
    validated_assets = [_validate_asset(asset, index, seen_asset_ids) for index, asset in enumerate(assets)]

    mitigants = payload.get("mitigants", [])
    if not isinstance(mitigants, list):
        raise CollateralInputError(f"collateral payload: mitigants must be a list; found {mitigants!r}")
    seen_mitigant_ids: set = set()
    validated_mitigants = [
        _validate_mitigant(mitigant, index, seen_mitigant_ids) for index, mitigant in enumerate(mitigants)
    ]

    return {"assets": validated_assets, "mitigants": validated_mitigants}


# ---------------------------------------------------------------------------
# Canonical digest and version (decision 5)
# ---------------------------------------------------------------------------

def canonical_input_digest(validated: dict) -> str:
    """SHA-256 of the canonical JSON of the validated inputs (assets and mitigants)."""
    canonical = json.dumps(
        {"assets": validated["assets"], "mitigants": validated["mitigants"]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stored_analysis_digest_matches(analysis: Any) -> bool:
    """True only when a stored analysis's ``input_digest`` equals the digest of its own stored inputs.

    Decision 5 on issue #266: a reader (the policy ground truth, the workbook) uses a stored result only
    while the inputs it was calculated from are unchanged. A malformed analysis, a hand-edited input
    (including a NaN) or a wrong digest is "not current": never an exception.
    """
    if not isinstance(analysis, dict) or not isinstance(analysis.get("assets"), list):
        return False
    try:
        recomputed = canonical_input_digest({
            "assets": [dict(asset["inputs"], asset_id=asset["asset_id"]) for asset in analysis["assets"]],
            "mitigants": analysis.get("mitigants", []),
        })
    except (KeyError, TypeError, ValueError):
        return False
    return recomputed == analysis.get("input_digest")


# ---------------------------------------------------------------------------
# Per-asset results (F-053 to F-055)
# ---------------------------------------------------------------------------

def calculate_asset_results(inputs: dict) -> dict:
    """The per-asset collateral figures; N/A (None) where the register says so, never zero."""
    financed_exposure = inputs["valuation"] * (1.0 - inputs["down_payment_pct"])
    rv_exposure = inputs["valuation"] * inputs["est_rv_pct"]
    non_rv_funded_exposure = financed_exposure - rv_exposure
    total_exposure = non_rv_funded_exposure + rv_exposure
    collateral_value = max(0.0, inputs.get("cap_or_model_value", 0.0)
                           - inputs.get("non_recovery", 0.0) - inputs.get("costs", 0.0))
    coverage_pct = None if total_exposure <= 0 else collateral_value / total_exposure
    uncovered_risk = max(0.0, total_exposure - collateral_value)
    return {
        "non_rv_funded_exposure": non_rv_funded_exposure,
        "rv_exposure": rv_exposure,
        "total_exposure": total_exposure,
        "collateral_value": collateral_value,
        "coverage_pct": coverage_pct,
        "uncovered_risk": uncovered_risk,
        "lgd_pct": inputs.get("lgd_pct"),
    }


# ---------------------------------------------------------------------------
# Netting (F-066): eligible mitigants, allocated pro rata to total exposure
# ---------------------------------------------------------------------------

def allocate_net_exposure(assets: list, eligible_total: float) -> dict:
    """Allocate eligible mitigants pro rata to each asset's total exposure."""
    total = sum(calculate_asset_results(asset)["total_exposure"] for asset in assets)
    if total <= 0:
        return {asset["asset_id"]: 0.0 for asset in assets}
    share = min(1.0, eligible_total / total)
    return {asset["asset_id"]: calculate_asset_results(asset)["total_exposure"] * (1.0 - share)
            for asset in assets}


# ---------------------------------------------------------------------------
# Deal-level results (F-055 to F-057)
# ---------------------------------------------------------------------------

def calculate_deal_results(assets_results: list, eligible_mitigants_total: float) -> dict:
    total_exposure = sum(asset["results"]["total_exposure"] for asset in assets_results)
    total_collateral_value = sum(asset["results"]["collateral_value"] for asset in assets_results)
    coverage_pct = None if total_exposure <= 0 else total_collateral_value / total_exposure
    gross_uncovered_risk = sum(asset["results"]["uncovered_risk"] for asset in assets_results)
    net_uncovered_risk = sum(asset["results"]["net_uncovered_risk"] for asset in assets_results)
    excess_mitigant = max(0.0, eligible_mitigants_total - total_exposure)

    lgd_values = [asset["results"]["lgd_pct"] for asset in assets_results]
    lgd_known = [lgd for lgd in lgd_values if lgd is not None]
    collateral_max_lgd_pct = max(lgd_known) if len(lgd_known) == len(assets_results) and assets_results else None
    if total_exposure > 0 and len(lgd_known) == len(assets_results) and assets_results:
        collateral_avg_lgd_pct = sum(
            asset["results"]["total_exposure"] * asset["results"]["lgd_pct"]
            for asset in assets_results
        ) / total_exposure
    else:
        collateral_avg_lgd_pct = None

    return {
        "total_exposure": total_exposure,
        "total_collateral_value": total_collateral_value,
        "coverage_pct": coverage_pct,
        "gross_uncovered_risk": gross_uncovered_risk,
        "net_uncovered_risk": net_uncovered_risk,
        "excess_mitigant": excess_mitigant,
        "collateral_max_lgd_pct": collateral_max_lgd_pct,
        "collateral_avg_lgd_pct": collateral_avg_lgd_pct,
    }


# ---------------------------------------------------------------------------
# The full analysis (decision 4: the stored object)
# ---------------------------------------------------------------------------

def calculate_collateral_analysis(payload: Any) -> dict:
    """Validate, compute, and assemble the stored collateral_analysis object."""
    validated = validate_collateral_payload(payload)
    eligible_total = sum(
        mitigant["amount"] for mitigant in validated["mitigants"] if mitigant["eligible_for_netting"]
    )
    net_exposures = allocate_net_exposure(validated["assets"], eligible_total)

    assets = []
    for inputs in validated["assets"]:
        results = calculate_asset_results(inputs)
        results["net_exposure"] = net_exposures[inputs["asset_id"]]
        results["net_uncovered_risk"] = max(0.0, results["net_exposure"] - results["collateral_value"])
        stored_inputs = {key: value for key, value in inputs.items() if key != "asset_id"}
        assets.append({"asset_id": inputs["asset_id"], "inputs": stored_inputs, "results": results})

    deal_results = calculate_deal_results(assets, eligible_total)

    return {
        "assets": assets,
        "mitigants": validated["mitigants"],
        "results": deal_results,
        "input_digest": canonical_input_digest(validated),
        "calculator_version": CALCULATOR_VERSION,
    }


def stored_analysis_is_current(analysis: Any) -> bool:
    """True only for a stored analysis the current calculator would reproduce exactly (decision 5 on issue #266).

    The one freshness rule every reader (the workbook, the policy ground truth) applies before using a stored
    result (review finding CG559-3): the stored ``calculator_version`` equals the current one, the stored
    ``input_digest`` still matches the stored inputs, and recalculating the stored inputs and mitigants with the
    current calculator reproduces the stored per-asset and deal-level results exactly. A hand-edited result, an
    analysis from another calculator version, a changed input or a malformed object is therefore "not current":
    never an exception, and readers omit it rather than repair it.
    """
    if not stored_analysis_digest_matches(analysis) or analysis.get("calculator_version") != CALCULATOR_VERSION:
        return False
    try:
        recomputed = calculate_collateral_analysis({
            # None is how the calculator stores an absent optional input (lgd_pct): send it back as absent.
            "assets": [
                {**{key: value for key, value in asset["inputs"].items() if value is not None},
                 "asset_id": asset["asset_id"]}
                for asset in analysis["assets"]
            ],
            "mitigants": analysis.get("mitigants", []),
        })
    except (CollateralInputError, KeyError, TypeError, ValueError, OverflowError):
        return False
    return (
        recomputed["input_digest"] == analysis["input_digest"]
        and [asset["results"] for asset in recomputed["assets"]]
        == [asset.get("results") for asset in analysis["assets"]]
        and recomputed["results"] == analysis.get("results")
    )


def collateral_projection(analysis: dict) -> list:
    """The backward-compatible ``collateral`` rows, from the stored analysis results."""
    return [
        {
            "asset_id": asset["asset_id"],
            "asset_class": asset["inputs"].get("asset_class", ""),
            "exposure": asset["results"]["total_exposure"],
            "number_of_units": asset["inputs"].get("number_of_units", 0),
            "cap_value": asset["inputs"].get("cap_or_model_value", 0.0),
            "non_recovery": asset["inputs"].get("non_recovery", 0.0),
            "costs": asset["inputs"].get("costs", 0.0),
            "collateral_value": asset["results"]["collateral_value"],
            "perfection_status": asset["inputs"].get("perfection_status", ""),
        }
        for asset in analysis["assets"]
    ]


# ---------------------------------------------------------------------------
# Stressed residual-realisation sensitivity (issue #549; register F-067)
# ---------------------------------------------------------------------------

def stressed_collateral_value(results: dict, rv_realisation_pct: float) -> float:
    """The residual-only stress of decision 12: only the residual part is stressed.

    Residual part = the lower of the F-053 collateral value and the F-050 RV
    exposure; the rest is non-residual.
    stressed = non-residual part + (rv_realisation_pct / 100) x residual part.
    """
    collateral_value = results["collateral_value"]
    residual_part = min(collateral_value, results["rv_exposure"])
    non_residual_part = collateral_value - residual_part
    return max(0.0, non_residual_part + (rv_realisation_pct / 100.0) * residual_part)


def apply_rv_realisation_stress(analysis: dict, rv_realisation_pct: float) -> dict:
    """The stressed variants of F-053, F-054, F-055 and F-066, report-only.

    The exposure denominators are the unchanged F-051 total exposure and
    F-052 net exposure; F-049, F-050, F-051, F-052, F-056 and F-057 have no
    stressed variants.
    """
    assets = []
    for asset in analysis["assets"]:
        results = asset["results"]
        stressed_value = stressed_collateral_value(results, rv_realisation_pct)
        total_exposure = results["total_exposure"]
        assets.append({
            "asset_id": asset["asset_id"],
            "stressed_collateral_value": stressed_value,
            "stressed_coverage_pct": (
                None if total_exposure <= 0 else stressed_value / total_exposure
            ),
            "stressed_uncovered_risk": max(0.0, total_exposure - stressed_value),
            "stressed_net_uncovered_risk": max(0.0, results["net_exposure"] - stressed_value),
        })

    total_exposure = sum(asset["results"]["total_exposure"] for asset in analysis["assets"])
    stressed_total = sum(asset["stressed_collateral_value"] for asset in assets)
    return {
        "rv_realisation_pct": rv_realisation_pct,
        "assets": assets,
        "results": {
            "stressed_total_collateral_value": stressed_total,
            "stressed_coverage_pct": None if total_exposure <= 0 else stressed_total / total_exposure,
            "stressed_gross_uncovered_risk": sum(
                asset["stressed_uncovered_risk"] for asset in assets
            ),
            "stressed_net_uncovered_risk": sum(
                asset["stressed_net_uncovered_risk"] for asset in assets
            ),
        },
    }


def _stressed_output(state: dict | None, analysis: dict) -> dict | None:
    """The report-only stressed figures, or None when the deal carries no assumption.

    Nothing derived is persisted: this object goes to the CLI output only.
    A stored assumption that violates the 0-100 rule is refused, never clamped.
    """
    if not state:
        return None
    assumptions = state.get("stress_assumptions") or {}
    if not isinstance(assumptions, dict) or "rv_realisation_pct" not in assumptions:
        return None
    value = assumptions["rv_realisation_pct"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value \
            or value in (float("inf"), float("-inf")):
        raise CollateralInputError(
            "stress assumptions: rv_realisation_pct must be a finite number; "
            f"found {value!r}"
        )
    if not 0 <= value <= 100:
        raise CollateralInputError(
            f"stress assumptions: rv_realisation_pct must be within [0, 100] "
            f"(100 is full realisation); found {value!r}"
        )
    return apply_rv_realisation_stress(analysis, float(value))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="collateral_check",
        description="Compute and record the collateral exposure analysis for a deal (issue #266 PR B).",
    )
    parser.add_argument("--company", required=True, help="Company name for the deal state")
    parser.add_argument("--proposal", required=True, help="Proposal name for the deal state")
    parser.add_argument("--input-file", required=True,
                        help="Path to the collateral payload JSON ('-' reads stdin)")
    return parser


def _load_payload(path: str) -> Any:
    if path == "-":
        text = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    return json.loads(text)


def _record_analysis(company: str, proposal: str, analysis: dict, state: dict | None) -> None:
    """Atomically record the analysis, refresh the projection, complete the step.

    Only the analysis itself is recorded: the report-only stressed figures of
    issue #549 never reach state.json.
    """
    steps = list(state.get("steps_completed", [])) if state else []
    if "collateral" not in steps:
        steps.append("collateral")
    write_state(
        company, proposal,
        collateral_analysis=analysis,
        collateral=collateral_projection(analysis),
        steps_completed=steps,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        payload = _load_payload(args.input_file)
        analysis = calculate_collateral_analysis(payload)
        state = read_state(args.company, args.proposal)
        stressed = _stressed_output(state, analysis)
        _record_analysis(args.company, args.proposal, analysis, state)
    except CollateralInputError as error:
        sys.exit(f"error: {error}")
    except json.JSONDecodeError as error:
        sys.exit(f"error: the collateral payload is not valid JSON ({error})")
    except FileNotFoundError as error:
        sys.exit(f"error: {error}")
    except (OSError, ValueError) as error:
        sys.exit(f"error: {error}")

    output = dict(analysis)
    if stressed is not None:
        output["stressed"] = stressed
    sys.stdout.write(json.dumps(output, sort_keys=True, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    sys.exit(main(sys.argv[1:]))
