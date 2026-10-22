"""Read-only deterministic comparison of two dated states for the same deal."""
import argparse
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import json
import math
import os
import re
import sys

from state_manager import state_path, validate_iso_date_str

COMPARISON_VERSION = 1
BASIS_WARNING = (
    "COMPARABILITY WARNING: financials_source differs between reviews. Values are compared as stored; "
    "movement across a basis change is not necessarily like-for-like."
)
MISSING_DOWNSIDE_NOTE = "not recorded in either state; this is not evidence that downside risk is absent"
COMPARE_FIELDS = (
    "financials_source", "financials_source_note", "financials", "ratios", "multi_period_financials",
    "analyst_supplied_financials", "forecast_source", "stress_assumptions", "downside_case", "covenants",
    "policy_state.covenant_results", "policy_state.forward_covenant_results",
    "policy_state.downside_covenant_breaches", "policy_state.security_gaps",
    "policy_state.required_conditions_precedent", "policy_state.required_conditions_subsequent",
    "collateral", "security_package", "guarantees", "ownership", "facility_terms",
)
# A state is a few levels deep; the comparison walks it recursively, so a hostile or corrupt file nested past the
# interpreter's recursion limit would end in a traceback. Refuse anything deeper than this, before any walk.
MAX_STATE_DEPTH = 100
HISTORICAL_PERIOD_RE = re.compile(r"^FY-(Current|[0-9]+)$")
FORWARD_PERIOD_RE = re.compile(r"^FY\+")


class ComparisonError(ValueError):
    """Expected refusal reported by the CLI as one error line."""


_MISSING = object()


def _validate_date(value, flag):
    try:
        validate_iso_date_str(value, field_name=flag)
        date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ComparisonError(str(exc)) from exc
    return value


def _parse_constant(value):
    try:
        return Decimal(value)
    except InvalidOperation:
        return value


def _exceeds_depth(value):
    """True when `value` is nested (objects and lists) more than MAX_STATE_DEPTH levels deep. Iterative on purpose."""
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, dict):
            children = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > MAX_STATE_DEPTH:
            return True
        pending.extend((child, depth + 1) for child in children)
    return False


def _first_nonfinite(value, path="$"):
    if isinstance(value, Decimal):
        return None if value.is_finite() else path
    if isinstance(value, float):
        return None if math.isfinite(value) else path
    if isinstance(value, dict):
        for key in sorted(value, key=lambda item: str(item)):
            found = _first_nonfinite(value[key], f"{path}.{key}")
            if found is not None:
                return found
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _first_nonfinite(item, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _read_state(company, proposal, date_value, label):
    try:
        path = state_path(company, proposal, date_str=date_value)
    except (OSError, ValueError) as exc:
        raise ComparisonError(f"cannot resolve {label} state path: {exc}") from exc
    if not os.path.exists(path):
        raise ComparisonError(f"{label} state not found: {path}")
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle, parse_int=Decimal, parse_float=Decimal, parse_constant=_parse_constant)
    except (OSError, UnicodeError) as exc:
        raise ComparisonError(f"{label} state is unreadable: {path}: {exc}") from exc
    except (RecursionError, ValueError) as exc:
        raise ComparisonError(f"{label} state is corrupt JSON: {path}") from exc
    if not isinstance(state, dict):
        raise ComparisonError(f"{label} state must be a JSON object: {path}")
    if _exceeds_depth(state):
        raise ComparisonError(f"{label} state is nested more than {MAX_STATE_DEPTH} levels deep: {path}")
    nonfinite_path = _first_nonfinite(state)
    if nonfinite_path is not None:
        raise ComparisonError(f"{label} state contains a non-finite number at {nonfinite_path}: {path}")
    if state.get("company") != company:
        raise ComparisonError(f"{label} state company does not match --company {company!r}: {path}")
    if state.get("proposal") != proposal:
        raise ComparisonError(f"{label} state proposal does not match --proposal {proposal!r}: {path}")
    return state


def _is_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float, Decimal))


def _as_decimal(value):
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    return Decimal(str(value))


def _exact_delta(current, prior):
    current_decimal, prior_decimal = _as_decimal(current), _as_decimal(prior)
    low_exponent = min(current_decimal.as_tuple().exponent, prior_decimal.as_tuple().exponent)
    highest = max(
        current_decimal.adjusted() if not current_decimal.is_zero() else 0,
        prior_decimal.adjusted() if not prior_decimal.is_zero() else 0,
    )
    precision = max(28, highest - low_exponent + 3,
                    len(current_decimal.as_tuple().digits), len(prior_decimal.as_tuple().digits))
    with localcontext() as context:
        context.prec = precision
        return current_decimal - prior_decimal


