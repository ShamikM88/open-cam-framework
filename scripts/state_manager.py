"""File-backed state persistence for a single deal.

Dependency-free (no anthropic/docx/openpyxl imports), matching
template_resolver.py's pattern, so it's cheap to unit test and safe to
import from anywhere (a slash command's Bash step, orchestrator.py) without
pulling in heavy dependencies.

See CLAUDE.md's "Context Window & State Management Protocol" section for the
full rationale: every step of a deal checkpoints its results here, so a
multi-step deal survives context compaction or a resumed session instead of
relying on the conversation itself to remember figures.

Date resolution: when `date_str` isn't given, an existing
deals/<company>/<proposal>_<date>/ folder for this company/proposal is found
automatically (most recent wins, if somehow more than one exists) rather
than always defaulting to today. This is what lets a deal resumed on a later
calendar day still find its original state.json. deal_export.py's
export_deal() itself still defaults to today for a bare call, so its callers
must resolve the date the same way (resolve_date_str()) or a multi-day deal's
export lands in a new folder away from its own state.json (issue #97).

review_trail: append_review_trail() is the one exception to write_state()'s
plain shallow-merge semantics baked into this module itself, rather than
left to each caller -- see its docstring.
"""
import argparse
import glob
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime

from numeric_validation import NonFiniteNumberError, validate_finite_numbers

DEALS_DIR = "deals"

# Bumped whenever state.json's schema gains a new top-level shape a reader
# needs to know how to interpret (e.g. this version's downside_case/
# stress_assumptions keys) -- not on every new optional field. A deal
# folder created before this constant existed has no "schema_version" key
# at all; read_state() reports LEGACY_SCHEMA_VERSION for those rather than
# leaving the key absent, so future migration logic always has a defined
# starting point instead of needing its own "key missing" special case.
SCHEMA_VERSION = "1.1.0"
LEGACY_SCHEMA_VERSION = "0.0.0"

# A recorded version must look exactly like SCHEMA_VERSION: MAJOR.MINOR.PATCH, each a run of digits. Compared
# as integers, never as strings ("1.10.0" is newer than "1.9.0").
_SCHEMA_VERSION_RE = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")   # ASCII digits only (\d also matches e.g. Arabic-Indic)


class StateError(ValueError):
    """A problem with a state.json file's CONTENT (as opposed to a bug in the code reading it): corrupt JSON, the wrong
    shape, a version this code must not overwrite. Every CLI that touches state turns it into one `error:` line and
    exit status 1 instead of a traceback; it is still a ValueError for any caller that already catches that."""


class SchemaVersionError(StateError):
    """write_state() refused to write because the file's recorded schema_version is newer than this code supports
    (it would have been silently downgraded) or is not a MAJOR.MINOR.PATCH string (it cannot be compared, so
    nothing is overwritten). Nothing was written."""


class StateShapeError(StateError):
    """state.json is not valid JSON, or a key this code relies on has the wrong type (see validate_state())."""


# The structural contract of the keys the framework reads (issue #171). For the general, unrelated consumers,
# an absent or null key is "not recorded" and is accepted. group_basis is deliberately consumer-scoped below:
# only steps that actually consume that metadata opt into its stricter, required-field validation.
# Unknown keys are never inspected (a newer framework may add them, and they must survive a write).
#   object:  a JSON object                          periods: an object keyed by period whose values are objects
#   downside: an object whose optional "financials" and "ratios" are `periods`
#   list:    a list                                 source: "framework-computed" or "analyst-supplied"
STATE_SHAPES = {
    "financials": "periods", "ratios": "periods", "multi_period_financials": "periods",
    "analyst_supplied_financials": "periods",
    "inputs": "object", "stress_assumptions": "object", "downside_case": "downside",
    "collateral": "list", "covenants": "list", "security_package": "list", "guarantees": "list",
    "steps_completed": "list", "review_trail": "list",
    "financials_source": "source",
}
# STATE_SHAPES kinds, plus "downside_lenient" (deal_export only): like "downside" but a non-object value is tolerated
# and the inner financials/ratios need only be objects.
FINANCIALS_SOURCES = ("framework-computed", "analyst-supplied")
# State fields whose numeric contents participate in framework-computed financial or policy decisions.
FINITE_NUMERIC_STATE_KEYS = (
    "financials", "ratios", "multi_period_financials", "analyst_supplied_financials",
    "stress_assumptions", "downside_case", "covenants", "collateral", "security_package",
)


def validate_finite_state_numbers(state, path="state.json"):
    """Reject non-finite numbers in financial/policy data, without inspecting unrelated state keys."""
    if not isinstance(state, dict):
        return state  # validate_state() reports the existing top-level shape error
    for key in FINITE_NUMERIC_STATE_KEYS:
        if key in state:
            validate_finite_numbers(state[key], path=f"{path}[{key!r}]")
    return state


_SHAPE_DESCRIPTIONS = {
    "object": "an object ({...})",
    "periods": 'an object keyed by period whose values are objects (e.g. {"FY-Current": {...}})',
    "downside": 'an object ({"financials": {...}, "ratios": {...}})',
    "list": "a list ([...])",
    "source": " or ".join(f'"{value}"' for value in FINANCIALS_SOURCES),
}


def _json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "list"
    if isinstance(value, str):
        return "string"
    return "number"



# Case A metadata labels mirror spreading_builder.PERIOD_KEYS without importing its
# workbook dependencies into this deliberately lightweight state-management module.
_GROUP_BASIS_PERIOD_KEYS = ("FY-2", "FY-1", "FY-Current", "FY+1", "FY+2", "FY+3")


_SOURCE_NOT_CHECKED = object()


