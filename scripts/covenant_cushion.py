"""Covenant cushion: EBITDA break-evens and single-shock reverse stress, read-only.

Built under issue #470 from the design in issue #196 (comment 6269047318,
accepted) and the plan sign-off (comment 6277984692, item 1). One job: show the
remaining mechanical distance between a recorded covenant's PASS and its first
non-PASS result, without recalculating a ratio in prose and without writing
anything. Every calculation is done by this script from the recorded state; the
policy engine's statuses and limits are reused verbatim through
policy_engine.evaluate_single_covenant(), never re-implemented here.

What it reports, per recorded covenant and per period (FY-Current plus the
forward years the policy engine also tests; forward years are disclosure
only):

- the covenant row exactly as the policy engine records it (metric, type,
  threshold, actual, status, headroom_pct);
- the exact EBITDA break-even for the four EBITDA-driven covenants (DSCR, gross
  leverage, net debt / EBITDA, EBITDA / interest cover), with the side that
  satisfies the covenant, or the reason no boundary exists (zero/negative ratio
  denominator, zero threshold on a maximum covenant, a metric outside the four,
  or no recorded raw line items for the period);
- for each of the three recorded shocks (revenue haircut, opex increase, interest-rate
  bump; there is no cost-of-sales shock), two single-shock reverse-stress searches: the
  base scenario (all shocks at zero) and the downside scenario (the other shocks held at
  their recorded settings, the searched shock starting from its recorded setting). The
  recorded stress vector must pass the shared domain validator (the write path's own
  contract): a present malformed or negative assumption refuses the whole report with
  its reason rather than being read as a zero shock. Each search brackets the
  first non-PASS point (FAIL or UNRESOLVABLE alike) by expansion then
  bisection and reports the pass/non-pass bracket and its tolerance, never a
  point of false precision. A covenant whose ratio the shock cannot move is
  reported "not moved"; a driver whose predicate is non-monotone, a model point
  the spreading builder refuses, or a crossing outside the documented bound is
  reported UNAVAILABLE with its reason.

An N/A ratio (zero or negative denominator) keeps the covenant UNRESOLVABLE and
is never converted into a numerical cushion; a baseline that is already FAIL or
UNRESOLVABLE reports zero remaining cushion with no search. A period whose
figures are analyst-supplied, or whose raw line items are absent, reports the
break-even and the searches as unavailable with the reason: the framework's
shock model does not own figures it did not compute.

Read-only: this script never writes a deal file, state, draft, manifest or any
output file; the report is JSON on stdout. Exit 0 for a produced report; exit 1
with exactly one "error: ..." stderr line for an unreadable or invalid state or
an expected input error; exit 2 for argparse usage errors. No Anthropic
dependency. Formulas: docs/formula-register.md F-059..F-063.
"""
import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping

from policy_engine import DEFAULT_COVENANT_BASIS, evaluate_single_covenant
from numeric_validation import validate_stress_assumptions
from spreading_builder import apply_stress_shocks, evaluate_financial_model
from state_manager import (StateError, find_deal, read_state, sanitize_path_component,
                           state_path, validate_iso_date_str, validate_state)

# The state keys this script reads (and only these; the report is derived, never stored).
# `group_basis` is read to determine the recorded view exactly as the policy engine does;
# the design comment listed the other nine and omitted it (see the #470 report).
STATE_KEYS_READ = (
    "financials", "ratios", "financials_source", "forecast_source",
    "analyst_supplied_financials", "multi_period_financials", "stress_assumptions",
    "downside_case", "covenants", "group_basis",
)
# forecast_source has no shape in state_manager.STATE_SHAPES, so it is read but not
# shape-validated with the rest (it is disclosure-only here).
VALIDATED_STATE_KEYS = tuple(key for key in STATE_KEYS_READ if key != "forecast_source")

FORWARD_PERIOD_KEYS = ("FY+1", "FY+2", "FY+3")

# The three recorded shocks, their units, and the documented search parameters: expansion
# step, hard bound and bisection tolerance. The revenue haircut is searched only over
# 0-100%; the opex and interest bounds are documented unsupported boundaries (a crossing
# beyond them is reported, not chased).
SHOCK_SPECS: dict[str, dict[str, float | str]] = {
    "revenue_haircut_pct": {"unit": "percent of revenue", "step": 5.0, "bound": 100.0, "tolerance": 0.01},
    "opex_increase_pct": {"unit": "percent of admin expenses", "step": 10.0, "bound": 10000.0, "tolerance": 0.01},
    "interest_rate_bump_bps": {"unit": "basis points of interest rate", "step": 100.0, "bound": 10000.0, "tolerance": 0.5},
}

