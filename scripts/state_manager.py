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
than always defaulting to today -- unlike deal_export.py's export_deal(),
whose job (a one-shot export) never needs to be found again later, so
today is always correct there. This is what lets a deal resumed on a later
calendar day still find its original state.json.

review_trail: append_review_trail() is the one exception to write_state()'s
plain shallow-merge semantics baked into this module itself, rather than
left to each caller -- see its docstring.
"""
import glob
import json
import os
import re
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

_UNSAFE_PATH_CHARS_RE = re.compile(r'[\\/:*?"<>|]')

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
            except (FileExistsError, PermissionError):
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
                    )
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
    """
    prefix = f"{proposal}_"
    pattern = os.path.join(_deals_root(base_dir), company, f"{prefix}*")
    dates = [
        os.path.basename(p)[len(prefix):]
        for p in glob.glob(pattern)
        if os.path.isdir(p) and os.path.basename(p).startswith(prefix)
    ]
    return max(dates) if dates else None


def _resolve_date_str(company, proposal, date_str=None, base_dir=None, new_review=False):
    if date_str:
        return date_str
    if new_review:
        # Force today's date rather than resuming the most recent existing
        # folder -- e.g. a new annual review for the same company/proposal
        # must not silently merge into last year's state.json (stale PD/LGD,
        # financials, policy_state). Matches deal_export.py's own
        # always-today convention for a one-shot export.
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


def read_state(company, proposal, date_str=None, base_dir=None, new_review=False):
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
    """
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir, new_review=new_review)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        try:
            state = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"state.json at {path} is corrupted and could not be parsed ({e}). "
                "It may have been left partial by an interrupted write. Restore it "
                "from a backup or fix it by hand before continuing."
            ) from e
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


def _merge_and_write(path, company, proposal, date_str, base_dir, fields):
    """The actual read-modify-write, factored out so both write_state() and
    append_review_trail() can hold one _FileLock across their *entire*
    read-modify-write -- including, for append_review_trail(), the read and
    review_trail-list-build step that happens before this is called -- while
    this helper itself never acquires the lock (it would deadlock retrying
    to acquire a lock its own caller is already holding).
    """
    state = read_state(company, proposal, date_str=date_str, base_dir=base_dir) or {}
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
        existing = read_state(company, proposal, date_str=date_str, base_dir=base_dir) or {}
        trail = list(existing.get("review_trail") or [])
        trail.append({
            "iteration": len(trail) + 1,
            "verdict": verdict,
            "notes": notes,
            "timestamp": timestamp,
        })
        fields = dict(extra_fields, review_verdict=verdict, review_trail=trail)
        return _merge_and_write(path, company, proposal, date_str, base_dir, fields)