def _group_basis_problems(value, financials_source=_SOURCE_NOT_CHECKED):
    """Return validation problems for the optional group-basis metadata object.

    Unknown future fields are intentionally ignored. This object records source,
    period and analyst-confirmed perimeter metadata only; it never stores figures.
    `financials_source` is the deal's recorded value, with None meaning not recorded:
    an unrecorded source is read as framework-computed (the default, and what the
    exporter assumes), so "supplied" figures need an explicit "analyst-supplied".
    Left at its default the agreement rule is not applied.
    """
    if not isinstance(value, dict):
        return [f'"group_basis" must be an object, found {_json_type(value)}']

    problems = []

    def required_string(container, key, path):
        if key not in container:
            problems.append(f'"{path}" is required')
            return None
        item = container[key]
        if not isinstance(item, str):
            problems.append(f'"{path}" must be a non-empty string, found {_json_type(item)}')
            return None
        if not item.strip():
            problems.append(f'"{path}" must be a non-empty string')
            return None
        return item

    def calendar_date(container, key, path):
        if key not in container:
            problems.append(f'"{path}" is required')
            return
        item = container[key]
        if not isinstance(item, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", item):
            problems.append(f'"{path}" must be a real YYYY-MM-DD calendar date')
            return
        try:
            date.fromisoformat(item)
        except ValueError:
            problems.append(f'"{path}" must be a real YYYY-MM-DD calendar date')

    if value.get("basis") != "supplied_consolidated":
        problems.append('"group_basis.basis" must be "supplied_consolidated"')

    reporting_entity = required_string(value, "reporting_entity", "group_basis.reporting_entity")

    entities = value.get("entities_covered")
    if not isinstance(entities, list) or not entities:
        problems.append('"group_basis.entities_covered" must be a non-empty list of distinct non-empty strings')
        entities = []
    seen_entities = set()
    for index, entity in enumerate(entities):
        if not isinstance(entity, str):
            problems.append(f'"group_basis.entities_covered[{index}]" must be a non-empty string')
            continue
        if not entity.strip():
            problems.append(f'"group_basis.entities_covered[{index}]" must be a non-empty string')
        if entity in seen_entities:
            problems.append(f'"group_basis.entities_covered" contains duplicate entity {entity!r}')
        seen_entities.add(entity)
    if isinstance(reporting_entity, str) and reporting_entity not in seen_entities:
        problems.append('"group_basis.entities_covered" must include group_basis.reporting_entity')

    source = value.get("source")
    if not isinstance(source, dict):
        problems.append('"group_basis.source" must be an object')
        source = {}
    required_string(source, "document", "group_basis.source.document")
    required_string(source, "accounting_basis", "group_basis.source.accounting_basis")
    calendar_date(source, "date", "group_basis.source.date")

    calendar_date(value, "period_end", "group_basis.period_end")

    periods = value.get("periods")
    if not isinstance(periods, list) or not periods:
        problems.append('"group_basis.periods" must be a non-empty list of distinct framework period labels')
        periods = []
    seen_periods = set()
    for index, period in enumerate(periods):
        if not isinstance(period, str) or not period:
            problems.append(f'"group_basis.periods[{index}]" must be a framework period label')
            continue
        if period not in _GROUP_BASIS_PERIOD_KEYS:
            problems.append(f'"group_basis.periods[{index}]" has unknown period label {period!r}')
        if period in seen_periods:
            problems.append(f'"group_basis.periods" contains duplicate period {period!r}')
        seen_periods.add(period)

    figures_basis = value.get("figures_basis")
    if figures_basis not in ("supplied", "recomputed"):
        problems.append('"group_basis.figures_basis" must be "supplied" or "recomputed"')

    if value.get("perimeter_confirmed") is not True:
        problems.append('"group_basis.perimeter_confirmed" must be true after explicit analyst confirmation')
    calendar_date(value, "perimeter_confirmed_on", "group_basis.perimeter_confirmed_on")

    expected_source = (
        {"supplied": "analyst-supplied", "recomputed": "framework-computed"}.get(figures_basis)
        if isinstance(figures_basis, str) else None
    )
    if financials_source is _SOURCE_NOT_CHECKED:
        return problems
    effective_source = "framework-computed" if financials_source is None else financials_source
    if (isinstance(effective_source, str) and effective_source in FINANCIALS_SOURCES
            and expected_source is not None and effective_source != expected_source):
        recorded = (
            f'state financials_source is {financials_source!r}' if financials_source is not None
            else 'state financials_source is not recorded (read as "framework-computed")'
        )
        problems.append(f'"group_basis.figures_basis" is {figures_basis!r}, but {recorded}; these must agree')
    return problems


def record_group_basis(company, proposal, group_basis_file, perimeter_confirmed=False, date_str=None, base_dir=None):
    """Record case-A metadata on an existing deal only, without storing or changing figures.

    The read/check/write uses the same per-deal lock and shared atomic _merge_and_write
    path as write_state(). A missing or corrupt deal is never treated as a new deal.
    """
    if not perimeter_confirmed:
        raise StateError(
            "Refusing to record group_basis without explicit --perimeter-confirmed analyst confirmation; "
            "nothing was changed."
        )

    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    found = find_deal(company, proposal, base_dir=base_dir)
    if not found["record_found"]:
        raise StateError(
            f"Refusing to record group_basis: no exact existing state.json for company {company!r} and "
            f"proposal {proposal!r}; no deal was created."
        )

    try:
        with open(group_basis_file, encoding="utf-8") as handle:
            try:
                supplied = json.load(handle)
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
                raise StateError(
                    f"Cannot read group-basis file {group_basis_file!r} as UTF-8 JSON: {exc}"
                ) from exc
    except OSError as exc:
        raise StateError(f"Cannot read group-basis file {group_basis_file!r}: {exc}") from exc

    if not isinstance(supplied, dict):
        raise StateError(f"Group-basis file {group_basis_file!r} must contain a JSON object; nothing was changed.")

    confirmation_date = date_str if date_str is not None else datetime.now().strftime("%Y-%m-%d")
    group_basis = dict(supplied)
    group_basis["perimeter_confirmed"] = True
    group_basis["perimeter_confirmed_on"] = confirmation_date
    problems = _group_basis_problems(group_basis)
    if problems:
        raise StateError("Invalid group_basis: " + "; ".join(problems) + ". Nothing was changed.")

    record_date = found["date"]
    path = found["state_path"]
    with _FileLock(path):
        state = read_state(
            company, proposal, date_str=record_date, base_dir=base_dir,
            keys=("financials_source", "group_basis"),
        )
        if state is None:
            raise StateError(
                f"Refusing to record group_basis: state.json at {path!r} no longer exists; nothing was created."
            )
        problems = _group_basis_problems(group_basis, state.get("financials_source"))
        if problems:
            raise StateError("Refusing to record group_basis: " + "; ".join(problems) + ". Nothing was changed.")
        # This is the shared write_state path: schema-version check, finite-number guard,
        # same-directory temporary file and atomic replace, all under the same RMW lock.
        _merge_and_write(path, company, proposal, record_date, base_dir, {"group_basis": group_basis})

    return {"recorded": True, "state_path": path, "figures_basis": group_basis["figures_basis"]}


def _problems_with_periods(label, value):
    """Problems for a `periods` value. A null period is "not recorded" (evaluate_financial_model treats it as empty)."""
    if not isinstance(value, dict):
        return [f'"{label}" must be {_SHAPE_DESCRIPTIONS["periods"]}, found {_json_type(value)}']
    return [f'"{label}"[{period!r}] must be an object, found {_json_type(period_value)}'
            for period, period_value in value.items() if period_value is not None and not isinstance(period_value, dict)]


def validate_state(state, keys=None, path="state.json"):
    """Raise StateShapeError, naming every offending key and the type it must have, if `state` (a parsed state.json)
    is not usable. The top level must be an object. `keys` selects what to check: None means every key in
    STATE_SHAPES; an iterable of names checks just those; a mapping {name: kind} also overrides the kind (deal_export
    uses this to keep its documented tolerance of non-object periods). Each consumer validates the keys IT reads, so
    a deal is never rejected for a key nothing in that step uses. Returns `state`."""
    if not isinstance(state, dict):
        raise StateShapeError(
            f"Cannot use {path}: the top level must be a JSON object ({{...}}), found {_json_type(state)}. "
            "Restore the file from a backup or fix it by hand; the file was not modified.")
    if isinstance(keys, str):
        raise TypeError("keys must be an iterable of key names or a {name: kind} mapping, not a single string")
    shapes = dict(STATE_SHAPES) if keys is None else (
        dict(keys) if isinstance(keys, dict) else {
            key: ("group_basis" if key == "group_basis" else STATE_SHAPES[key])
            for key in keys
        })
    problems = []
    for key, kind in shapes.items():
        value = state.get(key)
        if value is None:
            continue
        if kind == "group_basis":
            problems.extend(_group_basis_problems(value, state.get("financials_source")))
        elif kind == "periods":
            problems.extend(_problems_with_periods(key, value))
        elif kind == "object":
            if not isinstance(value, dict):
                problems.append(f'"{key}" must be {_SHAPE_DESCRIPTIONS["object"]}, found {_json_type(value)}')
        elif kind in ("downside", "downside_lenient"):
            if not isinstance(value, dict):
                if kind == "downside":     # "downside_lenient" tolerates a non-object, as deal_export always has
                    problems.append(f'"{key}" must be {_SHAPE_DESCRIPTIONS["downside"]}, found {_json_type(value)}')
            else:
                for inner in ("financials", "ratios"):
                    if value.get(inner) is None:
                        continue
                    if kind == "downside":
                        problems.extend(_problems_with_periods(f"{key}.{inner}", value[inner]))
                    elif not isinstance(value[inner], dict):       # lenient: the container only, not each period
                        problems.append(f'"{key}.{inner}" must be {_SHAPE_DESCRIPTIONS["object"]}, '
                                        f"found {_json_type(value[inner])}")
        elif kind == "list":
            if not isinstance(value, list):
                problems.append(f'"{key}" must be {_SHAPE_DESCRIPTIONS["list"]}, found {_json_type(value)}')
        elif kind == "source":
            if value not in FINANCIALS_SOURCES:
                problems.append(f'"{key}" must be {_SHAPE_DESCRIPTIONS["source"]}, found {repr(value)[:60]}')
        else:
            raise ValueError(f"unknown state shape kind {kind!r} for key {key!r}")
    if problems:
        raise StateShapeError(f"Cannot use {path}: " + "; ".join(problems) +
                              ". Fix the file by hand or restore it from a backup; the file was not modified.")
    return state


def parse_schema_version(value):
    """`"1.10.0"` -> `(1, 10, 0)`. Raises ValueError for anything that is not a MAJOR.MINOR.PATCH string of digits
    -- a non-string (None, a number, a list...), `"1.2"`, `"1.2.3.4"`, `"v1.1.0"`, `" 1.1.0"`, `""`, a negative part."""
    match = _SCHEMA_VERSION_RE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError(f"schema_version {value!r} is not a MAJOR.MINOR.PATCH version string")
    return tuple(int(part) for part in match.groups())

_UNSAFE_PATH_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def validate_iso_date_str(value, field_name="date_str", require_shape=True):
    """Validate ISO-shaped dates against the calendar.

    Strict callers require the exact YYYY-MM-DD shape and a real calendar date. With
    require_shape=False, a non-shaped value is returned unchanged for callers that
    explicitly need the legacy permissive behavior; state-manager entry points use
    strict validation for every supplied date.
    """
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        if require_shape:
            raise ValueError(f"{field_name} {value!r} must be a YYYY-MM-DD date")
        return value
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} {value!r} is not a valid calendar date") from exc
    return value