# The four covenant families an exact EBITDA break-even is defined for. "EBITDA/Interest"
# is the workbook alias of ebitda_interest_cover; EBIT/Interest is a different, EBIT-based
# metric and is deliberately not one of the four (the EBITDA basis is issue #504, D-a/D-d).
BREAK_EVEN_METRICS = ("dscr", "gross_leverage", "net_debt_to_ebitda",
                      "ebitda_interest_cover", "EBITDA/Interest")

BASIS_NOTES = (
    "The DSCR here is the EBITDA-based DSCR the code computes today (decision D-d, "
    "issue #504); a cash-flow DSCR is a separate future issue (#521).",
    "EBITDA is before exceptional items and includes other income (decision D-a, issue #504).",
)

LIMITS = (
    "This report is the mechanical distance to a covenant's first non-PASS result under one "
    "shock at a time. It is not a credit decision, recommendation or approval.",
    "Forward-year rows are disclosure only, exactly as the policy engine records them.",
    "An N/A ratio stays UNRESOLVABLE and is never converted into a numerical cushion.",
    "Only the three recorded shocks are searched (revenue haircut, opex increase, interest-rate "
    "bump); there is no cost-of-sales shock.",
    "The search returns a pass/non-pass bracket and its tolerance, not a point of false precision.",
    "The policy check (scripts/policy_check.py) remains the authoritative record; this report "
    "writes nothing.",
)


def _raw_number(raw: Mapping[str, object], key: str) -> float:
    """One raw line item as a float, defaulting to 0 exactly as the model's g() does."""
    try:
        return float(raw.get(key) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} is not a number: {exc}") from exc


def _total_debt(raw: Mapping[str, object]) -> float:
    """The raw debt aggregate the model uses for total_debt/gross_leverage (F-016/F-018)."""
    return sum(_raw_number(raw, key) for key in ("current_debt", "overdraft", "long_term_debt", "loan_notes"))


