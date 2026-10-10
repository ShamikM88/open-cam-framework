"""Standalone CLI: record analyst-supplied forward-year figures for /project (issue #124).

`/project`'s default path hands forward-year raw line items to spreading_check.py, which recomputes every subtotal and
ratio with the framework's formulas and derives a downside case by applying three stress shocks to those raw lines.
That is wrong for an institution whose own forecast convention does not fit the framework's raw schema (the reason
`/spread` has an analyst-supplied mode): the formulas would silently misstate the forecast, and the shocks are
defined in terms of raw field names (`revenue`, `admin_expenses`, `interest_paid`, a specific debt aggregate) that an
analyst's forecast may not follow.

This script is the analyst-supplied alternative. It records the figures exactly as given and never passes them through
evaluate_financial_model() for the purpose of producing a recorded value (it uses that function only to *test* whether
the framework's shocks may be applied, below):

- `financials[FY+n]` holds the subtotals as given, `ratios[FY+n]` the ratios as given, and the optional raw
  breakdown goes to `analyst_supplied_financials[FY+n]` -- the same stores `/spread`'s analyst-supplied mode writes.
- `forecast_source` is `"analyst-supplied"` (absent means framework-computed, which is every existing deal), and the
  deal-wide `financials_source` is `"analyst-supplied"` so the CAM must carry the Guideline 9 caveat; a note is added
  to `financials_source_note`, which that caveat quotes.
- One basis per forecast: a deal whose forward years were computed from raw lines is refused (no blending), and so is
  one whose historical years were framework-computed (that would need the deal-wide flag and the workbook's raw
  figures to describe two bases at once).

The downside case, decided conservatively:

- An analyst's own stressed forecast (`downside`) is recorded as supplied, with its description, and is what the
  policy engine tests covenants against for those years. A downside case has one description, which describes all of
  its analyst-supplied years: a call whose description differs while earlier analyst years are not restated is refused.
- The framework's stress shocks are applied to a year only when they genuinely meet their assumptions: the analyst
  gave that year's raw lines, every line a requested shock acts on, every line the covenant ratios read (the evaluator
  treats an omitted line as zero, which is not a zero the analyst confirmed, so each is given, as an explicit 0 where
  it is nil), and the framework's formulas reproduce the subtotals and ratios the analyst gave for that year (to two
  decimal places). Then the shocked case is derived by evaluate_downside_case(), labelled framework-derived from the
  reconciled lines, and holds only the figures every one of whose lines was supplied.
- Otherwise, if a downside was wanted (stress assumptions or a scenario of the analyst's own exist), the year is
  recorded as unavailable with the reason, and policy_engine treats a covenant that passes in that year's base case as
  UNRESOLVABLE under stress. Nothing is manufactured and nothing is silently dropped.

Everything is written in one state update, after the state has been checked (shape, basis); write_state() itself
refuses to overwrite a newer schema version, before anything is touched.
No `anthropic` dependency.
"""
import argparse
import json
import math
import sys

from spreading_builder import (
    FORWARD_PERIOD_KEYS,
    SUPPLIED_RATIOS,
    SUPPLIED_SUBTOTALS,
    evaluate_downside_case,
    evaluate_financial_model,
)
from spreading_builder import FIELD_LABELS as RAW_FIELD_LABELS
from state_manager import StateError, read_state, state_path, write_state

RAW_FIELDS = tuple(RAW_FIELD_LABELS)
STRESS_KEYS = ("revenue_haircut_pct", "opex_increase_pct", "interest_rate_bump_bps")
RECONCILE_TOLERANCE = 0.005         # figures must agree to two decimal places
STATE_KEYS = ("financials", "ratios", "analyst_supplied_financials", "multi_period_financials",
              "stress_assumptions", "downside_case", "financials_source")
# The raw lines each shock acts on (spreading_builder.apply_stress_shocks): all must be given when it is requested.
SHOCK_LINES = {
    "revenue_haircut_pct": ("revenue",),
    "opex_increase_pct": ("admin_expenses",),
    "interest_rate_bump_bps": ("interest_paid", "current_debt", "overdraft", "long_term_debt", "loan_notes"),
}
FRAMEWORK_DERIVED = "framework-derived from reconciled analyst-supplied lines"

