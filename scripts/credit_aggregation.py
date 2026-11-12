"""Case-B credit aggregation, step B2a: validate and resolve the analyst-confirmed inputs.

This module records the *contract* the accounting-gated B2b step (eliminations and
calculation) will later fill: for each requested period it persists a manifest
(participants, period-end evidence, confirmed scope) and one SHA-256 input
fingerprint under the new top-level ``credit_aggregation`` key. It computes
nothing: no elimination, no subtotal, no ratio. A recorded period's optional
``computed`` object (``{"financials": {...}, "ratios": {...}}`` in the same shape
as the borrower's top-level ``financials[period]`` / ``ratios[period]``) is written
by B2b only; until it exists every consumer treats the period as UNRESOLVABLE with
a reason (see ``aggregation_period_status``).

Deterministic and dependency-free: importable without the anthropic SDK, like
state_manager.py, whose locked read-validate-write path this mirrors.
"""
import argparse
import hashlib
import json
import sys
from typing import Any

from numeric_validation import validate_finite_numbers
from state_manager import (
    computed_figures_problem,
    StateError,
    _FileLock,
    _merge_and_write,
    find_deal,
    read_state,
    sanitize_path_component,
)


class AggregationError(StateError):
    """A refused aggregation request. Nothing was written: every refusal in this module
    happens before the single locked atomic write, so state.json is left byte-for-byte
    unchanged."""


# The fingerprint's contract tag and version. The digest changes if either changes, so a
# period recorded under an older contract is never mistaken for a current one.
CREDIT_AGGREGATION_CONTRACT = "open-cam-credit-aggregation-input/v1"
CREDIT_AGGREGATION_CONTRACT_VERSION = 1

# Period labels mirror spreading_builder.PERIOD_KEYS without importing its workbook
# dependencies, exactly as state_manager's own copies do.
PERIOD_KEYS = ("FY-2", "FY-1", "FY-Current", "FY+1", "FY+2", "FY+3")
HISTORICAL_PERIOD_KEYS = ("FY-2", "FY-1", "FY-Current")
FORWARD_PERIOD_KEYS = ("FY+1", "FY+2", "FY+3")

# The reserved participant token: the borrower itself, mapped only to the existing legacy
# borrower stores. It is never an entity_records key (state_manager rejects it there).
RESERVED_BORROWER = "@borrower"

# The request's exact per-period key set. Unknown keys are refused, never ignored.
PERIOD_REQUEST_KEYS = ("participants", "borrower_period_end", "borrower_period_end_evidence",
                       "scope_confirmed")