def ebitda_break_even(covenant: Mapping[str, object], raw: Mapping[str, object] | None) -> dict[str, object]:
    """The exact EBITDA at which a covenant switches status, or the reason none exists.

    The passing side follows the covenant's recorded type, exactly as the policy engine
    evaluates it (a minimum covenant passes when the ratio is at or above the threshold, a
    maximum when it is at or below; equality is PASS for either), combined with the ratio's
    direction as EBITDA rises: DSCR and EBITDA / interest cover rise with EBITDA, so a
    minimum covenant passes at-or-above the boundary and a maximum at-or-below; the leverage
    ratios fall over positive debt and rise over net cash (a negative numerator), mirroring
    the two. A boundary at or below zero EBITDA is outside the meaningful region (the ratio
    is defined only for EBITDA > 0), so the side names where the testable points sit and a
    note says whether the covenant passes at every testable EBITDA or at none; a zero
    numerator makes the ratio 0 at every EBITDA and is reported the same way. The threshold
    uses the policy engine's own numeric contract: an int or float, never a boolean or a
    numeric string, so the report never presents a boundary for a row the engine records as
    UNRESOLVABLE. Register entries F-059..F-062.
    """
    metric = covenant.get("metric")
    type_ = covenant.get("type")
    threshold = covenant.get("threshold")
    result: dict[str, object] = {"metric": metric, "break_even_ebitda": None,
                                "pass_side": None, "note": None, "reason": None}
    if not isinstance(raw, Mapping):
        result["reason"] = "no recorded raw line items for this period; the break-even cannot be derived"
        return result
    if type_ not in ("minimum", "maximum"):
        result["reason"] = (f"covenant type {type_!r} is neither minimum nor maximum; "
                           "no EBITDA boundary is defined")
        return result
    if metric not in BREAK_EVEN_METRICS:
        result["reason"] = (f"metric {metric!r} is not one of the four EBITDA break-even covenants "
                            "(dscr, gross leverage, net debt / EBITDA, EBITDA / interest)")
        return result
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        result["reason"] = (f"threshold {threshold!r} is not a recorded number (the policy engine "
                            "accepts only an int or a float, never a boolean or a numeric string); "
                            "no boundary is defined")
        return result
    threshold_value = float(threshold)

    try:
        if metric in ("dscr", "ebitda_interest_cover", "EBITDA/Interest"):
            if metric == "dscr":
                denominator = _raw_number(raw, "interest_paid") + _raw_number(raw, "scheduled_principal")
                label = "debt service (interest paid plus scheduled principal)"
            else:
                denominator = _raw_number(raw, "interest_paid")
                label = "interest paid"
            if denominator <= 0:
                result["reason"] = (f"{label} is zero or negative, so the ratio is N/A and the covenant "
                                    "is UNRESOLVABLE for every EBITDA; no break-even exists")
                return result
            result["break_even_ebitda"] = threshold_value * denominator
            # The ratio rises with EBITDA, so a minimum covenant passes at-or-above the boundary
            # and a maximum at-or-below, exactly as the engine evaluates the recorded type.
            result["pass_side"] = "at_or_above" if type_ == "minimum" else "at_or_below"  # noqa: S105
            return result

        numerator = _total_debt(raw)
        if metric == "net_debt_to_ebitda":
            numerator -= _raw_number(raw, "cash")
    except ValueError as exc:
        result["reason"] = f"a raw line item is not a number ({exc}); no boundary is defined"
        return result
    if threshold_value == 0:
        result["reason"] = ("the threshold is zero; a ratio of the form (debt or net debt) / EBITDA "
                            "has no EBITDA boundary (the ratio would have to equal zero)")
        return result
    boundary = numerator / threshold_value
    result["break_even_ebitda"] = boundary
    if numerator == 0:
        # The ratio is 0 at every EBITDA, so no EBITDA switches the covenant's status; which
        # side of the zero-EBITDA boundary passes depends only on the recorded type and the
        # threshold's sign, never on a fixed direction.
        passes_at_testable = (type_ == "minimum") == (threshold_value < 0)
        result["pass_side"] = "at_or_above" if passes_at_testable else "at_or_below"  # noqa: S105
        result["note"] = ("the numerator is zero, so the ratio is 0 at every EBITDA and the boundary "
                          "is at zero EBITDA: the covenant "
                          + ("passes at every testable EBITDA (EBITDA > 0)"
                             if passes_at_testable
                             else "cannot pass at any testable EBITDA (EBITDA > 0)"))
        return result
    if boundary > 0:
        # Inside the meaningful region the ratio's direction in EBITDA is fixed by the numerator's
        # sign, and the passing side follows the covenant's recorded type.
        ratio_rises = numerator < 0
        if type_ == "minimum":
            result["pass_side"] = "at_or_above" if ratio_rises else "at_or_below"  # noqa: S105
        else:
            result["pass_side"] = "at_or_below" if ratio_rises else "at_or_above"  # noqa: S105
        return result
    # A boundary at or below zero EBITDA: every testable EBITDA (EBITDA > 0) lies above it, so
    # the covenant passes at every testable point or at none, per the recorded type and the
    # numerator's sign.
    passes_at_testable = (type_ == "minimum") == (numerator > 0)
    result["pass_side"] = "at_or_above" if passes_at_testable else "at_or_below"  # noqa: S105
    if passes_at_testable:
        result["note"] = ("the boundary is at or below zero EBITDA: the ratio is defined only "
                          "for EBITDA > 0, so the covenant passes at every testable EBITDA and "
                          "is UNRESOLVABLE at EBITDA <= 0 before it can FAIL")
    else:
        result["note"] = ("the boundary is at or below zero EBITDA: every testable EBITDA "
                          "(EBITDA > 0) is on the failing side of it, so the covenant cannot "
                          "pass at any testable EBITDA")
    return result


