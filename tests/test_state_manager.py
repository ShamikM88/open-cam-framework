import json
import os
from pathlib import Path
import threading
import time
import pytest
from datetime import datetime

import state_manager
from deal_export import export_deal
from state_manager import (
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SchemaVersionError,
    StateShapeError,
    _FileLock,
    append_review_trail,
    export_reviewer_view,
    parse_schema_version,
    read_state,
    required_steps_completed,
    resolve_date_str,
    sanitize_path_component,
    state_path,
    write_state,
)


def test_read_state_returns_none_when_no_file_exists(tmp_path):
    assert read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path)) is None


# ---------------------------------------------------------------------------
# Schema versioning: write_state() always stamps the current version;
# read_state() never assumes it's present on an older file.
# ---------------------------------------------------------------------------

def test_write_state_stamps_the_current_schema_version_on_a_fresh_deal(tmp_path):
    base = str(tmp_path)
    state = write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert state["schema_version"] == SCHEMA_VERSION == "1.2.0"


def test_write_state_sets_schema_version_on_every_subsequent_write_too(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, deal_type="asset_finance")
    state = write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, inputs={"pd": "0.20%"})
    assert state["schema_version"] == SCHEMA_VERSION


def test_read_state_reports_legacy_version_for_a_file_with_no_schema_version_key(tmp_path):
    """A deal folder written before schema_version existed at all -- must
    not raise, and must report LEGACY_SCHEMA_VERSION rather than leaving
    the key absent."""
    base = str(tmp_path)
    path = state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"company": "Acme Corp", "proposal": "Fleet Loan", "date": "2026-01-15"}, f)

    state = read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert state is not None
    assert state["schema_version"] == LEGACY_SCHEMA_VERSION == "0.0.0"


def test_write_state_upgrades_a_legacy_file_to_the_current_schema_version(tmp_path):
    base = str(tmp_path)
    path = state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"company": "Acme Corp", "proposal": "Fleet Loan", "date": "2026-01-15"}, f)

    state = write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, deal_type="asset_finance")
    assert state["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Never downgrade (issue #170): a file recorded as NEWER than SCHEMA_VERSION is refused, not re-stamped.
# ---------------------------------------------------------------------------

def _write_raw_state(base, recorded_version, **extra):
    """A state.json on disk with exactly this schema_version value (a missing key if the sentinel is MISSING)."""
    path = state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = {"company": "Acme Corp", "proposal": "Fleet Loan", "date": "2026-01-15", **extra}
    if recorded_version is not MISSING:
        content["schema_version"] = recorded_version
    with open(path, "w", encoding="utf-8") as f:
        json.dump(content, f)
    with open(path, "rb") as f:
        return path, f.read()


MISSING = object()


@pytest.mark.parametrize("text, expected", [("1.1.0", (1, 1, 0)), ("0.0.0", (0, 0, 0)), ("1.10.0", (1, 10, 0)),
                                            ("10.20.30", (10, 20, 30)), ("01.002.0", (1, 2, 0))])
def test_parse_schema_version_returns_integers(text, expected):
    assert parse_schema_version(text) == expected


def test_versions_compare_numerically_not_as_strings():
    assert parse_schema_version("1.10.0") > parse_schema_version("1.9.0")      # as strings, "1.10.0" < "1.9.0"
    assert parse_schema_version("2.0.0") > parse_schema_version("1.99.99")
    assert parse_schema_version("1.1.0") == parse_schema_version("1.1.0")
    assert parse_schema_version("1.1.1") > parse_schema_version("1.1.0")


MALFORMED_VERSIONS = ["", "abc", "1", "1.2", "1.2.3.4", "v1.1.0", "1.1.0-beta", " 1.1.0", "1.1.0 ", "1.1.0\n", "1..0",
                      "1.-1.0", "-1.0.0", "1.1.x", "\u0661.\u0661.\u0660", None, 1, 1.1, True, [], ["1.1.0"], {"v": "1.1.0"}]


@pytest.mark.parametrize("value", MALFORMED_VERSIONS, ids=repr)
def test_parse_schema_version_rejects_anything_that_is_not_major_minor_patch_digits(value):
    with pytest.raises(ValueError, match="not a MAJOR.MINOR.PATCH"):
        parse_schema_version(value)


def test_a_newer_schema_version_is_refused_and_the_file_is_left_byte_for_byte_unchanged(tmp_path):
    path, before = _write_raw_state(str(tmp_path), "9.9.9", future_key={"nested": [1, 2]})
    with pytest.raises(SchemaVersionError) as caught:
        write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path), inputs={"pd": "0.2%"})
    message = str(caught.value)
    assert "9.9.9" in message and SCHEMA_VERSION in message and path in message and "downgrade" in message
    with open(path, "rb") as f:
        assert f.read() == before
    assert os.listdir(os.path.dirname(path)) == ["state.json"]          # no temp file left behind


def test_the_refusal_is_a_value_error_so_existing_handlers_still_catch_it():
    assert issubclass(SchemaVersionError, ValueError)


def test_the_comparison_is_semantic_so_1_10_0_is_newer_than_1_9_0(tmp_path, monkeypatch):
    monkeypatch.setattr(state_manager, "SCHEMA_VERSION", "1.9.0")
    _write_raw_state(str(tmp_path), "1.10.0")
    with pytest.raises(SchemaVersionError):
        write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path))
    monkeypatch.setattr(state_manager, "SCHEMA_VERSION", "1.10.0")           # and 1.9.0 is older than 1.10.0
    _write_raw_state(str(tmp_path), "1.9.0")
    assert write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path))["schema_version"] == "1.10.0"


@pytest.mark.parametrize("recorded", ["1.1.0", "1.0.9", "1.0.0", "0.9.9", "0.0.0", MISSING],
                         ids=["equal", "older-patch", "older-minor", "older-major", "legacy", "missing"])
def test_equal_older_and_missing_versions_are_written_and_upgraded_as_before(tmp_path, recorded):
    _write_raw_state(str(tmp_path), recorded, keep="me")
    state = write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path), deal_type="x")
    assert state["schema_version"] == SCHEMA_VERSION and state["keep"] == "me" and state["deal_type"] == "x"
    assert read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path))["schema_version"] == SCHEMA_VERSION


def test_unknown_fields_survive_a_permitted_write(tmp_path):
    _write_raw_state(str(tmp_path), "1.0.0", future_key={"nested": [1, 2, {"deeper": True}]}, x_note="keep me")
    state = write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path), deal_type="x")
    assert state["future_key"] == {"nested": [1, 2, {"deeper": True}]} and state["x_note"] == "keep me"


@pytest.mark.parametrize("value", [v for v in MALFORMED_VERSIONS if v is not None] + [None], ids=repr)
def test_a_malformed_recorded_version_is_refused_not_overwritten(tmp_path, value):
    """It cannot be compared, so it cannot be shown to be older: overwriting it could be the same silent downgrade."""
    path, before = _write_raw_state(str(tmp_path), value)
    with pytest.raises(SchemaVersionError, match="not a MAJOR.MINOR.PATCH"):
        write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path), deal_type="x")
    with open(path, "rb") as f:
        assert f.read() == before


def test_append_review_trail_refuses_a_newer_schema_too(tmp_path):
    path, before = _write_raw_state(str(tmp_path), "2.0.0")
    with pytest.raises(SchemaVersionError):
        append_review_trail("Acme Corp", "Fleet Loan", "APPROVED", date_str="2026-01-15", base_dir=str(tmp_path))
    with open(path, "rb") as f:
        assert f.read() == before


def test_reading_a_newer_schema_is_still_allowed(tmp_path):
    """Only writing is refused: a newer file stays readable (the deal-export and policy checks read it)."""
    _write_raw_state(str(tmp_path), "9.9.9", future_key=1)
    state = read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path))
    assert state["schema_version"] == "9.9.9" and state["future_key"] == 1


def test_a_fresh_deal_with_no_file_is_still_created_at_the_current_version(tmp_path):
    state = write_state("Fresh Co", "Loan", date_str="2026-01-15", base_dir=str(tmp_path))
    assert state["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Path sanitization: company/proposal are used directly as filesystem path
# components -- an unsanitized value could otherwise escape the intended
# deals/<company>/<proposal>_<date>/ tree entirely (os.path.join discards
# every earlier segment when a later one looks like an absolute path).
# ---------------------------------------------------------------------------

def test_sanitize_path_component_rejects_path_separators():
    with pytest.raises(ValueError):
        sanitize_path_component("Acme/Corp", "company")
    with pytest.raises(ValueError):
        sanitize_path_component("Acme\\Corp", "company")


def test_sanitize_path_component_rejects_drive_qualified_paths():
    with pytest.raises(ValueError):
        sanitize_path_component("C:\\Windows\\Temp\\evil", "company")


def test_sanitize_path_component_rejects_dot_and_dotdot():
    with pytest.raises(ValueError):
        sanitize_path_component("..", "proposal")
    with pytest.raises(ValueError):
        sanitize_path_component(".", "proposal")


def test_sanitize_path_component_accepts_ordinary_names():
    assert sanitize_path_component("Acme Corp", "company") == "Acme Corp"


def test_write_state_rejects_unsafe_company(tmp_path):
    with pytest.raises(ValueError):
        write_state("C:\\Windows\\Temp\\evil", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path))
    # Nothing must have been written anywhere, including outside base_dir.
    assert not os.path.exists(os.path.join(str(tmp_path), "deals"))


def test_state_path_rejects_unsafe_proposal(tmp_path):
    with pytest.raises(ValueError):
        state_path("Acme Corp", "../../escape", date_str="2026-01-15", base_dir=str(tmp_path))


def test_state_path_rejects_unsafe_explicit_date_str(tmp_path):
    """date_str is embedded in the same "{proposal}_{date_str}" path
    component as proposal -- an explicitly-passed one needs the same check."""
    with pytest.raises(ValueError):
        state_path("Acme Corp", "Fleet Loan", date_str="../../escape", base_dir=str(tmp_path))


def test_write_state_rejects_unsafe_explicit_date_str(tmp_path):
    base = str(tmp_path)
    with pytest.raises(ValueError):
        write_state("Acme Corp", "Fleet Loan", date_str="../../escape", base_dir=base)
    assert not os.path.exists(os.path.join(base, "deals"))


# ---------------------------------------------------------------------------
# Corrupted state.json: read_state() must raise a clear, actionable error --
# never crash with a raw JSONDecodeError, and never silently treat corrupted
# data as "no state yet" (which write_state() would then happily overwrite).
# ---------------------------------------------------------------------------

def test_read_state_raises_a_clear_error_on_corrupted_json(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, deal_type="asset_finance")
    path = state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)

    with open(path, "w", encoding="utf-8") as f:
        f.write('{"company": "Acme Corp", "proposal": ')  # truncated, as if interrupted mid-write

    with pytest.raises(ValueError, match="corrupted"):
        read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)


def test_read_state_returns_none_when_no_dated_folder_exists_at_all(tmp_path):
    """No date_str given and nothing on disk -- must not fall back to creating anything."""
    base = str(tmp_path)
    assert read_state("Acme Corp", "Fleet Loan", base_dir=base) is None
    assert not os.path.exists(os.path.join(base, "deals"))


def test_write_state_creates_file_and_directory(tmp_path):
    base = str(tmp_path)

    write_state(
        "Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base,
        deal_type="asset_finance",
    )

    expected_path = os.path.join(base, "deals", "Acme Corp", "Fleet Loan_2026-01-15", "state.json")
    assert os.path.isfile(expected_path)

    with open(expected_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["company"] == "Acme Corp"
    assert data["proposal"] == "Fleet Loan"
    assert data["date"] == "2026-01-15"
    assert data["deal_type"] == "asset_finance"


def test_second_write_state_call_merges_rather_than_overwrites(tmp_path):
    base = str(tmp_path)

    write_state(
        "Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base,
        deal_type="asset_finance", inputs={"pd": "0.20%", "lgd": "LGD 3 (15%)"},
    )
    write_state(
        "Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base,
        steps_completed=["triage"],
    )

    state = read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert state["deal_type"] == "asset_finance"
    assert state["inputs"] == {"pd": "0.20%", "lgd": "LGD 3 (15%)"}
    assert state["steps_completed"] == ["triage"]


def test_write_state_returns_the_full_merged_state(tmp_path):
    base = str(tmp_path)

    first = write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, deal_type="asset_finance")
    second = write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, steps_completed=["triage"])

    assert first["deal_type"] == "asset_finance"
    assert second["deal_type"] == "asset_finance"  # preserved from the first write
    assert second["steps_completed"] == ["triage"]


def test_resolved_path_matches_the_deals_company_proposal_date_convention(tmp_path):
    base = str(tmp_path)
    path = state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert path == os.path.join(base, "deals", "Acme Corp", "Fleet Loan_2026-01-15", "state.json")


def test_resolved_directory_matches_deal_exports_output_directory(tmp_path):
    """state.json and the final .docx/.xlsx must land in the same folder."""
    base = str(tmp_path)
    os.makedirs(os.path.join(base, "templates", "cam"), exist_ok=True)
    with open(os.path.join(base, "templates", "cam", "asset_finance_cam.md"), "w", encoding="utf-8") as f:
        f.write("template")

    output_dir = export_deal(
        "Acme Corp", "Fleet Loan", "asset_finance", "# Draft",
        date_str="2026-01-15", base_dir=base,
    )
    path = state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)

    assert os.path.dirname(path) == output_dir


# ---------------------------------------------------------------------------
# Date auto-discovery: a deal resumed on a later calendar day must still
# find its original state.json without the caller having to remember or
# pass the date it was first created on.
# ---------------------------------------------------------------------------

