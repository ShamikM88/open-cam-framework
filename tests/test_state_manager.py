import json
import os

from deal_export import export_deal
from state_manager import read_state, state_path, write_state


def test_read_state_returns_none_when_no_file_exists(tmp_path):
    assert read_state("Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=str(tmp_path)) is None


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