def bracket_and_bisect(
    status_at: Callable[[float], str], start: float, step: float, bound: float, tolerance: float,
) -> dict[str, object]:
    """Find the first non-PASS point of one shock by expansion then bisection.

    `status_at(value)` returns the covenant status ("PASS", "FAIL" or "UNRESOLVABLE") at
    one shock setting and may raise ValueError when the model refuses the point. The
    search expands geometrically from `start` (offset doubled each round, capped at
    `bound`) until a non-PASS point or the bound; a PASS beyond the first non-PASS point
    is a non-monotone predicate and is refused. Bisection then narrows the
    pass/non-pass bracket to `tolerance`. Register entry F-063.
    """
    try:
        baseline = status_at(start)
    except ValueError as exc:
        return {"outcome": "refused_point", "reason": f"the model refused the scenario start ({exc})"}
    if baseline != "PASS":
        return {"outcome": "baseline_non_pass", "baseline_status": baseline}
    if start >= bound:
        return {"outcome": "no_crossing_in_bound",
                "reason": f"the documented search domain ({start} to {bound}) is empty"}
    lo = start
    hi = min(start + step, bound)
    while True:
        try:
            status = status_at(hi)
        except ValueError as exc:
            return {"outcome": "refused_point", "reason": f"the model refused a search point ({exc})"}
        if status == "PASS":
            if hi >= bound:
                return {"outcome": "no_crossing_in_bound",
                        "reason": (f"no non-PASS point within the documented search bound "
                                   f"(0 to {bound}); the cushion exceeds the bound or no crossing exists")}
            lo = hi
            hi = min(start + (hi - start) * 2.0, bound)
        else:
            break
    try:
        bound_status = status_at(bound)
    except ValueError:
        bound_status = None  # the bound itself refuses; the bracket above it stands
    if bound_status == "PASS":
        return {"outcome": "non_monotone",
                "reason": ("the covenant's status is non-monotone in this shock (a PASS lies beyond "
                           "the first non-PASS point), so no first crossing is well defined")}
    status_beyond = status
    while hi - lo > tolerance:
        mid = (lo + hi) / 2.0
        try:
            mid_status = status_at(mid)
        except ValueError as exc:
            return {"outcome": "refused_point", "reason": f"the model refused a bisection point ({exc})"}
        if mid_status == "PASS":
            lo = mid
        else:
            hi = mid
            status_beyond = mid_status
    return {"outcome": "crossing", "bracket": [lo, hi], "tolerance": tolerance,
            "status_beyond": status_beyond}


def _scenario_search(
    evaluate: Callable[[float], dict[str, object]], start: float, step: float,
    bound: float, tolerance: float,
) -> dict[str, object]:
    """One scenario's search, including the checks that need the ratio value.

    `evaluate(value)` returns the covenant result dict at one shock setting (or a dict
    with a "refused" key when the model refuses the point). A baseline that is not PASS
    reports zero remaining cushion; a ratio the shock cannot move is "not moved";
    everything else delegates to bracket_and_bisect().
    """
    baseline = evaluate(start)
    if "refused" in baseline:
        return {"outcome": "refused_point", "reason": f"the model refused the scenario start ({baseline['refused']})"}
    if baseline.get("status") != "PASS":
        return {"outcome": "baseline_non_pass", "baseline_status": baseline.get("status")}

    def status_at(value: float) -> str:
        evaluation = evaluate(value)
        if "refused" in evaluation:
            raise ValueError(str(evaluation["refused"]))
        return str(evaluation["status"])

    bound_result = evaluate(bound)
    if "refused" in bound_result:
        return {"outcome": "refused_point", "reason": f"the model refused the search bound ({bound_result['refused']})"}
    if baseline.get("actual") == bound_result.get("actual"):
        return {"outcome": "not_moved",
                "reason": "the recorded raw line items do not move this covenant's ratio under this shock"}
    return bracket_and_bisect(status_at, start=start, step=step, bound=bound, tolerance=tolerance)


def _evaluate_at(
    covenant: Mapping[str, object], period_raw: Mapping[str, object], assumptions: Mapping[str, float],
    period: str, recorded_view: str,
) -> dict[str, object]:
    """The covenant result for one shocked re-evaluation of one period, or the refusal.

    Re-runs the recorded raw line items through the spreading builder's own shock and
    model functions, then the policy engine's own single-covenant rule; nothing is
    re-implemented here. A ValueError from the model (non-finite or non-numeric input)
    is reported as a refused point, never as a traceback.
    """
    try:
        shocked = apply_stress_shocks(period_raw, assumptions)
        model = evaluate_financial_model({period: shocked})
    except (TypeError, ValueError) as exc:
        return {"refused": str(exc)}
    ratios = model["ratios"].get(period) or {}
    financials = model["financials"].get(period)
    result = evaluate_single_covenant(dict(covenant), ratios, financials, recorded_view)
    return dict(result)


