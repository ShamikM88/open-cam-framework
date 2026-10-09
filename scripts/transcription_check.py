"""Standalone CLI: safeguards for financial figures a /spread session transcribed from an image (issue #132).

A screenshot, a photographed page or a scanned statement is read by the session, not parsed by code, so a misread
digit, a dropped decimal, a flipped sign or a misaligned column would otherwise become the deal's ground truth with
nothing to notice it: in analyst-supplied mode nothing recomputes the figures at all, and in framework-computed mode
the ratios are recomputed from whatever raw lines were transcribed. This script puts two things between the
transcription and `state.json`, for both /spread modes:

1. A read-back (the default; it never touches a deal): the transcribed figures grouped by period and statement, each
   shown as written beside the value that would be recorded and how the one was read as the other, plus a cross-foot
   of the source's own subtotals against the sums of its transcribed lines, a fingerprint of the image and a digest of
   exactly what was shown.
2. A commit (`--commit --confirm <digest>`): refused, with nothing written, unless the digest matches the staged
   figures and the image as they are now (so any change after the read-back, including replacing the image, needs a
   new read-back), no cross-foot discrepancy is left unresolved, and the deal's existing basis is compatible. Only
   then does it save the image (verified against the fingerprint), record the figures and note in the state that they
   were transcribed from an image.

What code enforces here: the ordering (no state write before a read-back of the same figures and the same image was
produced and its digest quoted), that the figures parse strictly (no guessing), that every sign is read under an
explicit, confirmed convention, the cross-foot arithmetic, that a discrepancy blocks the commit until it is corrected
or acknowledged with a stated reason (never one that a different sign reading would explain), and that one deal never
mixes the two /spread modes. What code cannot enforce: that the analyst actually said yes (the session passes the
digest), that the transcription is true to the image, and that an image input is routed through this script at all --
those are instructions in .claude/commands/spread.md.

Signs. The framework reads raw lines with fixed semantics (spreading_builder.evaluate_financial_model): costs and cash
outflows are entered as positive amounts and subtracted, liabilities as positive amounts, a negative cost is a credit
or reversal. A source often presents them differently (a cost in brackets, creditors in brackets). So the staged file
declares, per class of line (`source.cost_sign`, `source.outflow_sign`, `source.liability_sign`), whether the source
writes an amount of that kind as a "positive" or a "negative" number; the figure is kept exactly as written, and the
value recorded is the written one ("positive") or its reverse ("negative"). Both are shown in the read-back and both
the declaration and the figures are in the digest. Subtotals and ratios are results with a sign of their own and are
recorded as written. The cross-foot uses the recorded values, so it checks what the framework will actually be given.

The cross-foot compares a subtotal the source states with the same subtotal derived from the recorded raw lines by
spreading_builder.evaluate_financial_model() (the framework's own formulas; which lines feed a subtotal, and with what
sign, is discovered from that function rather than written down a second time). Presented statements are rounded, so
the tolerance is the rounding the figures as written allow: half a unit of the last digit of the stated subtotal plus
half a unit of the last digit of every line that feeds it. A check is only made when every feeding line was
transcribed (an explicit "0" counts; an omitted line is unknown, not zero). Ratios are never cross-footed: the
rounding of their inputs cannot be bounded.

No `anthropic` dependency, no OCR and no network, matching policy_check.py/spreading_check.py.
"""
import argparse
import hashlib
import itertools
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date
from decimal import Decimal

from source_manifest import save_source, sources_dir
from spreading_builder import FIELD_LABELS, HISTORICAL_PERIOD_KEYS, evaluate_financial_model
from spreading_check import compute
from state_manager import StateError, read_state, write_state

MODES = ("framework-computed", "analyst-supplied")
SOURCE_KINDS = ("image", "scanned-document")
RAW_FIELDS = tuple(FIELD_LABELS)

# Subtotals and ratios an analyst-supplied deal records (the exact names .claude/commands/spread.md lists).
SUBTOTAL_FIELDS = (
    "gross_profit", "operating_profit", "ebitda", "profit_before_tax", "net_profit", "fcf", "current_assets",
    "current_liabilities", "total_assets", "total_liabilities", "total_equity", "total_debt", "tangible_net_worth",
)
RATIO_FIELDS = (
    "dscr", "gross_leverage", "net_debt_to_ebitda", "current_ratio", "gearing", "ebit_interest_cover",
    "ebitda_interest_cover", "fcf_conversion_pct",
)

# The raw lines whose sign a source may present the opposite way to the framework's input convention (costs, cash
# outflows and liabilities are entered as positive amounts). Income, assets and equity lines are recorded as written.
SIGN_CLASSES = {
    "cost_sign": ("cost_of_sales", "admin_expenses", "depreciation", "amortisation", "interest_paid",
                  "exceptional_costs", "tax_paid"),
    "outflow_sign": ("scheduled_principal", "capex"),
    "liability_sign": ("trade_creditors", "other_current_liabilities", "overdraft", "current_debt", "long_term_debt",
                       "loan_notes", "other_long_term_liabilities", "provisions"),
}
SIGN_LABELS = {"cost_sign": "costs", "outflow_sign": "cash outflows", "liability_sign": "liabilities"}
SIGN_VALUES = ("positive", "negative")
FIELD_SIGN_CLASS = {field: key for key, fields in SIGN_CLASSES.items() for field in fields}