def _validate_explicit_state_date(value):
    """Validate an explicitly supplied state date and report failures as StateError."""
    if value is None:
        return None
    try:
        return validate_iso_date_str(value)
    except ValueError as exc:
        raise StateError(str(exc)) from exc


def _is_valid_iso_date_str(value):
    try:
        validate_iso_date_str(value)
    except ValueError:
        return False
    return True


LOCK_TIMEOUT_SECONDS = 10
_LOCK_POLL_INTERVAL_SECONDS = 0.05


class _FileLock:
    """A minimal, dependency-free, cross-platform lock guarding one deal's
    read-modify-write against a concurrent write_state()/append_review_trail()
    call for the *same* company/proposal (e.g. two Claude Code sessions, or
    a slash command and a headless orchestrator.py run, both touching the
    same deal at once) -- without it, both reads see the same starting
    state, both compute an update from it, and the second write silently
    clobbers whatever the first one added (no error, no warning).

    Uses exclusive file creation (os.O_CREAT | os.O_EXCL), which is atomic
    on both Windows and POSIX -- no platform-conditional msvcrt/fcntl code
    or third-party dependency needed. Retries with a short poll interval up
    to `timeout` seconds before giving up.
    """

    def __init__(self, target_path, timeout=LOCK_TIMEOUT_SECONDS):
        self._lock_path = target_path + ".lock"
        self._timeout = timeout
        self._fd = None

    def __enter__(self):
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                self._fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except (FileExistsError, PermissionError) as contention:
                # On Windows, a genuine O_EXCL collision (another thread/
                # process winning the race to create the same lock file a
                # moment earlier) can surface as PermissionError rather than
                # FileExistsError -- NTFS briefly denies access to a file
                # another handle just created before its attributes settle.
                # Treat both identically: back off and retry.
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire lock {self._lock_path!r} within {self._timeout}s. "
                        "Either another process is genuinely mid-write, or this is a stale lock "
                        "left behind by a process that crashed while holding it -- if so, it's "
                        "safe to delete the .lock file by hand."
                    ) from contention
                time.sleep(_LOCK_POLL_INTERVAL_SECONDS)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd is not None:
            os.close(self._fd)
        try:
            os.remove(self._lock_path)
        except FileNotFoundError:
            pass