# The raw lines each figure the framework derives reads (spreading_builder.evaluate_financial_model). The evaluator
# reads an omitted line as zero, which is not the same as the analyst saying it is zero, so a derived downside figure
# is recorded only when every line it reads was supplied (an explicit 0 counts). A superset is safe, a missing entry
# is not: test_supplied_forecast checks every entry against the evaluator itself.
_PROFIT = ("revenue", "cost_of_sales", "admin_expenses", "depreciation", "amortisation", "other_income")
_BEFORE_TAX = (*_PROFIT, "interest_paid", "interest_received", "exceptional_costs")
_CASH_FLOW = (*_PROFIT, "capex", "tax_paid", "interest_paid", "interest_received")
_CURRENT_ASSETS = ("cash", "trade_debtors", "stock", "other_current_assets")
_CURRENT_LIABILITIES = ("trade_creditors", "current_debt", "overdraft", "other_current_liabilities")
_DEBT = ("current_debt", "overdraft", "long_term_debt", "loan_notes")
_EQUITY = ("share_capital", "retained_profit")
SUBTOTAL_DEPENDS = {
    "gross_profit": ("revenue", "cost_of_sales"),
    "operating_profit": _PROFIT,
    "ebitda": _PROFIT,
    "profit_before_tax": _BEFORE_TAX,
    "net_profit": (*_BEFORE_TAX, "tax_paid"),
    "fcf": _CASH_FLOW,
    "current_assets": _CURRENT_ASSETS,
    "current_liabilities": _CURRENT_LIABILITIES,
    "total_assets": (*_CURRENT_ASSETS, "tangible_assets", "intangible_assets", "other_fixed_assets"),
    "total_liabilities": (*_CURRENT_LIABILITIES, "long_term_debt", "loan_notes", "other_long_term_liabilities",
                          "provisions"),
    "total_equity": _EQUITY,
    "total_debt": _DEBT,
    "tangible_net_worth": (*_EQUITY, "intangible_assets"),
}
RATIO_DEPENDS = {
    "dscr": (*_PROFIT, "interest_paid", "scheduled_principal"),
    "gross_leverage": (*_DEBT, *_PROFIT),
    "net_debt_to_ebitda": (*_DEBT, "cash", *_PROFIT),
    "current_ratio": (*_CURRENT_ASSETS, *_CURRENT_LIABILITIES),
    "gearing": (*_DEBT, *_EQUITY),
    "ebit_interest_cover": (*_PROFIT, "interest_paid"),
    "ebitda_interest_cover": (*_PROFIT, "interest_paid"),
    "fcf_conversion_pct": _CASH_FLOW,
    "trade_debtor_days": ("trade_debtors", "revenue"),
    "trade_creditor_days": ("trade_creditors", "cost_of_sales"),
    "stock_days": ("stock", "cost_of_sales"),
    "working_capital_cycle_days": ("trade_debtors", "revenue", "stock", "trade_creditors", "cost_of_sales"),
    "EBIT/Interest": (*_PROFIT, "interest_paid"),
    "EBITDA/Interest": (*_PROFIT, "interest_paid"),
}
# Any covenant can be set on a ratio the analyst supplies, so a derived downside needs every line those ratios read.
REQUIRED_LINES = tuple(sorted({line for ratio in SUPPLIED_RATIOS for line in RATIO_DEPENDS[ratio]}))


UNKNOWN = object()        # the state file could not be read back, so whether it changed is not known


class ForecastError(ValueError):
    """The supplied forecast is unusable, or the deal cannot take it. Nothing was written."""


# ---------------------------------------------------------------------------
# Validation of the supplied input
# ---------------------------------------------------------------------------

def _number(value, label, allow_null=False):
    if value is None and allow_null:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ForecastError(f"{label}: expected a finite number{' or null (N/A)' if allow_null else ''}, "
                            f"found {value!r}")
    return value


def _figures(mapping, allowed, label, allow_null=False):
    if not isinstance(mapping, dict):
        raise ForecastError(f"{label} must be an object")
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise ForecastError(f"{label}: unknown name(s) {unknown}; allowed: {list(allowed)}")
    return {name: _number(value, f"{label}.{name}", allow_null) for name, value in mapping.items()}