# How the read-back groups the raw lines (so a reader can compare it with the statement in the image).
LINE_GROUPS = (
    ("Profit and loss", ("revenue", "cost_of_sales", "admin_expenses", "depreciation", "amortisation", "other_income",
                         "interest_paid", "interest_received", "exceptional_costs", "tax_paid")),
    ("Debt service and capital expenditure", ("scheduled_principal", "capex")),
    ("Balance sheet: assets", ("cash", "trade_debtors", "stock", "other_current_assets", "tangible_assets",
                               "intangible_assets", "other_fixed_assets")),
    ("Balance sheet: liabilities", ("trade_creditors", "other_current_liabilities", "overdraft", "current_debt",
                                    "long_term_debt", "loan_notes", "other_long_term_liabilities", "provisions")),
    ("Balance sheet: equity", ("share_capital", "retained_profit")),
)

BALANCE_CHECK = "balance_sheet_balances"
DIGEST_LENGTH = 16
TOP_LEVEL_KEYS = {"mode", "source", "periods", "acknowledged"}
SOURCE_KEYS = {"file", "kind", "description", "unit", *SIGN_CLASSES}
PERIOD_KEYS = {"lines", "subtotals", "ratios"}
FINANCIAL_STORES = ("financials", "ratios", "multi_period_financials", "analyst_supplied_financials")


class TranscriptionError(ValueError):
    """The staged transcription is unusable, or a commit was refused. Nothing was written."""


# ---------------------------------------------------------------------------
# Figures as written
# ---------------------------------------------------------------------------

_FIGURE_RE = re.compile(r"(?P<open>\()?(?P<sign>-)?(?P<whole>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?(?P<close>\))?")


class Figure:
    """One figure exactly as written in the image (`text`, read as the number `written_value`) and the number that
    is recorded (`value`). The two differ only by a declared sign convention, and `note` then says so."""

    def __init__(self, text, value, decimals, written_value=None, note=""):
        self.text = text
        self.value = value
        self.decimals = decimals
        self.written_value = value if written_value is None else written_value
        self.note = note

    @property
    def unit(self):
        """The size of the last written digit: what the figure was rounded to."""
        return Decimal(1).scaleb(-self.decimals)

    def reversed(self, note=""):
        """The same written figure recorded with the opposite sign (never a negative zero)."""
        return Figure(self.text, -self.value if self.value else self.value, self.decimals, self.written_value, note)

    def number(self):
        """The value as it is recorded in state.json: an int when written without decimals, else a float."""
        return int(self.value) if self.decimals == 0 else float(self.value)


def parse_figure(text, label, allow_suffix=False):
    """Read a figure as written. Accepts `1,234.5`, `-1234`, `(1,234)` (parentheses mean negative) and, when
    `allow_suffix`, a trailing `x` or `%` (a ratio, recorded as the number written). Anything else -- a currency
    symbol, a comma decimal such as `1,5`, `n/a`, a dash -- is refused rather than guessed at. Whether a negative is a
    cost shown in brackets or a genuine credit is a separate, declared question (see the module docstring)."""
    if not isinstance(text, str) or not text.strip():
        raise TranscriptionError(f"{label}: write the figure as text exactly as it appears (for example \"1,234.5\" "
                                 f"or \"(500)\"), found {text!r}")
    written = text.strip()
    cleaned = written.replace("−", "-").replace("–", "-")
    if allow_suffix and cleaned[-1] in "x%X":
        cleaned = cleaned[:-1].strip()
    match = _FIGURE_RE.fullmatch(cleaned)
    if not match or bool(match["open"]) != bool(match["close"]) or (match["open"] and match["sign"]):
        raise TranscriptionError(
            f"{label}: cannot read {written!r} as a figure. Write the number only, with thousands separated by commas "
            "and a point for decimals; negatives as -500 or (500); no currency symbol (put the unit in source.unit). "
            "If the image shows something else (a dash, n/a, a comma decimal), ask the analyst what it means.")
    frac = match["frac"] or ""
    value = Decimal(match["whole"].replace(",", "") + ("." + frac if frac else ""))
    if match["open"] or match["sign"]:
        value = -value
    return Figure(written, value, len(frac))