def sanitize_path_component(value, field_name):
    """Reject a company/proposal value that isn't safe to use directly as a
    filesystem path component -- a path separator, drive-letter colon, or a
    bare "." /".." traversal segment would otherwise let `os.path.join`
    escape the intended deals/<company>/<proposal>_<date>/ tree entirely
    (on Windows, joining an absolute-looking later component discards every
    path segment before it, rather than raising).
    """
    value = str(value)
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty.")
    if _UNSAFE_PATH_CHARS_RE.search(value) or value in (".", ".."):
        raise ValueError(
            f"{field_name} {value!r} isn't safe to use as a filesystem path "
            "component (no \\ / : * ? \" < > | characters, and not \".\" or \"..\")."
        )
    return value


def _deals_root(base_dir):
    return os.path.join(base_dir, DEALS_DIR) if base_dir else DEALS_DIR


def _matching_dated_folder_dates(company, proposal, base_dir=None):
    """Date suffixes of exact proposal_YYYY-MM-DD directories, newest last."""
    prefix = f"{proposal}_"
    pattern = os.path.join(
        glob.escape(_deals_root(base_dir)), glob.escape(company), f"{glob.escape(prefix)}*")
    dates = []
    for path in glob.glob(pattern):
        name = os.path.basename(path)
        if os.path.isdir(path) and name.startswith(prefix) and _is_valid_iso_date_str(name[len(prefix):]):
            dates.append(name[len(prefix):])
    return sorted(set(dates))


def _existing_date_str(company, proposal, base_dir=None):
    """Date of the newest folder containing state.json, or None when no deal state exists.

    Export-only folders created by deal_export --date-str do not qualify. A malformed
    but present state.json does qualify so read_state() surfaces its existing corruption
    error instead of silently skipping or overwriting it. Exact proposal/date matching and
    glob escaping prevent prefix-sharing or bracketed names from selecting a different deal.
    """
    dates = [
        date for date in _matching_dated_folder_dates(company, proposal, base_dir=base_dir)
        if os.path.isfile(os.path.join(
            _deals_root(base_dir), company, f"{proposal}_{date}", "state.json"))
    ]
    return dates[-1] if dates else None




def _require_known_company(company, base_dir=None):
    """The company's deal folder, or a refusal: starting a review is for a deal that already has a folder.

    A mistyped company name would otherwise quietly become a brand-new deal (issue #268); a deal that has never
    been reviewed starts with /triage or /spread instead.
    """
    company_dir = os.path.join(_deals_root(base_dir), company)
    if not os.path.isdir(company_dir):
        raise StateError(
            f"Refusing to start a review: there is no deal folder for company {company!r}; starting a review "
            "is for a deal that already has one (use /triage or /spread to start a new deal, or check the "
            "spelling); nothing was changed."
        )
    return company_dir


def _require_later_than_newest(company, proposal, date_str, base_dir=None):
    """Refuse a review date that is not strictly later than the newest existing state-bearing review.

    Every other command resumes the newest dated folder, so a review dated earlier would never become active.
    """
    newest_date = _existing_date_str(company, proposal, base_dir=base_dir)
    if newest_date is not None and date_str <= newest_date:
        raise StateError(
            f"Refusing to start review dated {date_str}: it must be strictly later than the newest "
            f"existing state-bearing review ({newest_date}) for this exact company and proposal; "
            "nothing was changed."
        )