def _period_block(body, label, require_figures):
    if not isinstance(body, dict):
        raise ForecastError(f"{label} must be an object")
    unknown = sorted(set(body) - {"subtotals", "ratios", "lines"})
    if unknown:
        raise ForecastError(f"{label}: unknown key(s) {unknown}; allowed: ['lines', 'ratios', 'subtotals']")
    block = {
        "subtotals": _figures(body.get("subtotals") or {}, SUPPLIED_SUBTOTALS, f"{label}.subtotals"),
        "ratios": _figures(body.get("ratios") or {}, SUPPLIED_RATIOS, f"{label}.ratios", allow_null=True),
        "lines": _figures(body.get("lines") or {}, RAW_FIELDS, f"{label}.lines"),
    }
    if require_figures and not (block["subtotals"] or block["ratios"]):
        raise ForecastError(
            f"{label}: give the subtotals and/or ratios as the analyst supplies them. Raw lines alone are the "
            "framework-computed mode's input: use the default /project path for those")
    return block


def validate(data):
    """{"forecast": {period: {subtotals, ratios, lines}}, "downside": None | {description, periods}}, or ForecastError."""
    if not isinstance(data, dict):
        raise ForecastError("the supplied forecast must be a JSON object")
    unknown = sorted(set(data) - {"forecast", "downside"})
    if unknown:
        raise ForecastError(f"unknown key(s) {unknown}; allowed: ['downside', 'forecast']")
    forecast_in = data.get("forecast")
    if not isinstance(forecast_in, dict) or not forecast_in:
        raise ForecastError("forecast must be a non-empty object keyed by forward year (FY+1, FY+2, FY+3)")
    bad = sorted(set(forecast_in) - set(FORWARD_PERIOD_KEYS))
    if bad:
        raise ForecastError(f"unknown forecast year(s) {bad}; /project records {list(FORWARD_PERIOD_KEYS)}")
    forecast = {period: _period_block(forecast_in[period], f"forecast.{period}", True)
                for period in FORWARD_PERIOD_KEYS if period in forecast_in}

    downside = None
    if data.get("downside") is not None:
        raw = data["downside"]
        if not isinstance(raw, dict):
            raise ForecastError("downside must be an object {description, periods}")
        unknown = sorted(set(raw) - {"description", "periods"})
        if unknown:
            raise ForecastError(f"downside: unknown key(s) {unknown}; allowed: ['description', 'periods']")
        if not isinstance(raw.get("description"), str) or not raw["description"].strip():
            raise ForecastError("downside.description is required: say what scenario the analyst's stressed forecast "
                                "represents")
        periods_in = raw.get("periods")
        if not isinstance(periods_in, dict) or not periods_in:
            raise ForecastError("downside.periods must be a non-empty object keyed by forward year")
        bad = sorted(set(periods_in) - set(FORWARD_PERIOD_KEYS))
        if bad:
            raise ForecastError(f"downside: unknown year(s) {bad}; allowed {list(FORWARD_PERIOD_KEYS)}")
        periods = {}
        for period in FORWARD_PERIOD_KEYS:
            if period in periods_in:
                block = _period_block(periods_in[period], f"downside.periods.{period}", True)
                if block["lines"]:
                    raise ForecastError(f"downside.periods.{period}: a supplied stressed forecast is subtotals and "
                                        "ratios only; raw lines are not accepted for it")
                periods[period] = block
        downside = {"description": raw["description"].strip(), "periods": periods}
    return {"forecast": forecast, "downside": downside}


def validate_stress(data):
    if not isinstance(data, dict):
        raise ForecastError("the stress assumptions must be a JSON object")
    unknown = sorted(set(data) - set(STRESS_KEYS))
    if unknown:
        raise ForecastError(f"stress assumptions: unknown key(s) {unknown}; allowed: {list(STRESS_KEYS)}")
    return {key: _number(value, f"stress.{key}") for key, value in data.items()}


# ---------------------------------------------------------------------------
# What the deal can take
# ---------------------------------------------------------------------------