def file_sha256(path):
    """The SHA-256 of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise TranscriptionError(f"cannot read the source file {path!r}: {exc.strerror or exc}") from exc
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Which raw lines feed which subtotal (discovered from the framework's own formulas)
# ---------------------------------------------------------------------------

def _coefficients():
    """{subtotal: {raw field: signed weight}}. Every subtotal evaluate_financial_model() derives from raw lines is a
    sum of them, so the weight of a field is the subtotal's value when that field alone is 1."""
    weights = {name: {} for name in SUBTOTAL_FIELDS}
    for field in RAW_FIELDS:
        derived = evaluate_financial_model({"probe": {field: 1}})["financials"]["probe"]
        for name in SUBTOTAL_FIELDS:
            weight = derived.get(name) or 0
            if weight:
                weights[name][field] = Decimal(str(weight))
    return weights


COEFFICIENTS = _coefficients()
# Total assets less total liabilities less total equity: zero on a balance sheet that balances.
BALANCE_COEFFICIENTS = {
    field: COEFFICIENTS["total_assets"].get(field, Decimal(0)) - COEFFICIENTS["total_liabilities"].get(field, Decimal(0))
    - COEFFICIENTS["total_equity"].get(field, Decimal(0))
    for field in RAW_FIELDS
}
BALANCE_COEFFICIENTS = {field: weight for field, weight in BALANCE_COEFFICIENTS.items() if weight}


# ---------------------------------------------------------------------------
# Loading and validating the staged transcription
# ---------------------------------------------------------------------------

def _reject_unknown(mapping, allowed, where):
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise TranscriptionError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


def _figures(mapping, allowed, label, allow_suffix=False):
    if not isinstance(mapping, dict):
        raise TranscriptionError(f"{label} must be an object of figures written as text")
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise TranscriptionError(f"{label}: unknown name(s) {unknown}; allowed: {list(allowed)}")
    return {name: parse_figure(text, f"{label}.{name}", allow_suffix) for name, text in mapping.items()}


def _apply_sign_conventions(source, periods):
    """Check each class of line has a declared convention wherever it is used, then record the figures of a class
    declared "negative" with their sign reversed. Returns the declarations."""
    declared = {}
    for key in SIGN_CLASSES:
        if key in source:
            if source[key] not in SIGN_VALUES:
                raise TranscriptionError(f"source.{key} must be one of {list(SIGN_VALUES)}, found {source[key]!r}")
            declared[key] = source[key]
    for key, fields in SIGN_CLASSES.items():
        used = sorted({field for entry in periods.values() for field in entry["lines"] if field in fields})
        if used and key not in declared:
            raise TranscriptionError(
                f"source.{key} is required because {used} are transcribed. Say how the source writes "
                f"{SIGN_LABELS[key]}: \"positive\" if an amount of that kind is written as a positive number (a credit "
                "or reversal would then be negative), or \"negative\" if it is written in brackets or with a minus "
                "sign (a credit or reversal would then be positive). The framework records them as positive "
                "amounts. If the image does not make this plain, ask the analyst; it is never guessed.")
        if declared.get(key) == "negative":
            for entry in periods.values():
                for field in [f for f in entry["lines"] if f in fields]:
                    entry["lines"][field] = entry["lines"][field].reversed(
                        f"sign reversed: {SIGN_LABELS[key]} are written as negatives in this source")
    return declared


