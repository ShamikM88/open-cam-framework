import json
import os

import docx
import pytest

import research_export
from research_export import export_research_brief
from state_manager import read_state, write_state


VALID_BRIEF = (
    "# Synthetic Research Brief\n\n## Go/No-Go\n\nGO -- synthetic example.\n\n"
    "## Sources\n\n- Synthetic registry record: https://example.invalid/registry\n"
)
NO_SOURCES_BRIEF = (
    "# Synthetic Research Brief\n\n## Go/No-Go\n\n"
    "Synthetic notes only; no sources list is present.\n"
)


def test_creates_dated_output_folder_when_none_exists_yet(tmp_path):
    base = str(tmp_path)
    output_path = export_research_brief(
        "Acme Corp", "Fleet Loan", VALID_BRIEF, date_str="2026-01-15", base_dir=base,
    )
    expected_dir = os.path.join(base, "deals", "Acme Corp", "Fleet Loan_2026-01-15")
    assert output_path == os.path.join(expected_dir, "Acme Corp_Fleet Loan_Research_Brief.docx")
    assert os.path.isfile(output_path)


def test_reuses_an_existing_dated_folder_when_date_str_not_given(tmp_path):
    """The brief belongs alongside whatever state.json /research already wrote for this deal."""
    base = str(tmp_path)
    write_state("Acme Corp", "Fleet Loan", date_str="2025-06-01", base_dir=base, triage={"go_no_go": "GO"})
    output_path = export_research_brief("Acme Corp", "Fleet Loan", VALID_BRIEF, base_dir=base)
    assert "Fleet Loan_2025-06-01" in output_path
    assert os.path.isfile(output_path)


def test_exports_readable_docx_content(tmp_path):
    output_path = export_research_brief(
        "Acme Corp", "Fleet Loan", VALID_BRIEF, date_str="2026-01-15", base_dir=str(tmp_path),
    )
    doc = docx.Document(output_path)
    assert doc.paragraphs[0].text == "Synthetic Research Brief"


def test_missing_sources_is_rejected_before_output(tmp_path):
    with pytest.raises(ValueError, match="populated Sources or References section"):
        export_research_brief("Acme Co", "Synthetic Lease", NO_SOURCES_BRIEF, base_dir=str(tmp_path))
    assert not (tmp_path / "deals").exists()


def test_analyst_override_allows_missing_sources_and_adds_final_footnote(tmp_path, monkeypatch):
    monkeypatch.setattr(research_export, "_today_date", lambda: "2026-10-14")
    output_path = export_research_brief(
        "Acme Co", "Synthetic Lease", NO_SOURCES_BRIEF, base_dir=str(tmp_path),
        analyst_override_no_sources="  analyst explicitly\n authorised omission  ",
    )
    doc = docx.Document(output_path)
    assert doc.paragraphs[-1].text == (
        "Footnote: No sources list is included in this brief. Exported under analyst override on "
        "2026-10-14: analyst explicitly authorised omission."
    )


@pytest.mark.parametrize("reason", ["", "   ", "\n \t"])
def test_empty_source_override_reason_is_rejected_before_writing(tmp_path, reason):
    with pytest.raises(ValueError, match="reason must not be empty"):
        export_research_brief(
            "Acme Co", "Synthetic Lease", NO_SOURCES_BRIEF, base_dir=str(tmp_path),
            analyst_override_no_sources=reason,
        )
    assert not (tmp_path / "deals").exists()


def test_sources_heading_inside_fenced_code_does_not_satisfy_source_gate(tmp_path):
    brief = (
        "# Synthetic brief\n\n```markdown\n## Sources\n- Fake: https://example.invalid/fake\n```\n"
        "\n## Findings\nSynthetic content.\n"
    )
    with pytest.raises(ValueError, match="populated Sources or References section"):
        export_research_brief("Acme Co", "Synthetic Lease", brief, base_dir=str(tmp_path))
    assert not (tmp_path / "deals").exists()


def _state_with_trail(base_dir, trail, date_str="2026-10-14"):
    return write_state(
        "Acme Co", "Synthetic Lease", date_str=date_str, base_dir=str(base_dir), review_trail=trail,
    )