def _deep_equal(left, right):
    if left is right:
        return True
    if _is_number(left) and _is_number(right):
        try:
            return _as_decimal(left) == _as_decimal(right)
        except InvalidOperation:
            return False
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_deep_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_deep_equal(a, b) for a, b in zip(left, right, strict=True))
    return type(left) is type(right) and left == right


def _recorded(value):
    return value is not _MISSING and value is not None


def _get_field(state, dotted_path):
    current = state
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _json_compact(value):
    return _json_dump(value, indent=None)


def _stable_list_kind(path):
    return {
        "covenants": "covenant",
        "policy_state.covenant_results": "covenant",
        "policy_state.forward_covenant_results": "forward_id",
        "policy_state.downside_covenant_breaches": "breach_id",
        "policy_state.required_conditions_precedent": "cp_id",
        "policy_state.required_conditions_subsequent": "cs_id",
        "collateral": "asset_id",
        "guarantees": "guarantee_id",
    }.get(path)


def _record_maps(records, kind):
    if not isinstance(records, list):
        return None
    result, counts = {}, {}
    for record in records:
        if not isinstance(record, dict):
            return None
        if kind == "covenant":
            if record.get("metric") is None or record.get("type") is None:
                return None
            canonical = (_json_compact(record["metric"]), _json_compact(record["type"]))
            counts[canonical] = counts.get(canonical, 0) + 1
            identity = ("metric_type", canonical[0], canonical[1], counts[canonical])
        else:
            value = record.get(kind)
            if value is None or (
                kind == "guarantee_id" and isinstance(value, str) and not value.strip()
            ):
                return None
            identity = (kind, _json_compact(value))
        if identity in result:
            return None
        result[identity] = record
    return result


def _identity_label(identity):
    if identity[0] == "metric_type":
        return f"metric={identity[1]},type={identity[2]},occurrence={identity[3]}"
    return f"{identity[0]}={identity[1]}"


def _join_path(base, child):
    return child if not base else f"{base}.{child}"


def _record_path(base, identity):
    return f"{base}[{_identity_label(identity)}]"


def _transition_kind(prior, current):
    p_missing = prior is _MISSING or prior is None
    c_missing = current is _MISSING or current is None
    if p_missing and c_missing:
        return None
    if p_missing:
        return "not_recorded_to_value"
    if c_missing:
        return "value_to_not_recorded"
    return "value_changed"