def _shock_rows(
    covenant: Mapping[str, object], period: str, raw: Mapping[str, object],
    recorded_view: str, stress_assumptions: Mapping[str, float],
) -> list[dict[str, object]]:
    """One row per recorded shock: the base scenario and the downside scenario.

    The base scenario holds every shock at zero. The downside scenario holds the other
    shocks at their recorded settings and starts the searched shock from its recorded
    setting, reporting both the cumulative crossing and the additional cushion beyond
    that setting. The recorded vector has already passed the shared domain validator
    (compute_from_state), so a present value here is a number; only an absent key
    defaults to zero.
    """

    def recorded(key: str) -> float:
        return float(stress_assumptions.get(key) or 0)

    def scenario(key: str, fixed: dict[str, float], start: float) -> dict[str, object]:
        spec = SHOCK_SPECS[key]

        def evaluate(value: float) -> dict[str, object]:
            assumptions = dict(fixed)
            assumptions[key] = value
            return _evaluate_at(covenant, raw, assumptions, period, recorded_view)

        search = _scenario_search(evaluate, start=start, step=float(spec["step"]),
                                  bound=float(spec["bound"]), tolerance=float(spec["tolerance"]))
        row: dict[str, object] = {"start": start}
        row.update(search)
        if search.get("outcome") == "crossing":
            lo, hi = search["bracket"]
            row["additional_cushion_bracket"] = [lo - start, hi - start]
        return row

    rows = []
    for key in SHOCK_SPECS:
        others = [other for other in SHOCK_SPECS if other != key]
        base_fixed = {other: 0.0 for other in others}
        down_fixed = {other: recorded(other) for other in others}
        rows.append({
            "shock": key,
            "unit": SHOCK_SPECS[key]["unit"],
            "base_scenario": scenario(key, base_fixed, 0.0),
            "downside_scenario": scenario(key, down_fixed, recorded(key)),
        })
    return rows


def _report_periods(ratios: Mapping[str, object]) -> list[str]:
    """FY-Current (the gate period) plus the forward years with recorded ratios."""
    periods = ["FY-Current"]
    for key in FORWARD_PERIOD_KEYS:
        if isinstance(ratios.get(key), Mapping) and ratios[key]:
            periods.append(key)
    return periods


def _period_raw(
    period: str, state: Mapping[str, object],
) -> tuple[Mapping[str, object] | None, str | None]:
    """The period's raw line items, or the reason no break-even or search may use them.

    A period whose figures are analyst-supplied (financials_source for FY-Current,
    forecast_source for a forward year) has no framework-owned raw-input set, and a
    period absent from multi_period_financials has no raw lines at all; both make the
    re-evaluation the search depends on dishonest, so both are unavailable with a reason.
    """
    if period == "FY-Current" and state.get("financials_source") == "analyst-supplied":
        return None, ("the recorded figures are analyst-supplied (financials_source); the framework's "
                      "shock model does not own them, so no break-even or shock search is computed")
    if period != "FY-Current" and state.get("forecast_source") == "analyst-supplied":
        return None, ("the forward figures are analyst-supplied (forecast_source); the framework's "
                      "shock model does not own them, so no break-even or shock search is computed")
    multi = state.get("multi_period_financials")
    raw = multi.get(period) if isinstance(multi, Mapping) else None
    if not isinstance(raw, Mapping):
        return None, (f"no recorded raw line items for {period}; the recorded figures cannot be "
                      "re-evaluated under a shock")
    return raw, None