def get_review_candidates(company, proposal, date_str=None, base_dir=None):
    """List eligible earlier review states for an exact company/proposal.

    The dated folder must contain a readable state.json accepted by read_state().
    Export-only folders, impossible folder dates and non-exact proposal names are not
    candidates; an earlier folder whose state.json cannot be read is not offered either, but it is
    reported under `skipped` (date and reason) so that "no earlier review" is never said about a
    review that exists. The company must already have a deal folder, and the date must be strictly
    later than the newest existing review (the same rules as start_review). Read-only.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    date_str = _validate_explicit_state_date(date_str)
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    _require_known_company(company, base_dir=base_dir)
    _require_later_than_newest(company, proposal, date_str, base_dir=base_dir)

    candidates = []
    skipped = []
    for candidate_date in reversed(_matching_dated_folder_dates(company, proposal, base_dir=base_dir)):
        if candidate_date >= date_str:
            continue
        candidate_path = state_path(company, proposal, date_str=candidate_date, base_dir=base_dir)
        if not os.path.isfile(candidate_path):
            continue
        try:
            candidate_state = read_state(
                company, proposal, date_str=candidate_date, base_dir=base_dir,
            )
        except (OSError, ValueError) as exc:
            # A broken or unusable state file is not an eligible comparison baseline, but it is not silent.
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            skipped.append({"date": candidate_date, "reason": f"state.json cannot be read: {reason}"})
            continue
        if candidate_state is None:
            # It may have disappeared after the file-existence check.
            continue
        candidates.append({"date": candidate_date, "state_path": candidate_path})

    default_prior_date = candidates[0]["date"] if candidates else None
    return {
        "company": company,
        "proposal": proposal,
        "date": date_str,
        "candidates": candidates,
        "default_prior_date": default_prior_date,
        "skipped": skipped,
    }


def _create_review_folder(target_dir, target_state_path, state):
    """Create the dated folder and its single state.json so that a failure leaves nothing behind.

    The state is written to a temporary file in the new folder and moved into place, and if anything fails
    (including an interrupt) the temporary file and the folder this call created are removed again.
    """
    try:
        os.mkdir(target_dir)
    except FileExistsError as exc:
        raise StateError(
            f"Refusing to start review: the dated folder already exists ({target_dir}); nothing was changed."
        ) from exc
    except OSError as exc:
        raise StateError(f"Could not start the review: {exc}; nothing was changed.") from exc
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, indent=2)
            state_file.write("\n")
        os.replace(tmp_path, target_state_path)
    except BaseException as exc:
        cleaned_up = True
        for leftover in (tmp_path, target_state_path):
            if leftover and os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except OSError:
                    cleaned_up = False
        try:
            os.rmdir(target_dir)
        except OSError:
            cleaned_up = False
        if isinstance(exc, OSError):
            outcome = (
                "the folder this call created was removed and nothing was changed" if cleaned_up
                else f"the new folder {target_dir} could not be removed and should be deleted by hand"
            )
            raise StateError(f"Could not start the review: {exc}; {outcome}.") from exc
        raise


def start_review(company, proposal, date_str=None, prior_date=None, no_prior=False, base_dir=None):
    """Create a fresh dated review state without inheriting any prior review fields.

    The company must already have a deal folder. The new date must be strictly later than every existing
    state-bearing date for this exact company/proposal. A selected prior is a link only: its data is never
    copied. The new dated folder and state.json are never overwritten, the whole decision and creation runs
    under one lock per company/proposal so that two starts cannot both pass the date check, and a failed
    creation leaves nothing behind.
    """
    if no_prior and prior_date is not None:
        raise StateError("--prior-date and --no-prior cannot be used together")
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    date_str = _validate_explicit_state_date(date_str)
    if prior_date is not None:
        prior_date = _validate_explicit_state_date(prior_date)

    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    company_dir = _require_known_company(company, base_dir=base_dir)
    target_state_path = state_path(company, proposal, date_str=date_str, base_dir=base_dir)
    target_dir = os.path.dirname(target_state_path)

    with _FileLock(os.path.join(company_dir, f"{proposal}.start"), timeout=LOCK_TIMEOUT_SECONDS):
        if os.path.lexists(target_dir):
            raise StateError(
                f"Refusing to start review: the dated folder already exists for {company!r} / "
                f"{proposal!r} on {date_str}; nothing was changed."
            )
        report = get_review_candidates(company, proposal, date_str=date_str, base_dir=base_dir)
        eligible_dates = {item["date"] for item in report["candidates"]}
        if prior_date is not None:
            if prior_date >= date_str:
                raise StateError(
                    f"Prior review date {prior_date} is not eligible: it must be earlier than the new review date "
                    f"{date_str}; nothing was changed."
                )
            if prior_date not in eligible_dates:
                raise StateError(
                    f"Prior review date {prior_date} is not eligible for exact company {company!r} and proposal "
                    f"{proposal!r}: no valid state.json exists in that exact dated folder; nothing was changed."
                )
            linked_date = prior_date
            message = f"Started a fresh review linked to the explicitly selected prior review dated {linked_date}."
        elif no_prior:
            linked_date = None
            message = "Started a fresh review without a prior link by request (--no-prior)."
        else:
            linked_date = report["default_prior_date"]
            if linked_date is None:
                message = "Started a fresh review without a prior link because no eligible earlier review was found."
            else:
                message = f"Started a fresh review linked to the nearest earlier review dated {linked_date}."

        state = {
            "company": company,
            "proposal": proposal,
            "date": date_str,
            "schema_version": SCHEMA_VERSION,
        }
        if linked_date is not None:
            state["prior_review_date"] = linked_date
        _create_review_folder(target_dir, target_state_path, state)

    return {
        "started": True,
        "state_path": target_state_path,
        "date": date_str,
        "prior_review_date": linked_date,
        "linked": linked_date is not None,
        "message": message,
    }


def _normalize_proposal_for_suggestion(proposal):
    """Normalize only case and runs of whitespace/hyphens for advisory near matches."""
    return re.sub(r"[\s-]+", " ", proposal.casefold()).strip()


def find_deal(company, proposal, base_dir=None):
    """Read-only exact deal lookup with normalization-only advisory candidates.

    A matching dated folder counts when its state.json is present, even when
    the file is corrupt; the normal read path remains responsible for raising
    that established error. Suggestions only consider valid dated folders
    with state.json, and never resolve/select a non-exact proposal.
    """
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    date_str = _existing_date_str(company, proposal, base_dir=base_dir)
    if date_str is not None:
        return {
            "record_found": True,
            "state_path": state_path(company, proposal, date_str=date_str, base_dir=base_dir),
            "date": date_str,
        }

    company_dir = os.path.join(_deals_root(base_dir), company)
    company_folder_exists = os.path.isdir(company_dir)
    candidates_by_proposal = {}
    if company_folder_exists:
        requested_key = _normalize_proposal_for_suggestion(proposal)
        with os.scandir(company_dir) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                parts = entry.name.rsplit("_", 1)
                if len(parts) != 2:
                    continue
                candidate_proposal, candidate_date = parts
                if not _is_valid_iso_date_str(candidate_date):
                    continue
                if candidate_proposal == proposal:
                    continue
                if _normalize_proposal_for_suggestion(candidate_proposal) != requested_key:
                    continue
                candidate_path = os.path.join(entry.path, "state.json")
                if not os.path.isfile(candidate_path):
                    continue
                previous = candidates_by_proposal.get(candidate_proposal)
                if previous is None or candidate_date > previous["date"]:
                    candidates_by_proposal[candidate_proposal] = {
                        "proposal": candidate_proposal,
                        "date": candidate_date,
                        "state_path": candidate_path,
                    }

    near_misses = sorted(
        candidates_by_proposal.values(),
        key=lambda item: (item["proposal"].casefold(), item["proposal"]),
    )
    return {
        "record_found": False,
        "company_folder_exists": company_folder_exists,
        "near_misses": near_misses,
    }


def existing_state_date_str(company, proposal, base_dir=None):
    """Public form of _existing_date_str(): the date of this deal's newest folder that holds a state.json, or None
    when the deal has no state anywhere. Lets an exporter tell "a brand-new deal" from "a deal whose state is in
    another dated folder"."""
    return _existing_date_str(company, proposal, base_dir=base_dir)


def resolve_export_date_str(company, proposal, date_str=None, base_dir=None):
    """Choose a standalone export folder without changing state discovery.

    An explicit date wins. Otherwise prefer the newest state-bearing folder so output
    stays with the active deal. If no state exists, reuse the newest existing date-shaped
    output folder (the export CLI's historical behavior); if none exists, use today.
    """
    if date_str:
        return date_str
    state_date = _existing_date_str(company, proposal, base_dir=base_dir)
    if state_date:
        return state_date
    dates = _matching_dated_folder_dates(company, proposal, base_dir=base_dir)
    return dates[-1] if dates else datetime.now().strftime("%Y-%m-%d")

def _resolve_date_str(company, proposal, date_str=None, base_dir=None, new_review=False):
    if date_str is not None:
        return _validate_explicit_state_date(date_str)
    if new_review:
        # Force today's date rather than resuming the most recent existing
        # folder -- e.g. a new annual review for the same company/proposal
        # must not silently merge into last year's state.json (stale PD/LGD,
        # financials, policy_state). Callers that run a new review resolve
        # this once and pass the concrete date everywhere, including to
        # deal_export.export_deal() (issue #97).
        return datetime.now().strftime("%Y-%m-%d")
    return _existing_date_str(company, proposal, base_dir=base_dir) or datetime.now().strftime("%Y-%m-%d")