def _canonical_value(value):
    """Sort object keys recursively while preserving list order and exact scalar values."""
    if isinstance(value, dict):
        return {key: _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _change(path, kind, prior, current):
    row = {
        "path": path,
        "change": kind,
        "prior": None if prior is _MISSING else _canonical_value(prior),
        "current": None if current is _MISSING else _canonical_value(current),
    }
    if _is_number(prior) and _is_number(current):
        row["delta"] = _exact_delta(current, prior)
    return row


def _diff_value(path, prior, current, output):
    p_missing = prior is _MISSING or prior is None
    c_missing = current is _MISSING or current is None
    if p_missing and c_missing:
        return
    if not p_missing and not c_missing and _deep_equal(prior, current):
        return

    if (p_missing and isinstance(current, dict)) or (c_missing and isinstance(prior, dict)):
        start = len(output)
        p_obj = {} if p_missing else prior
        c_obj = {} if c_missing else current
        for key in sorted(set(p_obj) | set(c_obj), key=str):
            _diff_value(_join_path(path, str(key)), p_obj.get(key, _MISSING),
                        c_obj.get(key, _MISSING), output)
        if len(output) == start and path:
            output.append(_change(path, _transition_kind(prior, current), prior, current))
        return

    if isinstance(prior, dict) and isinstance(current, dict):
        for key in sorted(set(prior) | set(current), key=str):
            _diff_value(_join_path(path, str(key)), prior.get(key, _MISSING),
                        current.get(key, _MISSING), output)
        return

    kind = _stable_list_kind(path)
    if kind and (isinstance(prior, list) or p_missing) and (isinstance(current, list) or c_missing):
        p_list = [] if p_missing else prior
        c_list = [] if c_missing else current
        p_map, c_map = _record_maps(p_list, kind), _record_maps(c_list, kind)
        if p_map is not None and c_map is not None:
            start = len(output)
            for identity in sorted(set(p_map) | set(c_map), key=repr):
                record_path = _record_path(path, identity)
                if identity not in p_map:
                    output.append(_change(record_path, "added", _MISSING, c_map[identity]))
                elif identity not in c_map:
                    output.append(_change(record_path, "removed", p_map[identity], _MISSING))
                else:
                    _diff_value(record_path, p_map[identity], c_map[identity], output)
            if len(output) == start and p_missing != c_missing:
                output.append(_change(path, _transition_kind(prior, current), prior, current))
            return

    if isinstance(prior, list) and isinstance(current, list):
        output.append(_change(path, "value_changed", prior, current))
        return
    output.append(_change(path, _transition_kind(prior, current), prior, current))


def _basis_change(prior_state, current_state):
    prior_basis = prior_state.get("financials_source")
    current_basis = current_state.get("financials_source")
    changed = not _deep_equal(prior_basis, current_basis)
    return {
        "prior": prior_basis,
        "current": current_basis,
        "comparable_like_for_like": not changed,
        "warning": BASIS_WARNING if changed else None,
    }


def _period_keys(state):
    keys = set()
    for field in ("financials", "ratios", "multi_period_financials", "analyst_supplied_financials"):
        periods = state.get(field)
        if isinstance(periods, dict):
            keys.update(p for p in periods if isinstance(p, str) and not FORWARD_PERIOD_RE.match(p))
    return keys


def _period_order(period):
    match = HISTORICAL_PERIOD_RE.fullmatch(period)
    if not match:
        return (1, period.casefold(), period)
    if match.group(1) == "Current":
        return (0, 0, period)
    return (0, int(match.group(1)) + 1, period)


def _next_older_period(period):
    match = HISTORICAL_PERIOD_RE.fullmatch(period)
    if not match:
        return None
    return "FY-1" if match.group(1) == "Current" else f"FY-{int(match.group(1)) + 1}"


def _period_value(state, field, period):
    container = state.get(field)
    return container.get(period, _MISSING) if isinstance(container, dict) else _MISSING


def _period_present(state, period):
    return any(_recorded(_period_value(state, field, period)) for field in (
        "financials", "ratios", "multi_period_financials", "analyst_supplied_financials"
    ))


def _raw_period(state, period):
    supplied = _period_value(state, "analyst_supplied_financials", period)
    financial = _period_value(state, "financials", period)
    multiperiod = _period_value(state, "multi_period_financials", period)
    financial_raw = financial.get("raw", _MISSING) if isinstance(financial, dict) else _MISSING
    if state.get("financials_source") == "analyst-supplied" and _recorded(supplied):
        return supplied, f"analyst_supplied_financials.{period}"
    if _recorded(financial_raw):
        return financial_raw, f"financials.{period}.raw"
    if _recorded(multiperiod):
        return multiperiod, f"multi_period_financials.{period}"
    if _recorded(supplied):
        return supplied, f"analyst_supplied_financials.{period}"
    return _MISSING, None


def _period_snapshot(state, period):
    raw, raw_store = _raw_period(state, period)
    financial = _period_value(state, "financials", period)
    ratios = _period_value(state, "ratios", period)
    subtotals = {key: val for key, val in financial.items() if key != "raw"} if isinstance(financial, dict) else financial
    return {
        "raw_line_items": None if raw is _MISSING else _canonical_value(raw),
        "raw_store": raw_store,
        "financials": None if subtotals is _MISSING else _canonical_value(subtotals),
        "ratios": None if ratios is _MISSING else _canonical_value(ratios),
    }


def _restatement_check(prior_state, current_state, basis):
    periods = []
    for prior_period in sorted(_period_keys(prior_state), key=_period_order):
        current_period = _next_older_period(prior_period)
        prior_values = _period_snapshot(prior_state, prior_period)
        warning = basis["warning"]
        if current_period is None or not _period_present(current_state, current_period):
            periods.append({
                "prior_period": prior_period, "current_period": current_period, "status": "not_comparable",
                "prior_values": prior_values, "current_values": None, "differences": [], "warning": warning,
                "reason": f"current period {current_period} is not recorded" if current_period else
                          "period label has no defined one-year roll-forward mapping",
            })
            continue
        current_values = _period_snapshot(current_state, current_period)
        differences = []
        # raw_store identifies where the saved lines were found, not a value to
        # compare; otherwise the period label itself would make every pair differ.
        prior_comparable = {key: value for key, value in prior_values.items() if key != "raw_store"}
        current_comparable = {key: value for key, value in current_values.items() if key != "raw_store"}
        _diff_value("", prior_comparable, current_comparable, differences)
        for row in differences:
            if "delta" in row:
                row["difference"] = row.pop("delta")
        periods.append({
            "prior_period": prior_period, "current_period": current_period,
            "status": "restated" if differences else "consistent",
            "prior_values": prior_values, "current_values": current_values,
            "differences": differences, "warning": warning,
        })
    return {
        "periods_rolled_one_year_confirmed": True,
        "basis_change_warning": basis["warning"],
        "periods": periods,
    }


def compare_states(company, proposal, prior_date, current_date, prior_state, current_state,
                   periods_rolled_one_year=False):
    """Build the stable report from two already-read state dictionaries."""
    if prior_state.get("company") != company or current_state.get("company") != company:
        raise ComparisonError(f"states must both match --company {company!r}")
    if prior_state.get("proposal") != proposal or current_state.get("proposal") != proposal:
        raise ComparisonError(f"states must both match --proposal {proposal!r}")
    if prior_date >= current_date:
        raise ComparisonError("prior date must be strictly earlier than current date")
    for label, saved in (("prior", prior_state), ("current", current_state)):
        if _exceeds_depth(saved):
            raise ComparisonError(f"{label} state is nested more than {MAX_STATE_DEPTH} levels deep")
        bad_number = _first_nonfinite(saved)
        if bad_number is not None:
            raise ComparisonError(f"{label} state contains a non-finite number at {bad_number}")
    changes, unchanged, not_recorded = [], [], []
    for field in COMPARE_FIELDS:
        before, after = _get_field(prior_state, field), _get_field(current_state, field)
        start = len(changes)
        _diff_value(field, before, after, changes)
        if not _recorded(before) and not _recorded(after):
            not_recorded.append(field)
        elif _recorded(before) and _recorded(after) and len(changes) == start:
            unchanged.append(field)
    basis = _basis_change(prior_state, current_state)
    report = {
        "comparison_version": COMPARISON_VERSION, "company": company, "proposal": proposal,
        "prior_date": prior_date, "current_date": current_date, "basis_change": basis,
        "summary": {
            "changed_paths": len(changes),
            "unchanged_categories": unchanged,
            "not_recorded_in_both": not_recorded,
        },
        "changes": changes,
    }
    if periods_rolled_one_year:
        report["restatement_check"] = _restatement_check(prior_state, current_state, basis)
    return report


def compare_deal(company, proposal, current_date, prior_date=None, periods_rolled_one_year=False):
    current_date = _validate_date(current_date, "--current-date")
    if prior_date is not None:
        prior_date = _validate_date(prior_date, "--prior-date")
        if prior_date >= current_date:
            raise ComparisonError("--prior-date must be strictly earlier than --current-date")
        prior_state = _read_state(company, proposal, prior_date, "prior")
        current_state = _read_state(company, proposal, current_date, "current")
    else:
        current_state = _read_state(company, proposal, current_date, "current")
        prior_date = current_state.get("prior_review_date")
        if prior_date is None:
            raise ComparisonError("current state has no prior_review_date; pass --prior-date explicitly")
        prior_date = _validate_date(prior_date, "prior_review_date")
        if prior_date >= current_date:
            raise ComparisonError("recorded prior_review_date must be strictly earlier than --current-date")
        prior_state = _read_state(company, proposal, prior_date, "prior")
    return compare_states(company, proposal, prior_date, current_date, prior_state, current_state,
                          periods_rolled_one_year=periods_rolled_one_year)


def _json_dump(value, indent=2):
    """Encode Decimal values as exact JSON numbers, without a float conversion."""
    def encode(item, level):
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError("cannot serialize a non-finite decimal")
            return str(item)
        if isinstance(item, int) and not isinstance(item, bool):
            return str(item)
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("cannot serialize a non-finite float")
            return repr(item)
        if isinstance(item, dict):
            if not item:
                return "{}"
            pairs = []
            for key, nested in item.items():
                key_json = json.dumps(str(key), ensure_ascii=False)
                value_json = encode(nested, level + 1)
                if indent is None:
                    pairs.append(f"{key_json}:{value_json}")
                else:
                    pairs.append(" " * (indent * (level + 1)) + f"{key_json}: " + value_json)
            return ("{" + ",".join(pairs) + "}") if indent is None else (
                "{\n" + ",\n".join(pairs) + "\n" + " " * (indent * level) + "}"
            )
        if isinstance(item, (list, tuple)):
            if not item:
                return "[]"
            values = [encode(nested, level + 1) for nested in item]
            return "[" + ",".join(values) + "]" if indent is None else (
                "[\n" + ",\n".join(" " * (indent * (level + 1)) + val for val in values)
                + "\n" + " " * (indent * level) + "]"
            )
        if item is _MISSING:
            return "null"
        return json.dumps(item, ensure_ascii=False, allow_nan=False)
    return encode(value, 0)


def _display_value(value):
    return "not recorded" if value is _MISSING or value is None else _json_compact(value)


def render_markdown(report):
    lines = [
        f"# Review comparison — {report['company']} / {report['proposal']}", "",
        f"Prior review: {report['prior_date']}  ", f"Current review: {report['current_date']}", "",
    ]
    basis = report["basis_change"]
    if basis["warning"]:
        lines.extend([f"> **{basis['warning']}**", ""])
    lines.extend(["## Changes", "", "| Item | Prior | Current | Change |",
                  "| :--- | :--- | :--- | :--- |"])
    if not report["changes"]:
        lines.append("| None | — | — | No changed values recorded |")
    for row in report["changes"]:
        delta = f"; delta {_json_compact(row['delta'])}" if "delta" in row else ""
        lines.append(f"| {row['path']} | {_display_value(row['prior'])} | "
                     f"{_display_value(row['current'])} | {row['change']}{delta} |")
    lines.extend(["", f"Changed paths: {report['summary']['changed_paths']}", "",
                  "## Unchanged by-label categories", ""])
    unchanged = report["summary"]["unchanged_categories"]
    lines.append(", ".join(unchanged) if unchanged else "None recorded as unchanged.")
    lines.extend(["", "## Categories not recorded in both states", ""])
    absent = report["summary"]["not_recorded_in_both"]
    lines.append(", ".join(absent) if absent else "None.")
    if "downside_case" in absent:
        lines.extend(["", f"Downside case: {MISSING_DOWNSIDE_NOTE}"])
    else:
        lines.extend(["", "Downside case: recorded in at least one state; this comparison does not assess its adequacy."])
    if "restatement_check" in report:
        check = report["restatement_check"]
        lines.extend(["", "## Restatement check", "",
                      "Analyst confirmation recorded: --periods-rolled-one-year = true."])
        if check["basis_change_warning"]:
            lines.extend([f"> **{check['basis_change_warning']}**", ""])
        lines.extend(["", "| Prior period | Current period | Status | Differences |",
                      "| :--- | :--- | :--- | :--- |"])
        if not check["periods"]:
            lines.append("| None | None | not_comparable | No historical periods were recorded |")
        for pair in check["periods"]:
            diffs = "; ".join(
                f"{row['path']}: {_display_value(row['prior'])} → {_display_value(row['current'])}"
                + (f" (difference {_json_compact(row['difference'])})" if "difference" in row
                   else f" ({row['change']})")
                for row in pair["differences"]
            ) or ("No recorded-value differences" if pair["status"] == "consistent" else pair.get("reason", ""))
            warning = f"; {pair['warning']}" if pair["warning"] else ""
            lines.append(f"| {pair['prior_period'] or '—'} | {pair['current_period'] or '—'} | "
                         f"{pair['status']}{warning} | {diffs or '—'} |")
    lines.append("")
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Compare two exact dated deal states read-only; no model calls or writes.")
    parser.add_argument("--company", required=True, help="Exact company name used in the deal folder.")
    parser.add_argument("--proposal", required=True, help="Exact proposal name used in the deal folder.")
    parser.add_argument("--current-date", required=True, help="Current review date in YYYY-MM-DD form.")
    parser.add_argument("--prior-date", help="Explicit earlier review date; otherwise read prior_review_date from current state.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json",
                        help="Output format (default: json).")
    parser.add_argument("--periods-rolled-one-year", action="store_true",
                        help="Record analyst confirmation that historical period labels rolled forward exactly one year.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        report = compare_deal(args.company, args.proposal, args.current_date, prior_date=args.prior_date,
                              periods_rolled_one_year=args.periods_rolled_one_year)
        output = _json_dump(report) if args.format == "json" else render_markdown(report)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        # RecursionError is a safety net: states deeper than MAX_STATE_DEPTH are refused before any recursive walk.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    raise SystemExit(main())
