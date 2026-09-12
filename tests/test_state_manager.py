import json
import os
from datetime import datetime

from deal_export import export_deal
from state_manager import append_review_trail, read_state, state_path, write_state


def test_read_state_returns_none_when_no_file_exists(tmp_path):
    assert read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path)) is None


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