def resolve_date_str(company, proposal, date_str=None, base_dir=None, new_review=False):
    """Public wrapper around _resolve_date_str() -- for a caller (e.g.
    orchestrator.py's run_pipeline()) that needs to resolve this deal's
    dated-folder date *once* and then pass that concrete value to every
    subsequent read_state()/write_state()/append_review_trail()/
    state_path() call via their existing `date_str` parameter, rather than
    re-deriving it at each call site via `new_review=new_review`.

    Re-deriving per call site is fragile: every one of those functions'
    own `new_review` resolution is independent, so a call site that forgets
    to pass `new_review=new_review` (or, like append_review_trail() before
    this function existed, has no `new_review` parameter to pass at all)
    silently falls back to auto-discovering the most recent existing dated
    folder instead of forcing today's -- exactly the "new annual review
    silently merges into last year's state" bug `new_review` exists to
    prevent, now reintroduced at the one call site that got missed. Only
    the *first* resolution in a run should ever pass `new_review`; every
    later call in that same run should pass the concrete `date_str` this
    returns instead, so there is no second flag to forget.
    """
    return _resolve_date_str(company, proposal, date_str=date_str, base_dir=base_dir, new_review=new_review)


def state_path(company, proposal, date_str=None, base_dir=None, new_review=False):
    """Path to this deal's state.json, resolved the same way deal_export.py
    resolves its output directory: deals/<Company>/<Proposal>_<Date>/ --
    except that an explicit `date_str` isn't required to find an existing
    file; see the module docstring.

    `new_review=True` forces today's date instead of auto-resuming the most
    recent existing dated folder for this company/proposal -- see
    _resolve_date_str(). Ignored if `date_str` is given explicitly.

    `company`/`proposal` are sanitized here -- the one place every other
    function in this module (and deal_export.py, via its own call) routes
    through -- since they're used directly as path components below. An
    explicitly-passed `date_str` must first be a real calendar date in exact
    YYYY-MM-DD form, then is sanitized as a path component; an auto-resolved
    one (the common case -- see _resolve_date_str()) is always either today's
    own ISO-format date or discovered from a valid existing directory name.
    """
    date_str = _validate_explicit_state_date(date_str)
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str is not None:
        date_str = sanitize_path_component(date_str, "date_str")
    date_str = _resolve_date_str(company, proposal, date_str=date_str, base_dir=base_dir, new_review=new_review)
    return os.path.join(_deals_root(base_dir), company, f"{proposal}_{date_str}", "state.json")


def read_state(company, proposal, date_str=None, base_dir=None, new_review=False, keys=()):
    """Return the parsed state.json dict for this deal, or None if it doesn't exist yet.

    Raises ValueError (not a raw json.JSONDecodeError) if the file exists
    but is corrupted -- most likely left partial by an interrupted write --
    rather than either crashing with an opaque traceback or, worse, silently
    treating corrupted-but-present data as "no state yet" (which write_state()
    would then happily overwrite, permanently losing whatever was still
    readable).

    `new_review=True` forces today's date instead of auto-resuming the most
    recent existing dated folder -- see _resolve_date_str(). A caller
    starting a genuinely new annual review under the same company/proposal
    should pass this so it reads back `None` (nothing yet exists for
    today's fresh folder) rather than last year's stale state.

    A deal folder written before SCHEMA_VERSION existed has no
    "schema_version" key at all -- callers must never assume the key is
    present. Rather than leave that as an unhandled absence for every
    caller to guard against separately, it's normalized here: the returned
    dict always has a "schema_version" key, defaulting to
    LEGACY_SCHEMA_VERSION when the file itself doesn't carry one.

    The top level must be a JSON object, or StateShapeError is raised. `keys` (default none) names the keys
    the caller is about to rely on; they are checked with validate_state() so a wrongly-typed value fails here, naming
    the key, instead of as an opaque AttributeError/TypeError later. A corrupt file is a StateShapeError too.
    """
    date_str = _validate_explicit_state_date(date_str)
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir, new_review=new_review)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        try:
            state = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
            # Not valid UTF-8 JSON (a UTF-16 file from PowerShell's `>`, say) or nested absurdly deep: the file's
            # fault, reported like any other unusable state rather than as a traceback.
            detail = e if isinstance(e, json.JSONDecodeError) else f"{type(e).__name__}: {str(e)[:80]}"
            raise StateShapeError(
                f"state.json at {path} is corrupted and could not be parsed ({detail}). "
                "It may have been left partial by an interrupted write. Restore it "
                "from a backup or fix it by hand before continuing."
            ) from e
    try:
        validate_finite_state_numbers(state, path=path)
    except NonFiniteNumberError as exc:
        raise StateShapeError(
            f"state.json at {path} contains {exc}. Fix it by hand or restore it from a backup; nothing was changed."
        ) from exc
    validate_state(state, keys=keys, path=path)
    state.setdefault("schema_version", LEGACY_SCHEMA_VERSION)
    return state


def write_state(company, proposal, date_str=None, base_dir=None, new_review=False, **fields):
    """Merge `fields` into this deal's state.json (if any) and write it back.

    Creates deals/<Company>/<Proposal>_<Date>/ if it doesn't exist yet --
    reusing an existing dated folder for this company/proposal when
    `date_str` isn't given, rather than always creating a new one dated
    today. `company`, `proposal`, and `date` are always kept in sync
    automatically.

    `new_review=True` forces today's date instead of auto-resuming the most
    recent existing dated folder -- see _resolve_date_str(). Use this for a
    genuinely new annual review under the same company/proposal, so it gets
    its own fresh dated folder (and, paired with `read_state(...,
    new_review=True)`, doesn't inherit stale PD/LGD/financials/policy_state
    from a prior year's folder) rather than silently merging into it.

    The merge is a shallow dict.update(): a fresh `financials={...}` replaces
    the whole financials dict rather than merging inside it, and a fresh
    `steps_completed=[...]` replaces the whole list. Callers that want to
    extend a nested dict or list should read_state() first, build the full
    updated value themselves, and pass that back.

    Returns the full state dict that was written.
    """
    # Reject invalid dates before any path handling or directory creation.
    date_str = _validate_explicit_state_date(date_str)

    # Reject invalid numeric values before path creation or state mutation.
    try:
        validate_finite_state_numbers(fields, path="state update")
    except NonFiniteNumberError as exc:
        raise StateShapeError(
            f"Refusing to write state.json: {exc}. No state was changed."
        ) from exc

    # Sanitize before the first direct use below (_resolve_date_str's own
    # os.path.join) -- state_path() sanitizes again for its own join, but
    # that's just a redundant, harmless re-validation of the same strings.
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str is not None:
        date_str = sanitize_path_component(date_str, "date_str")

    date_str = _resolve_date_str(company, proposal, date_str=date_str, base_dir=base_dir, new_review=new_review)
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with _FileLock(path):
        return _merge_and_write(path, company, proposal, date_str, base_dir, fields)


