import os

import docx
import pytest

import research_export
from research_export import export_research_brief
from state_manager import write_state


VALID_BRIEF = (
    "# Synthetic Research Brief\n\n## Go/No-Go\n\nGO -- synthetic example.\n\n"
    "## Sources\n\n- Synthetic registry record: https://example.invalid/registry\n"
)
NO_SOURCES_BRIEF = (
    "# Synthetic Research Brief\n\n## Go/No-Go\n\n"
    "The word sources in this paragraph is not a sources section.\n"
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
    """The brief belongs alongside whatever state.json /research (or
    /triage + /commercial) already wrote for this deal -- it must not
    create a second, differently-dated folder of its own."""
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


def test_missing_sources_section_is_rejected_before_creating_any_output(tmp_path):
    with pytest.raises(ValueError, match="populated Sources or References section"):
        export_research_brief(
            "Acme Co", "Synthetic Lease", NO_SOURCES_BRIEF, date_str="2026-01-15", base_dir=str(tmp_path),
        )
    assert not (tmp_path / "deals").exists()


def test_empty_sources_section_is_rejected_before_creating_any_output(tmp_path):
    brief = "# Synthetic brief\n\n## Sources\n\n## Findings\n\nNo source list here."
    with pytest.raises(ValueError, match="populated Sources or References section"):
        export_research_brief("Acme Co", "Synthetic Lease", brief, date_str="2026-01-15", base_dir=str(tmp_path))
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("heading", ["# SOURCES", "### rEfErEnCeS ###", "###### Sources  ####"])
def test_populated_sources_heading_is_case_insensitive_any_level_and_allows_closing_hashes(tmp_path, heading):
    brief = f"# Synthetic brief\n\n{heading}\n\n1) Synthetic reference\n"
    output = export_research_brief("Acme Co", "Synthetic Lease", brief, date_str="2026-01-15",
                                   base_dir=str(tmp_path))
    assert os.path.isfile(output)


def test_sources_section_can_be_populated_by_a_url_without_a_list_item(tmp_path):
    brief = "# Synthetic brief\n\n## References\n\nhttps://example.invalid/source\n"
    output = export_research_brief("Acme Co", "Synthetic Lease", brief, date_str="2026-01-15",
                                   base_dir=str(tmp_path))
    assert os.path.isfile(output)


def test_word_sources_in_a_paragraph_does_not_count_as_a_section(tmp_path):
    with pytest.raises(ValueError, match="populated Sources or References section"):
        export_research_brief("Acme Co", "Synthetic Lease", NO_SOURCES_BRIEF, base_dir=str(tmp_path))
    assert not (tmp_path / "deals").exists()


def test_sources_headings_and_entries_inside_fenced_code_are_ignored(tmp_path):
    brief = (
        "# Synthetic brief\n\n```markdown\n## Sources\n- Fake source: https://example.invalid/fake\n```\n"
        "\n## Findings\nNo real source section.\n"
    )
    with pytest.raises(ValueError, match="populated Sources or References section"):
        export_research_brief("Acme Co", "Synthetic Lease", brief, base_dir=str(tmp_path))
    assert not (tmp_path / "deals").exists()


def test_sources_section_stops_at_next_heading_of_same_or_higher_level(tmp_path):
    brief = "# Synthetic brief\n\n## Sources\n\n### Still in this section\n\n## Findings\n- Not a source entry\n"
    with pytest.raises(ValueError, match="populated Sources or References section"):
        export_research_brief("Acme Co", "Synthetic Lease", brief, base_dir=str(tmp_path))
    assert not (tmp_path / "deals").exists()


def test_override_adds_the_disclosed_footnote_as_the_last_docx_paragraph(tmp_path, monkeypatch):
    monkeypatch.setattr(research_export, "_today_date", lambda: "2026-10-14")
    reason = "  analyst  explicitly\n authorised\t omission  "
    output = export_research_brief(
        "Acme Co", "Synthetic Lease", NO_SOURCES_BRIEF, date_str="2026-01-15", base_dir=str(tmp_path),
        analyst_override_no_sources=reason,
    )
    doc = docx.Document(output)
    assert doc.paragraphs[-1].text == (
        "Footnote: No sources list is included in this brief. Exported under analyst override on "
        "2026-10-14: analyst explicitly authorised omission."
    )


@pytest.mark.parametrize("reason", ["", "   ", "\n \t"])
def test_override_with_empty_or_whitespace_reason_is_rejected_before_writing(tmp_path, reason):
    with pytest.raises(ValueError, match="reason must not be empty"):
        export_research_brief(
            "Acme Co", "Synthetic Lease", NO_SOURCES_BRIEF, base_dir=str(tmp_path),
            analyst_override_no_sources=reason,
        )
    assert not (tmp_path / "deals").exists()


def test_override_reason_is_required_even_when_sources_exist(tmp_path):
    with pytest.raises(ValueError, match="reason must not be empty"):
        export_research_brief(
            "Acme Co", "Synthetic Lease", VALID_BRIEF, base_dir=str(tmp_path),
            analyst_override_no_sources="  ",
        )
    assert not (tmp_path / "deals").exists()


def test_override_has_no_effect_when_sources_are_already_populated(tmp_path, monkeypatch):
    monkeypatch.setattr(research_export, "_today_date", lambda: "2026-10-14")
    output = export_research_brief(
        "Acme Co", "Synthetic Lease", VALID_BRIEF, base_dir=str(tmp_path),
        analyst_override_no_sources="Explicitly authorised synthetic exception",
    )
    doc = docx.Document(output)
    assert not any(p.text.startswith("Footnote: No sources list") for p in doc.paragraphs)


@pytest.mark.parametrize(
    "company,proposal,date_str",
    [
        ("C:\\\\Windows\\\\Temp\\\\evil", "Fleet Loan", None),
        ("Acme Corp", "../../evil", None),
        ("Acme Corp", "Fleet Loan", "../../evil"),
    ],
)
def test_rejects_unsafe_path_components(company, proposal, date_str, tmp_path):
    with pytest.raises(ValueError):
        export_research_brief(company, proposal, VALID_BRIEF, date_str=date_str, base_dir=str(tmp_path))
