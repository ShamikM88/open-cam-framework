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
from datetime import datetime

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


# The structural contract of the keys the framework reads (issue #171). A key that is absent or null is simply
# "not recorded" and is always accepted; unknown keys are never inspected (a newer framework may add them, and they
# must survive a write). Only the container types are checked -- the shape of each element of a list is not.
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
FINANCIALS_SOURCES = ("framework-computed", "analyst-supplied")
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
    shapes = dict(STATE_SHAPES) if keys is None else (
        dict(keys) if isinstance(keys, dict) else {key: STATE_SHAPES[key] for key in keys})
    problems = []
    for key, kind in shapes.items():
        value = state.get(key)
        if value is None:
            continue
        if kind == "periods":
            problems.extend(_problems_with_periods(key, value))
        elif kind == "object":
            if not isinstance(value, dict):
                problems.append(f'"{key}" must be {_SHAPE_DESCRIPTIONS["object"]}, found {_json_type(value)}')
        elif kind == "downside":
            if not isinstance(value, dict):
                problems.append(f'"{key}" must be {_SHAPE_DESCRIPTIONS["downside"]}, found {_json_type(value)}')
            else:
                for inner in ("financials", "ratios"):
                    if value.get(inner) is not None:
                        problems.extend(_problems_with_periods(f"{key}.{inner}", value[inner]))
        elif kind == "list":
            if not isinstance(value, list):
                problems.append(f'"{key}" must be {_SHAPE_DESCRIPTIONS["list"]}, found {_json_type(value)}')
        elif kind == "source":
            if value not in FINANCIALS_SOURCES:
                problems.append(f'"{key}" must be {_SHAPE_DESCRIPTIONS["source"]}, found {value!r}')
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
    if _UNSAFE_PATH_CHARS_RE.search(value) or value in (".", ".."):
        raise ValueError(
            f"{field_name} {value!r} isn't safe to use as a filesystem path "
            "component (no \\ / : * ? \" < > | characters, and not \".\" or \"..\")."
        )
    return value


def _deals_root(base_dir):
    return os.path.join(base_dir, DEALS_DIR) if base_dir else DEALS_DIR


def _existing_date_str(company, proposal, base_dir=None):
    """Date suffix of the most recent existing deals/<company>/<proposal>_<date>/
    folder for this company/proposal, or None if there isn't one yet.

    Date suffixes are ISO-8601 (YYYY-MM-DD), so sorting them as plain
    strings also sorts them chronologically -- the lexicographic max is
    the most recent.

    Only a folder whose remainder after `<proposal>_` is exactly a date
    counts: otherwise a proposal named "Fleet" would also claim a different
    deal's "Fleet_Q2_2026-05-01" folder (its "date" would be
    "Q2_2026-05-01", which sorts above any real one). The company and
    proposal are glob-escaped so a "[" or "]" in a name matches literally
    instead of as a character class.
    """
    prefix = f"{proposal}_"
    pattern = os.path.join(
        glob.escape(_deals_root(base_dir)), glob.escape(company), f"{glob.escape(prefix)}*")
    dates = []
    for p in glob.glob(pattern):
        name = os.path.basename(p)
        if os.path.isdir(p) and name.startswith(prefix) and _ISO_DATE_RE.fullmatch(name[len(prefix):]):
            dates.append(name[len(prefix):])
    return max(dates) if dates else None


def _resolve_date_str(company, proposal, date_str=None, base_dir=None, new_review=False):
    if date_str:
        return date_str
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
    explicitly-passed `date_str` is sanitized too (it's embedded in the
    same "{proposal}_{date_str}" component); an auto-resolved one (the
    common case -- see _resolve_date_str()) is always either today's own
    ISO-format date or discovered from an existing directory name, so it
    never needs this check.
    """
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str:
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
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir, new_review=new_review)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        try:
            state = json.load(f)
        except json.JSONDecodeError as e:
            raise StateShapeError(
                f"state.json at {path} is corrupted and could not be parsed ({e}). "
                "It may have been left partial by an interrupted write. Restore it "
                "from a backup or fix it by hand before continuing."
            ) from e
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
    # Sanitize before the first direct use below (_resolve_date_str's own
    # os.path.join) -- state_path() sanitizes again for its own join, but
    # that's just a redundant, harmless re-validation of the same strings.
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str:
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
    timestamp = timestamp or datetime.now().isoformat()

    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str:
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
        description="Check whether a deal's state.json has recorded all of a given set of "
                     "required steps in steps_completed. Prints JSON ({\"missing_steps\": [...], "
                     "\"ok\": true/false}) to stdout and exits 0 whatever the result (a state.json it cannot read is an error instead: one 'error:' line, exit 1) -- this project doesn't "
                     "use exit codes as its enforcement mechanism (see policy_check.py); the "
                     "caller (a slash command's Bash step, read by the Claude session running "
                     "it) is the one that decides whether to stop and tell the user what's "
                     "missing. No Anthropic dependency -- callable from a slash command's Bash "
                     "step."
    )
    parser.add_argument("--check-steps", action="store_true", required=True,
                         help="The only supported mode right now -- reserved so future "
                              "state_manager CLI subcommands don't have to guess this flag's "
                              "absence means something else.")
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--required", required=True,
                         help="Comma-separated list of steps that must appear in this deal's "
                              "steps_completed, e.g. spread,collateral")
    args = parser.parse_args()

    required = [s.strip() for s in args.required.split(",") if s.strip()]
    try:
        state = read_state(args.company, args.proposal, keys=("steps_completed",)) or {}
    except StateError as exc:
        sys.exit(f"error: {exc}")    # a state it cannot read is an error, not a "missing steps" result
    missing = required_steps_completed(state.get("steps_completed") or [], required)

    print(json.dumps({"missing_steps": missing, "ok": len(missing) == 0}, indent=2))