def _check_schema_version_is_writable(recorded, path):
    """Refuse (before anything is touched) to overwrite a state this code cannot safely own: one recorded as NEWER
    than SCHEMA_VERSION would be stamped back down to this version, losing the fact that it holds newer-shaped data
    (issue #170); one whose version cannot be parsed cannot be compared at all. Equal, older, and absent (read_state
    reports a missing key as LEGACY_SCHEMA_VERSION) are written and upgraded as before."""
    try:
        recorded_version = parse_schema_version(recorded)
    except ValueError as exc:
        raise SchemaVersionError(
            f"Refusing to write {path}: {exc}. Fix or remove the \"schema_version\" value by hand "
            f"(this code writes version {SCHEMA_VERSION}); nothing was changed.") from exc
    if recorded_version > parse_schema_version(SCHEMA_VERSION):
        raise SchemaVersionError(
            f"Refusing to write {path}: it was written by a newer framework (schema_version {recorded}) than "
            f"this one supports ({SCHEMA_VERSION}); writing would downgrade it. Update this checkout of the framework "
            "instead; nothing was changed.")


def _merge_and_write(path, company, proposal, date_str, base_dir, fields):
    """The actual read-modify-write, factored out so both write_state() and
    append_review_trail() can hold one _FileLock across their *entire*
    read-modify-write -- including, for append_review_trail(), the read and
    review_trail-list-build step that happens before this is called -- while
    this helper itself never acquires the lock (it would deadlock retrying
    to acquire a lock its own caller is already holding).
    """
    state = read_state(company, proposal, date_str=date_str, base_dir=base_dir) or {}
    # No file yet (state == {}) has no version either; that is a fresh deal, not a malformed one. An existing file's
    # missing key already reads back as LEGACY_SCHEMA_VERSION; an explicit null stays None and is refused below.
    _check_schema_version_is_writable(state.get("schema_version", LEGACY_SCHEMA_VERSION), path)
    state["company"] = company
    state["proposal"] = proposal
    state["date"] = date_str
    state["schema_version"] = SCHEMA_VERSION
    state.update(fields)
    try:
        validate_finite_state_numbers(state, path=path)
    except NonFiniteNumberError as exc:
        raise StateShapeError(
            f"Refusing to write state.json: {exc}. No state was changed."
        ) from exc

    # Atomic: write to a temp file in the same directory (so os.replace() is
    # a same-filesystem rename, not a cross-device copy) and swap it into
    # place, rather than truncating state.json directly. A crash, Ctrl+C, or
    # power loss mid-write then leaves the temp file damaged and state.json
    # untouched, instead of leaving state.json itself half-written and
    # unreadable by every subsequent read_state() call for this deal.
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return state


def append_review_trail(company, proposal, verdict, notes=None, timestamp=None,
                         date_str=None, base_dir=None, **extra_fields):
    """Append one entry to this deal's append-only `review_trail`, and set
    `review_verdict` to this iteration's verdict as a "latest verdict"
    convenience field.

    write_state()'s shallow merge would silently replace the whole
    review_trail list if a caller just passed a fresh `review_trail=[...]`
    -- since /assemble loops /review until APPROVED, that was losing every
    intermediate REJECTED verdict and its revision notes as soon as the
    next iteration wrote state. This reads the existing trail first (`[]`
    if there isn't one yet), appends, and writes the full list back, so
    `review_trail` is always the deal's complete audit history regardless
    of how many review iterations it took.

    `iteration` is always `len(existing review_trail) + 1`, so it's correct
    whether this is orchestrator.py's one-shot audit call or one loop of
    /assemble -> /review. Any `extra_fields` are merged in on the same
    write (e.g. `deal_type`, `steps_completed`), exactly like write_state().

    Returns the full state dict that was written.
    """
    date_str = _validate_explicit_state_date(date_str)
    timestamp = timestamp or datetime.now().isoformat()

    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str is not None:
        date_str = sanitize_path_component(date_str, "date_str")
    date_str = _resolve_date_str(company, proposal, date_str=date_str, base_dir=base_dir)
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # The read (existing trail) and the write must happen under the *same*
    # lock acquisition -- two concurrent calls each locking only around
    # write_state()'s own internal read-modify-write would still race here,
    # since each would have already computed its `trail` from an unlocked
    # read taken before either write happened, and the second write would
    # clobber the first's appended entry with its own stale-based list.
    with _FileLock(path):
        existing = read_state(company, proposal, date_str=date_str, base_dir=base_dir, keys=("review_trail",)) or {}
        trail = list(existing.get("review_trail") or [])
        trail.append({
            "iteration": len(trail) + 1,
            "verdict": verdict,
            "notes": notes,
            "timestamp": timestamp,
        })
        fields = dict(extra_fields, review_verdict=verdict, review_trail=trail)
        return _merge_and_write(path, company, proposal, date_str, base_dir, fields)


def export_reviewer_view(company, proposal, base_dir=None):
    """Write derived review-only copies beside a deal's state.json.

    The source of truth remains state.json. These files are a convenience for
    the interactive Reviewer, not records for any other script to consume.
    Validate the entire state and its schema version before creating or
    replacing either derived file.
    """
    source_path = state_path(company, proposal, base_dir=base_dir)
    state = read_state(company, proposal, base_dir=base_dir)
    if state is None:
        raise StateShapeError(
            f"Cannot export the Reviewer view: no state.json exists at {source_path}; nothing was written.")

    validate_state(state, path=source_path)
    _check_schema_version_is_writable(state.get("schema_version", LEGACY_SCHEMA_VERSION), source_path)

    review_trail = state.get("review_trail") or []
    state_for_review = dict(state)
    state_for_review.pop("review_trail", None)
    state_for_review.pop("review_verdict", None)

    review_view_dir = os.path.join(os.path.dirname(source_path), "review_view")
    state_for_review_path = os.path.join(review_view_dir, "state_for_review.json")
    review_trail_path = os.path.join(review_view_dir, "review_trail.json")
    os.makedirs(review_view_dir, exist_ok=True)
    with open(state_for_review_path, "w", encoding="utf-8") as f:
        json.dump(state_for_review, f, indent=2)
    with open(review_trail_path, "w", encoding="utf-8") as f:
        json.dump(review_trail, f, indent=2)

    return {"state_for_review": state_for_review_path, "review_trail": review_trail_path}