def test_unapproved_release_discloses_trailing_rejection_count_near_document_top_and_records_state(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(research_export, "_today_date", lambda: "2026-10-14")
    trail = [
        {"iteration": 1, "verdict": "REJECTED", "notes": "old finding"},
        {"iteration": 2, "verdict": "APPROVED", "notes": "previous review cycle"},
        {"iteration": 3, "verdict": "REJECTED", "notes": "synthetic finding one"},
        {"iteration": 4, "verdict": "REJECTED", "notes": "synthetic finding two"},
        {"iteration": 5, "verdict": "REJECTED", "notes": "synthetic finding three"},
    ]
    _state_with_trail(tmp_path, trail)
    output_path = export_research_brief(
        "Acme Co", "Synthetic Lease", VALID_BRIEF, date_str="2026-10-14", base_dir=str(tmp_path),
        analyst_release_unapproved="  analyst requested\n manual review ",
    )
    doc = docx.Document(output_path)
    assert doc.paragraphs[0].text == "Synthetic Research Brief"
    assert doc.paragraphs[1].text == (
        "Review status: Not approved by the independent Risk Reviewer. 3 review passes returned REJECTED. "
        "Released for manual analyst review at the analyst's request on 2026-10-14: analyst requested manual review."
    )
    state = read_state("Acme Co", "Synthetic Lease", date_str="2026-10-14", base_dir=str(tmp_path))
    assert state["review_stopped_by_analyst"] == {
        "passes": 3, "date": "2026-10-14", "reason": "analyst requested manual review",
    }
    assert state["review_trail"] == trail


@pytest.mark.parametrize("reason", ["", "   ", "\n \t"])
def test_unapproved_release_requires_nonempty_reason_before_writing(tmp_path, reason):
    with pytest.raises(ValueError, match="Analyst release reason must not be empty"):
        export_research_brief(
            "Acme Co", "Synthetic Lease", VALID_BRIEF, base_dir=str(tmp_path),
            analyst_release_unapproved=reason,
        )
    assert not (tmp_path / "deals").exists()


def test_unapproved_release_state_is_written_only_after_docx_export(tmp_path, monkeypatch):
    monkeypatch.setattr(research_export, "_today_date", lambda: "2026-10-14")
    _state_with_trail(tmp_path, [{"verdict": "REJECTED", "notes": "synthetic finding"}])
    real_export = research_export.export_to_docx
    observed = {}

    def assert_export_then_return(markdown, output_path):
        real_export(markdown, output_path)
        observed["docx_exists"] = os.path.isfile(output_path)
        state = read_state("Acme Co", "Synthetic Lease", date_str="2026-10-14", base_dir=str(tmp_path))
        observed["release_record_present_during_export"] = "review_stopped_by_analyst" in state

    monkeypatch.setattr(research_export, "export_to_docx", assert_export_then_return)
    export_research_brief(
        "Acme Co", "Synthetic Lease", VALID_BRIEF, date_str="2026-10-14", base_dir=str(tmp_path),
        analyst_release_unapproved="explicit synthetic release",
    )
    assert observed == {"docx_exists": True, "release_record_present_during_export": False}
    state = read_state("Acme Co", "Synthetic Lease", date_str="2026-10-14", base_dir=str(tmp_path))
    assert state["review_stopped_by_analyst"]["passes"] == 1


def test_source_override_and_unapproved_release_combine_status_near_top_and_sources_footnote_last(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(research_export, "_today_date", lambda: "2026-10-14")
    _state_with_trail(tmp_path, [
        {"verdict": "REJECTED", "notes": "synthetic finding one"},
        {"verdict": "REJECTED", "notes": "synthetic finding two"},
        {"verdict": "REJECTED", "notes": "synthetic finding three"},
    ])
    output_path = export_research_brief(
        "Acme Co", "Synthetic Lease", NO_SOURCES_BRIEF, date_str="2026-10-14", base_dir=str(tmp_path),
        analyst_override_no_sources="analyst authorised missing source list",
        analyst_release_unapproved="analyst explicitly stopped review",
    )
    doc = docx.Document(output_path)
    assert doc.paragraphs[0].text == "Synthetic Research Brief"
    assert doc.paragraphs[1].text.startswith(
        "Review status: Not approved by the independent Risk Reviewer. 3 review passes returned REJECTED."
    )
    assert doc.paragraphs[-1].text == (
        "Footnote: No sources list is included in this brief. Exported under analyst override on "
        "2026-10-14: analyst authorised missing source list."
    )


def test_research_command_contract_pins_three_pass_checkpoint_options_and_explicit_consent():
    from pathlib import Path

    command = (Path(__file__).resolve().parents[1] / ".claude" / "commands" / "research.md").read_text(
        encoding="utf-8"
    )
    body = " ".join(command.split())
    required = (
        "three consecutive REJECTED",
        "review_trail on disk",
        "AskUserQuestion",
        "Run another review pass",
        "Stop and export for manual review (unapproved, disclosed)",
        "Stop without exporting",
        "Silence, an ambiguous answer, or \"carry on\" is never consent to export",
        "Never pass the flag on your own initiative",
        "--analyst-release-unapproved",
    )
    for phrase in required:
        assert phrase in body, phrase
    assert body.index("Run another review pass") < body.index("Stop and export for manual review") < body.index(
        "Stop without exporting"
    )


@pytest.mark.parametrize(("phrase", "replacement"), [
    ("Run another review pass", "run again maybe"),
    ("Stop and export for manual review (unapproved, disclosed)", "export whatever"),
    ("Stop without exporting", "export anyway"),
    ('Silence, an ambiguous answer, or "carry on" is never consent to export', "silence means yes"),
    ("Never pass the flag on your own initiative", "use the flag whenever helpful"),
    ("review_trail on disk", "conversation history"),
])
def test_research_command_contract_rejects_weakened_checkpoint_rules(phrase, replacement):
    from pathlib import Path

    command = (Path(__file__).resolve().parents[1] / ".claude" / "commands" / "research.md").read_text(
        encoding="utf-8"
    )
    changed = command.replace(phrase, replacement)
    body = " ".join(changed.split())
    assert phrase not in body


def test_trailing_rejection_counter_stops_at_latest_non_rejected_entry():
    assert research_export._trailing_rejected_count([
        {"verdict": "REJECTED"}, {"verdict": "APPROVED"},
        {"verdict": "REJECTED"}, {"verdict": "REJECTED"},
    ]) == 2
    assert research_export._trailing_rejected_count([{"verdict": "APPROVED"}]) == 0