def validate(data):
    """The staged JSON as a checked structure, or TranscriptionError. See the module docstring and spread.md for
    the shape: {mode, source: {file, kind, description, unit, cost_sign, outflow_sign, liability_sign}, periods:
    {period: {lines, subtotals, ratios}}, acknowledged: [{period, check, reason}]}."""
    if not isinstance(data, dict):
        raise TranscriptionError("the staged transcription must be a JSON object")
    _reject_unknown(data, TOP_LEVEL_KEYS, "transcription")
    mode = data.get("mode")
    if mode not in MODES:
        raise TranscriptionError(f"mode must be one of {list(MODES)}, found {mode!r}")

    source = data.get("source")
    if not isinstance(source, dict):
        raise TranscriptionError("source must be an object with file, kind, description and unit")
    _reject_unknown(source, SOURCE_KEYS, "source")
    for key in ("file", "description", "unit"):
        if not isinstance(source.get(key), str) or not source[key].strip():
            raise TranscriptionError(f"source.{key} is required (the unit, for example \"GBP thousands\", must be "
                                     "read from the image and shown to the analyst)" if key == "unit"
                                     else f"source.{key} is required and must be non-empty text")
    if source.get("kind") not in SOURCE_KINDS:
        raise TranscriptionError(f"source.kind must be one of {list(SOURCE_KINDS)}, found {source.get('kind')!r}")

    periods_in = data.get("periods")
    if not isinstance(periods_in, dict) or not periods_in:
        raise TranscriptionError("periods must be a non-empty object keyed by period")
    unknown_periods = sorted(set(periods_in) - set(HISTORICAL_PERIOD_KEYS))
    if unknown_periods:
        raise TranscriptionError(f"unknown period(s) {unknown_periods}; /spread records {HISTORICAL_PERIOD_KEYS}")
    periods = {}
    for period in HISTORICAL_PERIOD_KEYS:
        if period not in periods_in:
            continue
        body = periods_in[period]
        if not isinstance(body, dict):
            raise TranscriptionError(f"periods.{period} must be an object")
        _reject_unknown(body, PERIOD_KEYS, f"periods.{period}")
        entry = {
            "lines": _figures(body.get("lines") or {}, RAW_FIELDS, f"{period}.lines"),
            "subtotals": _figures(body.get("subtotals") or {}, SUBTOTAL_FIELDS, f"{period}.subtotals"),
            "ratios": _figures(body.get("ratios") or {}, RATIO_FIELDS, f"{period}.ratios", allow_suffix=True),
        }
        if mode == "framework-computed":
            if entry["ratios"]:
                raise TranscriptionError(f"{period}: ratios are recomputed from the lines in framework-computed mode "
                                         "and are not recorded from the image; remove them (subtotals may stay, for "
                                         "the cross-foot only)")
            if not entry["lines"]:
                raise TranscriptionError(f"{period}: framework-computed mode needs the raw lines; a period with only "
                                         "subtotals has nothing to compute")
        elif not (entry["lines"] or entry["subtotals"] or entry["ratios"]):
            raise TranscriptionError(f"{period}: no figures given")
        periods[period] = entry
    declared = _apply_sign_conventions(source, periods)

    acknowledged = data.get("acknowledged") or []
    if not isinstance(acknowledged, list):
        raise TranscriptionError("acknowledged must be a list of {period, check, reason}")
    allowed_checks = set(SUBTOTAL_FIELDS) | {BALANCE_CHECK}
    acks = {}
    for index, ack in enumerate(acknowledged):
        if not isinstance(ack, dict):
            raise TranscriptionError(f"acknowledged[{index}] must be an object")
        _reject_unknown(ack, {"period", "check", "reason"}, f"acknowledged[{index}]")
        if ack.get("period") not in periods:
            raise TranscriptionError(f"acknowledged[{index}].period {ack.get('period')!r} is not a period in this "
                                     "transcription")
        if ack.get("check") not in allowed_checks:
            raise TranscriptionError(f"acknowledged[{index}].check {ack.get('check')!r} is not a cross-foot check; "
                                     f"one of {sorted(allowed_checks)}")
        if not isinstance(ack.get("reason"), str) or not ack["reason"].strip():
            raise TranscriptionError(f"acknowledged[{index}] needs the analyst's reason, in their words")
        key = (ack["period"], ack["check"])
        if key in acks:
            raise TranscriptionError(f"acknowledged lists {key[1]} for {key[0]} more than once")
        acks[key] = ack["reason"].strip()

    return {
        "mode": mode,
        "source": {key: source[key].strip() for key in ("file", "description", "unit")} | {"kind": source["kind"]}
        | declared,
        "periods": periods,
        "acknowledged": acks,
    }


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        raise TranscriptionError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise TranscriptionError(f"{path} is not valid JSON: {exc}") from exc
    return validate(data)


