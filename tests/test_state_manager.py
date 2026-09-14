import json
import os
import threading
import time
import pytest
from datetime import datetime

from deal_export import export_deal
from state_manager import (
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    _FileLock,
    append_review_trail,
    read_state,
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

def test_write_state_sets_schema_version_unconditionally(tmp_path):
    base = str(tmp_path)
    state = write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base)
    assert state["schema_version"] == SCHEMA_VERSION == "1.1.0"


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
    with open(os.path.join(base, "templates", "cam", "asset_finance_cam.md"), "w") as f:
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


def test_write_state_leaves_no_stray_temp_file_behind(tmp_path):
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base, deal_type="asset_finance")

    deal_dir = os.path.dirname(state_path("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base))
    entries = os.listdir(deal_dir)
    assert entries == ["state.json"]
