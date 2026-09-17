import os

import docx
import pytest

from research_export import export_research_brief
from state_manager import write_state


def test_creates_dated_output_folder_when_none_exists_yet(tmp_path):
    base = str(tmp_path)

    output_path = export_research_brief(
        "Acme Corp", "Fleet Loan", "# Research Brief", date_str="2026-01-15", base_dir=base,
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

    output_path = export_research_brief("Acme Corp", "Fleet Loan", "# Research Brief", base_dir=base)

    assert "Fleet Loan_2025-06-01" in output_path
    assert os.path.isfile(output_path)


def test_exports_readable_docx_content(tmp_path):
    base = str(tmp_path)

    output_path = export_research_brief(
        "Acme Corp", "Fleet Loan",
        "# Acme Corp Research Brief\n\n## Go/No-Go\n\nGO -- long trading history.",
        date_str="2026-01-15", base_dir=base,
    )

    doc = docx.Document(output_path)
    assert doc.paragraphs[0].text == "Acme Corp Research Brief"


def test_rejects_unsafe_company(tmp_path):
    base = str(tmp_path)
    with pytest.raises(ValueError):
        export_research_brief("C:\\Windows\\Temp\\evil", "Fleet Loan", "# Brief", base_dir=base)


def test_rejects_unsafe_proposal(tmp_path):
    base = str(tmp_path)
    with pytest.raises(ValueError):
        export_research_brief("Acme Corp", "../../evil", "# Brief", base_dir=base)


def test_rejects_unsafe_date_str(tmp_path):
    base = str(tmp_path)
    with pytest.raises(ValueError):
        export_research_brief("Acme Corp", "Fleet Loan", "# Brief", date_str="../../evil", base_dir=base)