def deal_conflict(existing):
    """Why this deal cannot take an analyst-supplied forecast, or None. One basis per forecast and per deal: an
    existing framework-computed forward year, or framework-computed (or unknown-basis) historical figures, refuse it
    rather than blend two bases under one deal-wide flag."""
    forward_on_file = [p for p in FORWARD_PERIOD_KEYS
                       if p in (existing.get("multi_period_financials") or {})
                       or (existing.get("forecast_source") != "analyst-supplied"
                           and (p in (existing.get("financials") or {}) or p in (existing.get("ratios") or {})))]
    if forward_on_file:
        return (f"forward years {forward_on_file} are already recorded from raw line items (framework-computed); an "
                "analyst-supplied forecast would overwrite or blend them. Use the default /project path for this deal")
    historical = any(period not in FORWARD_PERIOD_KEYS
                     for store in ("financials", "ratios", "multi_period_financials", "analyst_supplied_financials")
                     for period in (existing.get(store) or {}))
    basis = existing.get("financials_source")
    if basis == "framework-computed":
        return ("this deal's historical figures were computed by the framework (financials_source is "
                "'framework-computed'); an analyst-supplied forecast would need the deal-wide basis to be "
                "analyst-supplied, which would mislabel them and drop their workbook inputs. Use the default /project "
                "path, or redo the historical spread in /spread's analyst-supplied mode")
    if basis is None and historical:
        return ("this deal records historical figures but no financials_source, so their basis is unknown and an "
                "analyst-supplied forecast cannot safely be added to them. Set financials_source by hand once the "
                "basis is known")
    return None


def _merge(existing, additions):
    merged = dict(existing or {})
    for period, fields in additions.items():
        period_dict = dict(merged.get(period) or {})
        period_dict.update(fields)
        merged[period] = period_dict
    return merged


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def reconciliation_problems(lines, subtotals, ratios, stress):
    """Why the framework's stress shocks may not be applied to this year, as a list of reasons (empty means they
    may). The year needs its raw lines; every line a requested shock acts on; every line a covenant ratio reads (an
    omitted line is not a known zero, so each is given, as an explicit 0 where it is nil); and the framework's
    formulas must reproduce the figures the analyst gave for the year. Stored values that are not numbers are a
    reason, never an error."""
    if not lines:
        return ["no raw lines were supplied for this year, so the framework's shocks have nothing to act on"]
    requested = [key for key in STRESS_KEYS if stress.get(key)]
    if not requested:
        return ["none of the stress shocks is non-zero"]
    problems = []
    unreadable = [f"line {name}: {value!r} is not a number" for name, value in lines.items() if not _is_number(value)]
    if unreadable:
        return unreadable[:4] + ([f"and {len(unreadable) - 4} more"] if len(unreadable) > 4 else [])
    shock_missing = set()
    for key in requested:
        missing = [line for line in SHOCK_LINES[key] if line not in lines]
        shock_missing.update(missing)
        if missing:
            problems.append(f"{key} acts on {missing}, which were not supplied")
    missing = [line for line in REQUIRED_LINES if line not in lines and line not in shock_missing]
    if missing:
        problems.append(f"the covenant ratios also read {missing}, which were not supplied: an omitted line is not a "
                        "known zero, so give each as an explicit 0 where it is nil")
    derived = evaluate_financial_model({"p": lines})
    for name, value in subtotals.items():
        recomputed = derived["financials"]["p"].get(name)
        if not _is_number(value):
            problems.append(f"{name}: supplied {value!r} is not a number")
        elif not isinstance(recomputed, (int, float)) or abs(value - recomputed) > RECONCILE_TOLERANCE:
            problems.append(f"{name}: supplied {value}, the framework's formulas give {recomputed}")
    for name, value in ratios.items():
        recomputed = derived["ratios"]["p"].get(name)
        if value is not None and not _is_number(value):
            problems.append(f"{name}: supplied {value!r} is not a number")
        elif value is None or recomputed is None:
            if value is not recomputed:
                problems.append(f"{name}: supplied {value}, the framework's formulas give {recomputed}")
        elif abs(value - recomputed) > RECONCILE_TOLERANCE:
            problems.append(f"{name}: supplied {value}, the framework's formulas give {recomputed}")
    if len(problems) > 4:
        problems = problems[:4] + [f"and {len(problems) - 4} more"]
    return problems