def digest_of(staged, source_sha256):
    """A short digest of exactly what the read-back shows: the mode, the source (including the declared sign
    conventions and the fingerprint of the image's bytes), every figure as written and every acknowledgement. Change
    any of it, or replace the image, and the digest changes, so a confirmation of the old read-back stops working."""
    canonical = {
        "mode": staged["mode"],
        "source": staged["source"],
        "source_sha256": source_sha256,
        "periods": {
            period: {part: {name: figure.text for name, figure in sorted(figures.items())}
                     for part, figures in sorted(entry.items())}
            for period, entry in staged["periods"].items()
        },
        "acknowledged": sorted([period, check, reason] for (period, check), reason in staged["acknowledged"].items()),
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


# ---------------------------------------------------------------------------
# Cross-foot
# ---------------------------------------------------------------------------

def _check(name, weights, lines, stated):
    """One comparison: `stated` (a Figure, or None for the balance-sheet identity, which should come to zero)
    against the weighted sum of the recorded values of the transcribed lines that feed it."""
    missing = [field for field in weights if field not in lines]
    result = {"check": name, "status": "not_assessed", "stated": stated.text if stated else None, "missing": missing}
    if missing:
        return result
    computed = sum((weight * lines[field].value for field, weight in weights.items()), Decimal(0))
    tolerance = Decimal("0.5") * (sum((abs(weight) * lines[field].unit for field, weight in weights.items()),
                                      Decimal(0)) + (stated.unit if stated else Decimal(0)))
    difference = (stated.value if stated else Decimal(0)) - computed
    result.update(status="match" if abs(difference) <= tolerance else "mismatch", computed=str(computed),
                  difference=str(difference), tolerance=str(tolerance))
    return result


def _sign_alternative(name, weights, lines, stated, classes):
    """The classes of line that, read with the opposite sign, would make this check match -- or None. A mismatch that
    a different sign reading explains is a doubt about the declaration, not a difference of definition."""
    for size in range(1, len(classes) + 1):
        for combination in itertools.combinations(classes, size):
            flipped = {field: figure.reversed() if FIELD_SIGN_CLASS.get(field) in combination else figure
                       for field, figure in lines.items()}
            if _check(name, weights, flipped, stated)["status"] == "match":
                return combination
    return None


def cross_foot(staged):
    """{"periods": {period: [check results]}, "unresolved": [[period, check]], "refused_acknowledgements": [...],
    "unused_acknowledgements": [...], "ready": bool}. A mismatch is "acknowledged" only when the staged file carries
    a reason for exactly that period and check, and only if no different sign reading would make it match; it
    still shows in the read-back."""
    results, unresolved, used, refused = {}, [], set(), []
    declared = [key for key in SIGN_CLASSES if key in staged["source"]]
    for period, entry in staged["periods"].items():
        lines, subtotals, rows, plans = entry["lines"], entry["subtotals"], [], {}
        for name in SUBTOTAL_FIELDS:
            if name in subtotals:
                plans[name] = (COEFFICIENTS[name], subtotals[name])
        if any(field in lines for field in BALANCE_COEFFICIENTS):
            plans[BALANCE_CHECK] = (BALANCE_COEFFICIENTS, None)
        present = [key for key in declared if any(field in lines for field in SIGN_CLASSES[key])]
        for name, (weights, stated) in plans.items():
            row = _check(name, weights, lines, stated)
            if row["status"] == "mismatch":
                alternative = _sign_alternative(name, weights, lines, stated, present)
                reason = staged["acknowledged"].get((period, name))
                if alternative:
                    row["sign_suspect"] = "matches if " + " and ".join(SIGN_LABELS[k] for k in alternative) + \
                        " were read with the opposite sign"
                    if reason:
                        refused.append([period, name])
                    unresolved.append([period, name])
                elif reason:
                    row["status"], row["reason"] = "acknowledged", reason
                    used.add((period, name))
                else:
                    unresolved.append([period, name])
            rows.append(row)
        results[period] = rows
    covered = used | {tuple(pair) for pair in refused}
    unused = sorted([period, check] for (period, check) in staged["acknowledged"] if (period, check) not in covered)
    return {"periods": results, "unresolved": unresolved, "refused_acknowledgements": refused,
            "unused_acknowledgements": unused, "ready": not unresolved}


def notices(staged):
    """{period: [text]} things to look at that are not discrepancies: a declared sign convention that every line
    contradicts, and every line that will be recorded as negative (a genuine credit, reversal or deficit?)."""
    found = {}
    for period, entry in staged["periods"].items():
        items = []
        for key, fields in SIGN_CLASSES.items():
            if key not in staged["source"]:
                continue
            written = [entry["lines"][f].written_value for f in fields if f in entry["lines"]
                       and entry["lines"][f].written_value != 0]
            declared = staged["source"][key]
            if written and ((declared == "positive" and all(v < 0 for v in written))
                            or (declared == "negative" and all(v > 0 for v in written))):
                items.append(f"all the {SIGN_LABELS[key]} are written as {'negative' if declared == 'positive' else 'positive'}"
                             f" numbers, but {key} is declared {declared}: check the declaration against the image")
        negatives = [f"{_label(f)} (written {fig.text}, recorded {fig.value})" for f, fig in entry["lines"].items()
                     if fig.value < 0]
        if negatives:
            items.append("recorded as negative, so a credit, a reversal or a deficit: " + "; ".join(negatives)
                         + ". Confirm each is genuinely so")
        if items:
            found[period] = items
    return found


# ---------------------------------------------------------------------------
# The read-back
# ---------------------------------------------------------------------------

def _label(name):
    return FIELD_LABELS.get(name) or name.replace("_", " ").capitalize()


def _table(rows, interpretation=True):
    if interpretation:
        out = ["| Line item | As written | Recorded as | Interpretation |", "| :--- | :--- | ---: | :--- |"]
        return out + [f"| {label} | {written} | {recorded} | {note} |" for label, written, recorded, note in rows]
    out = ["| Figure | As written | Recorded as |", "| :--- | :--- | ---: |"]
    return out + [f"| {label} | {written} | {recorded} |" for label, written, recorded, _ in rows]


def _convention_lines(staged):
    lines = []
    for key, fields in SIGN_CLASSES.items():
        if key not in staged["source"] or not any(f in entry["lines"] for entry in staged["periods"].values()
                                                  for f in fields):
            continue
        label = SIGN_LABELS[key]
        if staged["source"][key] == "positive":
            lines.append(f"- Sign convention, {label}: written as positive numbers in the source, so recorded as "
                         "written (a negative would be a credit or reversal).")
        else:
            lines.append(f"- Sign convention, {label}: written as negative numbers (brackets or a minus sign) in the "
                         "source, so each is recorded with its sign reversed: (700) is recorded as 700, and a "
                         "positive figure is recorded as a negative (a credit or reversal).")
    return lines


def render_readback(staged, report, digest, source_sha256):
    source = staged["source"]
    out = [
        "TRANSCRIPTION READ-BACK -- nothing has been recorded",
        "",
        f"- Source: {source['file']} ({source['kind']}): {source['description']}",
        f"- Source fingerprint (SHA-256): {source_sha256}",
        f"- Unit: {source['unit']} (figures are recorded as written; nothing is scaled)",
        f"- Mode: {staged['mode']}",
        *_convention_lines(staged),
        "- Subtotals and ratios are results with a sign of their own and are recorded as written.",
        f"- Digest: {digest}",
        "",
        "Confirm only if every figure below matches the source exactly: digits, decimals, signs and the unit, and "
        "if each sign convention above is how the source really writes those lines.",
    ]
    for period, entry in staged["periods"].items():
        out += ["", f"## {period}"]
        for group, fields in LINE_GROUPS:
            rows = [(_label(f), entry["lines"][f].text, f"{entry['lines'][f].value}",
                     entry["lines"][f].note or "as written") for f in fields if f in entry["lines"]]
            if rows:
                out += ["", group, ""] + _table(rows)
        for title, key, order in (("Subtotals stated by the source", "subtotals", SUBTOTAL_FIELDS),
                                  ("Ratios stated by the source", "ratios", RATIO_FIELDS)):
            rows = [(_label(f), entry[key][f].text, f"{entry[key][f].value}", "") for f in order if f in entry[key]]
            if rows:
                out += ["", title, ""] + _table(rows, interpretation=False)
    attention = notices(staged)
    if attention:
        out += ["", "## Look at these before confirming", ""]
        out += [f"- {period}: {item}" for period, items in attention.items() for item in items]
    out += ["", "## Cross-foot against the source's own subtotals", ""]
    rows = [(period, row) for period, period_rows in report["periods"].items() for row in period_rows]
    if rows:
        out += ["| Period | Check | Source states | Lines give | Difference | Tolerance | Result |",
                "| :--- | :--- | ---: | ---: | ---: | ---: | :--- |"]
        for period, row in rows:
            if row["status"] == "not_assessed":
                result = "NOT ASSESSED: not transcribed: " + ", ".join(row["missing"])
                cells = [row["stated"] or "-", "-", "-", "-"]
            else:
                result = {"match": "match", "mismatch": "MISMATCH",
                          "acknowledged": f"ACKNOWLEDGED: {row.get('reason', '')}"}[row["status"]]
                if row.get("sign_suspect"):
                    result += f" ({row['sign_suspect']}: correct the sign declaration; an acknowledgement cannot " \
                              "cover this)"
                cells = [row["stated"] or "0 (balance sheet identity)", row["computed"], row["difference"],
                         row["tolerance"]]
            out.append(f"| {period} | {row['check']} | " + " | ".join(cells) + f" | {result} |")
    else:
        out.append("No source subtotal was given and no balance sheet lines were transcribed, so there is nothing "
                   "to compare. This is not a check that passed.")
    out += ["", "\"Lines give\" uses the recorded values (after the sign conventions above), so the cross-foot checks "
                "what the framework will actually be given. Tolerance is the rounding the figures as written allow: "
                "half a unit of the last written digit of the subtotal plus half a unit of the last written digit of "
                "each line that feeds it. A check is made only when every feeding line was transcribed; an omitted "
                "line is unknown, not zero."]
    has_ratios = any(entry["ratios"] for entry in staged["periods"].values())
    out.append("Ratios are " + ("recorded exactly as given and are not cross-footed (the rounding of their inputs "
                                "cannot be bounded)." if has_ratios else "recomputed by the framework from the lines."))
    if report["unused_acknowledgements"]:
        out.append("Acknowledgements that match no discrepancy (ignored, shown so none is hidden): "
                   + ", ".join(f"{p}/{c}" for p, c in report["unused_acknowledgements"]))
    if report["refused_acknowledgements"]:
        out.append("Acknowledgements REFUSED because a different sign reading would explain the mismatch: "
                   + ", ".join(f"{p}/{c}" for p, c in report["refused_acknowledgements"]))
    out.append("")
    if report["ready"]:
        out.append("STATUS: ready for the analyst's confirmation.")
    else:
        out.append("STATUS: BLOCKED. Resolve each MISMATCH: correct the transcription or the sign declaration, or "
                   "(only where no sign reading explains it) record the analyst's reason in `acknowledged`, then read "
                   "back again. A commit is refused until then.")
    out += ["Show this read-back to the analyst unchanged. Only an explicit confirmation of these figures permits:",
            f"python scripts/transcription_check.py --transcription <file> --commit --confirm {digest} "
            "--company <company> --proposal <proposal>"]
    return "\n".join(out)


def readback(staged):
    report = cross_foot(staged)
    fingerprint = file_sha256(staged["source"]["file"])
    digest = digest_of(staged, fingerprint)
    return {"digest": digest, "source_sha256": fingerprint, "report": report,
            "text": render_readback(staged, report, digest, fingerprint)}


# ---------------------------------------------------------------------------
# The commit
# ---------------------------------------------------------------------------

def _merge_periods(existing, new_by_period):
    merged = dict(existing or {})
    for period, fields in new_by_period.items():
        merged_period = dict(merged.get(period) or {})
        merged_period.update(fields)
        merged[period] = merged_period
    return merged


def disclosure_sentence(filename):
    return (f"Figures were transcribed from an image ({filename}) and confirmed line by line by the analyst "
            "against it.")


def mode_conflict(existing, mode):
    """Why `mode` cannot be added to a deal with this state, or None. `financials_source` is a whole-deal flag: a
    deal's figures are all framework-computed or all analyst-supplied, and this never converts one into the other.
    A deal that records financial figures but no `financials_source` (an older or hand-edited state) has an unknown
    basis, so it is treated as a conflict rather than assumed to be either mode; a deal with no figures yet has none."""
    recorded = existing.get("financials_source")
    if recorded in MODES:
        if recorded != mode:
            return (f"this deal's financials_source is {recorded!r} and this transcription is {mode!r}. One deal "
                    "never mixes the two bases, and nothing here converts a deal from one to the other: record this "
                    "under a different proposal, or ask the analyst whether to redo the deal's spreading in one mode")
        return None
    if any(existing.get(store) for store in FINANCIAL_STORES):
        return ("this deal records financial figures but no financials_source, so their basis is unknown and a "
                f"{mode!r} transcription cannot safely be added to them. Set financials_source by hand once the "
                "basis is known, or use a different proposal")
    return None


def commit(staged, confirm, company, proposal, source_note=None):
    """Record a confirmed transcription. Every refusal raises TranscriptionError before anything is written."""
    shown = readback(staged)
    if not isinstance(confirm, str) or confirm.strip() != shown["digest"]:
        raise TranscriptionError(
            "refused: the confirmation does not match these figures. Read them back again, show the analyst the new "
            f"read-back (digest {shown['digest']}) and commit only the digest the analyst confirmed. Any change "
            "to the figures, the unit, a sign convention, the source description, an acknowledgement or the image "
            "file itself makes an earlier confirmation stale.")
    if not shown["report"]["ready"]:
        blocked = ", ".join(f"{p}/{c}" for p, c in shown["report"]["unresolved"])
        raise TranscriptionError(
            f"refused: unresolved cross-foot discrepancies ({blocked}). Correct the transcription or the sign "
            "declaration, or (where no different sign reading explains it) record the analyst's reason in "
            "`acknowledged`, read back again and have the new read-back confirmed.")
    source_path = staged["source"]["file"]
    if staged["mode"] == "analyst-supplied" and not (source_note and source_note.strip()):
        raise TranscriptionError("refused: analyst-supplied mode needs --source-note, the confirmed description of "
                                 "the analyst's convention (spread.md asks for it); the disclosure of the image "
                                 "transcription is added to it")
    if staged["mode"] == "framework-computed" and source_note:
        raise TranscriptionError("refused: --source-note is only used in analyst-supplied mode")

    existing = read_state(company, proposal, keys=(
        "financials", "ratios", "analyst_supplied_financials", "multi_period_financials", "financials_source",
        "steps_completed")) or {}
    conflict = mode_conflict(existing, staged["mode"])
    if conflict:
        raise TranscriptionError(f"refused: {conflict}.")
    earlier = existing.get("financials_transcriptions")
    if earlier is not None and not isinstance(earlier, list):
        raise StateError('Cannot use state.json: "financials_transcriptions" must be a list ([...]); fix the file by '
                         "hand or restore it from a backup; the file was not modified.")

    # Copy the image aside first and check the copy against the confirmed fingerprint, so the exact bytes that are
    # saved as the source of record are the ones the analyst's confirmed read-back covered.
    source = staged["source"]
    with tempfile.TemporaryDirectory() as scratch:
        staging_copy = os.path.join(scratch, os.path.basename(source_path))
        shutil.copyfile(source_path, staging_copy)
        if file_sha256(staging_copy) != shown["source_sha256"]:
            raise TranscriptionError("refused: the source image changed while it was being read; nothing was saved. "
                                     "Read back again.")
        entry = save_source(company, proposal, step="spread", source_path=staging_copy,
                            claim=f"{source['description']} (figures transcribed from {source['kind']}; "
                                  f"analyst-confirmed read-back {shown['digest']}; "
                                  f"sha256 {shown['source_sha256'][:16]})")
    saved = os.path.join(sources_dir(company, proposal), entry["filename"])
    if file_sha256(saved) != shown["source_sha256"]:
        raise TranscriptionError(
            f"refused: the saved copy {saved!r} does not match the fingerprint of the confirmed read-back; no figures "
            "were recorded. Delete that file and its sources/manifest.json entry, then read back and commit again.")

    periods = list(staged["periods"])
    fields = {}
    if staged["mode"] == "framework-computed":
        compute(company, proposal, {period: {name: figure.number() for name, figure in entry_["lines"].items()}
                                    for period, entry_ in staged["periods"].items()})
        financials_source = "framework-computed"
    else:
        by_part = {part: {period: {name: figure.number() for name, figure in staged["periods"][period][part].items()}
                          for period in periods if staged["periods"][period][part]}
                   for part in ("subtotals", "ratios", "lines")}
        if by_part["subtotals"]:
            fields["financials"] = _merge_periods(existing.get("financials"), by_part["subtotals"])
        if by_part["ratios"]:
            fields["ratios"] = _merge_periods(existing.get("ratios"), by_part["ratios"])
        if by_part["lines"]:
            fields["analyst_supplied_financials"] = _merge_periods(existing.get("analyst_supplied_financials"),
                                                                    by_part["lines"])
        fields["financials_source"] = financials_source = "analyst-supplied"
        fields["financials_source_note"] = f"{source_note.strip()} {disclosure_sentence(entry['filename'])}"

    rows = [(period, row) for period, period_rows in shown["report"]["periods"].items() for row in period_rows]
    record = {
        "method": source["kind"], "mode": staged["mode"], "source_file": entry["filename"],
        "source_sha256": shown["source_sha256"], "unit": source["unit"],
        "sign_conventions": {key: source[key] for key in SIGN_CLASSES if key in source},
        "periods": periods, "confirmed_digest": shown["digest"], "confirmed_on": date.today().isoformat(),
        "cross_foot": {
            "matched": [f"{period}/{row['check']}" for period, row in rows if row["status"] == "match"],
            "acknowledged": [{"period": period, "check": row["check"], "difference": row["difference"],
                              "reason": row["reason"]} for period, row in rows if row["status"] == "acknowledged"],
            "not_assessed": [f"{period}/{row['check']}" for period, row in rows if row["status"] == "not_assessed"],
            "ratios_cross_footed": False,
        },
    }
    record_fields = {"financials_transcriptions": [*(earlier or []), record]}
    if staged["mode"] == "framework-computed":      # compute() has already written the figures; add the record
        steps = (read_state(company, proposal, keys=("steps_completed",)) or {}).get("steps_completed") or []
    else:
        steps = existing.get("steps_completed") or []
        record_fields.update(fields)
    record_fields["steps_completed"] = [*steps, "spread"] if "spread" not in steps else list(steps)
    write_state(company, proposal, **record_fields)
    return {"committed": True, "mode": staged["mode"], "periods": periods, "financials_source": financials_source,
            "source_file": entry["filename"], "source_sha256": shown["source_sha256"], "digest": shown["digest"],
            "cross_foot": record["cross_foot"], "disclosure": disclosure_sentence(entry["filename"])}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Safeguards for /spread figures transcribed from an image (issue #132). By default: read the "
                    "staged transcription back (grouped by period, as written beside the recorded value and how one "
                    "was read as the other), cross-foot it against the source's own subtotals and print the image's "
                    "fingerprint and a digest -- this never reads or writes a deal. With --commit and --confirm "
                    "<digest> (the digest of the read-back the analyst confirmed): save the image and record the "
                    "figures; refused, with nothing written, if the digest is stale (including a changed image), a "
                    "cross-foot discrepancy is unresolved, or the deal's existing basis is the other /spread mode.")
    parser.add_argument("--transcription", required=True,
                         help="Path to the staged JSON: {mode, source: {file, kind, description, unit, cost_sign, "
                              "outflow_sign, liability_sign}, periods: {period: {lines, subtotals, ratios}}, "
                              "acknowledged: [{period, check, reason}]}, every figure written as text exactly as it "
                              "appears in the image")
    parser.add_argument("--json", action="store_true",
                         help="Print the read-back as JSON (digest, source fingerprint, cross-foot results, text) "
                              "instead of text")
    parser.add_argument("--commit", action="store_true",
                         help="Record the transcription (needs --confirm, --company and --proposal)")
    parser.add_argument("--confirm", help="With --commit: the digest of the read-back the analyst confirmed")
    parser.add_argument("--company")
    parser.add_argument("--proposal")
    parser.add_argument("--source-note",
                         help="With --commit in analyst-supplied mode: the confirmed convention description, to "
                              "which the image-transcription disclosure is appended as financials_source_note")
    args = parser.parse_args(argv)

    try:
        staged = load(args.transcription)
        if not args.commit:
            shown = readback(staged)
            print(json.dumps(shown, indent=2) if args.json else shown["text"])
            return
        if not (args.company and args.proposal and args.confirm):
            parser.error("--commit needs --company, --proposal and --confirm")
        print(json.dumps(commit(staged, args.confirm, args.company, args.proposal, args.source_note), indent=2))
    except (TranscriptionError, StateError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    main()