# The state keys the read-validate-write path validates (forecast_source is read from the
# returned full state but is not a STATE_SHAPES key, so it is deliberately not validated here).
STATE_KEYS_READ = ("financials", "analyst_supplied_financials", "financials_source",
                   "group_basis", "entity_records", "credit_aggregation")


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strict_date(value: object, path: str) -> None:
    """Refuse anything that is not a real YYYY-MM-DD calendar date."""
    import re
    from datetime import date
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise AggregationError(f"{path} must be a real YYYY-MM-DD calendar date; found {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise AggregationError(f"{path} must be a real YYYY-MM-DD calendar date; found {value!r}") from None


def _reject_boolean_numbers(value: object, path: str) -> None:
    """A boolean is not a figure: JSON's true/false must never be read as 1/0."""
    if isinstance(value, bool):
        raise AggregationError(f"{path} is a boolean; credit aggregation figures must be numbers, "
                               "strings or null")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_boolean_numbers(item, f"{path}[{key!r}]")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_boolean_numbers(item, f"{path}[{index}]")


def _reject_non_finite_numbers(value: object, path: str) -> None:
    try:
        validate_finite_numbers(value, path=path)
    except ValueError as exc:
        raise AggregationError(f"{path}: {exc}") from exc


def _borrower_input(state: dict[str, Any], period: str) -> dict[str, Any]:
    """Resolve the borrower participant for one period against the legacy stores only.

    The mapping is deliberately one-way: the reserved token never creates an entity
    record, and the raw period object is read from the framework-computed store
    (``financials[period]["raw"]``) exactly as the exporter selects it. Analyst-supplied
    figures are recorded as given (D4) and are never silently reconciled into an
    aggregation input, so a period whose figures are analyst-supplied-only is refused.
    """
    financials = state.get("financials")
    entry = financials.get(period) if isinstance(financials, dict) else None
    raw = entry.get("raw") if isinstance(entry, dict) else None
    supplied_store = state.get("analyst_supplied_financials")
    supplied = supplied_store.get(period) if isinstance(supplied_store, dict) else None
    if isinstance(raw, dict) and raw and isinstance(supplied, dict) and supplied:
        raise AggregationError(
            f"the borrower has figures in both raw stores for period {period!r}; the aggregation is "
            "refused rather than guessing which store to read"
        )
    if period in FORWARD_PERIOD_KEYS:
        if state.get("forecast_source") == "analyst-supplied":
            raise AggregationError(
                f"the borrower's {period} figures are analyst-supplied (forecast_source); "
                "analyst-supplied-only inputs are refused"
            )
    elif state.get("financials_source") == "analyst-supplied":
        raise AggregationError(
            f"the borrower's {period} figures are analyst-supplied (financials_source); "
            "analyst-supplied-only inputs are refused"
        )
    if not isinstance(raw, dict) or not raw:
        raise AggregationError(
            f"the borrower's financials store has no raw figures for period {period!r}; a missing period "
            "is never rolled, aligned or inferred"
        )
    _reject_boolean_numbers(raw, f"the borrower's raw figures for {period!r}")
    _reject_non_finite_numbers(raw, f"the borrower's raw figures for {period!r}")
    return {
        "store": "financials",
        "raw_period": raw,
        # An absent flag is read as framework-computed everywhere else, so it must fingerprint the same way.
        "financials_source": state.get("financials_source") or "framework-computed",
        "forecast_source": state.get("forecast_source"),
    }


def _entity_input(state: dict[str, Any], period: str, entity_id: str, common_period_end: str) -> dict[str, Any]:
    """Resolve one explicit entity participant: exact-ID join, perimeter-confirmed, never inferred."""
    registry = state.get("entity_records")
    if not isinstance(registry, dict):
        raise AggregationError(
            "entity_records is absent or is not an object; explicit entity IDs cannot be resolved"
        )
    record = registry.get(entity_id)
    if not isinstance(record, dict) or record.get("entity_id") != entity_id:
        raise AggregationError(f"participant {entity_id!r} is not a registered entity record")
    perimeter = record.get("perimeter")
    if not isinstance(perimeter, dict) or perimeter.get("included") is not True:
        raise AggregationError(
            f"participant {entity_id!r} is not inside the confirmed perimeter "
            "(perimeter.included is not true)"
        )
    if perimeter.get("confirmed") is not True:
        raise AggregationError(f"participant {entity_id!r} has an unconfirmed perimeter")
    period_ends = record.get("period_ends")
    if not isinstance(period_ends, dict) or period not in period_ends:
        raise AggregationError(
            f"participant {entity_id!r} has no period end recorded for {period!r}; a missing period or "
            "date is never rolled, aligned or inferred"
        )
    entity_date = period_ends[period]
    if entity_date != common_period_end:
        raise AggregationError(
            f"participant {entity_id!r} records period end {entity_date!r} for {period!r}, which does not "
            f"match the requested common period end {common_period_end!r}; no rolling, aligning or inferring"
        )
    multi = record.get("multi_period_financials")
    raw = multi.get(period) if isinstance(multi, dict) else None
    supplied_store = record.get("analyst_supplied_financials")
    supplied = supplied_store.get(period) if isinstance(supplied_store, dict) else None
    if not isinstance(raw, dict) or not raw:
        if isinstance(supplied, dict) and supplied:
            raise AggregationError(
                f"participant {entity_id!r} has only analyst-supplied figures for {period!r}; "
                "analyst-supplied-only inputs are refused"
            )
        raise AggregationError(
            f"participant {entity_id!r} has no raw figures for {period!r}; a missing period is never "
            "rolled, aligned or inferred"
        )
    _reject_boolean_numbers(raw, f"participant {entity_id!r} raw figures for {period!r}")
    _reject_non_finite_numbers(raw, f"participant {entity_id!r} raw figures for {period!r}")
    lines = record.get("intercompany_lines")
    if not isinstance(lines, list):
        raise AggregationError(
            f"participant {entity_id!r} has a missing or non-list intercompany_lines; a corrupted record "
            "is never read as having no intercompany lines"
        )
    # Only this period's lines are inputs of this aggregation (sorted, so a reordering is not a change).
    period_lines = sorted(
        (line for line in lines if isinstance(line, dict) and line.get("period") == period),
        key=lambda line: json.dumps(line, sort_keys=True, default=str),
    )
    return {
        "store": "multi_period_financials",
        "intercompany_lines": period_lines,
        "raw_period": raw,
        "source": record.get("source"),
        "perimeter": perimeter,
        "raw_figures_source": record.get("raw_figures_source"),
    }


def _counterparty_problems(state: dict[str, Any], entity_id: str, line: dict[str, Any],
                           participants_by_period: dict[str, list[str]]) -> None:
    """One intercompany line of a selected participant, checked against its own period's participants.

    Every counterparty reference must resolve to a selected participant (or to the borrower
    view through the reserved token). A dangling, unselected, out-of-perimeter or unconfirmed
    reference is a named hard error: never assumed external, never matched by name.
    """
    line_period = line.get("period")   # callers pass only this period's lines, so it is a requested period
    if line.get("confirmed") is not True:
        raise AggregationError(
            f"participant {entity_id!r} has an unconfirmed intercompany line for {line_period!r}; an "
            "unconfirmed line cannot join an aggregation"
        )
    counterparty = line.get("counterparty_entity_id")
    if counterparty == RESERVED_BORROWER:
        return          # the reserved token resolves to the borrower view
    if not isinstance(counterparty, str) or not counterparty.strip():
        raise AggregationError(
            f"participant {entity_id!r} has an intercompany line for {line_period!r} with no "
            "counterparty_entity_id"
        )
    selected = participants_by_period[str(line_period)]
    if counterparty in selected:
        return
    registry = state.get("entity_records")
    record = registry.get(counterparty) if isinstance(registry, dict) else None
    if not isinstance(record, dict) or record.get("entity_id") != counterparty:
        raise AggregationError(
            f"participant {entity_id!r} references dangling counterparty {counterparty!r} for "
            f"{line_period!r}; it is not a registered entity record"
        )
    perimeter = record.get("perimeter")
    if not isinstance(perimeter, dict) or perimeter.get("included") is not True:
        raise AggregationError(
            f"participant {entity_id!r} references counterparty {counterparty!r} for {line_period!r}, "
            "which is outside the confirmed perimeter"
        )
    if perimeter.get("confirmed") is not True:
        raise AggregationError(
            f"participant {entity_id!r} references counterparty {counterparty!r} for {line_period!r}, "
            "whose perimeter is unconfirmed"
        )
    raise AggregationError(
        f"participant {entity_id!r} references counterparty {counterparty!r} for {line_period!r}, which "
        "is not among that period's selected participants"
    )


def _period_payload(state: dict[str, Any], period: str, period_request: dict[str, Any]) -> dict[str, Any]:
    """Build one period's canonical fingerprint payload: every covered input, no computed outputs."""
    participants = period_request["participants"]
    inputs: dict[str, Any] = {}
    for participant in participants:
        if participant == RESERVED_BORROWER:
            inputs[participant] = _borrower_input(state, period)
        else:
            inputs[participant] = _entity_input(
                state, period, participant, period_request["borrower_period_end"])
    return {
        "contract": CREDIT_AGGREGATION_CONTRACT,
        "contract_version": CREDIT_AGGREGATION_CONTRACT_VERSION,
        "period": period,
        "period_end": period_request["borrower_period_end"],
        "participants": sorted(participants),
        "borrower_period_end_evidence": period_request["borrower_period_end_evidence"],
        "scope_confirmed": True,
        "inputs": inputs,
    }


COMPUTED_OUTPUT_CONTRACT = "open-cam-credit-aggregation-output/v1"


def computed_output_digest(input_fingerprint: str, financials: dict[str, Any], ratios: dict[str, Any]) -> str:
    """The SHA-256 a ``computed`` object must carry as ``output_digest`` (issue #472, CG587-11).

    It binds the computed figures to the exact input fingerprint they were calculated from, so an edited figure,
    or figures calculated for other inputs, is never read as current. The B2b writer must call this helper;
    nothing else may fill ``computed``.
    """
    return _canonical_digest({
        "contract": COMPUTED_OUTPUT_CONTRACT,
        "input_fingerprint": input_fingerprint,
        "financials": financials,
        "ratios": ratios,
    })


def _canonical_digest(payload: dict[str, Any]) -> str:
    """One SHA-256 over canonical JSON, lowercase hex: deterministic for identical inputs."""
    try:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise AggregationError(f"the aggregation inputs are not canonical JSON: {exc}") from exc
    return hashlib.sha256(canonical).hexdigest()


def _validated_period_request(period: str, period_request: object) -> dict[str, Any]:
    """Shape-check one period's request object: known keys only, explicit analyst confirmation."""
    if not isinstance(period_request, dict):
        raise AggregationError(f"the request for period {period!r} must be a JSON object")
    unknown = sorted(set(period_request) - set(PERIOD_REQUEST_KEYS))
    if unknown:
        raise AggregationError(
            f"the request for period {period!r} has unexpected key(s) "
            + ", ".join(repr(key) for key in unknown)
            + f"; expected exactly {', '.join(repr(key) for key in PERIOD_REQUEST_KEYS)}"
        )
    for key in PERIOD_REQUEST_KEYS:
        if key not in period_request:
            raise AggregationError(f"the request for period {period!r} is missing required key {key!r}")
    if period_request["scope_confirmed"] is not True:
        raise AggregationError(
            f'the request for period {period!r} must carry "scope_confirmed": true; an unconfirmed or '
            "ambiguous scope is refused"
        )
    participants = period_request["participants"]
    if not isinstance(participants, list) or not participants:
        raise AggregationError(
            f"the request for period {period!r} must carry a non-empty participants list"
        )
    seen: set[str] = set()
    for participant in participants:
        if not _nonempty_string(participant):
            raise AggregationError(
                f"the participants for period {period!r} must be non-empty strings; found {participant!r}"
            )
        if participant in seen:
            raise AggregationError(
                f"the participants for period {period!r} contain duplicate {participant!r}"
            )
        seen.add(participant)
    if RESERVED_BORROWER not in seen:
        raise AggregationError(
            f'the participants for period {period!r} must include the reserved token "{RESERVED_BORROWER}"'
        )
    _strict_date(period_request["borrower_period_end"], f"the request for period {period!r} borrower_period_end")
    if not _nonempty_string(period_request["borrower_period_end_evidence"]):
        raise AggregationError(
            f"the request for period {period!r} must carry a non-empty borrower_period_end_evidence "
            "reference"
        )
    return period_request


def resolve_request(state: dict[str, Any], request: object) -> dict[str, dict[str, Any]]:
    """Validate one aggregation request against the live state and resolve every period.

    Returns ``{period: {"manifest": {...}, "input_fingerprint": "<sha256>"}}``. Raises
    :class:`AggregationError` on the first deterministic problem, before anything is
    written: a missing or ambiguous scope, a missing token, an unregistered, duplicate
    or out-of-perimeter participant, a missing or mismatched period end, a dangling,
    unselected, out-of-perimeter or unconfirmed counterparty, an unconfirmed line,
    analyst-supplied-only figures, a case-A group_basis collision, non-finite or
    boolean numbers, or an unknown key. Nothing is rolled, aligned or inferred.
    """
    if not isinstance(request, dict):
        raise AggregationError("the aggregation request must be a JSON object")
    unknown = sorted(set(request) - {"periods"})
    if unknown:
        raise AggregationError(
            "the aggregation request has unexpected key(s) "
            + ", ".join(repr(key) for key in unknown)
            + '; only "periods" is allowed'
        )
    if isinstance(state.get("group_basis"), dict):
        raise AggregationError(
            "this deal already records a case-A group_basis; a credit aggregation cannot be recorded "
            "alongside it (fail closed, no winner by insertion order)"
        )
    periods = request.get("periods")
    if not isinstance(periods, dict) or not periods:
        raise AggregationError('"periods" must be a non-empty object keyed by period label')

    # Pass 1: shape-check every period and collect its selected participants, so a
    # counterparty can be checked against its own period's selection.
    checked: dict[str, dict[str, Any]] = {}
    for period in sorted(periods):
        if period not in PERIOD_KEYS:
            raise AggregationError(
                f"unknown period label {period!r}; expected one of {', '.join(PERIOD_KEYS)}"
            )
        checked[period] = _validated_period_request(period, periods[period])
    participants_by_period = {
        period: request_["participants"] for period, request_ in checked.items()
    }

    # Pass 2: resolve every participant against the live state, then check every
    # counterparty line of the selected participants for the requested periods.
    records: dict[str, dict[str, Any]] = {}
    for period in sorted(checked):
        period_request = checked[period]
        payload = _period_payload(state, period, period_request)
        for participant in sorted(participants_by_period[period]):
            if participant == RESERVED_BORROWER:
                continue
            for line in payload["inputs"][participant]["intercompany_lines"]:
                _counterparty_problems(state, participant, line, participants_by_period)
        records[period] = {
            "manifest": {
                "participants": sorted(period_request["participants"]),
                "period_end": period_request["borrower_period_end"],
                "period_end_evidence": period_request["borrower_period_end_evidence"],
                "scope_confirmations": {
                    participant: payload["inputs"][participant]["perimeter"]
                    for participant in sorted(period_request["participants"])
                    if participant != RESERVED_BORROWER
                },
            },
            "input_fingerprint": _canonical_digest(payload),
        }
    return records


def _computed_digest_problem(computed: object, fingerprint: str) -> str | None:
    """Why a ``computed`` object's output digest is missing or wrong, or None (shape is checked separately)."""
    if not isinstance(computed, dict) or computed_figures_problem(computed) is not None:
        return None     # reported as a malformed object by the caller
    digest = computed.get("output_digest")
    if not isinstance(digest, str):
        return "it has no output_digest"
    try:
        expected = computed_output_digest(fingerprint, computed["financials"], computed["ratios"])
    except AggregationError as exc:      # e.g. a lone surrogate in a metric name: fail closed, never crash a reader
        return f"its figures cannot be serialised as canonical JSON ({exc})"
    if digest != expected:
        return "its output digest does not match the figures and the input fingerprint (edited or foreign output)"
    return None


def aggregation_period_status(state: dict[str, Any], period: str) -> dict[str, Any]:
    """The read-side contract every consumer of a recorded period must use.

    Recomputes the period's fingerprint from the live state and the stored manifest
    and reports one of: ``current`` (the fingerprint matches and a well-shaped
    ``computed`` is present), ``not_calculated`` (the fingerprint matches but B2b has
    not computed the period), ``stale`` (the recorded inputs no longer validate, or
    the fingerprint differs), ``invalid`` (the recorded ``computed`` object is
    malformed) or ``absent`` (nothing is recorded for the period). A consumer never
    falls back to another view on the back of this status.
    """
    recorded = state.get("credit_aggregation") if isinstance(state, dict) else None
    entry = recorded.get(period) if isinstance(recorded, dict) else None
    if not isinstance(entry, dict):
        return {
            "status": "absent",
            "reason": f"no credit aggregation is recorded for {period!r}",
            "input_fingerprint": None,
        }
    manifest = entry.get("manifest")
    fingerprint = entry.get("input_fingerprint")
    if not isinstance(manifest, dict) or not isinstance(fingerprint, str):
        return {
            "status": "stale",
            "reason": f"the recorded credit aggregation for {period!r} has no valid manifest or fingerprint",
            "input_fingerprint": fingerprint if isinstance(fingerprint, str) else None,
        }
    rebuilt_request = {
        "participants": manifest.get("participants"),
        "borrower_period_end": manifest.get("period_end"),
        "borrower_period_end_evidence": manifest.get("period_end_evidence"),
        "scope_confirmed": True,
    }
    try:
        records = resolve_request(state, {"periods": {period: rebuilt_request}})
    except AggregationError as exc:
        return {
            "status": "stale",
            "reason": f"the recorded credit aggregation inputs for {period!r} no longer validate: {exc}",
            "input_fingerprint": fingerprint,
        }
    if records[period]["input_fingerprint"] != fingerprint:
        return {
            "status": "stale",
            "reason": (
                f"the recorded input fingerprint for {period!r} does not match the current inputs: a "
                "participant, source, period, scope or contract version changed"
            ),
            "input_fingerprint": fingerprint,
        }
    if records[period]["manifest"] != manifest:
        return {
            "status": "stale",
            "reason": (
                f"the recorded manifest for {period!r} differs from the one the current inputs produce "
                "(for example an edited scope confirmation)"
            ),
            "input_fingerprint": fingerprint,
        }
    computed = entry.get("computed")
    if computed is None:
        return {
            "status": "not_calculated",
            "reason": (
                f"credit aggregation period {period!r} is recorded but not calculated; the accounting "
                "step (B2b) has not computed it"
            ),
            "input_fingerprint": fingerprint,
        }
    digest_problem = _computed_digest_problem(computed, fingerprint)
    if digest_problem is not None:
        return {
            "status": "invalid",
            "reason": f"the recorded computed object for {period!r} cannot be trusted: {digest_problem}",
            "input_fingerprint": fingerprint,
        }
    if computed_figures_problem(computed) is not None:
        return {
            "status": "invalid",
            "reason": f"the recorded computed object for {period!r} is malformed",
            "input_fingerprint": fingerprint,
        }
    return {"status": "current", "reason": None, "input_fingerprint": fingerprint}


AGGREGATION_SHEET_DISCLOSURE = (
    "Credit aggregation of the borrower and the entities the analyst confirmed, never a statutory consolidation. "
    "It does not cover equity, goodwill, non-controlling interests or other material adjustments."
)


def aggregation_sheet_rows(state: dict[str, Any]) -> list[list[Any]] | None:
    """The rows of the workbook's "Credit aggregation" sheet (issue #472, B4), or None when none is recorded.

    Output only: it reads the recorded manifest, the freshness status from ``aggregation_period_status`` and, for
    a ``current`` period only, the ``computed`` figures. A period that is not current shows its status and reason
    and no figures. Borrower and entity figures are never summed here.
    """
    recorded = state.get("credit_aggregation") if isinstance(state, dict) else None
    if not isinstance(recorded, dict) or not recorded:
        return None
    rows: list[list[Any]] = [[AGGREGATION_SHEET_DISCLOSURE]]
    for period in PERIOD_KEYS:
        entry = recorded.get(period)
        if not isinstance(entry, dict):
            continue
        recorded_manifest = entry.get("manifest")
        manifest: dict[str, Any] = recorded_manifest if isinstance(recorded_manifest, dict) else {}
        status = aggregation_period_status(state, period)
        participants = manifest.get("participants")
        fingerprint = status.get("input_fingerprint")
        rows += [
            [],
            ["Period", period],
            ["Participants", ", ".join(participants) if isinstance(participants, list) else None],
            ["Confirmed scope", ", ".join(sorted(manifest.get("scope_confirmations") or {}))],
            ["Period end", manifest.get("period_end")],
            ["Period end evidence", manifest.get("period_end_evidence")],
            ["Status", str(status.get("status")).replace("_", " ")],
            ["Input fingerprint", fingerprint[:16] if isinstance(fingerprint, str) else None],
        ]
        if status.get("reason"):
            rows.append(["Reason", status["reason"]])
        computed = entry.get("computed")
        if status.get("status") == "current" and isinstance(computed, dict):
            for group in ("financials", "ratios"):
                for name, value in sorted(computed[group].items()):
                    if name != "raw":
                        rows.append([name, value])
    return rows


def record_credit_aggregation(
    company: str, proposal: str, request_file: str,
    base_dir: str | None = None,
) -> dict[str, Any]:
    """Validate one aggregation request and persist it under ``credit_aggregation``.

    The read/check/write uses the same per-deal lock and shared atomic
    ``_merge_and_write`` path as state_manager's other record functions. Full
    validation happens before the single write, so any refusal leaves state.json
    byte-for-byte unchanged; recording a period replaces that period's entry (and
    drops any earlier ``computed`` for it, which the new inputs have invalidated)
    while periods not named in the request are preserved.
    """
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    found = find_deal(company, proposal, base_dir=base_dir)
    if not found["record_found"]:
        raise AggregationError(
            f"Refusing to record credit_aggregation: no exact existing state.json for company "
            f"{company!r} and proposal {proposal!r}; no deal was created."
        )

    try:
        with open(request_file, encoding="utf-8") as handle:
            try:
                request: Any = json.load(handle)
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
                raise AggregationError(
                    f"Cannot read aggregation request file {request_file!r} as UTF-8 JSON: {exc}"
                ) from exc
    except OSError as exc:
        raise AggregationError(f"Cannot read aggregation request file {request_file!r}: {exc}") from exc
    if not isinstance(request, dict):
        raise AggregationError(
            f"Aggregation request file {request_file!r} must contain one JSON object; nothing was changed."
        )

    record_date = found["date"]
    path = found["state_path"]
    if not isinstance(record_date, str) or not isinstance(path, str):
        raise AggregationError(
            "Refusing to record credit_aggregation: exact existing state path was not resolved."
        )
    with _FileLock(path):
        state = read_state(company, proposal, date_str=record_date, base_dir=base_dir,
                           keys=STATE_KEYS_READ)
        if state is None:
            raise AggregationError(
                f"Refusing to record credit_aggregation: state.json at {path!r} no longer exists; "
                "nothing was created."
            )
        records = resolve_request(state, request)
        existing = state.get("credit_aggregation")
        merged: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
        merged.update(records)
        # The shared write path: schema-version check, finite-number guard, same-directory
        # temporary file and atomic replace, all under the same read-modify-write lock.
        _merge_and_write(path, company, proposal, record_date, base_dir,
                         {"credit_aggregation": merged})

    return {"recorded": True, "periods": sorted(records), "state_path": path}


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="Record case-B credit aggregation inputs (B2a) on an existing deal: validate one "
                    "analyst-confirmed request file and persist each period's manifest and input "
                    "fingerprint under the credit_aggregation key. Computes nothing; the accounting step "
                    "(B2b) is separate and gated."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--request-file", required=True,
                        help="UTF-8 JSON object: {\"periods\": {\"<period>\": {participants, "
                             "borrower_period_end, borrower_period_end_evidence, scope_confirmed}}}")
    args = parser.parse_args()

    try:
        report = record_credit_aggregation(args.company, args.proposal, args.request_file)
    except StateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(report, indent=2))