def build_downside(existing_downside, forecast_periods, financials, ratios, lines, stress, supplied):
    """The downside case to record: the analyst's own scenario where given, else the framework's shocks where they
    genuinely apply, else an explicit `unavailable` reason -- see the module docstring."""
    existing_downside = existing_downside if isinstance(existing_downside, dict) else {}
    old_basis = existing_downside.get("basis") if isinstance(existing_downside.get("basis"), dict) else {}
    old_fin = existing_downside.get("financials") if isinstance(existing_downside.get("financials"), dict) else {}
    old_rat = existing_downside.get("ratios") if isinstance(existing_downside.get("ratios"), dict) else {}

    down_fin, down_rat, basis, unavailable = {}, {}, {}, {}
    description = None
    kept = []
    for period, how in old_basis.items():                  # an earlier analyst scenario for a year not re-supplied
        if how == "analyst-supplied" and period in forecast_periods and period in old_fin | old_rat \
                and not (supplied and period in supplied["periods"]):
            down_fin[period], down_rat[period], basis[period] = old_fin.get(period), old_rat.get(period), how
            description = existing_downside.get("description")
            kept.append(period)
    if supplied:
        old_description = description.strip() if isinstance(description, str) else ""
        if kept and old_description and old_description != supplied["description"]:
            raise ForecastError(
                f"refused: the analyst scenario already recorded for {kept} is described as {old_description!r}, "
                f"but this call describes its scenario as {supplied['description']!r}, and a downside case has one "
                f"description. Restate {kept} in this call with the new description, or use the recorded one; "
                "nothing was written")
        description = supplied["description"]
        for period, block in supplied["periods"].items():
            if period not in forecast_periods:
                raise ForecastError(f"downside.periods.{period}: there is no analyst-supplied forecast for {period} "
                                    "to stress")
            down_fin[period], down_rat[period], basis[period] = block["subtotals"], block["ratios"], "analyst-supplied"

    wanted = bool(stress) or bool(basis)
    for period in forecast_periods:
        if period in basis:
            continue
        if not wanted:
            continue
        problems = reconciliation_problems(lines.get(period) or {}, financials.get(period) or {},
                                           ratios.get(period) or {}, stress) if stress else [
            "no downside scenario was supplied for this year (neither stress shocks nor a stressed forecast of the analyst's own)"]
        if problems:
            unavailable[period] = "; ".join(problems)
            continue
        derived = evaluate_downside_case({period: lines[period]}, stress)
        # Only figures every one of whose lines was supplied: the evaluator reads an omitted line as zero.
        down_fin[period] = {key: value for key, value in derived["financials"][period].items()
                            if key == "raw" or (key in SUBTOTAL_DEPENDS
                                                and all(line in lines[period] for line in SUBTOTAL_DEPENDS[key]))}
        down_rat[period] = derived["ratios"][period]       # every ratio's lines are in REQUIRED_LINES, checked above
        basis[period] = FRAMEWORK_DERIVED

    if not (down_fin or down_rat or unavailable):
        return {}
    case = {"financials": down_fin, "ratios": down_rat, "basis": basis, "unavailable": unavailable}
    if description:
        case["description"] = description
    return case


def plan(existing, supplied, stress_in, note):
    """The one set of fields to write, from the existing state and the validated input; raises ForecastError."""
    conflict = deal_conflict(existing)
    if conflict:
        raise ForecastError(f"refused: {conflict}.")
    if not isinstance(note, str) or not note.strip():
        raise ForecastError("refused: --note is required: the analyst's confirmed description of their forecast "
                            "convention, which the CAM's analyst-supplied caveat quotes")
    forecast = supplied["forecast"]
    sub_by_period = {p: b["subtotals"] for p, b in forecast.items() if b["subtotals"]}
    rat_by_period = {p: b["ratios"] for p, b in forecast.items() if b["ratios"]}
    line_by_period = {p: b["lines"] for p, b in forecast.items() if b["lines"]}

    financials = _merge(existing.get("financials"), sub_by_period)
    ratios = _merge(existing.get("ratios"), rat_by_period)
    lines = _merge(existing.get("analyst_supplied_financials"), line_by_period)
    stress = dict(existing.get("stress_assumptions") or {})
    stress.update(stress_in or {})
    for key in STRESS_KEYS:
        if stress.get(key) is not None and not _is_number(stress[key]):
            raise ForecastError(
                f"refused: the stress_assumptions recorded in state.json have a malformed {key} ({stress[key]!r}); "
                "expected a finite number or null. Correct or remove it by hand, or restore state.json from a "
                "backup; the file was not modified.")
    forecast_periods = [p for p in FORWARD_PERIOD_KEYS if p in financials or p in ratios]

    sentence = f"Forecast (FY+1 to FY+3) figures are analyst-supplied: {note.strip()}"
    old_note = (existing.get("financials_source_note") or "").strip()
    new_note = old_note if sentence in old_note else f"{old_note} {sentence}".strip()

    fields = {
        "financials": financials, "ratios": ratios,
        "financials_source": "analyst-supplied", "forecast_source": "analyst-supplied",
        "financials_source_note": new_note,
        "downside_case": build_downside(existing.get("downside_case"), forecast_periods, financials, ratios, lines,
                                        stress, supplied["downside"]),
    }
    if line_by_period or existing.get("analyst_supplied_financials"):
        fields["analyst_supplied_financials"] = lines
    if stress:
        fields["stress_assumptions"] = stress
    return fields