def test_write_state_without_date_str_reuses_an_existing_dated_folder(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-10", base_dir=base, deal_type="asset_finance")

    # No date_str at all this time -- must find and update 2026-01-10's
    # folder, not create a new one dated "today".
    write_state("Acme Corp", "Fleet Loan", base_dir=base, steps_completed=["triage"])

    original_path = os.path.join(base, "deals", "Acme Corp", "Fleet Loan_2026-01-10", "state.json")
    with open(original_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["deal_type"] == "asset_finance"
    assert data["steps_completed"] == ["triage"]

    today = datetime.now().strftime("%Y-%m-%d")
    if today != "2026-01-10":
        assert not os.path.exists(os.path.join(base, "deals", "Acme Corp", f"Fleet Loan_{today}"))


def test_read_state_without_date_str_finds_an_existing_dated_folder(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-10", base_dir=base, deal_type="asset_finance")

    state = read_state("Acme Corp", "Fleet Loan", base_dir=base)
    assert state is not None
    assert state["deal_type"] == "asset_finance"


def test_prefers_the_most_recent_dated_folder_when_more_than_one_exists(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-05", base_dir=base, note="older")
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-20", base_dir=base, note="newer")

    state = read_state("Acme Corp", "Fleet Loan", base_dir=base)
    assert state["note"] == "newer"
    assert state["date"] == "2026-01-20"


# ---------------------------------------------------------------------------
# new_review: a genuinely new annual review under the same company/proposal
# must get its own fresh dated folder, never silently merge into whatever
# dated folder already exists (stale PD/LGD, financials, policy_state).
# ---------------------------------------------------------------------------

def test_read_state_with_new_review_ignores_an_existing_dated_folder(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2025-01-10", base_dir=base,
                deal_type="asset_finance", inputs={"pd": "0.10%"})

    # Without new_review, this would resume 2025-01-10's stale state.
    state = read_state("Acme Corp", "Fleet Loan", base_dir=base, new_review=True)
    assert state is None


def test_write_state_with_new_review_creates_a_fresh_dated_folder(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2025-01-10", base_dir=base,
                deal_type="asset_finance", inputs={"pd": "0.10%"})

    new_state = write_state("Acme Corp", "Fleet Loan", base_dir=base, new_review=True,
                             inputs={"pd": "0.20%"})

    today = datetime.now().strftime("%Y-%m-%d")
    assert new_state["date"] == today
    assert new_state["inputs"] == {"pd": "0.20%"}  # not merged with 2025-01-10's stale inputs

    # The old folder is untouched, not overwritten.
    old_state = read_state("Acme Corp", "Fleet Loan", date_str="2025-01-10", base_dir=base)
    assert old_state["inputs"] == {"pd": "0.10%"}


# ---------------------------------------------------------------------------
# File locking: a concurrent write_state()/append_review_trail() call for
# the *same* deal must not silently clobber the other's update -- see
# _FileLock's docstring.
# ---------------------------------------------------------------------------

def test_file_lock_serializes_concurrent_acquisition(tmp_path):
    """Direct proof of the mutual-exclusion property _FileLock provides:
    two threads racing for the same lock must never both hold it at once."""
    lock_target = str(tmp_path / "state.json")
    holders = []
    overlap_detected = []

    def worker():
        with _FileLock(lock_target):
            holders.append(1)
            if len(holders) > 1:
                overlap_detected.append(True)
            time.sleep(0.05)
            holders.pop()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert overlap_detected == []
    # The lock file itself is cleaned up after every acquisition completes.
    assert not os.path.exists(lock_target + ".lock")


def test_file_lock_raises_timeout_error_when_already_held(tmp_path):
    lock_target = str(tmp_path / "state.json")
    with _FileLock(lock_target):
        with pytest.raises(TimeoutError):
            _FileLock(lock_target, timeout=0.2).__enter__()


# ---------------------------------------------------------------------------
# _FileLock's retry path, driven deterministically (issue #104). On Windows a genuine O_EXCL collision can surface
# as PermissionError instead of FileExistsError, and `except (FileExistsError, PermissionError)` is what makes the
# lock retry rather than crash. A real race cannot be forced reliably (and never on Linux CI), so os.open, the clock
# and the sleep are replaced by fakes: no filesystem race, no real waiting.
# ---------------------------------------------------------------------------

class _FakeClock:
    """Stands in for the `time` module inside state_manager: a monotonic clock that only sleep() advances."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class _FlakyOs:
    """Stands in for `os` inside state_manager: os.open raises the queued errors first (forever if `forever`),
    then really creates the file; everything else is the real os."""

    def __init__(self, errors, forever=False):
        self.errors, self.forever, self.open_calls = list(errors), forever, 0

    MAX_OPEN_CALLS = 200     # a correct lock needs about a dozen; more means the retry loop no longer terminates

    def open(self, *args, **kwargs):
        self.open_calls += 1
        if self.open_calls > self.MAX_OPEN_CALLS:
            # Without this a regression that removes the sleep (so the fake clock never advances) would spin until
            # pytest-timeout, re-raising one exception whose traceback grows without bound (about 1 GB in 8 s).
            raise AssertionError(f"_FileLock retried {self.open_calls} times without timing out: runaway loop")
        if self.errors:
            error = self.errors[0] if self.forever else self.errors.pop(0)
            raise error
        return os.open(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(os, name)


@pytest.fixture
def flaky_lock(monkeypatch):
    """flaky_lock(errors, forever=False) -> (fake_os, fake_clock), installed into state_manager."""
    def install(errors, forever=False):
        fake_os, clock = _FlakyOs(errors, forever), _FakeClock()
        monkeypatch.setattr(state_manager, "os", fake_os)
        monkeypatch.setattr(state_manager, "time", clock)
        return fake_os, clock
    return install


@pytest.mark.parametrize("error", [PermissionError(13, "Access is denied"), FileExistsError(17, "File exists")],
                         ids=["PermissionError", "FileExistsError"])
def test_a_lock_collision_is_retried_with_the_normal_backoff_until_the_lock_is_free(tmp_path, flaky_lock, error):
    fake_os, clock = flaky_lock([error, error, error])
    target = str(tmp_path / "state.json")

    with _FileLock(target) as lock:
        assert lock._fd is not None and os.path.exists(target + ".lock")    # acquired on the 4th attempt

    assert fake_os.open_calls == 4
    assert clock.sleeps == [state_manager._LOCK_POLL_INTERVAL_SECONDS] * 3   # the same fixed poll between attempts
    assert not os.path.exists(target + ".lock")                              # released and removed on exit


def test_permission_error_and_file_exists_error_are_treated_identically(tmp_path, flaky_lock):
    """The point of catching PermissionError: it must behave exactly like the ordinary collision."""
    results = {}
    for name, error in (("permission", PermissionError(13, "denied")), ("exists", FileExistsError(17, "exists"))):
        fake_os, clock = flaky_lock([error] * 2)
        with _FileLock(str(tmp_path / f"{name}.json")):
            pass
        results[name] = (fake_os.open_calls, clock.sleeps)
    assert results["permission"] == results["exists"] == (3, [state_manager._LOCK_POLL_INTERVAL_SECONDS] * 2)


@pytest.mark.parametrize("error", [PermissionError(13, "Access is denied"), FileExistsError(17, "File exists")],
                         ids=["PermissionError", "FileExistsError"])
def test_a_lock_that_never_frees_times_out_after_the_configured_wait(tmp_path, flaky_lock, error):
    fake_os, clock = flaky_lock([error], forever=True)
    target = str(tmp_path / "state.json")
    poll = state_manager._LOCK_POLL_INTERVAL_SECONDS

    with pytest.raises(TimeoutError, match="Could not acquire lock") as caught:
        _FileLock(target, timeout=0.5).__enter__()

    message = str(caught.value)
    assert repr(target + ".lock") in message and "0.5s" in message and "stale lock" in message
    assert caught.value.__cause__ is error                     # the collision that was being retried is chained
    waited = sum(clock.sleeps)
    assert 0.5 - poll <= waited <= 0.5 + poll                  # waits the configured time, not one poll, not forever
    assert set(clock.sleeps) == {poll} and fake_os.open_calls == len(clock.sleeps) + 1
    assert not os.path.exists(target + ".lock")                # a failed acquisition leaves nothing behind


def test_a_zero_timeout_gives_up_at_the_first_collision_without_sleeping(tmp_path, flaky_lock):
    fake_os, clock = flaky_lock([PermissionError(13, "denied")], forever=True)
    with pytest.raises(TimeoutError):
        _FileLock(str(tmp_path / "state.json"), timeout=0).__enter__()
    assert fake_os.open_calls == 1 and clock.sleeps == []


def test_the_default_timeout_and_poll_interval_are_unchanged():
    assert state_manager.LOCK_TIMEOUT_SECONDS == 10 and state_manager._LOCK_POLL_INTERVAL_SECONDS == 0.05
    assert _FileLock("x")._timeout == state_manager.LOCK_TIMEOUT_SECONDS


@pytest.mark.parametrize("error", [OSError(28, "No space left on device"), FileNotFoundError(2, "no such directory"),
                                   IsADirectoryError(21, "is a directory")],
                         ids=["ENOSPC", "FileNotFoundError", "IsADirectoryError"])
def test_other_os_errors_are_not_retried(tmp_path, flaky_lock, error):
    """Only a collision is worth waiting for. Broadening the except clause to every OSError would turn a real fault
    (a full disk, a missing directory) into a ten-second hang followed by a misleading 'stale lock' message."""
    fake_os, clock = flaky_lock([error])
    with pytest.raises(OSError) as caught:
        _FileLock(str(tmp_path / "state.json")).__enter__()
    assert caught.value is error and fake_os.open_calls == 1 and clock.sleeps == []


def test_concurrent_write_state_calls_do_not_clobber_each_others_update(tmp_path):
    """Without the lock, two concurrent write_state() calls each read the
    same starting state, compute an update from it, and the second write
    silently drops whatever the first one added. With it, both updates
    survive regardless of interleaving."""
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, deal_type="asset_finance")

    def writer(field_name, value):
        write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, **{field_name: value})

    t1 = threading.Thread(target=writer, args=("steps_completed", ["triage"]))
    t2 = threading.Thread(target=writer, args=("inputs", {"pd": "0.20%"}))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    state = read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert state["steps_completed"] == ["triage"]
    assert state["inputs"] == {"pd": "0.20%"}


def test_concurrent_append_review_trail_calls_do_not_lose_an_entry(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, deal_type="asset_finance")

    def appender(verdict):
        append_review_trail("Acme Corp", "Fleet Loan", verdict, date_str="2026-01-15", base_dir=base)

    t1 = threading.Thread(target=appender, args=("REJECTED",))
    t2 = threading.Thread(target=appender, args=("APPROVED",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    state = read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert len(state["review_trail"]) == 2  # neither call's entry was lost
    assert {"REJECTED", "APPROVED"} == {e["verdict"] for e in state["review_trail"]}
    assert {1, 2} == {e["iteration"] for e in state["review_trail"]}  # no duplicate iteration numbers


def test_new_review_still_resumes_within_the_same_day(tmp_path):
    """new_review=True is a one-time "start clean" trigger, not a
    permanent per-call override -- a second write the same day (with or
    without new_review) must land in the folder the first call just
    created, not fork yet another new one."""
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2025-01-10", base_dir=base, note="old year")
    first = write_state("Acme Corp", "Fleet Loan", base_dir=base, new_review=True, note="new year")

    second = write_state("Acme Corp", "Fleet Loan", base_dir=base, steps_completed=["triage"])

    assert second["date"] == first["date"]
    assert second["note"] == "new year"  # merged with the fresh folder, not the old one
    assert second["steps_completed"] == ["triage"]


def test_auto_discovery_is_scoped_to_the_matching_company_and_proposal(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-10", base_dir=base, note="acme's deal")
    write_state("Other Corp", "Fleet Loan", date_str="2026-06-01", base_dir=base, note="unrelated, later date")

    state = read_state("Acme Corp", "Fleet Loan", base_dir=base)
    assert state["note"] == "acme's deal"


# ---------------------------------------------------------------------------
# review_trail: append-only audit history.
#
# write_state()'s shallow merge would silently replace the whole list if a
# caller passed a fresh review_trail=[...] -- these confirm
# append_review_trail() reads the existing trail first and only ever adds
# to it, since /assemble loops /review until APPROVED and every
# intermediate REJECTED verdict must survive that loop.
# ---------------------------------------------------------------------------

def test_append_review_trail_initializes_it_when_missing(tmp_path):
    base = str(tmp_path)

    state = append_review_trail(
        "Acme Corp", "Fleet Loan", verdict="REJECTED", notes="fix the ratios",
        timestamp="2026-01-15T10:00:00", date_str="2026-01-15", base_dir=base,
    )

    assert state["review_trail"] == [
        {"iteration": 1, "verdict": "REJECTED", "notes": "fix the ratios", "timestamp": "2026-01-15T10:00:00"},
    ]
    assert state["review_verdict"] == "REJECTED"


def test_append_review_trail_appends_rather_than_replaces(tmp_path):
    base = str(tmp_path)

    append_review_trail(
        "Acme Corp", "Fleet Loan", verdict="REJECTED", notes="fix the ratios",
        timestamp="2026-01-15T10:00:00", date_str="2026-01-15", base_dir=base,
    )
    state = append_review_trail(
        "Acme Corp", "Fleet Loan", verdict="APPROVED", notes=None,
        timestamp="2026-01-15T11:00:00", date_str="2026-01-15", base_dir=base,
    )

    assert state["review_trail"] == [
        {"iteration": 1, "verdict": "REJECTED", "notes": "fix the ratios", "timestamp": "2026-01-15T10:00:00"},
        {"iteration": 2, "verdict": "APPROVED", "notes": None, "timestamp": "2026-01-15T11:00:00"},
    ]
    # review_verdict reflects only the latest iteration, not the history.
    assert state["review_verdict"] == "APPROVED"


def test_append_review_trail_persists_across_separate_read_state_calls(tmp_path):
    """Not just the returned dict -- the file on disk must have both entries."""
    base = str(tmp_path)

    append_review_trail("Acme Corp", "Fleet Loan", verdict="REJECTED",
                         date_str="2026-01-15", base_dir=base)
    append_review_trail("Acme Corp", "Fleet Loan", verdict="APPROVED",
                         date_str="2026-01-15", base_dir=base)

    state = read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert [entry["verdict"] for entry in state["review_trail"]] == ["REJECTED", "APPROVED"]
    assert [entry["iteration"] for entry in state["review_trail"]] == [1, 2]


def test_append_review_trail_merges_extra_fields_like_write_state(tmp_path):
    base = str(tmp_path)

    state = append_review_trail(
        "Acme Corp", "Fleet Loan", verdict="APPROVED",
        date_str="2026-01-15", base_dir=base,
        deal_type="asset_finance", steps_completed=["draft", "audit"],
    )

    assert state["deal_type"] == "asset_finance"
    assert state["steps_completed"] == ["draft", "audit"]


def test_append_review_trail_does_not_drop_fields_from_other_steps(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base,
                inputs={"pd": "0.20%"})

    state = append_review_trail("Acme Corp", "Fleet Loan", verdict="APPROVED",
                                 date_str="2026-01-15", base_dir=base)

    assert state["inputs"] == {"pd": "0.20%"}


def test_append_review_trail_with_explicit_date_str_ignores_a_newer_auto_discovered_folder(tmp_path):
    """Regression test for a real bug found in code review: orchestrator.py
    used to call append_review_trail() with no date_str/new_review at all,
    silently relying on auto-discovery -- correct only by the coincidence
    that a fresh folder was always created immediately beforehand in the
    same run. This proves the actual mechanism the fix now depends on:
    an explicit date_str must be honored over whatever auto-discovery
    (unqualified, no date_str) would otherwise resolve to -- an older
    folder stays targeted even once a newer one exists."""
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2025-01-10", base_dir=base, note="old year")
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, note="new year")

    # Without date_str, auto-discovery would resolve to 2026-01-15 (most
    # recent) -- explicitly targeting the older folder must override that.
    append_review_trail("Acme Corp", "Fleet Loan", verdict="APPROVED",
                         date_str="2025-01-10", base_dir=base)

    old_state = read_state("Acme Corp", "Fleet Loan", date_str="2025-01-10", base_dir=base)
    new_state = read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert len(old_state["review_trail"]) == 1
    assert "review_trail" not in new_state


# ---------------------------------------------------------------------------
# resolve_date_str(): the public wrapper orchestrator.py's run_pipeline()
# uses to resolve a deal's dated folder exactly once, then threads that
# concrete value through every subsequent call via date_str -- rather than
# re-deriving it per call site via new_review, which is fragile (a call
# site that forgets to pass new_review=new_review, or has no such
# parameter at all, silently falls back to auto-discovery).
# ---------------------------------------------------------------------------

def test_resolve_date_str_returns_the_given_date_str_unchanged(tmp_path):
    assert resolve_date_str("Acme Corp", "Fleet Loan", date_str="2026-01-15",
                             base_dir=str(tmp_path)) == "2026-01-15"


@pytest.mark.parametrize("operation", ["state_path", "read_state", "write_state", "append_review_trail"])
@pytest.mark.parametrize("date_str", ["foo", "2026-1-5", "", "2026-02-30"])
def test_state_entry_points_reject_non_iso_explicit_dates_without_creating_folders(tmp_path, operation, date_str):
    base = tmp_path / operation

    with pytest.raises(state_manager.StateError):
        if operation == "state_path":
            state_manager.state_path("Synthetic Company", "Test Proposal",
                                     date_str=date_str, base_dir=str(base))
        elif operation == "read_state":
            state_manager.read_state("Synthetic Company", "Test Proposal",
                                     date_str=date_str, base_dir=str(base))
        elif operation == "write_state":
            state_manager.write_state("Synthetic Company", "Test Proposal",
                                      date_str=date_str, base_dir=str(base), note="must not write")
        else:
            state_manager.append_review_trail("Synthetic Company", "Test Proposal", "REJECTED",
                                              date_str=date_str, base_dir=str(base))

    assert not base.exists(), f"{operation} created a directory for invalid date_str={date_str!r}"


@pytest.mark.parametrize("date_str", ["2026-02-31", "2026-13-01", "2026-00-01"])
def test_state_path_and_write_state_reject_impossible_explicit_calendar_dates(tmp_path, date_str):
    base = str(tmp_path)
    with pytest.raises(ValueError, match="calendar date"):
        state_path("Acme Corp", "Fleet Loan", date_str=date_str, base_dir=base)
    with pytest.raises(ValueError, match="calendar date"):
        write_state("Acme Corp", "Fleet Loan", date_str=date_str, base_dir=base, note="must not write")
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("date_str", ["2024-02-29", "2026-01-31"])
def test_valid_leap_day_and_month_end_dates_are_accepted(tmp_path, date_str):
    base = str(tmp_path)
    path = state_path("Acme Corp", "Fleet Loan", date_str=date_str, base_dir=base)
    assert path == os.path.join(base, "deals", "Acme Corp", f"Fleet Loan_{date_str}", "state.json")
    written = write_state("Acme Corp", "Fleet Loan", date_str=date_str, base_dir=base, note=date_str)
    assert written["date"] == date_str
    assert read_state("Acme Corp", "Fleet Loan", date_str=date_str, base_dir=base)["note"] == date_str


def test_auto_discovery_ignores_impossible_date_shaped_folder_names(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-31", base_dir=base, note="valid state")
    impossible = tmp_path / "deals" / "Acme Corp" / "Fleet Loan_2026-02-31"
    impossible.mkdir(parents=True)
    (impossible / "state.json").write_text(
        json.dumps({"company": "Acme Corp", "proposal": "Fleet Loan", "date": "2026-02-31",
                    "note": "must be ignored"}),
        encoding="utf-8",
    )
    assert resolve_date_str("Acme Corp", "Fleet Loan", base_dir=base) == "2026-01-31"
    assert read_state("Acme Corp", "Fleet Loan", base_dir=base)["note"] == "valid state"


def test_resolve_date_str_with_new_review_ignores_an_existing_dated_folder(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2025-01-10", base_dir=base)

    resolved = resolve_date_str("Acme Corp", "Fleet Loan", base_dir=base, new_review=True)

    assert resolved != "2025-01-10"
    assert resolved == datetime.now().strftime("%Y-%m-%d")


def test_resolve_date_str_without_new_review_auto_discovers_the_most_recent_folder(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2025-01-10", base_dir=base)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)

    assert resolve_date_str("Acme Corp", "Fleet Loan", base_dir=base) == "2026-01-15"


# ---------------------------------------------------------------------------
# required_steps_completed(): pure comparison used to hard-gate /assemble
# (and any other step) against steps_completed recorded in state.json,
# rather than relying only on prose instructions -- see issue #28.
# ---------------------------------------------------------------------------

def test_required_steps_completed_returns_empty_when_nothing_missing():
    assert required_steps_completed(["triage", "spread"], ["spread"]) == []


def test_required_steps_completed_returns_empty_when_all_required_present():
    assert required_steps_completed(["triage", "spread", "collateral"], ["spread", "triage"]) == []


def test_required_steps_completed_returns_the_missing_step():
    assert required_steps_completed(["triage"], ["spread"]) == ["spread"]


def test_required_steps_completed_returns_multiple_missing_steps_in_required_order():
    assert required_steps_completed([], ["spread", "collateral"]) == ["spread", "collateral"]
    assert required_steps_completed(["collateral"], ["spread", "collateral"]) == ["spread"]


def test_required_steps_completed_with_empty_required_list_is_always_empty():
    assert required_steps_completed(["spread"], []) == []
    assert required_steps_completed([], []) == []


def test_required_steps_completed_with_empty_steps_completed_returns_all_required():
    assert required_steps_completed([], ["spread"]) == ["spread"]


def test_required_steps_completed_tolerates_none_steps_completed():
    """steps_completed may be absent from a legacy state.json entirely --
    read_state() would then hand back None/missing rather than []."""
    assert required_steps_completed(None, ["spread"]) == ["spread"]


def test_required_steps_completed_does_not_duplicate_a_repeated_required_entry():
    assert required_steps_completed([], ["spread", "spread"]) == ["spread"]


def test_write_state_leaves_no_stray_temp_file_behind(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, deal_type="asset_finance")

    deal_dir = os.path.dirname(state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base))
    entries = os.listdir(deal_dir)
    assert entries == ["state.json"]


# ---------------------------------------------------------------------------
# Date discovery must only count real <proposal>_<YYYY-MM-DD> folders (found
# in the independent review of the PR for issue #97, once exports started
# relying on it).
# ---------------------------------------------------------------------------

def test_date_discovery_ignores_a_prefix_sharing_proposals_folder(tmp_path):
    base = str(tmp_path)
    write_state("Acme", "Fleet", date_str="2026-01-10", base_dir=base, note="fleet")
    write_state("Acme", "Fleet_Q2", date_str="2026-05-01", base_dir=base, note="quarterly")

    assert resolve_date_str("Acme", "Fleet", base_dir=base) == "2026-01-10"
    assert resolve_date_str("Acme", "Fleet_Q2", base_dir=base) == "2026-05-01"


def test_date_discovery_skips_newer_export_only_folder_and_surfaces_malformed_state(tmp_path):
    base = str(tmp_path)
    write_state("Acme", "Fleet", date_str="2026-01-10", base_dir=base, note="source state")
    export_only = os.path.join(base, "deals", "Acme", "Fleet_2026-03-01")
    os.makedirs(export_only)
    with open(os.path.join(export_only, "Acme_Fleet_CAM.docx"), "w", encoding="utf-8") as f:
        f.write("synthetic exported document")

    assert resolve_date_str("Acme", "Fleet", base_dir=base) == "2026-01-10"
    assert read_state("Acme", "Fleet", base_dir=base)["note"] == "source state"

    # A present but malformed state must not be skipped as though it were export-only.
    malformed = os.path.join(base, "deals", "Acme", "Fleet_2026-04-01")
    os.makedirs(malformed)
    with open(os.path.join(malformed, "state.json"), "w", encoding="utf-8") as f:
        f.write("{")
    assert resolve_date_str("Acme", "Fleet", base_dir=base) == "2026-04-01"
    with pytest.raises(ValueError, match="corrupted"):
        read_state("Acme", "Fleet", base_dir=base)


def test_date_discovery_ignores_non_date_suffixes_and_files(tmp_path):
    base = str(tmp_path)
    os.makedirs(os.path.join(base, "deals", "Acme", "Fleet_foo"))
    with open(os.path.join(base, "deals", "Acme", "Fleet_2099-01-01"), "w", encoding="utf-8") as f:
        f.write("a file, not a deal folder")

    today = datetime.now().strftime("%Y-%m-%d")
    assert resolve_date_str("Acme", "Fleet", base_dir=base) == today


def test_date_discovery_matches_bracketed_names_literally(tmp_path):
    base = str(tmp_path)
    write_state("Acme", "Fleet", date_str="2026-01-10", base_dir=base, note="acme")

    today = datetime.now().strftime("%Y-%m-%d")
    # "[A]cme" is a glob character class for "Acme" -- it must not match it.
    assert resolve_date_str("[A]cme", "Fleet", base_dir=base) == today
    write_state("[A]cme", "Fleet", date_str="2026-02-02", base_dir=base, note="bracketed")
    assert resolve_date_str("[A]cme", "Fleet", base_dir=base) == "2026-02-02"


# ---------------------------------------------------------------------------
# Non-finite numeric values must never enter or survive persisted deal state
# (issue #244). Invalid updates fail before replacing an existing file.
# ---------------------------------------------------------------------------

def test_merge_and_write_wraps_non_finite_validator_error_before_writing(tmp_path, monkeypatch):
    base = str(tmp_path)
    path = state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)

    def reject_state(_state, path=None):
        raise state_manager.NonFiniteNumberError(f"non-finite numeric value at {path}")

    monkeypatch.setattr(state_manager, "validate_finite_state_numbers", reject_state)
    with pytest.raises(state_manager.StateShapeError, match="Refusing to write state.json: non-finite"):
        state_manager._merge_and_write(
            path, "Acme Corp", "Fleet Loan", "2026-01-15", base, {"note": "test"},
        )
    assert not os.path.exists(os.path.join(base, "deals"))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "positive-infinity", "negative-infinity"])
def test_write_state_rejects_non_finite_new_values_before_creating_a_deal(tmp_path, value):
    with pytest.raises(ValueError, match="non-finite numeric value"):
        write_state("Acme Corp", "Fleet Loan", base_dir=str(tmp_path),
                    financials={"FY-Current": {"revenue": value}})
    assert not os.path.exists(os.path.join(str(tmp_path), "deals"))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "positive-infinity", "negative-infinity"])
def test_write_state_rejects_non_finite_update_without_changing_existing_state(tmp_path, value):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", base_dir=base, note="keep")
    path = state_path("Acme Corp", "Fleet Loan", base_dir=base)
    with open(path, "rb") as f:
        before = f.read()

    with pytest.raises(ValueError, match="non-finite numeric value"):
        write_state("Acme Corp", "Fleet Loan", base_dir=base,
                    financials={"FY-Current": {"revenue": value}})

    with open(path, "rb") as f:
        assert f.read() == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "positive-infinity", "negative-infinity"])
def test_read_state_refuses_existing_non_finite_json_without_modifying_it(tmp_path, value):
    base = str(tmp_path)
    path = state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    invalid = {
        "company": "Acme Corp",
        "proposal": "Fleet Loan",
        "date": "2026-01-15",
        "financials": {"FY-Current": {"revenue": value}},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(invalid, f, allow_nan=True)
    with open(path, "rb") as f:
        before = f.read()

    with pytest.raises(ValueError, match="non-finite numeric value"):
        read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)

    with open(path, "rb") as f:
        assert f.read() == before


# Reviewer view: derived copies for the interactive first pass, never a new record.
# ---------------------------------------------------------------------------

def test_export_reviewer_view_separates_history_without_changing_state_json(tmp_path):
    base = str(tmp_path)
    write_state("Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base,
                inputs={"pd": "0.20%"}, review_verdict="REJECTED",
                review_trail=[{"iteration": 1, "verdict": "REJECTED", "notes": "synthetic finding",
                               "timestamp": "2026-01-15T10:00:00"}])
    source_path = state_path("Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base)
    with open(source_path, "rb") as f:
        source_before = f.read()

    paths = export_reviewer_view("Synthetic Co", "Synthetic Loan", base_dir=base)

    with open(paths["state_for_review"], encoding="utf-8") as f:
        record = json.load(f)
    with open(paths["review_trail"], encoding="utf-8") as f:
        trail = json.load(f)
    assert record["inputs"] == {"pd": "0.20%"}
    assert "review_trail" not in record and "review_verdict" not in record
    assert trail == [{"iteration": 1, "verdict": "REJECTED", "notes": "synthetic finding",
                      "timestamp": "2026-01-15T10:00:00"}]
    with open(source_path, "rb") as f:
        assert f.read() == source_before


def test_export_reviewer_view_writes_an_empty_list_when_no_trail_exists(tmp_path):
    base = str(tmp_path)
    write_state("Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base,
                deal_type="asset_finance")
    paths = export_reviewer_view("Synthetic Co", "Synthetic Loan", base_dir=base)
    with open(paths["state_for_review"], encoding="utf-8") as f:
        record = json.load(f)
    with open(paths["review_trail"], encoding="utf-8") as f:
        trail = json.load(f)
    assert record["deal_type"] == "asset_finance"
    assert "review_trail" not in record and "review_verdict" not in record
    assert trail == []


def test_export_reviewer_view_overwrites_both_derived_files_on_each_run(tmp_path):
    base = str(tmp_path)
    write_state("Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base,
                review_verdict="REJECTED",
                review_trail=[{"iteration": 1, "verdict": "REJECTED", "notes": "first",
                               "timestamp": "2026-01-15T10:00:00"}])
    first_paths = export_reviewer_view("Synthetic Co", "Synthetic Loan", base_dir=base)
    write_state("Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base,
                review_verdict="APPROVED",
                review_trail=[{"iteration": 1, "verdict": "APPROVED", "notes": None,
                               "timestamp": "2026-01-15T11:00:00"}], note="updated")
    second_paths = export_reviewer_view("Synthetic Co", "Synthetic Loan", base_dir=base)
    assert second_paths == first_paths
    with open(second_paths["state_for_review"], encoding="utf-8") as f:
        record = json.load(f)
    with open(second_paths["review_trail"], encoding="utf-8") as f:
        trail = json.load(f)
    assert record["note"] == "updated"
    assert "review_verdict" not in record and "review_trail" not in record
    assert trail[0]["verdict"] == "APPROVED"


def test_export_reviewer_view_rejects_invalid_state_without_writing(tmp_path):
    base = str(tmp_path)
    path, before = _write_raw_state(base, SCHEMA_VERSION, review_trail="not-a-list")
    with pytest.raises(StateShapeError, match="review_trail"):
        export_reviewer_view("Acme Corp", "Fleet Loan", base_dir=base)
    with open(path, "rb") as f:
        assert f.read() == before
    assert not os.path.exists(os.path.join(os.path.dirname(path), "review_view"))


@pytest.mark.parametrize("version", ["9.9.9", "v1.1.0", None])
def test_export_reviewer_view_refuses_newer_or_malformed_schema_without_writing(tmp_path, version):
    base = str(tmp_path)
    path, before = _write_raw_state(base, version)
    with pytest.raises(SchemaVersionError):
        export_reviewer_view("Acme Corp", "Fleet Loan", base_dir=base)
    with open(path, "rb") as f:
        assert f.read() == before
    assert not os.path.exists(os.path.join(os.path.dirname(path), "review_view"))


def test_export_reviewer_view_rejects_missing_state_without_creating_deal_folder(tmp_path):
    base = str(tmp_path)
    with pytest.raises(StateShapeError, match="no state.json"):
        export_reviewer_view("Synthetic Co", "Synthetic Loan", base_dir=base)
    assert not os.path.exists(os.path.join(base, "deals"))


# Decision D4 (#244): collateral and security_package carry figures too, so non-finite values never reach state.json.
NON_FINITE_VALUES = [float("nan"), float("inf"), float("-inf"), json.loads("1e999")]
NON_FINITE_IDS = ["nan", "positive-infinity", "negative-infinity", "overflow-1e999"]
COLLATERAL_FIELDS = [
    ("collateral", [{"asset_class": "HGV", "exposure": "VALUE", "collateral_value": 80}]),
    ("security_package", [{"secures_asset_id": "A1", "security_type": "charge", "value": "VALUE"}]),
]


def _with_value(template, value):
    return [{key: (value if item == "VALUE" else item) for key, item in row.items()} for row in template]


@pytest.mark.parametrize("value", NON_FINITE_VALUES, ids=NON_FINITE_IDS)
@pytest.mark.parametrize(("key", "template"), COLLATERAL_FIELDS, ids=[name for name, _ in COLLATERAL_FIELDS])
def test_write_state_rejects_non_finite_collateral_and_security_without_changing_state(tmp_path, key, template, value):
    base = str(tmp_path)
    with pytest.raises(ValueError, match="non-finite numeric value"):
        write_state("Acme Corp", "Fleet Loan", base_dir=base, **{key: _with_value(template, value)})
    assert not os.path.exists(os.path.join(base, "deals"))

    write_state("Acme Corp", "Fleet Loan", base_dir=base, note="keep")
    path = state_path("Acme Corp", "Fleet Loan", base_dir=base)
    with open(path, "rb") as f:
        before = f.read()
    with pytest.raises(ValueError, match="non-finite numeric value"):
        write_state("Acme Corp", "Fleet Loan", base_dir=base, **{key: _with_value(template, value)})
    with open(path, "rb") as f:
        assert f.read() == before


@pytest.mark.parametrize(("key", "template"), COLLATERAL_FIELDS, ids=[name for name, _ in COLLATERAL_FIELDS])
def test_read_state_refuses_existing_non_finite_collateral_without_modifying_it(tmp_path, key, template):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", base_dir=base, note="keep")
    path = state_path("Acme Corp", "Fleet Loan", base_dir=base)
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state[key] = _with_value(template, float("nan"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    with open(path, "rb") as f:
        before = f.read()

    with pytest.raises(ValueError, match="non-finite numeric value"):
        read_state("Acme Corp", "Fleet Loan", base_dir=base)

    with open(path, "rb") as f:
        assert f.read() == before


# ---------------------------------------------------------------------------
# Read-only discovery mode: an advisory lookup must never create or select a
# near match. A present but corrupt state remains a record so read_state()
# continues to surface its established corruption error.
# ---------------------------------------------------------------------------

def test_find_deal_exact_match_returns_resolved_path_and_newest_date(tmp_path):
    base = str(tmp_path)
    write_state("Synthetic Co", "Credit Facility", date_str="2026-01-10", base_dir=base)
    write_state("Synthetic Co", "Credit Facility", date_str="2026-03-15", base_dir=base)

    result = state_manager.find_deal("Synthetic Co", "Credit Facility", base_dir=base)

    assert result == {
        "record_found": True,
        "state_path": state_path("Synthetic Co", "Credit Facility", date_str="2026-03-15", base_dir=base),
        "date": "2026-03-15",
    }


def test_find_deal_misspelling_is_not_fuzzy_matched_and_creates_nothing(tmp_path):
    base = str(tmp_path)
    write_state("Synthetic Co", "Oakhaven Capital", date_str="2026-01-10", base_dir=base)
    before = sorted(os.path.relpath(os.path.join(root, name), base)
                    for root, dirs, files in os.walk(base) for name in files)

    result = state_manager.find_deal("Synthetic Co", "Oakhaven Capitl", base_dir=base)

    assert result == {
        "record_found": False,
        "company_folder_exists": True,
        "near_misses": [],
    }
    after = sorted(os.path.relpath(os.path.join(root, name), base)
                   for root, dirs, files in os.walk(base) for name in files)
    assert after == before


@pytest.mark.parametrize("requested", ["oakhaven capital", "  OAKHAVEN   CAPITAL  ", "Oakhaven---Capital"])
def test_find_deal_normalizes_case_whitespace_and_hyphen_runs_only_for_advice(tmp_path, requested):
    base = str(tmp_path)
    write_state("Synthetic Co", "Oakhaven Capital", date_str="2026-01-10", base_dir=base)
    write_state("Synthetic Co", "Oakhaven Capital", date_str="2026-05-16", base_dir=base)

    result = state_manager.find_deal("Synthetic Co", requested, base_dir=base)

    assert result["record_found"] is False
    assert [item["proposal"] for item in result["near_misses"]] == ["Oakhaven Capital"]
    assert result["near_misses"][0]["date"] == "2026-05-16"


def test_find_deal_never_uses_a_prefix_match_to_select_or_suggest_a_proposal(tmp_path):
    base = str(tmp_path)
    write_state("Synthetic Co", "Alpha Revamp", date_str="2026-01-10", base_dir=base)
    write_state("Synthetic Co", "Alpha Revampment", date_str="2026-01-11", base_dir=base)

    exact = state_manager.find_deal("Synthetic Co", "Alpha Revamp", base_dir=base)
    miss = state_manager.find_deal("Synthetic Co", "Alpha Revam", base_dir=base)

    assert exact["record_found"] is True
    assert exact["date"] == "2026-01-10"
    assert miss["record_found"] is False
    assert miss["near_misses"] == []


def test_find_deal_distinguishes_missing_company_folder_without_creating_it(tmp_path):
    base = str(tmp_path)
    result = state_manager.find_deal("Missing Synthetic Co", "Unknown Loan", base_dir=base)
    assert result == {
        "record_found": False,
        "company_folder_exists": False,
        "near_misses": [],
    }
    assert not (tmp_path / "deals" / "Missing Synthetic Co").exists()


def test_find_deal_keeps_corrupt_but_present_state_as_a_found_record(tmp_path):
    base = str(tmp_path)
    path = state_path("Synthetic Co", "Corrupt Loan", date_str="2026-01-10", base_dir=base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("{corrupt json")

    result = state_manager.find_deal("Synthetic Co", "Corrupt Loan", base_dir=base)
    assert result["record_found"] is True
    assert result["date"] == "2026-01-10"
    assert result["state_path"] == path
    with pytest.raises(ValueError, match="state.json"):
        read_state("Synthetic Co", "Corrupt Loan", date_str="2026-01-10", base_dir=base)


def test_find_deal_excludes_export_only_and_malformed_date_folders_from_candidates(tmp_path):
    base = tmp_path
    company_dir = base / "deals" / "Synthetic Co"
    (company_dir / "Oakhaven-Capital_2026-01-10").mkdir(parents=True)
    # An exact proposal folder without state.json is export-only, not a record.
    (company_dir / "Oakhaven Capital_2026-01-10").mkdir()
    (company_dir / "not-a-dated-folder").mkdir()
    (company_dir / "standalone-file.txt").write_text("not a deal", encoding="utf-8")
    malformed = company_dir / "Oakhaven Capital_2026-02-30"
    malformed.mkdir(parents=True)
    (malformed / "state.json").write_text("{}", encoding="utf-8")

    result = state_manager.find_deal("Synthetic Co", "Oakhaven Capital", base_dir=str(base))
    assert result == {
        "record_found": False,
        "company_folder_exists": True,
        "near_misses": [],
    }


@pytest.mark.parametrize("error", [PermissionError("denied"), FileNotFoundError("vanished")])
def test_find_cli_reports_an_unlistable_company_folder_as_one_error_line(
        tmp_path, monkeypatch, capsys, error):
    import runpy
    import sys

    monkeypatch.chdir(tmp_path)
    (tmp_path / "deals" / "Synthetic Co").mkdir(parents=True)
    company_dir = os.path.join("deals", "Synthetic Co")
    real_scandir = os.scandir

    def failing_scandir(path="."):
        if os.fspath(path).endswith(company_dir):
            raise error
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", failing_scandir)
    script = os.path.join(os.path.dirname(state_manager.__file__), "state_manager.py")
    monkeypatch.setattr(sys, "argv", ["state_manager.py", "--find", "--company", "Synthetic Co",
                                      "--proposal", "Credit Facility"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(script, run_name="__main__")

    assert str(excinfo.value).startswith("error: ")
    assert "Traceback" not in str(excinfo.value)
    assert "\n" not in str(excinfo.value)
    assert capsys.readouterr().out == ""

# ---------------------------------------------------------------------------
# Additional reviewer-view / review-trail survivor cases.
# Prefixes are intentionally distinct from the find_deal job and each other.
# ---------------------------------------------------------------------------

class reviewer_view_fixed_datetime:
    @classmethod
    def now(cls):
        return datetime(2026, 1, 15, 12, 34, 56, 123456)


class review_trail_fixed_datetime:
    @classmethod
    def now(cls):
        return datetime(2026, 1, 15, 12, 34, 56, 123456)


def test_reviewer_view_exports_exact_paths_and_preserves_every_non_history_field(tmp_path):
    base = str(tmp_path)
    trail = [
        {
            "iteration": 1,
            "verdict": "REJECTED",
            "notes": "Synthetic finding one",
            "timestamp": "2026-01-15T10:00:00",
        },
        {
            "iteration": 2,
            "verdict": "APPROVED",
            "notes": None,
            "timestamp": "2026-01-15T11:00:00",
        },
    ]
    write_state(
        "Synthetic Co",
        "Synthetic Loan",
        date_str="2026-01-15",
        base_dir=base,
        deal_type="asset_finance",
        steps_completed=["triage", "spread", "review"],
        synthetic_extension={"nested": {"labels": ["alpha", 3], "enabled": True}},
        note="Preserve this note",
        review_verdict="APPROVED",
        review_trail=trail,
    )
    source_path = state_path(
        "Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base,
    )
    with open(source_path, "rb") as source_file:
        source_before = source_file.read()

    paths = export_reviewer_view("Synthetic Co", "Synthetic Loan", base_dir=base)

    expected_review_dir = os.path.join(
        base, "deals", "Synthetic Co", "Synthetic Loan_2026-01-15", "review_view",
    )
    assert paths == {
        "state_for_review": os.path.join(expected_review_dir, "state_for_review.json"),
        "review_trail": os.path.join(expected_review_dir, "review_trail.json"),
    }
    with open(paths["state_for_review"], encoding="utf-8") as review_file:
        record = json.load(review_file)
    with open(paths["review_trail"], encoding="utf-8") as trail_file:
        exported_trail = json.load(trail_file)

    assert record == {
        "company": "Synthetic Co",
        "proposal": "Synthetic Loan",
        "date": "2026-01-15",
        "schema_version": SCHEMA_VERSION,
        "deal_type": "asset_finance",
        "steps_completed": ["triage", "spread", "review"],
        "synthetic_extension": {"nested": {"labels": ["alpha", 3], "enabled": True}},
        "note": "Preserve this note",
    }
    assert exported_trail == trail
    with open(source_path, "rb") as source_file:
        assert source_file.read() == source_before


def test_reviewer_view_missing_state_pins_exact_error_and_does_not_create_a_deal_folder(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(state_manager, "datetime", reviewer_view_fixed_datetime)
    base = str(tmp_path)
    expected_source_path = os.path.join(
        base, "deals", "Synthetic Co", "Synthetic Loan_2026-01-15", "state.json",
    )

    with pytest.raises(StateShapeError) as excinfo:
        export_reviewer_view("Synthetic Co", "Synthetic Loan", base_dir=base)

    assert str(excinfo.value) == (
        f"Cannot export the Reviewer view: no state.json exists at {expected_source_path}; "
        "nothing was written."
    )
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize(
    "use_empty_timestamp",
    [False, True],
    ids=["timestamp-omitted", "timestamp-empty-string"],
)
def test_review_trail_defaults_notes_to_none_and_uses_iso_now_for_a_falsey_timestamp(
    tmp_path, monkeypatch, use_empty_timestamp,
):
    monkeypatch.setattr(state_manager, "datetime", review_trail_fixed_datetime)
    kwargs = {"timestamp": ""} if use_empty_timestamp else {}

    state = append_review_trail(
        "Synthetic Co", "Synthetic Loan", verdict="REJECTED",
        date_str="2026-01-15", base_dir=str(tmp_path), **kwargs,
    )

    expected_timestamp = review_trail_fixed_datetime.now().isoformat()
    assert state["review_trail"] == [{
        "iteration": 1,
        "verdict": "REJECTED",
        "notes": None,
        "timestamp": expected_timestamp,
    }]
    assert state["review_verdict"] == "REJECTED"


def test_review_trail_reserved_extra_fields_cannot_replace_the_history_or_latest_verdict(tmp_path):
    base = str(tmp_path)
    first_entry = {
        "iteration": 1,
        "verdict": "REJECTED",
        "notes": "Synthetic first finding",
        "timestamp": "2026-01-15T10:00:00",
    }
    write_state(
        "Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base,
        review_verdict="REJECTED", review_trail=[first_entry], note="Keep this field",
    )

    state = append_review_trail(
        "Synthetic Co", "Synthetic Loan", verdict="APPROVED", notes=None,
        timestamp="2026-01-15T11:00:00", date_str="2026-01-15", base_dir=base,
        review_verdict="FORGED",
        review_trail=[{"iteration": 99, "verdict": "FORGED"}],
        synthetic_extension={"kept": True},
    )

    assert state["review_trail"] == [
        first_entry,
        {
            "iteration": 2,
            "verdict": "APPROVED",
            "notes": None,
            "timestamp": "2026-01-15T11:00:00",
        },
    ]
    assert state["review_verdict"] == "APPROVED"
    assert state["synthetic_extension"] == {"kept": True}
    assert state["note"] == "Keep this field"


def test_review_trail_rejects_a_malformed_existing_history_without_changing_any_file(tmp_path):
    base = str(tmp_path)
    path, before = _write_raw_state(
        base, SCHEMA_VERSION, review_trail={"iteration": 1, "verdict": "REJECTED"},
    )
    parent = os.path.dirname(path)
    before_names = sorted(os.listdir(parent))

    with pytest.raises(StateShapeError, match="review_trail"):
        append_review_trail(
            "Acme Corp", "Fleet Loan", verdict="APPROVED",
            date_str="2026-01-15", base_dir=base,
        )

    with open(path, "rb") as state_file:
        assert state_file.read() == before
    assert sorted(os.listdir(parent)) == before_names


@pytest.mark.parametrize("version", ["v1.1.0", "1.2", None], ids=["prefixed", "short", "null"])
def test_review_trail_refuses_a_malformed_schema_version_without_changing_any_file(
    tmp_path, version,
):
    base = str(tmp_path)
    path, before = _write_raw_state(base, version, future_extension={"synthetic": True})
    parent = os.path.dirname(path)
    before_names = sorted(os.listdir(parent))

    with pytest.raises(SchemaVersionError, match="not a MAJOR.MINOR.PATCH"):
        append_review_trail(
            "Acme Corp", "Fleet Loan", verdict="APPROVED",
            date_str="2026-01-15", base_dir=base,
        )

    with open(path, "rb") as state_file:
        assert state_file.read() == before
    assert sorted(os.listdir(parent)) == before_names


def test_review_trail_upgrades_a_legacy_state_and_preserves_unknown_fields(tmp_path):
    base = str(tmp_path)
    path, _ = _write_raw_state(
        base, MISSING, future_extension={"nested": [1, 2, {"retained": True}]},
    )

    state = append_review_trail(
        "Acme Corp", "Fleet Loan", verdict="APPROVED", notes=None,
        timestamp="2026-01-15T12:00:00", date_str="2026-01-15", base_dir=base,
    )

    assert state["schema_version"] == SCHEMA_VERSION
    assert state["future_extension"] == {"nested": [1, 2, {"retained": True}]}
    assert state["review_trail"] == [{
        "iteration": 1,
        "verdict": "APPROVED",
        "notes": None,
        "timestamp": "2026-01-15T12:00:00",
    }]
    with open(path, encoding="utf-8") as state_file:
        persisted = json.load(state_file)
    assert persisted["schema_version"] == SCHEMA_VERSION
    assert persisted["future_extension"] == {"nested": [1, 2, {"retained": True}]}

def test_reviewer_view_normalizes_a_null_history_to_an_empty_json_array(tmp_path):
    base = str(tmp_path)
    write_state(
        "Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base,
        review_verdict="REJECTED", review_trail=None, note="Keep source unchanged",
    )
    source_path = state_path(
        "Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base,
    )
    with open(source_path, "rb") as source_file:
        source_before = source_file.read()

    paths = export_reviewer_view("Synthetic Co", "Synthetic Loan", base_dir=base)

    with open(paths["review_trail"], encoding="utf-8") as trail_file:
        assert json.load(trail_file) == []
    with open(paths["state_for_review"], encoding="utf-8") as review_file:
        record = json.load(review_file)
    assert record["note"] == "Keep source unchanged"
    assert "review_trail" not in record
    assert "review_verdict" not in record
    with open(source_path, "rb") as source_file:
        assert source_file.read() == source_before


def test_review_trail_appends_to_a_null_history_as_iteration_one(tmp_path):
    base = str(tmp_path)
    write_state(
        "Synthetic Co", "Synthetic Loan", date_str="2026-01-15", base_dir=base,
        review_trail=None, note="Preserve unrelated state",
    )

    state = append_review_trail(
        "Synthetic Co", "Synthetic Loan", verdict="REJECTED",
        notes="Synthetic finding", timestamp="2026-01-15T10:00:00",
        date_str="2026-01-15", base_dir=base,
    )

    assert state["review_trail"] == [{
        "iteration": 1,
        "verdict": "REJECTED",
        "notes": "Synthetic finding",
        "timestamp": "2026-01-15T10:00:00",
    }]
    assert state["review_verdict"] == "REJECTED"
    assert state["note"] == "Preserve unrelated state"

def _find_deal_snapshot(base):
    """Capture every path and the bytes of every existing file under a lookup root."""
    paths = [base, *base.rglob("*")]
    return sorted(
        (
            "." if path == base else str(path.relative_to(base)),
            path.is_dir(),
            path.read_bytes() if path.is_file() else None,
        )
        for path in paths
    )


# ---------------------------------------------------------------------------
# Additional read-only find_deal() survivor cases (job prefix: find_deal).
# Existing tests already pin exact matching, newest exact date, one normalized
# candidate, misspellings, prefix rejection, corrupt state and CLI errors.
# ---------------------------------------------------------------------------

def test_find_deal_sorts_multiple_normalized_candidates_and_uses_each_newest_date(
    tmp_path, monkeypatch,
):
    base = str(tmp_path)
    company = "Synthetic Co"
    proposals = {
        "Synthetic Alpha - Capital": ("2026-01-11", "2026-06-12"),
        "Synthetic Alpha--Capital": ("2026-02-10", "2026-05-12"),
        "Synthetic Alpha-Capital": ("2026-03-11",),
    }
    for proposal, dates in proposals.items():
        for date_str in dates:
            write_state(company, proposal, date_str=date_str, base_dir=base)
    before = _find_deal_snapshot(tmp_path)

    real_scandir = os.scandir
    company_dir = os.path.normcase(os.path.join(base, "deals", company))

    from contextlib import contextmanager

    @contextmanager
    def reversed_company_scandir(path="."):
        if os.path.normcase(os.fspath(path)) != company_dir:
            with real_scandir(path) as entries:
                yield entries
            return
        with real_scandir(path) as entries:
            ordered_entries = sorted(entries, key=lambda entry: entry.name, reverse=True)
            insertion_proposals = list(dict.fromkeys(
                entry.name.rsplit("_", 1)[0] for entry in ordered_entries
                if entry.is_dir(follow_symlinks=False)
            ))
            assert insertion_proposals == [
                "Synthetic Alpha-Capital",
                "Synthetic Alpha--Capital",
                "Synthetic Alpha - Capital",
            ]
            yield iter(ordered_entries)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", reversed_company_scandir)
        result = state_manager.find_deal(
            company, "Synthetic Alpha Capital", base_dir=base,
        )

    assert result == {
        "record_found": False,
        "company_folder_exists": True,
        "near_misses": [
            {
                "proposal": "Synthetic Alpha - Capital",
                "date": "2026-06-12",
                "state_path": str(
                    tmp_path / "deals" / company
                    / "Synthetic Alpha - Capital_2026-06-12" / "state.json"
                ),
            },
            {
                "proposal": "Synthetic Alpha--Capital",
                "date": "2026-05-12",
                "state_path": str(
                    tmp_path / "deals" / company
                    / "Synthetic Alpha--Capital_2026-05-12" / "state.json"
                ),
            },
            {
                "proposal": "Synthetic Alpha-Capital",
                "date": "2026-03-11",
                "state_path": str(
                    tmp_path / "deals" / company
                    / "Synthetic Alpha-Capital_2026-03-11" / "state.json"
                ),
            },
        ],
    }
    after = _find_deal_snapshot(tmp_path)
    assert after == before


def test_find_deal_does_not_treat_a_file_as_an_existing_company_directory(tmp_path):
    base = tmp_path
    deals_dir = base / "deals"
    deals_dir.mkdir()
    company_file = deals_dir / "Synthetic Co"
    company_file.write_text("not a directory", encoding="utf-8")
    before = _find_deal_snapshot(base)

    result = state_manager.find_deal(
        "Synthetic Co", "Synthetic Facility", base_dir=str(base),
    )

    assert result == {
        "record_found": False,
        "company_folder_exists": False,
        "near_misses": [],
    }
    after = _find_deal_snapshot(base)
    assert after == before


@pytest.mark.parametrize(
    ("company", "proposal", "field_name"),
    [
        ("Synthetic/Co", "Synthetic Facility", "company"),
        ("Synthetic Co", "Synthetic/Facility", "proposal"),
    ],
)
def test_find_deal_rejects_unsafe_path_components_without_creating_files(
    tmp_path, company, proposal, field_name,
):
    before = _find_deal_snapshot(tmp_path)

    with pytest.raises(ValueError, match=field_name):
        state_manager.find_deal(company, proposal, base_dir=str(tmp_path))

    after = _find_deal_snapshot(tmp_path)
    assert after == before


def test_find_deal_excludes_candidate_dates_that_are_not_exact_iso_shape(tmp_path):
    base = tmp_path
    company_dir = base / "deals" / "Synthetic Co"
    malformed = company_dir / "Synthetic Alpha-Capital_2026-1-10"
    malformed.mkdir(parents=True)
    (malformed / "state.json").write_text("{}", encoding="utf-8")
    before = _find_deal_snapshot(base)

    result = state_manager.find_deal(
        "Synthetic Co", "Synthetic Alpha Capital", base_dir=str(base),
    )

    assert result == {
        "record_found": False,
        "company_folder_exists": True,
        "near_misses": [],
    }
    after = _find_deal_snapshot(base)
    assert after == before


# ---------------------------------------------------------------------------
# Issue #203: safe fresh-review candidates and start primitive.
# ---------------------------------------------------------------------------

def _review_203_write_state(base, date_str, proposal="Credit Facility", **fields):
    return state_manager.write_state(
        "Synthetic Co", proposal, date_str=date_str, base_dir=str(base), **fields,
    )


def _review_203_make_folder(base, folder_name, state_text=None):
    folder = base / "deals" / "Synthetic Co" / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    if state_text is not None:
        (folder / "state.json").write_text(state_text, encoding="utf-8")
    return folder


def test_review_203_candidates_returns_empty_and_writes_nothing(tmp_path):
    (tmp_path / "deals" / "Synthetic Co").mkdir(parents=True)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    report = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )

    assert report == {
        "company": "Synthetic Co",
        "proposal": "Credit Facility",
        "date": "2026-04-15",
        "candidates": [],
        "default_prior_date": None,
        "skipped": [],
    }
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


def test_review_203_candidates_are_descending_exact_and_skip_invalid_folders(tmp_path):
    _review_203_write_state(tmp_path, "2025-01-02")
    _review_203_write_state(tmp_path, "2025-05-03")
    _review_203_make_folder(tmp_path, "Credit Facility_2025-02-04")  # export-only
    _review_203_make_folder(tmp_path, "Credit Facility_2025-04-01", "not JSON")
    _review_203_make_folder(tmp_path, "Credit Facility_2025-13-01", "{}")  # impossible calendar date
    _review_203_make_folder(tmp_path, "Credit Facility_2025-1-02", "{}")  # malformed ISO shape
    _review_203_make_folder(tmp_path, "Credit-Facility_2025-06-01", "{}")  # normalized near-match only
    _review_203_make_folder(tmp_path, "Credit Facility Plus_2025-06-02", "{}")  # different proposal
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    report = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2025-06-01", base_dir=str(tmp_path),
    )

    assert [item["date"] for item in report["candidates"]] == ["2025-05-03", "2025-01-02"]
    assert report["default_prior_date"] == "2025-05-03"
    assert report["candidates"][0]["state_path"] == str(
        tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2025-05-03" / "state.json"
    )
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


def test_review_203_candidates_default_date_uses_the_existing_today_source(tmp_path, monkeypatch):
    class FixedDateTime:
        @staticmethod
        def now():
            return datetime(2026, 4, 15)

    _review_203_write_state(tmp_path, "2026-04-01")
    monkeypatch.setattr(state_manager, "datetime", FixedDateTime)

    report = state_manager.get_review_candidates("Synthetic Co", "Credit Facility", base_dir=str(tmp_path))

    assert report["date"] == "2026-04-15"
    assert [item["date"] for item in report["candidates"]] == ["2026-04-01"]


def test_review_203_candidates_refuse_a_date_that_matches_or_precedes_the_newest_review(tmp_path):
    for value in ("2025-01-01", "2025-03-01", "2025-07-01"):
        _review_203_write_state(tmp_path, value)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    for refused in ("2025-07-01", "2025-02-01"):
        with pytest.raises(ValueError, match="strictly later than the newest existing state-bearing review"):
            state_manager.get_review_candidates(
                "Synthetic Co", "Credit Facility", date_str=refused, base_dir=str(tmp_path),
            )
    later = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2025-07-02", base_dir=str(tmp_path),
    )

    assert [item["date"] for item in later["candidates"]] == ["2025-07-01", "2025-03-01", "2025-01-01"]
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


def test_review_203_candidates_skip_state_disappearing_after_existence_check(tmp_path, monkeypatch):
    _review_203_make_folder(tmp_path, "Credit Facility_2025-01-01", "{}")
    monkeypatch.setattr(state_manager, "read_state", lambda *args, **kwargs: None)

    report = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2026-01-01", base_dir=str(tmp_path),
    )

    assert report["candidates"] == []
    assert report["default_prior_date"] is None


def test_review_203_start_without_prior_creates_minimal_active_state(tmp_path):
    (tmp_path / "deals" / "Synthetic Co").mkdir(parents=True)
    report = state_manager.start_review(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )

    expected_path = tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2026-04-15" / "state.json"
    assert report["started"] is True
    assert report["date"] == "2026-04-15"
    assert report["prior_review_date"] is None
    assert report["linked"] is False
    assert "no eligible earlier review was found" in report["message"]
    assert report["state_path"] == str(expected_path)
    saved = json.loads(expected_path.read_text(encoding="utf-8"))
    assert saved == {
        "company": "Synthetic Co",
        "proposal": "Credit Facility",
        "date": "2026-04-15",
        "schema_version": SCHEMA_VERSION,
    }
    assert set(saved) == {"company", "proposal", "date", "schema_version"}
    assert state_manager.read_state(
        "Synthetic Co", "Credit Facility", base_dir=str(tmp_path),
    )["date"] == "2026-04-15"
    assert state_manager.resolve_date_str(
        "Synthetic Co", "Credit Facility", base_dir=str(tmp_path),
    ) == "2026-04-15"
    found = state_manager.find_deal("Synthetic Co", "Credit Facility", base_dir=str(tmp_path))
    assert found["record_found"] is True
    assert found["date"] == "2026-04-15"
    assert found["state_path"] == str(expected_path)


def test_review_203_start_default_link_inherits_nothing_and_preserves_prior_and_conventions(tmp_path):
    _review_203_write_state(tmp_path, "2024-01-01", inputs={"pd": "0.20%"})
    _review_203_write_state(
        tmp_path,
        "2025-06-01",
        inputs={"pd": "0.08%", "lgd": "0.35%"},
        financials={"FY-Current": {"ebitda": 210, "total_debt": 800}},
        ratios={"FY-Current": {"gross_leverage": 3.8}},
        policy_state={"covenant_results": [{"metric": "gross_leverage", "status": "FAIL"}]},
        steps_completed=["spread", "policy_check"],
        review_trail=[{"iteration": 1, "verdict": "REJECTED", "notes": "synthetic"}],
        covenants=[{"metric": "gross_leverage", "threshold": 3.5}],
        downside_case={"financials": {"FY-Current": {"ebitda": 100}}, "ratios": {}},
        collateral=[{"asset_id": "SYN-AST-1", "value": 900}],
        security_package=[{"asset_id": "SYN-AST-1", "ranking": "first"}],
        facility_terms={"currency": "GBP", "limit": 500},
        guarantees=[{"provider": "Synthetic Parent", "type": "Corporate"}],
        drafts={"cam": "synthetic prior draft"},
        source_manifest={"entries": ["synthetic prior source"]},
    )
    company_dir = tmp_path / "deals" / "Synthetic Co"
    conventions = company_dir / "_conventions.json"
    conventions.write_bytes(b'{"confirmed": "synthetic convention"}\n')
    prior_path = company_dir / "Credit Facility_2025-06-01" / "state.json"
    prior_bytes = prior_path.read_bytes()
    manifest = company_dir / "Credit Facility_2025-06-01" / "sources" / "manifest.json"
    manifest.parent.mkdir()
    manifest_bytes = b'[{"filename":"synthetic.txt","claim":"synthetic only"}]\n'
    manifest.write_bytes(manifest_bytes)
    conventions_bytes = conventions.read_bytes()

    report = state_manager.start_review(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )

    new_path = company_dir / "Credit Facility_2026-04-15" / "state.json"
    saved = json.loads(new_path.read_text(encoding="utf-8"))
    assert report["linked"] is True
    assert report["prior_review_date"] == "2025-06-01"
    assert "2025-06-01" in report["message"]
    assert saved == {
        "company": "Synthetic Co",
        "proposal": "Credit Facility",
        "date": "2026-04-15",
        "schema_version": SCHEMA_VERSION,
        "prior_review_date": "2025-06-01",
    }
    assert set(saved) == {"company", "proposal", "date", "schema_version", "prior_review_date"}
    assert prior_path.read_bytes() == prior_bytes
    assert manifest.read_bytes() == manifest_bytes
    assert conventions.read_bytes() == conventions_bytes
    assert sorted(item.name for item in new_path.parent.iterdir()) == ["state.json"]
    assert state_manager.read_state("Synthetic Co", "Credit Facility", base_dir=str(tmp_path)) == saved
    assert state_manager.resolve_date_str("Synthetic Co", "Credit Facility", base_dir=str(tmp_path)) == "2026-04-15"
    assert state_manager.find_deal("Synthetic Co", "Credit Facility", base_dir=str(tmp_path))["date"] == "2026-04-15"


def test_review_203_start_explicitly_links_an_older_eligible_prior(tmp_path):
    _review_203_write_state(tmp_path, "2024-01-01")
    _review_203_write_state(tmp_path, "2025-06-01")
    report = state_manager.start_review(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15",
        prior_date="2024-01-01", base_dir=str(tmp_path),
    )

    assert report["prior_review_date"] == "2024-01-01"
    assert report["linked"] is True
    saved = json.loads(
        (tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2026-04-15" / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(saved) == {"company", "proposal", "date", "schema_version", "prior_review_date"}
    assert saved["prior_review_date"] == "2024-01-01"


def test_review_203_start_no_prior_overrides_available_candidates(tmp_path):
    _review_203_write_state(tmp_path, "2025-06-01")

    report = state_manager.start_review(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15",
        no_prior=True, base_dir=str(tmp_path),
    )

    assert report["linked"] is False
    assert report["prior_review_date"] is None
    assert "by request (--no-prior)" in report["message"]
    saved = json.loads(
        (tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2026-04-15" / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(saved) == {"company", "proposal", "date", "schema_version"}


@pytest.mark.parametrize("prior_case", ["same_date", "missing", "export_only", "other_proposal"])
def test_review_203_start_refuses_an_ineligible_explicit_prior(tmp_path, prior_case):
    _review_203_write_state(tmp_path, "2025-01-01")
    review_date = "2026-06-01"
    prior_date = review_date if prior_case == "same_date" else "2025-02-01"
    if prior_case == "export_only":
        _review_203_make_folder(tmp_path, "Credit Facility_2025-02-01")
    elif prior_case == "other_proposal":
        _review_203_make_folder(tmp_path, "Credit-Facility_2025-02-01", "{}")
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    with pytest.raises(ValueError) as exc_info:
        state_manager.start_review(
            "Synthetic Co", "Credit Facility", date_str=review_date,
            prior_date=prior_date, base_dir=str(tmp_path),
        )

    message = str(exc_info.value)
    assert "not eligible" in message
    if prior_case == "same_date":
        assert "earlier than the new review date" in message
    else:
        assert prior_date in message
    assert not (tmp_path / "deals" / "Synthetic Co" / f"Credit Facility_{review_date}").exists()
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


def test_review_203_start_rejects_impossible_review_and_prior_dates_before_writing(tmp_path):
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    with pytest.raises(ValueError, match="valid calendar date"):
        state_manager.start_review(
            "Synthetic Co", "Credit Facility", date_str="2026-02-30", base_dir=str(tmp_path),
        )
    with pytest.raises(ValueError, match="valid calendar date"):
        state_manager.start_review(
            "Synthetic Co", "Credit Facility", date_str="2026-04-15",
            prior_date="2026-02-30", base_dir=str(tmp_path),
        )
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("with_state", [False, True])
def test_review_203_start_refuses_existing_same_date_folder_without_mutation(tmp_path, with_state):
    folder = _review_203_make_folder(tmp_path, "Credit Facility_2026-04-15")
    if with_state:
        (folder / "state.json").write_text('{"unchanged": true}\n', encoding="utf-8")
    before = sorted(
        (str(path.relative_to(tmp_path)), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*")
    )

    with pytest.raises(ValueError, match="dated folder already exists"):
        state_manager.start_review(
            "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
        )

    after = sorted(
        (str(path.relative_to(tmp_path)), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*")
    )
    assert after == before


def test_review_203_start_refuses_backdating_before_the_newest_existing_state_folder(tmp_path):
    _review_203_write_state(tmp_path, "2026-05-01")
    target = tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2026-04-30"

    with pytest.raises(ValueError, match="strictly later than the newest existing state-bearing review"):
        state_manager.start_review(
            "Synthetic Co", "Credit Facility", date_str="2026-04-30", base_dir=str(tmp_path),
        )

    assert not target.exists()


def test_review_203_start_rejects_conflicting_prior_options_and_unsafe_names_without_writing(tmp_path):
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    with pytest.raises(ValueError, match="cannot be used together"):
        state_manager.start_review(
            "Synthetic Co", "Credit Facility", date_str="2026-04-15",
            prior_date="2025-01-01", no_prior=True, base_dir=str(tmp_path),
        )
    with pytest.raises(ValueError, match="company"):
        state_manager.start_review("..", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="proposal"):
        state_manager.get_review_candidates(
            "Synthetic Co", "../escape", date_str="2026-04-15", base_dir=str(tmp_path),
        )
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


def test_review_203_start_default_date_uses_the_existing_today_source(tmp_path, monkeypatch):
    class FixedDateTime:
        @staticmethod
        def now():
            return datetime(2026, 4, 15)

    monkeypatch.setattr(state_manager, "datetime", FixedDateTime)
    (tmp_path / "deals" / "Synthetic Co").mkdir(parents=True)

    report = state_manager.start_review("Synthetic Co", "Credit Facility", base_dir=str(tmp_path))

    assert report["date"] == "2026-04-15"
    assert report["started"] is True
    assert report["linked"] is False



# ---------------------------------------------------------------------------
# Issue #203 review fixes: unknown company, empty names, unreadable earlier reviews, the start lock and a failed
# creation leaving nothing behind (CG403-1, CG403-2, CG403-3 and the assistant's own findings).
# ---------------------------------------------------------------------------

def _review_203_tree(base):
    return sorted(str(path.relative_to(base)) for path in base.rglob("*"))


def test_review_203_start_and_candidates_refuse_an_unknown_company_and_create_nothing(tmp_path):
    _review_203_write_state(tmp_path, "2025-01-01")
    before = _review_203_tree(tmp_path)

    with pytest.raises(ValueError, match="no deal folder for company 'Wrem Kitchens'") as start_error:
        state_manager.start_review("Wrem Kitchens", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="no deal folder for company 'Wrem Kitchens'"):
        state_manager.get_review_candidates(
            "Wrem Kitchens", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
        )

    assert "/triage or /spread" in str(start_error.value)
    assert _review_203_tree(tmp_path) == before


def test_review_203_start_is_allowed_for_an_existing_company_with_a_new_proposal_name(tmp_path):
    _review_203_write_state(tmp_path, "2025-01-01", proposal="Old Facility Name")

    report = state_manager.start_review(
        "Synthetic Co", "Renamed Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )

    assert report["linked"] is False
    assert (tmp_path / "deals" / "Synthetic Co" / "Renamed Facility_2026-04-15" / "state.json").is_file()


@pytest.mark.parametrize(("company", "proposal"), [("", "Credit Facility"), ("   ", "Credit Facility"),
                                                    ("Synthetic Co", ""), ("Synthetic Co", " ")])
def test_review_203_empty_or_blank_names_are_refused_before_anything_is_written(tmp_path, company, proposal):
    _review_203_write_state(tmp_path, "2025-01-01")
    before = _review_203_tree(tmp_path)

    with pytest.raises(ValueError, match="must not be empty"):
        state_manager.start_review(company, proposal, date_str="2026-04-15", base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="must not be empty"):
        state_manager.get_review_candidates(company, proposal, date_str="2026-04-15", base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="must not be empty"):
        state_manager.sanitize_path_component(company if not company.strip() else proposal, "name")

    assert _review_203_tree(tmp_path) == before


def test_review_203_an_unreadable_earlier_review_is_reported_as_skipped_not_hidden(tmp_path):
    _review_203_write_state(tmp_path, "2025-06-01")
    _review_203_make_folder(tmp_path, "Credit Facility_2024-06-01", "{not json")

    report = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )

    assert [item["date"] for item in report["candidates"]] == ["2025-06-01"]
    assert [item["date"] for item in report["skipped"]] == ["2024-06-01"]
    assert report["skipped"][0]["reason"].startswith("state.json cannot be read: ")


def test_review_203_when_the_only_earlier_review_is_unreadable_there_are_no_candidates_but_it_is_skipped(tmp_path):
    _review_203_make_folder(tmp_path, "Credit Facility_2024-06-01", "{not json")

    report = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )
    started = state_manager.start_review(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )

    assert report["candidates"] == [] and report["default_prior_date"] is None
    assert [item["date"] for item in report["skipped"]] == ["2024-06-01"]
    assert started["linked"] is False


def test_review_203_a_held_start_lock_blocks_a_second_start_until_it_is_released(tmp_path, monkeypatch):
    _review_203_write_state(tmp_path, "2025-01-01")
    company_dir = tmp_path / "deals" / "Synthetic Co"
    monkeypatch.setattr(state_manager, "LOCK_TIMEOUT_SECONDS", 0.2)

    with state_manager._FileLock(str(company_dir / "Credit Facility.start"), timeout=1):
        with pytest.raises(TimeoutError, match="Could not acquire lock"):
            state_manager.start_review(
                "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
            )
        assert not (company_dir / "Credit Facility_2026-04-15").exists()

    report = state_manager.start_review(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )
    assert report["started"] is True
    assert not list(company_dir.glob("*.lock"))


class _InterruptedStart(BaseException):
    """Stands in for KeyboardInterrupt: not an Exception, so only cleanup in `except BaseException` handles it."""


@pytest.mark.parametrize("failure", ["write_error", "replace_error", "interrupt"])
def test_review_203_a_failed_creation_leaves_no_folder_file_or_lock_and_a_retry_succeeds(tmp_path, monkeypatch, failure):
    _review_203_write_state(tmp_path, "2025-01-01")
    company_dir = tmp_path / "deals" / "Synthetic Co"
    before = _review_203_tree(tmp_path)

    with monkeypatch.context() as patched:
        if failure == "write_error":
            def failing_dump(*args, **kwargs):
                raise OSError("disk full")
            patched.setattr(state_manager.json, "dump", failing_dump)
        elif failure == "replace_error":
            def failing_replace(*args, **kwargs):
                raise OSError("rename refused")
            patched.setattr(state_manager.os, "replace", failing_replace)
        else:
            def interrupting_dump(*args, **kwargs):
                raise _InterruptedStart()
            patched.setattr(state_manager.json, "dump", interrupting_dump)

        expected_error = _InterruptedStart if failure == "interrupt" else ValueError
        with pytest.raises(expected_error) as caught:
            state_manager.start_review(
                "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
            )

    if failure != "interrupt":
        assert "removed and nothing was changed" in str(caught.value)
    assert _review_203_tree(tmp_path) == before
    assert not list(company_dir.glob("*.lock"))

    retry = state_manager.start_review(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )
    assert retry["started"] is True


def test_review_203_a_folder_created_by_someone_else_after_the_check_is_a_clean_refusal(tmp_path, monkeypatch):
    _review_203_write_state(tmp_path, "2025-01-01")
    target = tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2026-04-15"
    real_mkdir = os.mkdir

    def racing_mkdir(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        raise FileExistsError(17, "File exists", path)

    monkeypatch.setattr(state_manager.os, "mkdir", racing_mkdir)
    with pytest.raises(ValueError, match="the dated folder already exists"):
        state_manager.start_review(
            "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
        )
    # The folder belongs to whoever created it (the fake race created it here), so it is left alone.
    assert target.is_dir()


def test_review_203_candidates_skip_a_later_dated_export_only_folder(tmp_path):
    _review_203_write_state(tmp_path, "2025-06-01")
    # Not state-bearing, so the newest-review rule ignores it, but its date is later than the new review's date.
    _review_203_make_folder(tmp_path, "Credit Facility_2027-01-01")

    report = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )

    assert [item["date"] for item in report["candidates"]] == ["2025-06-01"]
    assert report["skipped"] == []


def test_review_203_a_folder_that_cannot_be_created_is_a_clean_refusal_with_nothing_left(tmp_path, monkeypatch):
    _review_203_write_state(tmp_path, "2025-01-01")
    before = _review_203_tree(tmp_path)

    def refusing_mkdir(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(state_manager.os, "mkdir", refusing_mkdir)
    with pytest.raises(ValueError, match="Could not start the review: .*nothing was changed"):
        state_manager.start_review(
            "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
        )

    assert _review_203_tree(tmp_path) == before


def test_review_203_cleanup_that_itself_fails_never_hides_the_original_error(tmp_path, monkeypatch):
    _review_203_write_state(tmp_path, "2025-01-01")

    def failing_dump(*args, **kwargs):
        raise OSError("disk full")

    real_remove = os.remove

    def failing_remove(path, *args, **kwargs):
        if str(path).endswith(".lock"):
            return real_remove(path, *args, **kwargs)   # the start lock still has to be released
        raise OSError("remove refused")

    def failing_rmdir(*args, **kwargs):
        raise OSError("rmdir refused")

    with monkeypatch.context() as patched:
        patched.setattr(state_manager.json, "dump", failing_dump)
        patched.setattr(state_manager.os, "remove", failing_remove)
        patched.setattr(state_manager.os, "rmdir", failing_rmdir)
        with pytest.raises(ValueError, match="Could not start the review: disk full") as caught:
            state_manager.start_review(
                "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
            )

    # The error says so plainly instead of claiming a cleanup that did not happen.
    assert "could not be removed and should be deleted by hand" in str(caught.value)
    assert "was removed and nothing was changed" not in str(caught.value)


# ---------------------------------------------------------------------------
# Optional borrower-supplied consolidated group-basis metadata (issue #48, case A)
# ---------------------------------------------------------------------------

def _valid_group_basis_for_tests():
    return {
        "basis": "supplied_consolidated",
        "reporting_entity": "Synthetic Group Ltd",
        "entities_covered": ["Synthetic Group Ltd", "Synthetic Group OpCo"],
        "source": {
            "document": "Borrower-supplied consolidated accounts",
            "accounting_basis": "UK GAAP, consolidated",
            "date": "2026-08-31",
        },
        "period_end": "2026-03-31",
        "periods": ["FY-Current", "FY-1"],
        "figures_basis": "supplied",
        "perimeter_confirmed": True,
        "perimeter_confirmed_on": "2026-10-21",
    }


def test_validate_state_treats_a_null_group_basis_as_not_recorded():
    state = {"group_basis": None}
    assert state_manager.validate_state(state, keys=("group_basis",)) == state


@pytest.mark.parametrize("mutation", [
    "not_object", "basis_missing", "basis_wrong",
    "reporting_missing", "reporting_wrong_type", "reporting_empty",
    "entities_missing", "entities_wrong_type", "entities_empty", "entity_wrong_type", "entity_empty",
    "entities_duplicate", "reporting_not_covered",
    "source_missing", "source_wrong_type", "document_missing", "document_empty",
    "accounting_basis_missing", "accounting_basis_empty", "source_date_missing", "source_date_impossible",
    "period_end_missing", "period_end_wrong_shape", "period_end_impossible",
    "periods_missing", "periods_empty", "period_wrong_type", "period_unknown", "period_duplicate",
    "figures_basis_missing", "figures_basis_wrong", "figures_basis_wrong_type",
    "perimeter_missing", "perimeter_false", "perimeter_date_missing", "perimeter_date_impossible",
])
def test_validate_state_refuses_malformed_group_basis_with_the_key_named(mutation):
    group_basis = _valid_group_basis_for_tests()
    if mutation == "not_object":
        group_basis = ["not", "an", "object"]
    elif mutation == "basis_missing":
        del group_basis["basis"]
    elif mutation == "basis_wrong":
        group_basis["basis"] = "ownership_percentage"
    elif mutation == "reporting_missing":
        del group_basis["reporting_entity"]
    elif mutation == "reporting_wrong_type":
        group_basis["reporting_entity"] = 7
    elif mutation == "reporting_empty":
        group_basis["reporting_entity"] = "  "
    elif mutation == "entities_missing":
        del group_basis["entities_covered"]
    elif mutation == "entities_wrong_type":
        group_basis["entities_covered"] = "Synthetic Group Ltd"
    elif mutation == "entities_empty":
        group_basis["entities_covered"] = []
    elif mutation == "entity_wrong_type":
        group_basis["entities_covered"] = ["Synthetic Group Ltd", {}]
    elif mutation == "entity_empty":
        group_basis["entities_covered"] = ["Synthetic Group Ltd", ""]
    elif mutation == "entities_duplicate":
        group_basis["entities_covered"] = ["Synthetic Group Ltd", "Synthetic Group Ltd"]
    elif mutation == "reporting_not_covered":
        group_basis["entities_covered"] = ["Synthetic Group OpCo"]
    elif mutation == "source_missing":
        del group_basis["source"]
    elif mutation == "source_wrong_type":
        group_basis["source"] = "borrower document"
    elif mutation == "document_missing":
        del group_basis["source"]["document"]
    elif mutation == "document_empty":
        group_basis["source"]["document"] = ""
    elif mutation == "accounting_basis_missing":
        del group_basis["source"]["accounting_basis"]
    elif mutation == "accounting_basis_empty":
        group_basis["source"]["accounting_basis"] = " "
    elif mutation == "source_date_missing":
        del group_basis["source"]["date"]
    elif mutation == "source_date_impossible":
        group_basis["source"]["date"] = "2026-02-30"
    elif mutation == "period_end_missing":
        del group_basis["period_end"]
    elif mutation == "period_end_wrong_shape":
        group_basis["period_end"] = "31/03/2026"
    elif mutation == "period_end_impossible":
        group_basis["period_end"] = "2025-02-29"
    elif mutation == "periods_missing":
        del group_basis["periods"]
    elif mutation == "periods_empty":
        group_basis["periods"] = []
    elif mutation == "period_wrong_type":
        group_basis["periods"] = ["FY-Current", 7]
    elif mutation == "period_unknown":
        group_basis["periods"] = ["2026"]
    elif mutation == "period_duplicate":
        group_basis["periods"] = ["FY-Current", "FY-Current"]
    elif mutation == "figures_basis_missing":
        del group_basis["figures_basis"]
    elif mutation == "figures_basis_wrong":
        group_basis["figures_basis"] = "inferred"
    elif mutation == "figures_basis_wrong_type":
        group_basis["figures_basis"] = []
    elif mutation == "perimeter_missing":
        del group_basis["perimeter_confirmed"]
    elif mutation == "perimeter_false":
        group_basis["perimeter_confirmed"] = False
    elif mutation == "perimeter_date_missing":
        del group_basis["perimeter_confirmed_on"]
    elif mutation == "perimeter_date_impossible":
        group_basis["perimeter_confirmed_on"] = "2026-02-30"

    with pytest.raises(StateShapeError, match="group_basis"):
        state_manager.validate_state({"group_basis": group_basis}, keys=("group_basis",))


def test_group_basis_validation_accepts_unknown_future_fields_and_all_documented_periods():
    group_basis = _valid_group_basis_for_tests()
    group_basis["periods"] = ["FY-2", "FY-1", "FY-Current", "FY+1", "FY+2", "FY+3"]
    group_basis["future_metadata"] = {"opaque": ["not", "inspected"]}
    group_basis["source"]["future_source_detail"] = {"opaque": None}
    state = {"group_basis": group_basis, "financials_source": "analyst-supplied"}
    assert state_manager.validate_state(state, keys=("group_basis",))["group_basis"] == group_basis


@pytest.mark.parametrize(("figures_basis", "financials_source"), [
    ("supplied", "framework-computed"),
    ("recomputed", "analyst-supplied"),
])
def test_group_basis_refuses_a_contradictory_recorded_financials_source(figures_basis, financials_source):
    group_basis = _valid_group_basis_for_tests()
    group_basis["figures_basis"] = figures_basis
    with pytest.raises(StateShapeError, match="group_basis.*financials_source"):
        state_manager.validate_state(
            {"group_basis": group_basis, "financials_source": financials_source},
            keys=("group_basis", "financials_source"),
        )


@pytest.mark.parametrize(("figures_basis", "financials_source"), [
    ("supplied", "analyst-supplied"),
    ("recomputed", "framework-computed"),
    ("recomputed", None),
])
def test_group_basis_accepts_consistent_or_not_yet_recorded_financials_source(figures_basis, financials_source):
    group_basis = _valid_group_basis_for_tests()
    group_basis["figures_basis"] = figures_basis
    state = {"group_basis": group_basis}
    keys = ["group_basis"]
    if financials_source is not None:
        state["financials_source"] = financials_source
        keys.append("financials_source")
    assert state_manager.validate_state(state, keys=keys)["group_basis"] == group_basis


def test_group_basis_refuses_supplied_figures_when_the_financials_source_is_not_recorded():
    """An absent financials_source is read as framework-computed (what the exporter assumes), so 'supplied' needs
    an explicit analyst-supplied source (review CH4)."""
    group_basis = _valid_group_basis_for_tests()
    group_basis["figures_basis"] = "supplied"
    for state in ({"group_basis": group_basis}, {"group_basis": group_basis, "financials_source": None}):
        with pytest.raises(state_manager.StateShapeError) as caught:
            state_manager.validate_state(state, keys=("group_basis",))
        assert 'state financials_source is not recorded (read as "framework-computed")' in str(caught.value)


def test_group_basis_round_trips_through_write_state_and_read_state(tmp_path):
    group_basis = _valid_group_basis_for_tests()
    written = write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), group_basis=group_basis, financials_source="analyst-supplied",
    )
    loaded = read_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), keys=("group_basis",),
    )
    assert loaded["group_basis"] == group_basis
    assert written["group_basis"] == group_basis


def test_record_group_basis_requires_explicit_perimeter_confirmation_without_writing(tmp_path):
    group_basis_file = tmp_path / "group-basis.json"
    group_basis_file.write_text(json.dumps(_valid_group_basis_for_tests()), encoding="utf-8")
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), financials_source="analyst-supplied", marker="keep",
    )
    path = state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path)
    )
    before = Path(path).read_bytes()
    with pytest.raises(ValueError, match="--perimeter-confirmed"):
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
            perimeter_confirmed=False, base_dir=str(tmp_path),
        )
    assert Path(path).read_bytes() == before


def test_record_group_basis_merges_only_metadata_and_sets_analyst_confirmation_date(tmp_path):
    group_basis_file = tmp_path / "group-basis.json"
    supplied = _valid_group_basis_for_tests()
    supplied.pop("perimeter_confirmed")
    supplied.pop("perimeter_confirmed_on")
    supplied["future_metadata"] = {"retain": True}
    group_basis_file.write_text(json.dumps(supplied), encoding="utf-8")
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), financials_source="analyst-supplied",
        financials={"FY-Current": {"ebitda": 12}}, ratios={"FY-Current": {"dscr": 2.0}},
        multi_period_financials={"FY-Current": {"revenue": 100}},
        analyst_supplied_financials={"FY-Current": {"revenue": 101}}, marker="preserve",
    )
    result = state_manager.record_group_basis(
        "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
        perimeter_confirmed=True, date_str="2026-10-21", base_dir=str(tmp_path),
    )
    loaded = read_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), keys=("group_basis", "financials_source"),
    )
    assert result == {
        "recorded": True,
        "state_path": state_path("Synthetic Group Ltd", "Synthetic Group Facility",
                                 date_str="2026-01-01", base_dir=str(tmp_path)),
        "figures_basis": "supplied",
    }
    assert loaded["group_basis"]["perimeter_confirmed"] is True
    assert loaded["group_basis"]["perimeter_confirmed_on"] == "2026-10-21"
    assert loaded["group_basis"]["future_metadata"] == {"retain": True}
    assert loaded["financials"] == {"FY-Current": {"ebitda": 12}}
    assert loaded["ratios"] == {"FY-Current": {"dscr": 2.0}}
    assert loaded["multi_period_financials"] == {"FY-Current": {"revenue": 100}}
    assert loaded["analyst_supplied_financials"] == {"FY-Current": {"revenue": 101}}
    assert loaded["financials_source"] == "analyst-supplied" and loaded["marker"] == "preserve"


def test_record_group_basis_refuses_an_absent_deal_without_creating_one(tmp_path):
    group_basis_file = tmp_path / "group-basis.json"
    group_basis_file.write_text(json.dumps(_valid_group_basis_for_tests()), encoding="utf-8")
    with pytest.raises(ValueError, match="no exact existing state.json"):
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )
    assert not (tmp_path / "deals").exists()


def test_record_group_basis_refuses_a_non_object_file_without_writing(tmp_path):
    group_basis_file = tmp_path / "group-basis.json"
    group_basis_file.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), financials_source="analyst-supplied",
    )
    path = state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path)
    )
    before = Path(path).read_bytes()
    with pytest.raises(ValueError, match="JSON object"):
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )
    assert Path(path).read_bytes() == before


def test_record_group_basis_refuses_a_recorded_source_conflict_without_writing(tmp_path):
    group_basis_file = tmp_path / "group-basis.json"
    supplied = _valid_group_basis_for_tests()
    supplied.pop("perimeter_confirmed")
    supplied.pop("perimeter_confirmed_on")
    supplied["figures_basis"] = "recomputed"
    group_basis_file.write_text(json.dumps(supplied), encoding="utf-8")
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), financials_source="analyst-supplied",
    )
    path = state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path)
    )
    before = Path(path).read_bytes()
    with pytest.raises(ValueError, match="financials_source"):
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )
    assert Path(path).read_bytes() == before


def test_read_state_rejects_a_malformed_present_group_basis_with_its_key_named(tmp_path):
    path = state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path)
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"group_basis": {"basis": "wrong"}}, handle)
    with pytest.raises(StateShapeError, match="group_basis"):
        read_state(
            "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
            base_dir=str(tmp_path), keys=("group_basis",),
        )


def test_record_group_basis_defaults_confirmation_date_to_today(tmp_path, monkeypatch):
    class FixedDateTime:
        @staticmethod
        def now():
            return datetime(2026, 10, 21, 12, 0)

    monkeypatch.setattr(state_manager, "datetime", FixedDateTime)
    group_basis_file = tmp_path / "group-basis.json"
    supplied = _valid_group_basis_for_tests()
    supplied.pop("perimeter_confirmed")
    supplied.pop("perimeter_confirmed_on")
    group_basis_file.write_text(json.dumps(supplied), encoding="utf-8")
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), financials_source="analyst-supplied",
    )
    result = state_manager.record_group_basis(
        "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
        perimeter_confirmed=True, base_dir=str(tmp_path),
    )
    assert result["recorded"] is True
    saved = read_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), keys=("group_basis",),
    )
    assert saved["group_basis"]["perimeter_confirmed_on"] == "2026-10-21"


def test_record_group_basis_refuses_bad_input_date_before_writing(tmp_path):
    group_basis_file = tmp_path / "group-basis.json"
    supplied = _valid_group_basis_for_tests()
    supplied.pop("perimeter_confirmed")
    supplied.pop("perimeter_confirmed_on")
    supplied["source"]["date"] = "2026-02-30"
    group_basis_file.write_text(json.dumps(supplied), encoding="utf-8")
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), financials_source="analyst-supplied",
    )
    path = state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path)
    )
    before = Path(path).read_bytes()
    with pytest.raises(ValueError, match="group_basis.source.date"):
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )
    assert Path(path).read_bytes() == before


def test_record_group_basis_refuses_a_state_that_disappears_before_locked_read(tmp_path, monkeypatch):
    group_basis_file = tmp_path / "group-basis.json"
    group_basis_file.write_text(json.dumps(_valid_group_basis_for_tests()), encoding="utf-8")
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), financials_source="analyst-supplied",
    )
    monkeypatch.setattr(state_manager, "read_state", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="no longer exists"):
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )


def test_group_basis_period_labels_stay_aligned_with_spreading_builder():
    from spreading_builder import PERIOD_KEYS

    assert state_manager._GROUP_BASIS_PERIOD_KEYS == tuple(PERIOD_KEYS)


def test_record_group_basis_refuses_supplied_figures_on_a_deal_with_no_financials_source_without_writing(tmp_path):
    group_basis_file = tmp_path / "group-basis.json"
    supplied = _valid_group_basis_for_tests()
    supplied.pop("perimeter_confirmed")
    supplied.pop("perimeter_confirmed_on")
    supplied["figures_basis"] = "supplied"
    group_basis_file.write_text(json.dumps(supplied), encoding="utf-8")
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path),
        financials={"FY-Current": {"raw": {"revenue": 100}}},
    )
    path = state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path)
    )
    before = Path(path).read_bytes()
    with pytest.raises(ValueError, match="not recorded"):
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )
    assert Path(path).read_bytes() == before


def test_record_group_basis_accepts_recomputed_figures_on_a_deal_with_no_financials_source(tmp_path):
    group_basis_file = tmp_path / "group-basis.json"
    supplied = _valid_group_basis_for_tests()
    supplied.pop("perimeter_confirmed")
    supplied.pop("perimeter_confirmed_on")
    supplied["figures_basis"] = "recomputed"
    group_basis_file.write_text(json.dumps(supplied), encoding="utf-8")
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path),
        financials={"FY-Current": {"raw": {"revenue": 100}}},
    )
    report = state_manager.record_group_basis(
        "Synthetic Group Ltd", "Synthetic Group Facility", str(group_basis_file),
        perimeter_confirmed=True, date_str="2026-10-21", base_dir=str(tmp_path),
    )
    assert report["recorded"] is True and report["figures_basis"] == "recomputed"

# ---------------------------------------------------------------------------
# PR: test: kill case A group-basis mutation survivors (Part of #408)
# ---------------------------------------------------------------------------

def _ca409_survivor_basis():
    return {
        "basis": "supplied_consolidated",
        "reporting_entity": "Synthetic Group Ltd",
        "entities_covered": ["Synthetic Group Ltd", "Synthetic Group OpCo"],
        "source": {
            "document": "Borrower-supplied consolidated accounts",
            "accounting_basis": "UK GAAP, consolidated",
            "date": "2026-08-31",
        },
        "period_end": "2026-03-31",
        "periods": ["FY-Current", "FY-1"],
        "figures_basis": "recomputed",
        "perimeter_confirmed": True,
        "perimeter_confirmed_on": "2026-10-21",
    }


def _ca409_assert_basis_problems(value, problems):
    """Assert the complete public validate_state() diagnostic, not just a substring."""
    with pytest.raises(StateShapeError) as caught:
        state_manager.validate_state({"group_basis": value}, keys=("group_basis",))
    expected = (
        "Cannot use state.json: " + "; ".join(problems)
        + ". Fix the file by hand or restore it from a backup; the file was not modified."
    )
    assert str(caught.value) == expected


def _ca409_write_group_basis_file(path, basis, *, ensure_ascii=True):
    path.write_text(json.dumps(basis, ensure_ascii=ensure_ascii), encoding="utf-8")


def _ca409_seed_state(tmp_path, **fields):
    state_manager.write_state(
        "Synthetic Group Ltd",
        "Synthetic Group Facility",
        date_str="2026-01-01",
        base_dir=str(tmp_path),
        **fields,
    )
    return Path(state_manager.state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility",
        date_str="2026-01-01", base_dir=str(tmp_path),
    ))


def _ca409_record_expected_problem(problem):
    return f"Refusing to record group_basis: {problem}. Nothing was changed."


def test_ca409_group_basis_wrong_top_level_type_reports_its_actual_json_type():
    _ca409_assert_basis_problems(
        ["not", "an", "object"],
        ['"group_basis" must be an object, found list'],
    )


def test_ca409_group_basis_required_string_diagnostics_keep_the_actual_type_and_path():
    basis = _ca409_survivor_basis()
    basis["reporting_entity"] = 7
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.reporting_entity" must be a non-empty string, found number'],
    )

    basis = _ca409_survivor_basis()
    del basis["reporting_entity"]
    _ca409_assert_basis_problems(basis, ['"group_basis.reporting_entity" is required'])


def test_ca409_group_basis_basis_value_has_exact_refusal_text_and_valid_boundary():
    basis = _ca409_survivor_basis()
    _ca409_assert_basis_problems(
        {**basis, "basis": "ownership_percentage"},
        ['"group_basis.basis" must be "supplied_consolidated"'],
    )
    assert state_manager.validate_state(
        {"group_basis": basis}, keys=("group_basis",)
    )["group_basis"] == basis


def test_ca409_group_basis_entity_list_must_be_a_nonempty_list_of_distinct_names():
    basis = _ca409_survivor_basis()
    basis["entities_covered"] = []
    _ca409_assert_basis_problems(
        basis,
        [
            '"group_basis.entities_covered" must be a non-empty list of distinct non-empty strings',
            '"group_basis.entities_covered" must include group_basis.reporting_entity',
        ],
    )

    basis = _ca409_survivor_basis()
    basis["entities_covered"] = "Synthetic Group Ltd"
    _ca409_assert_basis_problems(
        basis,
        [
            '"group_basis.entities_covered" must be a non-empty list of distinct non-empty strings',
            '"group_basis.entities_covered" must include group_basis.reporting_entity',
        ],
    )


def test_ca409_group_basis_entity_loop_continues_after_each_invalid_entity_and_reports_duplicates():
    basis = _ca409_survivor_basis()
    basis["entities_covered"] = [None, "", "Synthetic Group OpCo", "Synthetic Group OpCo"]
    _ca409_assert_basis_problems(
        basis,
        [
            '"group_basis.entities_covered[0]" must be a non-empty string',
            '"group_basis.entities_covered[1]" must be a non-empty string',
            '"group_basis.entities_covered" contains duplicate entity \'Synthetic Group OpCo\'',
            '"group_basis.entities_covered" must include group_basis.reporting_entity',
        ],
    )


def test_ca409_group_basis_reporting_entity_must_be_in_the_covered_perimeter():
    basis = _ca409_survivor_basis()
    basis["entities_covered"] = ["Synthetic Group OpCo", "Synthetic Group Trading Ltd"]
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.entities_covered" must include group_basis.reporting_entity'],
    )


def test_ca409_group_basis_source_container_and_document_paths_are_exact():
    basis = _ca409_survivor_basis()
    basis["source"] = "Borrower document"
    _ca409_assert_basis_problems(
        basis,
        [
            '"group_basis.source" must be an object',
            '"group_basis.source.document" is required',
            '"group_basis.source.accounting_basis" is required',
            '"group_basis.source.date" is required',
        ],
    )

    basis = _ca409_survivor_basis()
    del basis["source"]["document"]
    _ca409_assert_basis_problems(basis, ['"group_basis.source.document" is required'])

    basis = _ca409_survivor_basis()
    basis["source"]["document"] = 17
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.source.document" must be a non-empty string, found number'],
    )


def test_ca409_group_basis_accounting_basis_required_string_path_is_exact():
    basis = _ca409_survivor_basis()
    del basis["source"]["accounting_basis"]
    _ca409_assert_basis_problems(basis, ['"group_basis.source.accounting_basis" is required'])

    basis = _ca409_survivor_basis()
    basis["source"]["accounting_basis"] = None
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.source.accounting_basis" must be a non-empty string, found null'],
    )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("source.date", "2026-8-31"),
        ("source.date", None),
        ("period_end", "31/03/2026"),
        ("period_end", None),
        ("perimeter_confirmed_on", "2026-03-31T00:00:00"),
        ("perimeter_confirmed_on", None),
    ],
)
def test_ca409_group_basis_calendar_date_shape_and_type_are_refused_exactly(field, bad_value):
    basis = _ca409_survivor_basis()
    if field == "source.date":
        basis["source"]["date"] = bad_value
    else:
        basis[field] = bad_value
    path = "group_basis." + field
    _ca409_assert_basis_problems(
        basis,
        [f'"{path}" must be a real YYYY-MM-DD calendar date'],
    )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("source.date", "2025-02-29"),
        ("source.date", "2026-02-30"),
        ("period_end", "2025-02-29"),
        ("period_end", "2026-02-30"),
        ("perimeter_confirmed_on", "2025-02-29"),
        ("perimeter_confirmed_on", "2026-02-30"),
    ],
)
def test_ca409_group_basis_rejects_impossible_calendar_dates_exactly(field, bad_value):
    basis = _ca409_survivor_basis()
    if field == "source.date":
        basis["source"]["date"] = bad_value
    else:
        basis[field] = bad_value
    _ca409_assert_basis_problems(
        basis,
        [f'"group_basis.{field}" must be a real YYYY-MM-DD calendar date'],
    )


def test_ca409_group_basis_accepts_a_real_leap_day_and_all_nonempty_metadata_strings():
    basis = _ca409_survivor_basis()
    basis["reporting_entity"] = "Synthetic Group Ltd"
    basis["source"]["document"] = "Synthetic audited group accounts"
    basis["source"]["accounting_basis"] = "Synthetic accounting basis"
    basis["source"]["date"] = "2024-02-29"
    basis["period_end"] = "2024-02-29"
    basis["perimeter_confirmed_on"] = "2024-02-29"
    assert state_manager.validate_state(
        {"group_basis": basis}, keys=("group_basis",)
    )["group_basis"] == basis


def test_ca409_group_basis_periods_must_be_a_nonempty_list():
    basis = _ca409_survivor_basis()
    basis["periods"] = []
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.periods" must be a non-empty list of distinct framework period labels'],
    )

    basis = _ca409_survivor_basis()
    basis["periods"] = ("FY-Current",)
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.periods" must be a non-empty list of distinct framework period labels'],
    )


def test_ca409_group_basis_period_type_checks_continue_and_detect_duplicate_labels():
    basis = _ca409_survivor_basis()
    basis["periods"] = ["FY-Current", 7]
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.periods[1]" must be a framework period label'],
    )

    basis = _ca409_survivor_basis()
    basis["periods"] = [None, "", "FY-Current", "FY-Current"]
    _ca409_assert_basis_problems(
        basis,
        [
            '"group_basis.periods[0]" must be a framework period label',
            '"group_basis.periods[1]" must be a framework period label',
            '"group_basis.periods" contains duplicate period \'FY-Current\'',
        ],
    )


def test_ca409_group_basis_rejects_unknown_period_labels_and_accepts_each_supported_label():
    basis = _ca409_survivor_basis()
    basis["periods"] = ["FY-3"]
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.periods[0]" has unknown period label \'FY-3\''],
    )

    basis = _ca409_survivor_basis()
    basis["periods"] = ["FY-2", "FY-1", "FY-Current", "FY+1", "FY+2", "FY+3"]
    assert state_manager.validate_state(
        {"group_basis": basis}, keys=("group_basis",)
    )["group_basis"] == basis


def test_ca409_group_basis_figures_basis_and_confirmation_errors_are_exact():
    basis = _ca409_survivor_basis()
    basis["figures_basis"] = "inferred"
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.figures_basis" must be "supplied" or "recomputed"'],
    )

    basis = _ca409_survivor_basis()
    basis["perimeter_confirmed"] = False
    _ca409_assert_basis_problems(
        basis,
        ['"group_basis.perimeter_confirmed" must be true after explicit analyst confirmation'],
    )


def test_ca409_group_basis_supplied_figures_require_a_matching_recorded_source():
    basis = _ca409_survivor_basis()
    basis["figures_basis"] = "supplied"
    _ca409_assert_basis_problems(
        basis,
        [
            '"group_basis.figures_basis" is \'supplied\', but state financials_source is not recorded '
            '(read as "framework-computed"); these must agree'
        ],
    )

    basis = _ca409_survivor_basis()
    basis["figures_basis"] = "supplied"
    state = {"group_basis": basis, "financials_source": "framework-computed"}
    with pytest.raises(StateShapeError) as caught:
        state_manager.validate_state(state, keys=("group_basis", "financials_source"))
    assert str(caught.value) == (
        'Cannot use state.json: "group_basis.figures_basis" is \'supplied\', but '
        'state financials_source is \'framework-computed\'; these must agree. '
        'Fix the file by hand or restore it from a backup; the file was not modified.'
    )


def test_ca409_record_group_basis_refusal_without_confirmation_is_exact_and_byte_preserving(tmp_path):
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, _ca409_survivor_basis())
    state_path_value = _ca409_seed_state(tmp_path, financials_source="analyst-supplied", marker="keep")
    before = state_path_value.read_bytes()

    with pytest.raises(ValueError) as caught:
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
            perimeter_confirmed=False, base_dir=str(tmp_path),
        )

    assert str(caught.value) == (
        "Refusing to record group_basis without explicit --perimeter-confirmed analyst confirmation; "
        "nothing was changed."
    )
    assert state_path_value.read_bytes() == before


@pytest.mark.parametrize(
    ("company", "proposal", "field", "unsafe"),
    [
        ("Synthetic/Group Ltd", "Synthetic Group Facility", "company", "Synthetic/Group Ltd"),
        ("Synthetic Group Ltd", "Synthetic/Group:Facility", "proposal", "Synthetic/Group:Facility"),
    ],
)
def test_ca409_record_group_basis_path_errors_name_the_exact_component_and_preserve_bytes(
    tmp_path, company, proposal, field, unsafe
):
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, _ca409_survivor_basis())
    state_path_value = _ca409_seed_state(tmp_path, financials_source="analyst-supplied", marker="keep")
    before = state_path_value.read_bytes()

    with pytest.raises(ValueError) as caught:
        state_manager.record_group_basis(
            company, proposal, str(basis_file), perimeter_confirmed=True, base_dir=str(tmp_path),
        )

    expected = (
        f"{field} {unsafe!r} isn't safe to use as a filesystem path component "
        '(no \\ / : * ? " < > | characters, and not "." or "..").'
    )
    assert str(caught.value) == expected
    assert state_path_value.read_bytes() == before


def test_ca409_record_group_basis_rejects_bad_json_with_exact_utf8_diagnostic_and_no_write(tmp_path):
    basis_file = tmp_path / "bad-group-basis.json"
    basis_file.write_text("not-json", encoding="utf-8")
    state_path_value = _ca409_seed_state(tmp_path, financials_source="analyst-supplied", marker="keep")
    before = state_path_value.read_bytes()

    with pytest.raises(ValueError) as caught:
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )

    assert str(caught.value) == (
        f"Cannot read group-basis file {str(basis_file)!r} as UTF-8 JSON: "
        "Expecting value: line 1 column 1 (char 0)"
    )
    assert state_path_value.read_bytes() == before


def test_ca409_record_group_basis_oserror_keeps_the_exact_wrapped_message_and_no_write(tmp_path, monkeypatch):
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, _ca409_survivor_basis())
    state_path_value = _ca409_seed_state(tmp_path, financials_source="analyst-supplied", marker="keep")
    before = state_path_value.read_bytes()

    def fail_open(_path, *, encoding):
        assert encoding == "utf-8"
        raise OSError("synthetic read denial")

    monkeypatch.setattr(state_manager, "open", fail_open, raising=False)
    with pytest.raises(ValueError) as caught:
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )

    assert str(caught.value) == f"Cannot read group-basis file {str(basis_file)!r}: synthetic read denial"
    assert state_path_value.read_bytes() == before


def test_ca409_record_group_basis_utf8_reader_preserves_non_ascii_source_text(tmp_path, cp1252_default_open):
    basis = _ca409_survivor_basis()
    basis["source"]["document"] = "Comptes consolidés — café"
    basis_file = tmp_path / "group-basis-utf8.json"
    _ca409_write_group_basis_file(basis_file, basis, ensure_ascii=False)
    state_path_value = _ca409_seed_state(tmp_path, financials_source="framework-computed", marker="keep")

    result = state_manager.record_group_basis(
        "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
        perimeter_confirmed=True, date_str="2026-10-21", base_dir=str(tmp_path),
    )
    saved = state_manager.read_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), keys=("group_basis",),
    )
    assert result["recorded"] is True
    assert saved["group_basis"]["source"]["document"] == "Comptes consolidés — café"
    assert saved["marker"] == "keep"


def test_ca409_record_group_basis_rejects_non_object_json_exactly_and_preserves_bytes(tmp_path):
    basis_file = tmp_path / "group-basis-array.json"
    basis_file.write_text('["not", "an", "object"]', encoding="utf-8")
    state_path_value = _ca409_seed_state(tmp_path, financials_source="analyst-supplied", marker="keep")
    before = state_path_value.read_bytes()

    with pytest.raises(ValueError) as caught:
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )

    assert str(caught.value) == (
        f"Group-basis file {str(basis_file)!r} must contain a JSON object; nothing was changed."
    )
    assert state_path_value.read_bytes() == before


def test_ca409_record_group_basis_initial_validation_refusal_is_exact_and_byte_preserving(tmp_path):
    basis = _ca409_survivor_basis()
    basis["basis"] = "wrong"
    basis["reporting_entity"] = "   "
    basis_file = tmp_path / "invalid-group-basis.json"
    _ca409_write_group_basis_file(basis_file, basis)
    state_path_value = _ca409_seed_state(tmp_path, financials_source="framework-computed", marker="keep")
    before = state_path_value.read_bytes()

    with pytest.raises(ValueError) as caught:
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )

    assert str(caught.value) == (
        'Invalid group_basis: "group_basis.basis" must be "supplied_consolidated"; '
        '"group_basis.reporting_entity" must be a non-empty string. Nothing was changed.'
    )
    assert state_path_value.read_bytes() == before


@pytest.mark.parametrize(
    ("figures_basis", "financials_source", "expected_problem"),
    [
        (
            "supplied", "framework-computed",
            '"group_basis.figures_basis" is \'supplied\', but state financials_source is \'framework-computed\'; these must agree',
        ),
        (
            "recomputed", "analyst-supplied",
            '"group_basis.figures_basis" is \'recomputed\', but state financials_source is \'analyst-supplied\'; these must agree',
        ),
        (
            "supplied", None,
            '"group_basis.figures_basis" is \'supplied\', but state financials_source is not recorded '
            '(read as "framework-computed"); these must agree',
        ),
    ],
)
def test_ca409_record_group_basis_refuses_each_basis_source_disagreement_without_writing(
    tmp_path, figures_basis, financials_source, expected_problem
):
    basis = _ca409_survivor_basis()
    basis["figures_basis"] = figures_basis
    basis.pop("perimeter_confirmed")
    basis.pop("perimeter_confirmed_on")
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, basis)
    fields = {} if financials_source is None else {"financials_source": financials_source}
    state_path_value = _ca409_seed_state(tmp_path, marker="keep", **fields)
    before = state_path_value.read_bytes()

    with pytest.raises(ValueError) as caught:
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )

    assert str(caught.value) == _ca409_record_expected_problem(expected_problem)
    assert state_path_value.read_bytes() == before


@pytest.mark.parametrize(
    ("figures_basis", "financials_source"),
    [
        ("supplied", "analyst-supplied"),
        ("recomputed", "framework-computed"),
        ("recomputed", None),
    ],
)
def test_ca409_record_group_basis_accepts_each_agreeing_or_default_basis_source_without_changing_other_fields(
    tmp_path, figures_basis, financials_source
):
    basis = _ca409_survivor_basis()
    basis["figures_basis"] = figures_basis
    basis.pop("perimeter_confirmed")
    basis.pop("perimeter_confirmed_on")
    basis["future_metadata"] = {"keep": ["opaque", 4]}
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, basis)
    fields = {} if financials_source is None else {"financials_source": financials_source}
    state_path_value = _ca409_seed_state(
        tmp_path,
        marker="keep",
        financials={"FY-Current": {"ebitda": 12}},
        ratios={"FY-Current": {"dscr": 2.0}},
        multi_period_financials={"FY-Current": {"revenue": 100}},
        analyst_supplied_financials={"FY-Current": {"revenue": 101}},
        **fields,
    )
    with open(state_path_value, encoding="utf-8") as handle:
        before = json.load(handle)

    result = state_manager.record_group_basis(
        "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
        perimeter_confirmed=True, date_str="2026-10-21", base_dir=str(tmp_path),
    )
    with open(state_path_value, encoding="utf-8") as handle:
        after = json.load(handle)

    assert result == {
        "recorded": True,
        "state_path": str(state_path_value),
        "figures_basis": figures_basis,
    }
    assert set(after) == set(before) | {"group_basis"}
    assert {key: value for key, value in after.items() if key != "group_basis"} == before
    expected_basis = dict(basis)
    expected_basis["perimeter_confirmed"] = True
    expected_basis["perimeter_confirmed_on"] = "2026-10-21"
    assert after["group_basis"] == expected_basis


def test_ca409_record_group_basis_rejects_an_invalid_recorded_source_exactly_without_writing(tmp_path):
    basis = _ca409_survivor_basis()
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, basis)
    state_path_value = _ca409_seed_state(tmp_path, financials_source="unknown-source", marker="keep")
    before = state_path_value.read_bytes()

    with pytest.raises(StateShapeError) as caught:
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )

    assert str(caught.value) == (
        f'Cannot use {state_path_value}: "financials_source" must be "framework-computed" or '
        '"analyst-supplied", found \'unknown-source\'. Fix the file by hand or restore it from a backup; '
        "the file was not modified."
    )
    assert state_path_value.read_bytes() == before


def test_ca409_record_group_basis_validates_only_the_fields_it_consumes(tmp_path):
    basis = _ca409_survivor_basis()
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, basis)
    state_path_value = _ca409_seed_state(
        tmp_path,
        financials_source="framework-computed",
        collateral="intentionally malformed but unrelated",
        marker="keep",
    )

    result = state_manager.record_group_basis(
        "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
        perimeter_confirmed=True, date_str="2026-10-21", base_dir=str(tmp_path),
    )
    saved = state_manager.read_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), keys=("group_basis", "financials_source"),
    )
    assert result["recorded"] is True
    assert saved["collateral"] == "intentionally malformed but unrelated"
    assert saved["financials_source"] == "framework-computed"
    assert saved["group_basis"]["figures_basis"] == "recomputed"


def test_ca409_record_group_basis_rechecks_the_date_found_before_the_locked_read(tmp_path, monkeypatch):
    basis = _ca409_survivor_basis()
    basis["figures_basis"] = "supplied"
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, basis)
    old_path = _ca409_seed_state(tmp_path, financials_source="analyst-supplied", marker="old-state")
    newer_path = Path(state_manager.state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility",
        date_str="2026-02-01", base_dir=str(tmp_path),
    ))
    created = {"done": False, "newer_bytes": None}

    class CreateNewerStateOnLock:
        def __init__(self, _path):
            self.path = _path

        def __enter__(self):
            if not created["done"]:
                newer_path.parent.mkdir(parents=True, exist_ok=True)
                newer_state = {
                    "company": "Synthetic Group Ltd",
                    "proposal": "Synthetic Group Facility",
                    "date": "2026-02-01",
                    "schema_version": state_manager.SCHEMA_VERSION,
                    "financials_source": "framework-computed",
                    "marker": "newer-state",
                }
                with open(newer_path, "w", encoding="utf-8") as handle:
                    json.dump(newer_state, handle, indent=2)
                created["newer_bytes"] = newer_path.read_bytes()
                created["done"] = True
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    monkeypatch.setattr(state_manager, "_FileLock", CreateNewerStateOnLock)
    result = state_manager.record_group_basis(
        "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
        perimeter_confirmed=True, date_str="2026-10-21", base_dir=str(tmp_path),
    )
    assert result["recorded"] is True
    saved_old = state_manager.read_state(
        "Synthetic Group Ltd", "Synthetic Group Facility",
        date_str="2026-01-01", base_dir=str(tmp_path), keys=("group_basis",),
    )
    assert saved_old["date"] == "2026-01-01"
    assert saved_old["marker"] == "old-state"
    assert saved_old["group_basis"]["perimeter_confirmed_on"] == "2026-10-21"
    assert newer_path.read_bytes() == created["newer_bytes"]


def test_ca409_record_group_basis_success_preserves_the_selected_dated_folder(tmp_path):
    basis = _ca409_survivor_basis()
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, basis)
    state_path_value = _ca409_seed_state(
        tmp_path,
        financials_source="framework-computed",
        financials={"FY-Current": {"ebitda": 12}},
        marker="preserve",
    )
    with open(state_path_value, encoding="utf-8") as handle:
        before = json.load(handle)

    state_manager.record_group_basis(
        "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
        perimeter_confirmed=True, date_str="2026-10-21", base_dir=str(tmp_path),
    )
    with open(state_path_value, encoding="utf-8") as handle:
        after = json.load(handle)

    assert after["date"] == "2026-01-01"
    assert {key: value for key, value in after.items() if key != "group_basis"} == before
    assert after["group_basis"]["perimeter_confirmed"] is True
    assert after["group_basis"]["perimeter_confirmed_on"] == "2026-10-21"


def test_ca409_record_group_basis_refusal_when_locked_state_disappears_preserves_bytes(tmp_path, monkeypatch):
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, _ca409_survivor_basis())
    state_path_value = _ca409_seed_state(
        tmp_path, financials_source="analyst-supplied", marker="keep",
    )
    before = state_path_value.read_bytes()
    monkeypatch.setattr(state_manager, "read_state", lambda *args, **kwargs: None)

    with pytest.raises(ValueError) as caught:
        state_manager.record_group_basis(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )

    assert str(caught.value) == (
        f"Refusing to record group_basis: state.json at {str(state_path_value)!r} "
        "no longer exists; nothing was created."
    )
    assert state_path_value.read_bytes() == before


def test_record_group_basis_refuses_when_the_confirmation_argument_is_left_out_entirely(tmp_path):
    """Review CL12: the analyst-confirmation default is False; if it were True, leaving the argument out would
    record a perimeter nobody confirmed. Every earlier test passed the argument explicitly."""
    basis_file = tmp_path / "group-basis.json"
    _ca409_write_group_basis_file(basis_file, _ca409_survivor_basis())
    state_path_value = _ca409_seed_state(tmp_path, financials_source="analyst-supplied", marker="keep")
    before = state_path_value.read_bytes()

    with pytest.raises(ValueError, match="--perimeter-confirmed"):
        state_manager.record_group_basis("Synthetic Group Ltd", "Synthetic Group Facility", str(basis_file),
                                         base_dir=str(tmp_path))

    assert state_path_value.read_bytes() == before


# ---------------------------------------------------------------------------
# Issue #408: behavioural mutation survivors in fresh-review creation.
# ---------------------------------------------------------------------------

def test_408_create_review_folder_uses_same_directory_tmp_suffix_utf8_and_indent_two(
    tmp_path, monkeypatch,
):
    target_dir = tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2026-04-15"
    target_state_path = target_dir / "state.json"
    target_dir.parent.mkdir(parents=True)
    temp_calls = []
    real_mkstemp = state_manager.tempfile.mkstemp
    real_fdopen = state_manager.os.fdopen
    real_json_dump = state_manager.json.dump

    def recording_mkstemp(*args, **kwargs):
        fd, temp_path = real_mkstemp(*args, **kwargs)
        temp_calls.append({"dir": kwargs.get("dir"), "suffix": kwargs.get("suffix"), "path": temp_path})
        return fd, temp_path

    def cp1252_when_no_explicit_encoding(fd, mode="r", *args, **kwargs):
        if "b" not in mode and kwargs.get("encoding") is None:
            kwargs["encoding"] = "cp1252"
        return real_fdopen(fd, mode, *args, **kwargs)

    def dump_unicode(value, handle, **kwargs):
        kwargs["ensure_ascii"] = False
        return real_json_dump(value, handle, **kwargs)

    monkeypatch.setattr(state_manager.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(state_manager.os, "fdopen", cp1252_when_no_explicit_encoding)
    monkeypatch.setattr(state_manager.json, "dump", dump_unicode)

    state_manager._create_review_folder(
        str(target_dir),
        str(target_state_path),
        {"company": "Synthetic Co", "proposal": "Credit Facility", "note": "東京"},
    )

    assert len(temp_calls) == 1
    assert Path(temp_calls[0]["dir"]) == target_dir
    assert temp_calls[0]["suffix"] == ".tmp"
    temporary_path = Path(temp_calls[0]["path"])
    assert temporary_path.parent == target_dir
    assert temporary_path.suffix == ".tmp"
    assert not temporary_path.exists()
    assert target_state_path.read_text(encoding="utf-8") == (
        '{\n'
        '  "company": "Synthetic Co",\n'
        '  "proposal": "Credit Facility",\n'
        '  "note": "東京"\n'
        '}\n'
    )


def test_408_create_review_folder_reports_exact_manual_cleanup_message_when_cleanup_fails(
    tmp_path, monkeypatch,
):
    target_dir = tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2026-04-15"
    target_state_path = target_dir / "state.json"
    target_dir.parent.mkdir(parents=True)

    def failing_dump(*args, **kwargs):
        raise OSError("disk full")

    def failing_remove(*args, **kwargs):
        raise OSError("remove refused")

    def failing_rmdir(*args, **kwargs):
        raise OSError("rmdir refused")

    with monkeypatch.context() as patched:
        patched.setattr(state_manager.json, "dump", failing_dump)
        patched.setattr(state_manager.os, "remove", failing_remove)
        patched.setattr(state_manager.os, "rmdir", failing_rmdir)
        with pytest.raises(state_manager.StateError) as caught:
            state_manager._create_review_folder(
                str(target_dir), str(target_state_path),
                {"company": "Synthetic Co", "proposal": "Credit Facility"},
            )

    assert str(caught.value) == (
        f"Could not start the review: disk full; the new folder {target_dir} "
        "could not be removed and should be deleted by hand."
    )


@pytest.mark.parametrize(
    ("caller", "bad_field", "label", "bad_value"),
    [
        ("candidates", "company", "company", "Synthetic/Co"),
        ("candidates", "proposal", "proposal", "Synthetic/Facility"),
        ("start", "company", "company", "Synthetic/Co"),
        ("start", "proposal", "proposal", "Synthetic/Facility"),
    ],
)
def test_408_review_entry_points_keep_the_company_and_proposal_sanitizer_labels(
    tmp_path, caller, bad_field, label, bad_value,
):
    with pytest.raises(ValueError) as expected:
        state_manager.sanitize_path_component(bad_value, label)

    if caller == "candidates":
        company = bad_value if bad_field == "company" else "Synthetic Co"
        proposal = bad_value if bad_field == "proposal" else "Credit Facility"
        operation = lambda: state_manager.get_review_candidates(
            company, proposal, date_str="2026-04-15", base_dir=str(tmp_path),
        )
    else:
        company = bad_value if bad_field == "company" else "Synthetic Co"
        proposal = bad_value if bad_field == "proposal" else "Credit Facility"
        operation = lambda: state_manager.start_review(
            company, proposal, date_str="2026-04-15", base_dir=str(tmp_path),
        )

    with pytest.raises(ValueError) as caught:
        operation()
    assert str(caught.value) == str(expected.value)


@pytest.mark.parametrize(
    ("exception_text", "expected_reason"),
    [
        ("synthetic first line\nignored diagnostic detail", "synthetic first line"),
        ("", "ValueError"),
    ],
)
def test_408_review_candidates_keep_exact_skipped_reason_and_continue_to_older_candidates(
    tmp_path, monkeypatch, exception_text, expected_reason,
):
    _review_203_make_folder(tmp_path, "Credit Facility_2025-06-01", "{}")
    _review_203_make_folder(tmp_path, "Credit Facility_2024-06-01", "{}")

    def selective_read_state(company, proposal, date_str=None, base_dir=None, **kwargs):
        if date_str == "2025-06-01":
            raise ValueError(exception_text)
        return {
            "company": company,
            "proposal": proposal,
            "date": date_str,
        }

    monkeypatch.setattr(state_manager, "read_state", selective_read_state)
    report = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2026-04-15", base_dir=str(tmp_path),
    )

    assert report == {
        "company": "Synthetic Co",
        "proposal": "Credit Facility",
        "date": "2026-04-15",
        "candidates": [{
            "date": "2024-06-01",
            "state_path": str(
                tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2024-06-01" / "state.json"
            ),
        }],
        "default_prior_date": "2024-06-01",
        "skipped": [{
            "date": "2025-06-01",
            "reason": f"state.json cannot be read: {expected_reason}",
        }],
    }


@pytest.mark.parametrize(
    ("prior_dates", "options", "expected_message"),
    [
        (
            (),
            {},
            "Started a fresh review without a prior link because no eligible earlier review was found.",
        ),
        (
            ("2025-06-01",),
            {"no_prior": True},
            "Started a fresh review without a prior link by request (--no-prior).",
        ),
        (
            ("2025-06-01",),
            {},
            "Started a fresh review linked to the nearest earlier review dated 2025-06-01.",
        ),
        (
            ("2024-01-01", "2025-06-01"),
            {"prior_date": "2024-01-01"},
            "Started a fresh review linked to the explicitly selected prior review dated 2024-01-01.",
        ),
    ],
)
def test_408_start_review_returns_each_success_message_exactly(
    tmp_path, prior_dates, options, expected_message,
):
    (tmp_path / "deals" / "Synthetic Co").mkdir(parents=True)
    for prior_date in prior_dates:
        _review_203_write_state(tmp_path, prior_date)

    result = state_manager.start_review(
        "Synthetic Co",
        "Credit Facility",
        date_str="2026-04-15",
        base_dir=str(tmp_path),
        **options,
    )
    assert result["message"] == expected_message


def test_408_start_review_conflicting_prior_options_keep_the_exact_refusal_message(tmp_path):
    (tmp_path / "deals" / "Synthetic Co").mkdir(parents=True)
    with pytest.raises(state_manager.StateError) as caught:
        state_manager.start_review(
            "Synthetic Co", "Credit Facility", date_str="2026-04-15",
            prior_date="2025-01-01", no_prior=True, base_dir=str(tmp_path),
        )
    assert str(caught.value) == "--prior-date and --no-prior cannot be used together"


def test_408_require_known_company_keeps_the_full_refusal_tail(tmp_path):
    with pytest.raises(state_manager.StateError) as caught:
        state_manager._require_known_company("Synthetic Co", base_dir=str(tmp_path))
    assert str(caught.value) == (
        "Refusing to start a review: there is no deal folder for company 'Synthetic Co'; "
        "starting a review is for a deal that already has one (use /triage or /spread to "
        "start a new deal, or check the spelling); nothing was changed."
    )


def test_408_require_later_than_newest_keeps_the_full_refusal_tail(tmp_path):
    _review_203_write_state(tmp_path, "2025-06-01")
    with pytest.raises(state_manager.StateError) as caught:
        state_manager._require_later_than_newest(
            "Synthetic Co", "Credit Facility", "2025-06-01", base_dir=str(tmp_path),
        )
    assert str(caught.value) == (
        "Refusing to start review dated 2025-06-01: it must be strictly later than the newest "
        "existing state-bearing review (2025-06-01) for this exact company and proposal; "
        "nothing was changed."
    )


# ---------------------------------------------------------------------------
# Review fixes (CL10): the survivors a test can still kill. The older cleanup test makes remove and rmdir fail
# together, which hides each failure flag behind the other; these isolate them.
# ---------------------------------------------------------------------------

def _folder_that_fails_to_write(tmp_path):
    target_dir = tmp_path / "deals" / "Synthetic Co" / "Credit Facility_2026-04-15"
    target_dir.parent.mkdir(parents=True)
    return target_dir, target_dir / "state.json"


def _create_with_disk_full(monkeypatch, target_dir, target_state_path, **patches):
    def failing_dump(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patched:
        patched.setattr(state_manager.json, "dump", failing_dump)
        for name, replacement in patches.items():
            patched.setattr(state_manager.os, name, replacement)
        with pytest.raises(state_manager.StateError) as caught:
            state_manager._create_review_folder(
                str(target_dir), str(target_state_path), {"company": "Synthetic Co", "proposal": "Credit Facility"})
    return str(caught.value)


def test_a_clean_failure_says_the_created_folder_was_removed_and_leaves_nothing(tmp_path, monkeypatch):
    target_dir, target_state_path = _folder_that_fails_to_write(tmp_path)

    message = _create_with_disk_full(monkeypatch, target_dir, target_state_path)

    assert message == ("Could not start the review: disk full; "
                       "the folder this call created was removed and nothing was changed.")
    assert not target_dir.exists()


def test_a_leftover_that_cannot_be_removed_is_reported_even_when_the_folder_removal_is_pretended_to_work(
    tmp_path, monkeypatch,
):
    target_dir, target_state_path = _folder_that_fails_to_write(tmp_path)

    def refused(*args, **kwargs):
        raise OSError("remove refused")

    message = _create_with_disk_full(monkeypatch, target_dir, target_state_path, remove=refused,
                                     rmdir=lambda *args, **kwargs: None)

    assert message == (f"Could not start the review: disk full; the new folder {target_dir} "
                       "could not be removed and should be deleted by hand.")


def test_a_folder_that_cannot_be_removed_is_reported_even_when_every_leftover_file_was_removed(tmp_path, monkeypatch):
    target_dir, target_state_path = _folder_that_fails_to_write(tmp_path)

    def refused(*args, **kwargs):
        raise OSError("rmdir refused")

    message = _create_with_disk_full(monkeypatch, target_dir, target_state_path, rmdir=refused)

    assert message == (f"Could not start the review: disk full; the new folder {target_dir} "
                       "could not be removed and should be deleted by hand.")
    assert list(target_dir.iterdir()) == []


def test_review_candidates_use_the_given_base_dir_for_the_known_company_check(tmp_path, monkeypatch):
    base, elsewhere = tmp_path / "base", tmp_path / "elsewhere"
    elsewhere.mkdir()
    write_state("Synthetic Co", "Credit Facility", date_str="2026-01-01", base_dir=str(base), marker=1)
    monkeypatch.chdir(elsewhere)

    report = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2026-05-01", base_dir=str(base))

    assert [c["date"] for c in report["candidates"]] == ["2026-01-01"]


def test_a_candidate_whose_state_vanishes_is_left_out_and_the_older_ones_are_still_offered(tmp_path, monkeypatch):
    for date_str in ("2026-01-01", "2026-02-01", "2026-03-01"):
        write_state("Synthetic Co", "Credit Facility", date_str=date_str, base_dir=str(tmp_path), marker=date_str)
    real_read_state = state_manager.read_state

    def vanishing_middle(company, proposal, date_str=None, **kwargs):
        return None if date_str == "2026-02-01" else real_read_state(company, proposal, date_str=date_str, **kwargs)

    monkeypatch.setattr(state_manager, "read_state", vanishing_middle)

    report = state_manager.get_review_candidates(
        "Synthetic Co", "Credit Facility", date_str="2026-05-01", base_dir=str(tmp_path))

    assert [c["date"] for c in report["candidates"]] == ["2026-03-01", "2026-01-01"]
    assert report["default_prior_date"] == "2026-03-01" and report["skipped"] == []


def test_start_review_takes_its_lock_on_the_proposal_start_file_in_the_company_folder(tmp_path, monkeypatch):
    write_state("Synthetic Co", "Credit Facility", date_str="2026-01-01", base_dir=str(tmp_path), marker=1)
    taken = []

    class RecordingLock:
        def __init__(self, path, timeout=None):
            taken.append((path, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(state_manager, "_FileLock", RecordingLock)

    state_manager.start_review("Synthetic Co", "Credit Facility", date_str="2026-05-01", no_prior=True,
                               base_dir=str(tmp_path))

    assert taken == [(os.path.join(str(tmp_path), "deals", "Synthetic Co", "Credit Facility.start"),
                      state_manager.LOCK_TIMEOUT_SECONDS)]


# ---------------------------------------------------------------------------
# Issue #472 / #473: entity record step 1 (recording and validation only).
# ---------------------------------------------------------------------------

def _entity_record_for_tests(entity_id="synthetic-parent", role="parent"):
    return {
        "entity_id": entity_id,
        "legal_name": f"Synthetic {entity_id} Ltd",
        "role": role,
        "source": {
            "document": f"Synthetic accounts for {entity_id}",
            "accounting_basis": "UK GAAP",
            "date": "2026-02-01",
        },
        "period_ends": {"FY-Current": "2025-12-31"},
        "perimeter": {
            "included": True,
            "control_basis": "analyst-confirmed",
            "scope_rationale": "Synthetic test perimeter, explicitly confirmed",
            "confirmed": True,
            "confirmed_on": "2026-10-27",
        },
        "raw_figures_source": "Synthetic source document",
        "multi_period_financials": {"FY-Current": {"revenue": 100}},
        "analyst_supplied_financials": {},
        "spread_status": "not-spread",
        "intercompany_lines": [],
        "guarantee_ids": [],
        "future_metadata": {"preserve": True},
    }


def _seed_entity_registry_state(tmp_path, entity_records=None, guarantees=None):
    write_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), financials_source="framework-computed",
        financials={"FY-Current": {"ebitda": 50}},
        ratios={"FY-Current": {"dscr": 1.5}},
        multi_period_financials={"FY-Current": {"revenue": 100}},
        analyst_supplied_financials={}, guarantees=[] if guarantees is None else guarantees,
        marker="keep",
    )
    path = Path(state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path),
    ))
    if entity_records is not None:
        state = json.loads(path.read_text(encoding="utf-8"))
        state["entity_records"] = entity_records
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def _register_entity_record_for_tests(tmp_path, record, *, perimeter_confirmed=True, date_str="2026-10-27"):
    record_file = tmp_path / "entity-record.json"
    record_file.write_text(json.dumps(record, allow_nan=True), encoding="utf-8")
    return state_manager.record_entity_record(
        "Synthetic Group Ltd", "Synthetic Group Facility", str(record_file),
        perimeter_confirmed=perimeter_confirmed, date_str=date_str, base_dir=str(tmp_path),
    )


def test_entity_records_are_optional_on_legacy_state_and_reading_does_not_migrate_it(tmp_path):
    path = Path(state_path(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01", base_dir=str(tmp_path),
    ))
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "company": "Synthetic Group Ltd", "proposal": "Synthetic Group Facility",
        "date": "2026-01-01", "schema_version": "1.1.0", "marker": "legacy",
    }), encoding="utf-8")
    before = path.read_bytes()

    loaded = read_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), keys=("entity_records",),
    )

    assert loaded is not None
    assert loaded["schema_version"] == "1.1.0"
    assert "entity_records" not in loaded
    assert path.read_bytes() == before


def test_validate_state_recognises_the_entity_records_shape():
    parent = _entity_record_for_tests()
    state = {"entity_records": {"synthetic-parent": parent}, "guarantees": []}
    assert state_manager.validate_state(state, keys=("entity_records",)) is state


def test_record_entity_record_round_trips_a_shared_registry_and_preserves_unknown_fields(tmp_path):
    guarantees = [{"guarantee_id": "GUARANTEE-SYN-001", "provider": "Synthetic Parent Ltd"}]
    path = _seed_entity_registry_state(tmp_path, guarantees=guarantees)
    original = json.loads(path.read_text(encoding="utf-8"))

    parent = _entity_record_for_tests()
    _register_entity_record_for_tests(tmp_path, parent)

    subsidiary = _entity_record_for_tests("synthetic-opco", "subsidiary")
    subsidiary["multi_period_financials"] = {"FY-Current": {"revenue": 200, "loan_notes": 25}}
    subsidiary["intercompany_lines"] = [{
        "period": "FY-Current", "line_key": "loan_notes",
        "counterparty_entity_id": "synthetic-parent", "direction": "receivable",
        "amount": 25, "confirmed": True,
    }]
    subsidiary["guarantee_ids"] = ["GUARANTEE-SYN-001"]
    _register_entity_record_for_tests(tmp_path, subsidiary)

    saved = read_state(
        "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
        base_dir=str(tmp_path), keys=("entity_records", "guarantees"),
    )
    assert saved is not None
    assert saved["schema_version"] == "1.2.0"
    assert set(saved["entity_records"]) == {"synthetic-parent", "synthetic-opco"}
    assert saved["entity_records"]["synthetic-opco"]["entity_id"] == "synthetic-opco"
    assert saved["entity_records"]["synthetic-opco"]["intercompany_lines"][0]["counterparty_entity_id"] == "synthetic-parent"
    assert saved["entity_records"]["synthetic-opco"]["guarantee_ids"] == ["GUARANTEE-SYN-001"]
    assert saved["entity_records"]["synthetic-opco"]["perimeter"]["confirmed"] is True
    assert saved["entity_records"]["synthetic-opco"]["perimeter"]["confirmed_on"] == "2026-10-27"
    assert saved["entity_records"]["synthetic-opco"]["future_metadata"] == {"preserve": True}
    assert saved["financials"] == original["financials"]
    assert saved["ratios"] == original["ratios"]
    assert saved["multi_period_financials"] == original["multi_period_financials"]
    assert saved["analyst_supplied_financials"] == original["analyst_supplied_financials"]
    assert saved["financials_source"] == original["financials_source"]
    assert saved["marker"] == "keep"
    assert saved["guarantees"] == guarantees


def test_record_entity_record_requires_explicit_perimeter_confirmation_and_preserves_bytes(tmp_path):
    path = _seed_entity_registry_state(tmp_path)
    before = path.read_bytes()
    record = _entity_record_for_tests()

    with pytest.raises(ValueError) as caught:
        _register_entity_record_for_tests(tmp_path, record, perimeter_confirmed=False)

    assert str(caught.value) == (
        "Refusing to register entity_record without explicit --perimeter-confirmed analyst confirmation; "
        "nothing was changed."
    )
    assert path.read_bytes() == before


def test_record_entity_record_refuses_a_missing_deal_without_creating_one(tmp_path):
    record_file = tmp_path / "entity-record.json"
    record_file.write_text(json.dumps(_entity_record_for_tests()), encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        state_manager.record_entity_record(
            "Synthetic Group Ltd", "Synthetic Group Facility", str(record_file),
            perimeter_confirmed=True, base_dir=str(tmp_path),
        )

    assert str(caught.value) == (
        "Refusing to record entity_record: no exact existing state.json for company 'Synthetic Group Ltd' "
        "and proposal 'Synthetic Group Facility'; no deal was created."
    )
    assert not (tmp_path / "deals").exists()


def test_record_entity_record_refuses_duplicate_id_without_replacing_existing_record(tmp_path):
    parent = _entity_record_for_tests()
    path = _seed_entity_registry_state(tmp_path, entity_records={"synthetic-parent": parent})
    before = path.read_bytes()

    with pytest.raises(ValueError) as caught:
        _register_entity_record_for_tests(tmp_path, parent)

    assert str(caught.value) == (
        "Refusing to register entity_record 'synthetic-parent': entity ID already exists; nothing was changed."
    )
    assert path.read_bytes() == before


def test_record_entity_record_refuses_a_mismatched_existing_registry_key_without_writing(tmp_path):
    malformed = _entity_record_for_tests()
    malformed["entity_id"] = "another-id"
    path = _seed_entity_registry_state(tmp_path, entity_records={"synthetic-parent": malformed})
    before = path.read_bytes()

    with pytest.raises(StateShapeError, match="entity_id.*registry key"):
        _register_entity_record_for_tests(tmp_path, _entity_record_for_tests("synthetic-opco", "subsidiary"))

    assert path.read_bytes() == before


def test_record_entity_record_validates_its_own_period_map_and_preserves_bytes(tmp_path):
    path = _seed_entity_registry_state(tmp_path, entity_records={"synthetic-parent": _entity_record_for_tests()})
    before = path.read_bytes()
    record = _entity_record_for_tests("synthetic-opco", "subsidiary")
    record["multi_period_financials"] = {"synthetic-opco": {"FY-Current": {"revenue": 200}}}

    with pytest.raises(ValueError) as caught:
        _register_entity_record_for_tests(tmp_path, record)

    assert str(caught.value) == (
        "Refusing to register entity_record: entity_records['synthetic-opco'].multi_period_financials: "
        "unexpected period key 'synthetic-opco'; expected one of FY-2, FY-1, FY-Current, FY+1, FY+2, FY+3. "
        "Nothing was changed."
    )
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("bad_date_path", "bad_date", "problem"),
    [
        ("source", "2026-02-30", "source.date"),
        ("period", "2026-02-30", "period_ends"),
    ],
)
def test_record_entity_record_refuses_impossible_dates_without_writing(tmp_path, bad_date_path, bad_date, problem):
    path = _seed_entity_registry_state(tmp_path)
    before = path.read_bytes()
    record = _entity_record_for_tests("synthetic-opco", "subsidiary")
    if bad_date_path == "source":
        record["source"]["date"] = bad_date
    else:
        record["period_ends"]["FY-Current"] = bad_date

    with pytest.raises(ValueError, match=problem):
        _register_entity_record_for_tests(tmp_path, record)

    assert path.read_bytes() == before


def test_record_entity_record_refuses_period_end_mismatch_across_registry_without_writing(tmp_path):
    path = _seed_entity_registry_state(tmp_path, entity_records={"synthetic-parent": _entity_record_for_tests()})
    before = path.read_bytes()
    record = _entity_record_for_tests("synthetic-opco", "subsidiary")
    record["period_ends"]["FY-Current"] = "2025-11-30"

    with pytest.raises(ValueError, match="period.*FY-Current.*2025-12-31.*2025-11-30"):
        _register_entity_record_for_tests(tmp_path, record)

    assert path.read_bytes() == before


def test_record_entity_record_refuses_unknown_counterparty_without_writing(tmp_path):
    path = _seed_entity_registry_state(tmp_path, entity_records={"synthetic-parent": _entity_record_for_tests()})
    before = path.read_bytes()
    record = _entity_record_for_tests("synthetic-opco", "subsidiary")
    record["intercompany_lines"] = [{
        "period": "FY-Current", "line_key": "loan_notes", "counterparty_entity_id": "missing-entity",
        "direction": "receivable", "amount": 10, "confirmed": True,
    }]

    with pytest.raises(ValueError, match="counterparty_entity_id.*missing-entity"):
        _register_entity_record_for_tests(tmp_path, record)

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "guarantees",
    [
        [],
        [{"provider": "Synthetic Parent Ltd"}],
        [
            {"guarantee_id": "GUARANTEE-SYN-001"},
            {"guarantee_id": "GUARANTEE-SYN-001"},
        ],
    ],
    ids=["missing-id", "provider-only-is-not-a-link", "duplicate-explicit-id"],
)
def test_record_entity_record_requires_a_unique_explicit_guarantee_id_without_writing(tmp_path, guarantees):
    path = _seed_entity_registry_state(tmp_path, guarantees=guarantees)
    before = path.read_bytes()
    record = _entity_record_for_tests("synthetic-parent", "parent")
    record["guarantee_ids"] = ["GUARANTEE-SYN-001"]

    with pytest.raises(ValueError, match="guarantee_ids.*GUARANTEE-SYN-001"):
        _register_entity_record_for_tests(tmp_path, record)

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "invalid_value",
    [True, False, float("nan"), float("inf"), float("-inf"), "100", [], {}],
    ids=["true", "false", "nan", "positive-infinity", "negative-infinity", "string", "list", "object"],
)
def test_record_entity_record_rejects_invalid_raw_line_numbers_without_writing(tmp_path, invalid_value):
    path = _seed_entity_registry_state(tmp_path)
    before = path.read_bytes()
    record = _entity_record_for_tests("synthetic-opco", "subsidiary")
    record["multi_period_financials"]["FY-Current"]["revenue"] = invalid_value

    with pytest.raises(ValueError, match="revenue"):
        _register_entity_record_for_tests(tmp_path, record)

    assert path.read_bytes() == before


def test_record_entity_record_rejects_figures_in_both_raw_stores_for_one_period_without_writing(tmp_path):
    path = _seed_entity_registry_state(tmp_path)
    before = path.read_bytes()
    record = _entity_record_for_tests("synthetic-opco", "subsidiary")
    record["analyst_supplied_financials"] = {"FY-Current": {"revenue": 100}}

    with pytest.raises(ValueError, match="both raw stores.*FY-Current"):
        _register_entity_record_for_tests(tmp_path, record)

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "invalid_amount",
    [True, False, float("nan"), float("inf"), float("-inf"), "10", None, [], {}],
    ids=["true", "false", "nan", "positive-infinity", "negative-infinity", "string", "null", "list", "object"],
)
def test_record_entity_record_rejects_invalid_intercompany_amount_without_writing(tmp_path, invalid_amount):
    path = _seed_entity_registry_state(tmp_path, entity_records={"synthetic-parent": _entity_record_for_tests()})
    before = path.read_bytes()
    record = _entity_record_for_tests("synthetic-opco", "subsidiary")
    record["multi_period_financials"] = {"FY-Current": {"revenue": 200}}
    record["intercompany_lines"] = [{
        "period": "FY-Current", "line_key": "loan_notes", "counterparty_entity_id": "synthetic-parent",
        "direction": "receivable", "amount": invalid_amount, "confirmed": True,
    }]

    with pytest.raises(ValueError, match="intercompany_lines.*amount"):
        _register_entity_record_for_tests(tmp_path, record)

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("perimeter_patch", "problem"),
    [
        ({"confirmed": False}, "perimeter.confirmed"),
        ({"confirmed": True, "confirmed_on": None}, "perimeter.confirmed_on"),
        ({"confirmed": True}, "perimeter.confirmed_on"),
    ],
    ids=["false-confirmation", "null-confirmation-date", "missing-confirmation-date"],
)
def test_validate_state_rejects_unconfirmed_or_undated_persisted_entity_without_writing(
    tmp_path, perimeter_patch, problem,
):
    record = _entity_record_for_tests()
    record["perimeter"].update(perimeter_patch)
    if perimeter_patch == {"confirmed": True}:
        record["perimeter"].pop("confirmed_on")
    path = _seed_entity_registry_state(tmp_path, entity_records={"synthetic-parent": record})
    before = path.read_bytes()

    with pytest.raises(StateShapeError, match=problem):
        read_state(
            "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
            base_dir=str(tmp_path), keys=("entity_records",),
        )

    assert path.read_bytes() == before


def test_validate_state_rejects_a_non_unique_guarantee_link_in_a_persisted_entity(tmp_path):
    record = _entity_record_for_tests()
    record["guarantee_ids"] = ["GUARANTEE-SYN-001"]
    path = _seed_entity_registry_state(
        tmp_path, entity_records={"synthetic-parent": record},
        guarantees=[{"guarantee_id": "GUARANTEE-SYN-001"}, {"guarantee_id": "GUARANTEE-SYN-001"}],
    )
    before = path.read_bytes()

    with pytest.raises(StateShapeError, match="guarantee_ids.*GUARANTEE-SYN-001"):
        read_state(
            "Synthetic Group Ltd", "Synthetic Group Facility", date_str="2026-01-01",
            base_dir=str(tmp_path), keys=("entity_records",),
        )

    assert path.read_bytes() == before