def required_steps_completed(steps_completed, required):
    """Return the subset of `required` that is missing from `steps_completed`
    (empty list if nothing is missing).

    This exists so step-order enforcement (e.g. "don't let /assemble draft
    before /spread has run") can be a code-level check against state.json's
    recorded `steps_completed`, rather than living purely in a slash
    command's prose instructions -- prose enforcement is easy for a session
    to drift past (a compacted conversation, a resumed session, a user who
    insists they already ran the step). This function stays a pure list
    comparison with no opinion on *which* steps are required for which
    command -- that's a policy call left to the caller (see
    .claude/commands/assemble.md, which currently hard-requires only
    "spread" and deliberately leaves "triage"/"collateral" as prompt-level
    reminders rather than hard gates, since a deal can legitimately have no
    collateral or skip triage in favor of user-supplied info).

    Order-preserving and duplicate-tolerant: iterates `required` in the
    order given (so the caller's own list order is what a human sees when
    this is printed) and includes an entry at most once even if `required`
    itself repeats it.
    """
    completed = set(steps_completed or [])
    missing = []
    for step in required:
        if step not in completed and step not in missing:
            missing.append(step)
    return missing


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="Manage a deal's persisted state without an Anthropic dependency. "
                    "--find is a read-only exact lookup; --review-candidates lists eligible earlier reviews; "
                    "--start-review creates a fresh state with an optional prior-review link; --check-steps "
                    "reports missing required steps; --export-reviewer-view separates review history; "
                    "--record-group-basis records source and perimeter metadata."
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--find", action="store_true",
                       help="Read-only exact deal lookup with advisory normalized near matches.")
    modes.add_argument("--review-candidates", action="store_true",
                       help="Read-only list of valid earlier reviews for the exact company and proposal.")
    modes.add_argument("--start-review", action="store_true",
                       help="Create a new review state without copying any prior-review content.")
    modes.add_argument("--check-steps", action="store_true",
                       help="Report which required steps are missing from steps_completed.")
    modes.add_argument("--export-reviewer-view", action="store_true",
                       help="Write state_for_review.json and review_trail.json under review_view/.")
    modes.add_argument("--record-group-basis", action="store_true",
                       help="Record metadata for borrower-supplied consolidated group accounts on an existing deal.")
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--date",
                        help="YYYY-MM-DD, defaults to today: the review date with --start-review and --review-candidates; the perimeter-confirmation date with --record-group-basis.")
    parser.add_argument("--group-basis-file",
                        help="UTF-8 JSON object with group-basis metadata, used only with --record-group-basis.")
    parser.add_argument("--perimeter-confirmed", action="store_true",
                        help="Explicitly confirm the group perimeter as an analyst; required with --record-group-basis.")
    prior = parser.add_mutually_exclusive_group()
    prior.add_argument("--prior-date",
                       help="Explicit earlier review date to link when starting a review.")
    prior.add_argument("--no-prior", action="store_true",
                       help="Start without a prior link, even when earlier reviews exist.")
    parser.add_argument("--required",
                        help="Comma-separated list of steps that must appear in this deal's "
                             "steps_completed, e.g. spread,collateral")
    args = parser.parse_args()

    if args.find or args.check_steps or args.export_reviewer_view:
        if args.date is not None:
            parser.error("--date is only valid with --review-candidates or --start-review")
        if args.prior_date is not None or args.no_prior:
            parser.error("--prior-date and --no-prior are only valid with --start-review")
    if args.review_candidates and (args.prior_date is not None or args.no_prior):
        parser.error("--prior-date and --no-prior are only valid with --start-review")
    if args.record_group_basis:
        if args.prior_date is not None or args.no_prior:
            parser.error("--prior-date and --no-prior are only valid with --start-review")
        if args.required is not None:
            parser.error("--required is only valid with --check-steps")
    elif args.group_basis_file is not None or args.perimeter_confirmed:
        parser.error("--group-basis-file and --perimeter-confirmed are only valid with --record-group-basis")

    if args.find:
        try:
            report = find_deal(args.company, args.proposal)
        except (OSError, ValueError) as exc:
            sys.exit(f"error: {exc}")
        print(json.dumps(report, indent=2))
    elif args.check_steps:
        if args.required is None:
            parser.error("--required is required with --check-steps")
        required = [s.strip() for s in args.required.split(",") if s.strip()]
        try:
            state = read_state(args.company, args.proposal, keys=("steps_completed",)) or {}
        except (OSError, ValueError) as exc:
            sys.exit(f"error: {exc}")    # unreadable state or unsafe names are errors, not a "missing steps" result
        missing = required_steps_completed(state.get("steps_completed") or [], required)
        print(json.dumps({"missing_steps": missing, "ok": len(missing) == 0}, indent=2))
    elif args.export_reviewer_view:
        try:
            paths = export_reviewer_view(args.company, args.proposal)
        except (OSError, ValueError) as exc:
            sys.exit(f"error: {exc}")
        print(json.dumps(paths, indent=2))
    elif args.record_group_basis:
        if not args.group_basis_file:
            sys.exit("error: --group-basis-file is required with --record-group-basis")
        try:
            report = record_group_basis(
                args.company, args.proposal, args.group_basis_file,
                perimeter_confirmed=args.perimeter_confirmed, date_str=args.date,
            )
        except (OSError, ValueError) as exc:
            sys.exit(f"error: {exc}")
        print(json.dumps(report))
    elif args.review_candidates:
        try:
            report = get_review_candidates(args.company, args.proposal, date_str=args.date)
        except (OSError, ValueError) as exc:
            sys.exit(f"error: {exc}")
        print(json.dumps(report, indent=2))
    else:
        try:
            report = start_review(
                args.company, args.proposal, date_str=args.date,
                prior_date=args.prior_date, no_prior=args.no_prior,
            )
        except (OSError, ValueError) as exc:
            sys.exit(f"error: {exc}")
        print(json.dumps(report, indent=2))