def compute_from_state(state: Mapping[str, object]) -> dict[str, object]:
    """The cushion report body from one deal's state. Pure: reads the mapping, writes nothing."""
    ratios = state.get("ratios") if isinstance(state.get("ratios"), Mapping) else {}
    financials = state.get("financials") if isinstance(state.get("financials"), Mapping) else {}
    covenants = state.get("covenants") if isinstance(state.get("covenants"), list) else []
    stress = state.get("stress_assumptions") if isinstance(state.get("stress_assumptions"), Mapping) else {}
    # A present malformed or negative recorded shock is a domain error, never a silent zero
    # shock: the shared write-path validator (numeric_validation) refuses the vector, so this
    # report refuses with its reason instead of searching as though nothing had been recorded.
    # Only an absent shock key defaults to zero.
    validate_stress_assumptions(stress)
    recorded_view = "consolidated" if isinstance(state.get("group_basis"), Mapping) else "borrower"

    caveats: list[str] = []
    covenant_reports: list[dict[str, object]] = []
    for covenant in covenants:
        if not isinstance(covenant, Mapping):
            continue
        period_entries: list[dict[str, object]] = []
        for period in _report_periods(ratios):
            raw, unavailable_reason = _period_raw(period, state)
            result = evaluate_single_covenant(
                dict(covenant), ratios.get(period) or {},
                financials.get(period) if isinstance(financials.get(period), Mapping) else None,
                recorded_view)
            if raw is None:
                break_even: dict[str, object] = {"metric": covenant.get("metric"), "break_even_ebitda": None,
                                                "pass_side": None, "note": None,
                                                "reason": unavailable_reason}
                shocks: list[dict[str, object]] | None = None
            else:
                break_even = ebitda_break_even(covenant, raw)
                shocks = _shock_rows(covenant, period, raw, recorded_view, stress)
                re_evaluated = _evaluate_at(covenant, raw, {}, period, recorded_view)
                if "refused" not in re_evaluated and (
                        (re_evaluated.get("actual"), re_evaluated.get("status"))
                        != (result.get("actual"), result.get("status"))):
                    caveats.append(
                        f"the recorded ratios for {period} disagree with a re-evaluation of the recorded "
                        f"raw line items (metric {covenant.get('metric')}: recorded {result.get('actual')}, "
                        f"re-evaluated {re_evaluated.get('actual')}); the shock search uses the re-evaluated model")
            if period == "FY-Current" and result.get("status") == "UNRESOLVABLE":
                caveats.append(
                    f"the {covenant.get('metric')} covenant is UNRESOLVABLE at FY-Current "
                    f"({result.get('reason')}); an N/A ratio is never converted into a numerical cushion")
            entry: dict[str, object] = {
                "period": period,
                "disclosure_only": period != "FY-Current",
                "result": result,
                "break_even_ebitda": break_even,
                "shocks": shocks,
                "shock_search_unavailable_reason": unavailable_reason,
            }
            period_entries.append(entry)
        covenant_reports.append({
            "metric": covenant.get("metric"), "type": covenant.get("type"),
            "threshold": covenant.get("threshold"), "periods": period_entries,
        })

    if not covenants:
        caveats.append("no covenants are recorded in state.json; there is nothing to cushion")

    return {
        "recorded_view": recorded_view,
        "financials_source": state.get("financials_source"),
        "forecast_source": state.get("forecast_source"),
        "analyst_supplied_financials_recorded": isinstance(state.get("analyst_supplied_financials"), Mapping)
        and bool(state["analyst_supplied_financials"]),
        "stress_assumptions": dict(stress),
        "basis_notes": list(BASIS_NOTES),
        "covenants": covenant_reports,
        "caveats": caveats,
        "limits": list(LIMITS),
    }


def generate(
    company: str, proposal: str, date_str: str | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Read the exact dated state and return the full report. Read-only; writes nothing."""
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str is not None:
        validate_iso_date_str(date_str, field_name="--date-str")
        if not os.path.isfile(state_path(company, proposal, date_str=date_str, base_dir=base_dir)):
            raise StateError(
                f"no state.json in the dated folder {proposal}_{date_str}; nothing to report")
    else:
        found = find_deal(company, proposal, base_dir=base_dir)
        if not found["record_found"]:
            raise StateError(f"no existing state.json for company {company!r} and proposal {proposal!r}")
        date_str = found["date"]
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir)
    state = read_state(company, proposal, date_str=date_str, base_dir=base_dir) or {}
    validate_state(state, keys=VALIDATED_STATE_KEYS, path=path)
    return {"status": "ok", "selected_folder": os.path.dirname(path), **compute_from_state(state)}


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: parse the flags, print the report as JSON, exit 0/1/2 per the convention."""
    parser = argparse.ArgumentParser(
        description="Print the covenant cushion report (exact EBITDA break-evens and single-shock "
                    "reverse-stress brackets) for one deal from its recorded state. Read-only: "
                    "writes nothing. Has no Anthropic dependency.")
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--date-str", default=None,
                        help="YYYY-MM-DD suffix of the deal's dated folder. Defaults to the deal's newest "
                             "dated folder that has a state.json.")
    args = parser.parse_args(argv)
    try:
        report = generate(args.company, args.proposal, date_str=args.date_str)
    except (OSError, ValueError) as exc:
        sys.exit(f"error: {exc}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    main()
