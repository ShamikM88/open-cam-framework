"""Deterministic, code-enforced credit policy validation.

evaluate_deal_policy() is the single source of truth for whether a deal's
own recorded structure -- its covenant thresholds, its collateral/security
mapping, its guarantees -- implies covenant breaches, security gaps, or
Conditions Precedent (CPs) the CAM must carry. It replaces what would
otherwise be an LLM "eyeballing" pass over the same checks: every branch
here is a pure function of `state_dict` (this deal's state.json content, or
a dict shaped like it), so the same input always produces the same output
-- including the same set of `cp_id`s -- and scripts/orchestrator.py can
enforce the result as a hard governance gate rather than a suggestion.

CP identifiers (`cp_id`) are the join key between this module and the
Underwriter's structured output (see agents/underwriter_agent.md): the
Underwriter is never asked to reproduce or paraphrase a CP's free-text
`text` for compliance-checking purposes, only to echo back the stable
`cp_id` it rendered, so orchestrator.py can check for exact set membership
rather than fuzzy-matching prose.
"""
import hashlib
import re

SLUG_RE = re.compile(r"[^A-Z0-9]+")

# Always required regardless of deal-specific structure.
STANDARD_CONDITIONS_PRECEDENT = [
    {"cp_id": "KYC-AML", "text": "Standard KYC/AML clearance for all borrowing entities."},
    {"cp_id": "FACILITY-EXECUTION", "text": "Execution of Facility Agreement."},
]


def _slugify(value):
    """Deterministic identifier fragment: uppercase, non-alphanumeric runs
    collapsed to a single "-", no leading/trailing "-". The same input
    always produces the same fragment, which is what makes a `cp_id` built
    from it a stable join key across governance-loop iterations.

    An input with no alphanumeric characters at all (rare, but not
    impossible for a hand-entered asset_id/provider) would otherwise slugify
    to an empty string -- falls back to a short deterministic hash of the
    original value instead, so two different such inputs still get distinct,
    stable `cp_id`s rather than colliding on the same empty fragment.
    """
    slug = SLUG_RE.sub("-", str(value).strip().upper()).strip("-")
    if slug:
        return slug
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12].upper()


def _safe_div(numerator, denominator):
    return numerator / denominator if denominator else 0


def _evaluate_covenant(covenant, ratios):
    """One covenant's PASS/FAIL/UNRESOLVABLE result against `ratios`
    (a single period's ratios dict, e.g. state.json's ratios["FY-Current"]).

    Boundary rule: actual == threshold is a PASS for both minimum and
    maximum covenants (>= and <= both include equality).

    Fallback rule: an unrecognized `metric` (doesn't exactly match a ratios
    key) or an unrecognized `type` (not "minimum"/"maximum") is
    UNRESOLVABLE, never silently skipped -- the covenant still appears in
    the result with actual/headroom_pct left as None.
    """
    metric = covenant.get("metric")
    covenant_type = covenant.get("type")
    threshold = covenant.get("threshold")

    result = {
        "metric": metric,
        "type": covenant_type,
        "threshold": threshold,
        "actual": None,
        "status": "UNRESOLVABLE",
        "headroom_pct": None,
    }

    if covenant_type not in ("minimum", "maximum") or metric not in ratios:
        return result

    actual = ratios[metric]
    result["actual"] = actual

    if covenant_type == "minimum":
        result["headroom_pct"] = _safe_div(actual - threshold, threshold)
        result["status"] = "PASS" if actual >= threshold else "FAIL"
    else:
        result["headroom_pct"] = _safe_div(threshold - actual, threshold)
        result["status"] = "PASS" if actual <= threshold else "FAIL"

    return result


