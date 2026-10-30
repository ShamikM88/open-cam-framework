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

Conditions Subsequent (`cs_id`) are this same pattern applied to the other
side of drawdown: post-drawdown, ongoing monitoring obligations (e.g.
periodic MI, covenant-compliance certification, re-confirming security
perfection or guarantor standing), as opposed to CPs, which are pre-
drawdown conditions to satisfy before completion. `cs_id`s are a distinct
namespace from `cp_id`s (always prefixed "CS-", except the always-present
standard ones -- see STANDARD_CONDITIONS_SUBSEQUENT) so the two can never
collide or be mistaken for one another in the Underwriter's structured
output.

Deliberately out of scope for this module (see the PR that introduced CS
tracking for the full disclosure): covenant test-frequency/step-down-over-
tenor modeling, a cure-period/maintenance-vs-incurrence distinction for
covenants, and severity/likelihood weighting of the risk taxonomy. Those
are separate, larger design discussions -- this module only adds Conditions
Subsequent tracking, mirroring the existing Conditions Precedent pattern.
"""
import hashlib
import re
from collections.abc import Iterable
from typing import Any, Literal, NotRequired, TypedDict

from financial_conventions import PERCENT_TYPE_RATIOS
from numeric_validation import validate_finite_numbers
from spreading_builder import FORWARD_PERIOD_KEYS, describe_undefined_ratio
from state_manager import JsonValue

SLUG_RE = re.compile(r"[^A-Z0-9]+")

# The figures a covenant can be tested on (issue #48, decision of 2026-10-22). A covenant names one with `basis`; an
# absent or null `basis` means "borrower", the legacy meaning of every covenant recorded before the field existed.
COVENANT_BASES = ("borrower", "consolidated", "guarantor_group")
DEFAULT_COVENANT_BASIS = "borrower"


class CovenantResult(TypedDict):
    """One covenant's result, keys in this order. `metric`, `type` and `threshold` echo the covenant as recorded, even
    when it is malformed (the result is then UNRESOLVABLE), so they are JSON values and not the happy-path types.
    `basis` is present only when the covenant stated one or the recorded figures are not the borrower's, and sits
    before `actual`."""
    metric: JsonValue
    type: JsonValue
    threshold: JsonValue
    actual: int | float | None
    status: Literal["PASS", "FAIL", "UNRESOLVABLE"]
    headroom_pct: int | float | None
    reason: str | None
    basis: NotRequired[JsonValue]


class ForwardCovenantResult(CovenantResult):
    """A covenant result for a forward year: the same keys, then `year` and `forward_id`."""
    year: str
    forward_id: str


class DownsideBreach(TypedDict):
    """A base-case PASS that fails or cannot be tested under stress. `base_actual` and `threshold` are copied from the
    base-case covenant result, so they carry that result's types (a PASS is always numeric, which a type cannot say);
    `basis` is copied last, and only when the base-case result has one."""
    year: str
    metric: JsonValue
    base_actual: int | float | None
    downside_actual: int | float | None
    threshold: JsonValue
    breach_id: str
    downside_status: Literal["FAIL", "UNRESOLVABLE"]
    reason: str | None
    basis: NotRequired[JsonValue]


class ConditionPrecedent(TypedDict):
    cp_id: str
    text: str


class ConditionSubsequent(TypedDict):
    cs_id: str
    text: str


class PolicyState(TypedDict):
    """What evaluate_deal_policy() returns: all six keys, always, in this order."""
    covenant_results: list[CovenantResult]
    security_gaps: list[str]
    required_conditions_precedent: list[ConditionPrecedent]
    required_conditions_subsequent: list[ConditionSubsequent]
    downside_covenant_breaches: list[DownsideBreach]
    forward_covenant_results: list[ForwardCovenantResult]


# Always required regardless of deal-specific structure.
STANDARD_CONDITIONS_PRECEDENT: list[ConditionPrecedent] = [
    {"cp_id": "KYC-AML", "text": "Standard KYC/AML clearance for all borrowing entities."},
    {"cp_id": "FACILITY-EXECUTION", "text": "Execution of Facility Agreement."},
]

# Always required regardless of deal-specific structure -- the Conditions
# Subsequent analogue of STANDARD_CONDITIONS_PRECEDENT above. Kept
# deliberately qualitative (no invented numeric deadline like "within 30
# days of period end") -- this codebase's grounding rule bars inventing
# figures, and a submission deadline is exactly the kind of deal-specific
# detail this module has no basis to assert.
STANDARD_CONDITIONS_SUBSEQUENT: list[ConditionSubsequent] = [
    {
        "cs_id": "MI-REPORTING",
        "text": (
            "Ongoing submission of periodic financial statements and management "
            "information to support continued monitoring of the facility."
        ),
    },
]


def _slugify(value: object) -> str:
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


def _evaluate_covenant(
    covenant: dict[str, Any], ratios: dict[str, Any], financials: dict[str, Any] | None = None,
    recorded_view: str = DEFAULT_COVENANT_BASIS,
) -> CovenantResult:
    """One covenant's PASS/FAIL/UNRESOLVABLE result against `ratios`
    (a single period's ratios dict, e.g. state.json's ratios["FY-Current"]).
    `financials` is that same period's `financials` dict, used only to explain WHY a ratio is N/A.

    `recorded_view` names the one set of figures `ratios` represents: "borrower" for an ordinary deal, "consolidated"
    when the deal records borrower-supplied consolidated group accounts (`group_basis`, issue #48). The covenant's own
    `basis` (absent or null means "borrower") must equal it; a covenant on a different basis, or on a basis that is not
    one of COVENANT_BASES, is UNRESOLVABLE with a reason, never tested against the figures that happen to be recorded.

    Every result carries a `reason`: None for PASS/FAIL, one sentence for UNRESOLVABLE. For a ratio that is N/A
    because its denominator is zero or negative (issue #169) the sentence names the metric and the denominator,
    e.g. "gross leverage not meaningful: EBITDA is negative". Such a covenant is UNRESOLVABLE -- never PASS, and
    never FAIL either (the ratio is not a number; the covenant cannot be tested) -- and the same holds for a
    minimum and a maximum covenant alike.

    Boundary rule: actual == threshold is a PASS for both minimum and
    maximum covenants (>= and <= both include equality).

    Fallback rule: an unrecognized `metric` (doesn't exactly match a ratios
    key), an unrecognized `type` (not "minimum"/"maximum"), a missing or
    non-numeric `threshold`, or a `metric` whose ratios value is itself
    missing/non-numeric (e.g. `None`, from a ratio spreading_builder.py
    left undefined because its denominator was zero or negative -- see
    evaluate_financial_model()) is UNRESOLVABLE, never silently skipped or
    allowed to crash the comparison below it -- the covenant still appears
    in the result with actual/headroom_pct left as None.

    A threshold of exactly 0 (a legitimate number, unlike a missing one)
    makes "headroom as a % of threshold" mathematically undefined, not
    zero -- headroom_pct is left as None in that case too, but status is
    still resolved by direct comparison rather than left UNRESOLVABLE
    (a zero threshold is a perfectly well-defined comparison, just not one
    with a meaningful percentage). Silently coercing headroom_pct to 0
    would read as "right at the compliance boundary" regardless of how far
    the actual value is from a zero threshold, understating a real
    breach's severity.
    """
    metric = covenant.get("metric")
    covenant_type = covenant.get("type")
    echoed_threshold = covenant.get("threshold")
    threshold: object = echoed_threshold
    basis = covenant.get("basis")
    basis_stated = basis is not None
    if not basis_stated:
        basis = DEFAULT_COVENANT_BASIS

    # A result names its basis when the covenant stated one, or when the recorded figures are not the borrower's (so
    # the default is visible where it matters). An ordinary deal's results keep exactly their earlier shape.
    result: CovenantResult
    if basis_stated or recorded_view != DEFAULT_COVENANT_BASIS:
        result = {
            "metric": metric,
            "type": covenant_type,
            "threshold": echoed_threshold,
            "basis": basis,
            "actual": None,
            "status": "UNRESOLVABLE",
            "headroom_pct": None,
            "reason": None,
        }
    else:
        result = {
            "metric": metric,
            "type": covenant_type,
            "threshold": echoed_threshold,
            "actual": None,
            "status": "UNRESOLVABLE",
            "headroom_pct": None,
            "reason": None,
        }

    if basis not in COVENANT_BASES:
        result["reason"] = (f"unrecognized covenant basis {basis!r} "
                            "(expected 'borrower', 'consolidated' or 'guarantor_group')")
        return result
    if basis != recorded_view:
        result["reason"] = (f"the covenant is on the {basis} basis but the recorded figures are the {recorded_view} "
                            f"view, and no {basis} figures are recorded; state the covenant's basis, or record "
                            "figures for it")
        return result

    if covenant_type not in ("minimum", "maximum"):
        result["reason"] = f"unrecognized covenant type {covenant_type!r} (expected 'minimum' or 'maximum')"
        return result
    if not (isinstance(threshold, (int, float)) and not isinstance(threshold, bool)):
        result["reason"] = "the covenant threshold is missing or not a number"
        return result
    if metric not in ratios:
        result["reason"] = f"metric {metric!r} is not among this period's computed ratios"
        return result

    actual = ratios[metric]
    actual_is_numeric = isinstance(actual, (int, float)) and not isinstance(actual, bool)
    explanation = describe_undefined_ratio(metric, financials)
    if explanation is not None:
        # A RECORDED denominator is zero or negative, so the ratio is not meaningful whatever number is stored next to
        # it: a ratio recorded as given in analyst-supplied mode, or checkpointed by an earlier version that divided by
        # a negative EBITDA, must not pass a covenant either (issue #169). The stored figure is ignored.
        result["reason"] = explanation + (f" (the recorded value {actual!r} is ignored)" if actual_is_numeric else "")
        return result
    if metric in PERCENT_TYPE_RATIOS:
        unit_mismatches = []
        if actual_is_numeric and abs(actual) > 10:
            unit_mismatches.append(f"actual ratio {actual!r}")
        if abs(threshold) > 10:
            unit_mismatches.append(f"covenant threshold {threshold!r}")
        if unit_mismatches:
            result["reason"] = (
                f"{metric} {' and '.join(unit_mismatches)} looks like a percent; percent-type ratios and their "
                "covenant thresholds are stored as fractions (0.87 means 87%). Confirm the unit before comparing."
            )
            return result

    if not actual_is_numeric:
        result["reason"] = f"{metric} has no computed value for this period"
        return result

    result["actual"] = actual

    if covenant_type == "minimum":
        if threshold:
            # The threshold's absolute value keeps headroom's sign meaningful for a negative
            # threshold too (issue #506): positive when the covenant passes, negative when it fails.
            result["headroom_pct"] = (actual - threshold) / abs(threshold)
        result["status"] = "PASS" if actual >= threshold else "FAIL"
    else:
        if threshold:
            result["headroom_pct"] = (threshold - actual) / abs(threshold)
        result["status"] = "PASS" if actual <= threshold else "FAIL"

    return result


def _unique_id(prefix: str, base_slug: str, used_ids: set[str]) -> str:
    """Collision-avoiding id builder shared by every CP/CS generator below:
    produce "<prefix>-<base_slug>", and if that's already in `used_ids`
    append a numeric suffix (-2, -3, ...) until it's unique. `used_ids` is
    checked (and then updated with the chosen id) against every id already
    handed out in this call, not just ones sharing the same base_slug -- a
    naturally-unique candidate could otherwise silently collide with
    another id's own disambiguated suffix (e.g. a second, differently-named
    entity whose slug happens to equal an earlier collision's "-2" form).
    """
    candidate = f"{prefix}-{base_slug}"
    suffix = 2
    while candidate in used_ids:
        candidate = f"{prefix}-{base_slug}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _evaluate_security(
    collateral: Iterable[dict[str, Any]], security_package: Iterable[dict[str, Any]],
) -> tuple[list[str], list[ConditionPrecedent]]:
    """Cross-reference collateral assets against the security package taken
    over them, joined on asset_id <-> secures_asset_id.

    An asset can have more than one charge registered against it (e.g. a
    senior and a subordinate charge from different points in the deal's
    history) -- every charge for a given asset is evaluated, not just one,
    so a non-compliant charge is never silently dropped just because
    another (compliant or not) charge for the same asset also exists.

    Returns (security_gaps: list[str], security_cps: list[{"cp_id", "text"}]).
    An asset (or a single charge on it) can independently fail perfection
    and ranking; each failure gets its own gap entry and its own CP.
    """
    collateral_by_id = {
        asset.get("asset_id"): asset for asset in collateral if asset.get("asset_id")
    }
    charges_by_asset_id: dict[Any, list[dict[str, Any]]] = {}
    for charge in security_package:
        asset_id = charge.get("secures_asset_id")
        if asset_id:
            charges_by_asset_id.setdefault(asset_id, []).append(charge)

    gaps: list[str] = []
    cps: list[ConditionPrecedent] = []

    # A per-prefix candidate is checked against every cp_id already
    # assigned anywhere in this call, not just other charges on the same
    # asset -- a multi-charge asset's disambiguated suffix (e.g.
    # "...-AST-001-2") could otherwise collide with a *different* asset
    # whose own id happens to naturally slugify to that exact string (see
    # _unique_id()'s own docstring for the general form of this).
    used_ids: set[str] = set()

    def unique_cp_id(prefix: str, base_slug: str) -> str:
        return _unique_id(prefix, base_slug, used_ids)

    for asset_id in collateral_by_id:
        charges = charges_by_asset_id.get(asset_id)
        asset_slug = _slugify(asset_id)

        if not charges:
            gaps.append(
                f"Uncharged Asset: {asset_id} has no corresponding security charge registered."
            )
            cps.append({
                "cp_id": unique_cp_id("SEC-MAPPING", asset_slug),
                "text": f"Resolution of collateral security mapping discrepancy for asset {asset_id}.",
            })
            continue

        for charge in charges:
            perfection_status = charge.get("perfection_status")
            ranking = charge.get("ranking")

            if perfection_status != "Perfected":
                gaps.append(
                    f"Unperfected Security: Asset {asset_id} charge status is "
                    f"'{perfection_status}', not Perfected."
                )
                cps.append({
                    "cp_id": unique_cp_id("SEC-PERFECT", asset_slug),
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
                    "cp_id": unique_cp_id("SEC-PRIORITY", asset_slug),
                    "text": (
                        "Negotiation, execution, and stamping of a formal Intercreditor "
                        f"Deed / Deed of Priority with existing chargeholders for asset "
                        f"{asset_id} (Current Ranking: {ranking})."
                    ),
                })

    for secures_asset_id in charges_by_asset_id:
        if secures_asset_id not in collateral_by_id:
            gaps.append(
                f"Dangling Reference: {secures_asset_id} does not exist in collateral records."
            )
            cps.append({
                "cp_id": unique_cp_id("SEC-MAPPING", _slugify(secures_asset_id)),
                "text": (
                    "Resolution of collateral security mapping discrepancy for asset "
                    f"{secures_asset_id}."
                ),
            })

    return gaps, cps


def _guarantee_cps(guarantees: Iterable[dict[str, Any]]) -> list[ConditionPrecedent]:
    """One CP per guarantee. `cp_id` is keyed on an explicit `guarantee_id`
    when supplied (and actually present -- a falsy-but-real id like `0` is
    honored, not treated as absent), otherwise on the provider -- unchanged
    from before for the common case of one guarantee per provider. A
    provider alone collides whenever the same person or entity guarantees
    more than one facility or amount, though -- a realistic deal structure,
    not an edge case -- so a colliding guarantee's cp_id gets a numeric
    suffix (-2, -3, ...) chosen against a running set of every cp_id
    already assigned so far in this call, not just other guarantees
    sharing its own base slug. That distinction matters: a naturally-
    unique guarantee whose slug happens to equal another guarantee's
    disambiguated suffix (e.g. provider "Acme Corp 1" naturally slugifying
    to the same string a second "Acme Corp" guarantee would be suffixed to)
    would otherwise silently collide with it.

    cp_id is deterministic and repeatable for a fixed `guarantees` list
    (the same list, called twice, always produces the same result -- see
    the module docstring), but a colliding guarantee's specific numeric
    suffix depends on its position relative to every other guarantee in
    the list at the time of the call. If this deal's guarantees list can be
    edited (reordered, or a new colliding entry inserted) between when an
    Underwriter reports a cp_id and a later evaluate_deal_policy() call,
    supply this guarantee's own stable `guarantee_id` explicitly rather
    than relying on positional disambiguation to keep that identity fixed.
    """
    def has_explicit_id(guarantee: dict[str, Any]) -> bool:
        guarantee_id = guarantee.get("guarantee_id")
        return guarantee_id is not None and not (
            isinstance(guarantee_id, str) and not guarantee_id.strip()
        )

    used_ids: set[str] = set()
    cps: list[ConditionPrecedent] = []
    for guarantee in guarantees:
        provider = guarantee.get("provider", "")
        guarantee_type = guarantee.get("type")
        if isinstance(guarantee_type, str) and not guarantee_type.strip():
            guarantee_type = None
        amount = guarantee.get("amount")
        # Only an absent/blank amount means "not recorded" -- a legitimate
        # falsy amount like 0 must not be overwritten with the recorded
        # "an amount to be confirmed" wording, which would materially misstate the CP.
        if amount is None or (isinstance(amount, str) and not amount.strip()):
            amount = "an amount to be confirmed"

        base_slug = _slugify(guarantee["guarantee_id"] if has_explicit_id(guarantee) else provider)

        cp_id = _unique_id("GUARANTEE", base_slug, used_ids)

        if guarantee_type is None:
            guarantee_label = "Guarantee"
        elif isinstance(guarantee_type, str) and "guarantee" in guarantee_type.lower():
            # The analyst already named it a guarantee ("Corporate Guarantee",
            # "Guarantee Bond"): use the type as written rather than appending
            # the word again.
            guarantee_label = guarantee_type
        else:
            guarantee_label = f"{guarantee_type} Guarantee"

        cps.append({
            "cp_id": cp_id,
            "text": f"Execution of {guarantee_label} by {provider} for {amount}.",
        })
    return cps


def _covenant_compliance_cs(covenants: list[dict[str, Any]] | None) -> list[ConditionSubsequent]:
    """One CS for the whole deal when `covenants` is non-empty -- not one
    per covenant. The underlying obligation (certify ongoing compliance
    with the covenant package under the Facility Agreement) is a single
    standing undertaking, not a separate administrative duty per metric; a
    per-covenant CS would also need its own id disambiguation and would
    churn (new ids appearing/disappearing) every time the covenant package
    itself is edited, for no real monitoring benefit over one deal-level
    certification obligation that already covers "all financial covenants"
    by reference.
    """
    if not covenants:
        return []
    return [{
        "cs_id": "CS-COVENANT-COMPLIANCE",
        "text": (
            "Ongoing certification of continued compliance with all financial "
            "covenants set out in the Facility Agreement."
        ),
    }]


def _security_reconfirmation_cs(security_package: Iterable[dict[str, Any]]) -> list[ConditionSubsequent]:
    """One CS per currently-Perfected charge -- mirrors _evaluate_security()'s
    own per-charge granularity (a multi-charge asset can have more than one
    charge independently reach Perfected status, each its own standing
    reconfirmation obligation). A charge that is *not* Perfected today is
    already covered by its own Conditions Precedent (see _evaluate_security()
    above) -- it has nothing to reconfirm post-drawdown yet, so it gets no CS
    entry here.

    `used_ids` is scoped to this function only (distinct "CS-SEC-REPERFECT-"
    prefix from every other CS/CP generator), matching _evaluate_security()'s
    own collision-avoidance pattern for the equivalent CP.
    """
    used_ids: set[str] = set()
    cs_list: list[ConditionSubsequent] = []

    def unique_cs_id(base_slug: str) -> str:
        return _unique_id("CS-SEC-REPERFECT", base_slug, used_ids)

    for charge in security_package:
        if charge.get("perfection_status") != "Perfected":
            continue
        asset_id = charge.get("secures_asset_id")
        cs_list.append({
            "cs_id": unique_cs_id(_slugify(asset_id)),
            "text": (
                "Ongoing (e.g. annual) reconfirmation of the continued perfection "
                f"status of the security charge over asset {asset_id}."
            ),
        })
    return cs_list


def _guarantee_standing_cs(guarantees: Iterable[dict[str, Any]]) -> list[ConditionSubsequent]:
    """One CS per guarantee -- mirrors _guarantee_cps()'s own one-per-
    guarantee granularity and id-disambiguation approach (a guarantee's own
    `guarantee_id` when supplied and actually present, else its `provider`,
    suffixed on collision). `used_ids` is scoped to this function only
    (distinct "CS-GUARANTEE-" prefix).
    """
    def has_explicit_id(guarantee: dict[str, Any]) -> bool:
        guarantee_id = guarantee.get("guarantee_id")
        return guarantee_id is not None and not (
            isinstance(guarantee_id, str) and not guarantee_id.strip()
        )

    used_ids: set[str] = set()
    cs_list: list[ConditionSubsequent] = []
    for guarantee in guarantees:
        provider = guarantee.get("provider", "")
        base_slug = _slugify(guarantee["guarantee_id"] if has_explicit_id(guarantee) else provider)

        cs_id = _unique_id("CS-GUARANTEE", base_slug, used_ids)

        cs_list.append({
            "cs_id": cs_id,
            "text": (
                "Ongoing (e.g. annual) reconfirmation of the continued standing of "
                f"guarantor {provider}."
            ),
        })
    return cs_list


def _evaluate_downside_covenants(
    covenants: Iterable[dict[str, Any]], all_ratios: dict[str, Any] | None, downside_ratios: dict[str, Any] | None,
    base_financials: dict[str, Any] | None = None, downside_financials: dict[str, Any] | None = None,
    unavailable: dict[str, str] | None = None, recorded_view: str = DEFAULT_COVENANT_BASIS,
) -> list[DownsideBreach]:
    """For each covenant, independently re-check it against every forward
    period that has both a base-case and a downside-case ratio set. Where
    the base case PASSes but the downside (stressed) case FAILs, record it
    as a downside breach.

    This is purely additive on top of _evaluate_covenant() -- it neither
    reads nor changes covenant_results's existing meaning (the current-
    period gate both orchestrator.py and policy_checks.py already enforce);
    it's a second, independent pass of the same per-covenant evaluation
    against different (forward-year, shocked) ratio inputs.

    `all_ratios`/`downside_ratios` are the full multi-period ratios dicts
    (e.g. state.json's `ratios` and `downside_case["ratios"]`) -- not
    pre-sliced to one period, unlike `_evaluate_covenant`'s own `ratios`
    argument, since this needs every forward period present in both.

    A covenant that PASSes in the base case but is UNRESOLVABLE in the downside case also counts (issue #169): if
    the stress drives EBITDA or equity to zero or below, the leverage/gearing ratio is N/A, which is not a pass --
    and silently dropping it here would hide exactly the severest stress outcome. Such an entry has
    `downside_actual` None, `downside_status` "UNRESOLVABLE" and a `reason` naming the metric and denominator;
    an ordinary FAIL has `downside_status` "FAIL" and `reason` None.

    `unavailable` ({year: reason}, from `downside_case["unavailable"]`, issue #124) names forward years for which a
    downside analysis was wanted but could not be produced (an analyst-supplied forecast the framework's stress
    shocks cannot meaningfully act on, and no scenario of the analyst's own). It is the same situation as a stress
    that makes a ratio N/A: a covenant that PASSes in that year's base case cannot be tested under stress, so it is
    recorded the same way (`downside_status` "UNRESOLVABLE", `downside_actual` None, the reason naming the year and
    why), and the draft must address it. It is never a silent absence and never a pass.

    Returns a list of {"year", "metric", "base_actual", "downside_actual",
    "threshold", "breach_id", "downside_status", "reason"} with an optional
    `basis` key copied from the base-case covenant result only when present.
    `breach_id` is built from _slugify() (the
    same stable-identifier convention as `cp_id`), so a downside breach for
    "FY+2"/"dscr" always slugifies to the same base id (e.g.
    "DOWNSIDE-FY-2-DSCR" -- "+" is not alphanumeric, so it slugifies to
    "-", the same as every other cp_id in this module). Multiple covenants
    on the same metric in one year receive a stable numeric suffix (e.g.
    "DOWNSIDE-FY-1-DSCR-2"); a single covenant keeps its existing ID.
    """
    breaches: list[DownsideBreach] = []
    used_ids: set[str] = set()
    all_ratios = all_ratios or {}
    downside_ratios = downside_ratios or {}
    unavailable = unavailable if isinstance(unavailable, dict) else {}
    for period in sorted(downside_ratios or {}):
        if period not in (all_ratios or {}):
            continue
        base_ratios_for_period = all_ratios[period] or {}
        downside_ratios_for_period = downside_ratios[period] or {}

        base_financials_for_period = _period_dict(base_financials, period)
        downside_financials_for_period = _period_dict(downside_financials, period)

        for covenant in covenants:
            base_result = _evaluate_covenant(covenant, base_ratios_for_period, base_financials_for_period,
                                             recorded_view)
            downside_result = _evaluate_covenant(covenant, downside_ratios_for_period,
                                                 downside_financials_for_period, recorded_view)

            downside_status = downside_result["status"]
            if base_result["status"] == "PASS" and downside_status in ("FAIL", "UNRESOLVABLE"):
                metric = covenant.get("metric")
                breach: DownsideBreach = {
                    "year": period,
                    "metric": metric,
                    "base_actual": base_result["actual"],
                    "downside_actual": downside_result["actual"],
                    "threshold": base_result["threshold"],
                    "breach_id": _unique_id("DOWNSIDE", f"{_slugify(period)}-{_slugify(metric)}", used_ids),
                    "downside_status": downside_status,
                    "reason": downside_result["reason"],
                }
                if "basis" in base_result:
                    breach["basis"] = base_result["basis"]
                breaches.append(breach)

    for period in sorted(unavailable if isinstance(unavailable, dict) else {}):
        base_ratios_for_period = (all_ratios or {}).get(period)
        if period in (downside_ratios or {}) or not isinstance(base_ratios_for_period, dict) \
                or not base_ratios_for_period:
            continue
        for covenant in covenants:
            base_result = _evaluate_covenant(covenant, base_ratios_for_period,
                                             _period_dict(base_financials, period), recorded_view)
            if base_result["status"] == "PASS":
                metric = covenant.get("metric")
                breach = {
                    "year": period,
                    "metric": metric,
                    "base_actual": base_result["actual"],
                    "downside_actual": None,
                    "threshold": base_result["threshold"],
                    "breach_id": _unique_id("DOWNSIDE", f"{_slugify(period)}-{_slugify(metric)}", used_ids),
                    "downside_status": "UNRESOLVABLE",
                    "reason": f"downside analysis unavailable for {period}: {unavailable[period]}",
                }
                if "basis" in base_result:
                    breach["basis"] = base_result["basis"]
                breaches.append(breach)

    return breaches


def _evaluate_forward_covenants(
    covenants: Iterable[dict[str, Any]], all_ratios: object, base_financials: object = None,
    recorded_view: str = DEFAULT_COVENANT_BASIS,
) -> list[ForwardCovenantResult]:
    """Every covenant against every forward BASE-CASE year the deal has projected (issue #176).

    Policy: a covenant applies to each forward year (`FY+1`, `FY+2`, `FY+3`) that has a recorded, non-empty ratio
    set in `all_ratios`. A year with no projection (absent, null or empty) is not evaluated -- there is nothing to
    test, and the list says nothing about it. state.json carries no covenant tenor, so "covered by the projection"
    is the whole definition: a covenant that really ends earlier is still reported for the later projected years.

    This is a separate, complete record next to `covenant_results` (which stays FY-Current only, and which
    `policy_checks.py` turns into reject reasons) and `downside_covenant_breaches` (base PASS -> stressed
    FAIL/UNRESOLVABLE only). It is DISCLOSURE ONLY: nothing in the code-enforced check reads it, so a forward-year
    FAIL or UNRESOLVABLE never rejects a deal by itself, but it can never go unrecorded either -- before this, a
    forward year whose own base-case ratio was N/A (or failing) appeared nowhere.

    Each entry is exactly `_evaluate_covenant()`'s result (metric, type, threshold, actual, status, headroom_pct,
    reason -- same #169 rules: a ratio whose denominator is zero or negative is UNRESOLVABLE, never PASS) plus
    `year` and a stable `forward_id`, "FORWARD-<year>-<metric>" built from _slugify() like `breach_id`, with a
    numeric suffix when two covenants name the same metric in the same year. Results are in year order, then
    covenant order, so the same input always gives the same list.
    """
    if not isinstance(all_ratios, dict):
        return []
    used_ids: set[str] = set()
    results: list[ForwardCovenantResult] = []
    for period in FORWARD_PERIOD_KEYS:
        period_ratios = all_ratios.get(period)
        if not isinstance(period_ratios, dict) or not period_ratios:
            continue
        period_financials = _period_dict(base_financials, period)
        for covenant in covenants:
            result = _evaluate_covenant(covenant, period_ratios, period_financials, recorded_view)
            forward_result: ForwardCovenantResult = {
                **result,
                "year": period,
                "forward_id": _unique_id(
                    "FORWARD", f"{_slugify(period)}-{_slugify(covenant.get('metric'))}", used_ids),
            }
            results.append(forward_result)
    return results


def _period_dict(financials: object, period: str) -> dict[str, Any] | None:
    """One period's `financials` dict, or None if `financials` is absent or not shaped like {period: {...}} --
    it only feeds an explanatory sentence, so a malformed value must never turn into a new failure here."""
    value = financials.get(period) if isinstance(financials, dict) else None
    return value if isinstance(value, dict) else None


def evaluate_deal_policy(state_dict: dict[str, Any] | None) -> PolicyState:
    """Evaluate one deal's state against deterministic credit policy rules.

    Reads (all optional, default to empty): `ratios` (the full multi-period
    dict -- `ratios["FY-Current"]` drives `covenant_results` below exactly
    as before; any `"FY+1"`/`"FY+2"`/`"FY+3"` entries feed `forward_covenant_results` and the downside
    check), `collateral` (a flat list of asset dicts, each expected to
    carry an `asset_id`), `security_package` (a flat list of
    {"secures_asset_id", "perfection_status", "ranking"} dicts), `guarantees`
    (a flat list of {"provider", "type", "amount"} dicts), `covenants`
    (a flat list of {"metric", "type", "threshold"} dicts), and
    `downside_case` ({"financials": {...}, "ratios": {...}} for forward
    periods only -- see spreading_builder.evaluate_downside_case()).

    Returns:
    {
        "covenant_results": [{"metric", "type", "threshold", "actual", "status", "headroom_pct", "reason"}, ...],
        "security_gaps": [str, ...],
        "required_conditions_precedent": [{"cp_id", "text"}, ...],
        "required_conditions_subsequent": [{"cs_id", "text"}, ...],
        "downside_covenant_breaches": [{"year", "metric", "base_actual", "downside_actual", "threshold", "breach_id",
                                        "downside_status", "reason"}, ...],
        "forward_covenant_results": [{"year", "forward_id", "metric", "type", "threshold", "actual", "status",
                                      "headroom_pct", "reason"}, ...],
    }
    """
    if state_dict:
        for key in (
            "financials", "ratios", "multi_period_financials", "analyst_supplied_financials",
            "stress_assumptions", "downside_case", "covenants", "collateral", "security_package",
        ):
            if key in state_dict:
                validate_finite_numbers(state_dict[key], path=f"policy state[{key!r}]")
    state_dict = state_dict or {}
    all_ratios = state_dict.get("ratios") or {}
    ratios = all_ratios.get("FY-Current") or {}
    collateral = state_dict.get("collateral") or []
    security_package = state_dict.get("security_package") or []
    guarantees = state_dict.get("guarantees") or []
    covenants = state_dict.get("covenants") or []
    downside_ratios = (state_dict.get("downside_case") or {}).get("ratios") or {}

    base_financials = state_dict.get("financials")
    # A recorded group_basis means the deal's recorded figures are borrower-supplied consolidated group accounts
    # (issue #48, case A); otherwise they are the borrower's own. A covenant is tested only on its own basis.
    recorded_view = "consolidated" if isinstance(state_dict.get("group_basis"), dict) else DEFAULT_COVENANT_BASIS
    covenant_results = [_evaluate_covenant(covenant, ratios, _period_dict(base_financials, "FY-Current"), recorded_view)
                        for covenant in covenants]
    security_gaps, security_cps = _evaluate_security(collateral, security_package)
    guarantee_cps = _guarantee_cps(guarantees)
    downside_covenant_breaches = _evaluate_downside_covenants(
        covenants, all_ratios, downside_ratios, base_financials,
        (state_dict.get("downside_case") or {}).get("financials"),
        (state_dict.get("downside_case") or {}).get("unavailable"), recorded_view)
    forward_covenant_results = _evaluate_forward_covenants(covenants, all_ratios, base_financials, recorded_view)

    required_conditions_precedent = (
        list(STANDARD_CONDITIONS_PRECEDENT) + security_cps + guarantee_cps
    )
    required_conditions_subsequent = (
        list(STANDARD_CONDITIONS_SUBSEQUENT)
        + _covenant_compliance_cs(covenants)
        + _security_reconfirmation_cs(security_package)
        + _guarantee_standing_cs(guarantees)
    )

    return {
        "covenant_results": covenant_results,
        "security_gaps": security_gaps,
        "required_conditions_precedent": required_conditions_precedent,
        "required_conditions_subsequent": required_conditions_subsequent,
        "downside_covenant_breaches": downside_covenant_breaches,
        "forward_covenant_results": forward_covenant_results,
    }