def record(company, proposal, supplied, stress_in, note):
    """Check the deal, then write the analyst-supplied forecast in one state update (write_state() refuses a newer
    schema version before touching the file). Returns the fields written."""
    try:
        existing = read_state(company, proposal, keys=STATE_KEYS) or {}
    except OSError as exc:
        raise StateError(f"could not read the deal's state: {' '.join(str(exc).split())}; nothing was recorded") from exc
    source = existing.get("forecast_source")
    if source is not None and source != "analyst-supplied":
        raise StateError('Cannot use state.json: "forecast_source" must be "analyst-supplied" when present; fix the '
                         "file by hand or restore it from a backup; the file was not modified.")
    fields = plan(existing, supplied, stress_in, note)
    before = _state_bytes(company, proposal)
    try:
        write_state(company, proposal, **fields)
    except OSError as exc:          # includes the lock timeout
        # write_state() replaces the file atomically, but an error can still come after the replacement (releasing the
        # lock), so "nothing was recorded" is said only when the file is shown to be what it was.
        reason = " ".join(str(exc).split())
        after = _state_bytes(company, proposal)
        if before is not UNKNOWN and after is not UNKNOWN and after == before:
            raise StateError(f"could not write the deal's state: {reason}; state.json is unchanged and nothing was "
                             "recorded") from exc
        raise StateError(f"the state update did not complete cleanly: {reason}. state.json may already hold this "
                         "forecast: inspect it before retrying") from exc
    return fields


def _state_bytes(company, proposal):
    """The deal's state.json as bytes (None if there is none yet), or UNKNOWN if it cannot be read."""
    try:
        with open(state_path(company, proposal), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return UNKNOWN


def _load(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as exc:
        raise ForecastError(f"cannot read {what} {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise ForecastError(f"{what} {path} is not valid JSON: {exc}") from exc


def summary(fields):
    downside = fields.get("downside_case") or {}
    return {
        "financials_source": fields["financials_source"],
        "forecast_source": fields["forecast_source"],
        "forecast_years": [p for p in FORWARD_PERIOD_KEYS if p in fields["financials"] or p in fields["ratios"]],
        "downside": {"basis": downside.get("basis") or {}, "unavailable": downside.get("unavailable") or {},
                     "description": downside.get("description")},
        "financials_source_note": fields["financials_source_note"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record analyst-supplied forward-year (FY+1 to FY+3) figures for /project exactly as given, "
                    "without recomputing them from raw lines (issue #124), together with an explicit downside "
                    "treatment: the analyst's own stressed forecast, the framework's shocks only where they "
                    "genuinely apply, or an 'unavailable' record the policy engine treats as UNRESOLVABLE. "
                    "Written in one state update; refused, with nothing written, if the deal cannot take it.")
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--forecast", required=True,
                         help='Path to a JSON file: {"forecast": {"FY+1": {"subtotals": {...}, "ratios": {...}, '
                              '"lines": {...optional raw breakdown}}}, "downside": {"description": "...", '
                              '"periods": {"FY+1": {"subtotals": {...}, "ratios": {...}}}}}; numbers as the '
                              "analyst gave them, a ratio that is N/A as null")
    parser.add_argument("--stress-assumptions",
                         help='Optional path to a JSON file: {"revenue_haircut_pct": ..., "opex_increase_pct": ..., '
                              '"interest_rate_bump_bps": ...}; the framework applies them only to years whose '
                              "supplied raw lines reproduce the supplied figures")
    parser.add_argument("--note", required=True,
                         help="The analyst's confirmed description of their forecast convention; appended to "
                              "financials_source_note, which the CAM's analyst-supplied caveat quotes")
    args = parser.parse_args(argv)
    try:
        supplied = validate(_load(args.forecast, "forecast file"))
        stress = validate_stress(_load(args.stress_assumptions, "stress file")) if args.stress_assumptions else None
        fields = record(args.company, args.proposal, supplied, stress, args.note)
    except (ForecastError, StateError) as exc:
        sys.exit(f"error: {exc}")
    print(json.dumps(summary(fields), indent=2))


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    main()