def _evaluate_security(collateral, security_package):
    """Cross-reference collateral assets against the security package taken
    over them, joined on asset_id <-> secures_asset_id.

    Returns (security_gaps: list[str], security_cps: list[{"cp_id", "text"}]).
    An asset can independently fail perfection and ranking; each failure
    gets its own gap entry and its own CP.
    """
    collateral_by_id = {
        asset.get("asset_id"): asset for asset in collateral if asset.get("asset_id")
    }
    security_by_asset_id = {
        charge.get("secures_asset_id"): charge
        for charge in security_package if charge.get("secures_asset_id")
    }

    gaps = []
    cps = []

    for asset_id in collateral_by_id:
        charge = security_by_asset_id.get(asset_id)

        if charge is None:
            gaps.append(
                f"Uncharged Asset: {asset_id} has no corresponding security charge registered."
            )
            cps.append({
                "cp_id": f"SEC-MAPPING-{_slugify(asset_id)}",
                "text": f"Resolution of collateral security mapping discrepancy for asset {asset_id}.",
            })
            continue

        perfection_status = charge.get("perfection_status")
        ranking = charge.get("ranking")

        if perfection_status != "Perfected":
            gaps.append(
                f"Unperfected Security: Asset {asset_id} charge status is "
                f"'{perfection_status}', not Perfected."
            )
            cps.append({
                "cp_id": f"SEC-PERFECT-{_slugify(asset_id)}",
                "text": (
                    "Execution and completion of registration to perfect charge over "
                    f"asset {asset_id} (Current Status: {perfection_status})."
                ),
            })

        if ranking != "First":
            gaps.append(
                f"Subordinate Ranking: Asset {asset_id} charge ranking is "
                f"'{ranking}', not First."
            )
            cps.append({
                "cp_id": f"SEC-PRIORITY-{_slugify(asset_id)}",
                "text": (
                    "Negotiation, execution, and stamping of a formal Intercreditor "
                    f"Deed / Deed of Priority with existing chargeholders for asset "
                    f"{asset_id} (Current Ranking: {ranking})."
                ),
            })

    for secures_asset_id in security_by_asset_id:
        if secures_asset_id not in collateral_by_id:
            gaps.append(
                f"Dangling Reference: {secures_asset_id} does not exist in collateral records."
            )
            cps.append({
                "cp_id": f"SEC-MAPPING-{_slugify(secures_asset_id)}",
                "text": (
                    "Resolution of collateral security mapping discrepancy for asset "
                    f"{secures_asset_id}."
                ),
            })

    return gaps, cps


def _guarantee_cps(guarantees):
    cps = []
    for guarantee in guarantees:
        provider = guarantee.get("provider", "")
        guarantee_type = guarantee.get("type", "Guarantee")
        amount = guarantee.get("amount")
        # Only an absent/blank amount means "no cap specified" -- a
        # legitimate falsy amount like 0 must not be overwritten with
        # "Unlimited Facility", which would materially misstate the CP.
        if amount is None or (isinstance(amount, str) and not amount.strip()):
            amount = "Unlimited Facility"
        cps.append({
            "cp_id": f"GUARANTEE-{_slugify(provider)}",
            "text": f"Execution of {guarantee_type} Guarantee by {provider} for {amount}.",
        })
    return cps


def evaluate_deal_policy(state_dict):
    """Evaluate one deal's state against deterministic credit policy rules.

    Reads (all optional, default to empty): `ratios["FY-Current"]`,
    `collateral` (a flat list of asset dicts, each expected to carry an
    `asset_id`), `security_package` (a flat list of
    {"secures_asset_id", "perfection_status", "ranking"} dicts), `guarantees`
    (a flat list of {"provider", "type", "amount"} dicts), and `covenants`
    (a flat list of {"metric", "type", "threshold"} dicts).

    Returns:
    {
        "covenant_results": [{"metric", "type", "threshold", "actual", "status", "headroom_pct"}, ...],
        "security_gaps": [str, ...],
        "required_conditions_precedent": [{"cp_id", "text"}, ...],
    }
    """
    state_dict = state_dict or {}
    ratios = (state_dict.get("ratios") or {}).get("FY-Current") or {}
    collateral = state_dict.get("collateral") or []
    security_package = state_dict.get("security_package") or []
    guarantees = state_dict.get("guarantees") or []
    covenants = state_dict.get("covenants") or []

    covenant_results = [_evaluate_covenant(covenant, ratios) for covenant in covenants]
    security_gaps, security_cps = _evaluate_security(collateral, security_package)
    guarantee_cps = _guarantee_cps(guarantees)

    required_conditions_precedent = (
        list(STANDARD_CONDITIONS_PRECEDENT) + security_cps + guarantee_cps
    )

    return {
        "covenant_results": covenant_results,
        "security_gaps": security_gaps,
        "required_conditions_precedent": required_conditions_precedent,
    }
