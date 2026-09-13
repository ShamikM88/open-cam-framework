import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH

from docx_builder import export_to_docx


def _build(tmp_path, markdown_text):
    out = tmp_path / "out.docx"
    export_to_docx(markdown_text, str(out))
    return docx.Document(str(out))


def test_headings(tmp_path):
    doc = _build(tmp_path, "# Title\n## Subtitle\n")
    assert doc.paragraphs[0].text == "Title"
    assert doc.paragraphs[0].style.name == "Heading 1"
    assert doc.paragraphs[1].text == "Subtitle"
    assert doc.paragraphs[1].style.name == "Heading 2"


def test_bullet_list(tmp_path):
    doc = _build(tmp_path, "- First\n- Second\n")
    assert [p.text for p in doc.paragraphs] == ["First", "Second"]
    assert doc.paragraphs[0].style.name == "List Bullet"


def test_plain_paragraph(tmp_path):
    doc = _build(tmp_path, "Just a sentence.\n")
    assert doc.paragraphs[0].text == "Just a sentence."


def test_blank_lines_are_skipped(tmp_path):
    doc = _build(tmp_path, "First.\n\n\nSecond.\n")
    assert [p.text for p in doc.paragraphs] == ["First.", "Second."]


MARKDOWN_TABLE = (
    "| Name | Amount | Notes |\n"
    "| :--- | ---: | :---: |\n"
    "| Alpha | 100 | ok |\n"
    "| Beta | 200 | **flag** |\n"
)


def test_table_is_rendered_as_a_real_table(tmp_path):
    doc = _build(tmp_path, MARKDOWN_TABLE)
    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert len(table.rows) == 3  # header + 2 body rows
    assert len(table.columns) == 3
    assert [c.text for c in table.rows[0].cells] == ["Name", "Amount", "Notes"]
    assert [c.text for c in table.rows[1].cells] == ["Alpha", "100", "ok"]
    # inline "**bold**" markers are stripped from cell text
    assert [c.text for c in table.rows[2].cells] == ["Beta", "200", "flag"]


def test_table_header_is_bold_and_body_is_not(tmp_path):
    doc = _build(tmp_path, MARKDOWN_TABLE)
    table = doc.tables[0]
    header_run = table.rows[0].cells[0].paragraphs[0].runs[0]
    body_run = table.rows[1].cells[0].paragraphs[0].runs[0]
    assert header_run.bold is True
    assert body_run.bold in (False, None)


def test_table_column_alignment_follows_separator_row(tmp_path):
    doc = _build(tmp_path, MARKDOWN_TABLE)
    header_cells = doc.tables[0].rows[0].cells
    assert header_cells[0].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert header_cells[1].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert header_cells[2].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_content_after_table_continues_as_a_paragraph(tmp_path):
    doc = _build(tmp_path, MARKDOWN_TABLE + "After the table.\n")
    assert len(doc.tables) == 1
    assert doc.paragraphs[-1].text == "After the table."


def test_ragged_table_rows_do_not_crash(tmp_path):
    markdown = (
        "| A | B |\n"
        "| --- | --- |\n"
        "| 1 |\n"          # missing a column -> padded with a blank cell
        "| 2 | 3 | 4 |\n"  # extra column -> silently dropped
    )
    doc = _build(tmp_path, markdown)
    table = doc.tables[0]
    assert [c.text for c in table.rows[1].cells] == ["1", ""]
    assert [c.text for c in table.rows[2].cells] == ["2", "3"]


def test_multiple_tables_in_one_document(tmp_path):
    markdown = MARKDOWN_TABLE + "\nSome text.\n\n" + MARKDOWN_TABLE
    doc = _build(tmp_path, markdown)
    assert len(doc.tables) == 2


def test_table_style_has_visible_borders(tmp_path):
    doc = _build(tmp_path, MARKDOWN_TABLE)
    assert doc.tables[0].style.name == "Table Grid"


# ---------------------------------------------------------------------------
# Inline markdown (bold/italic/code) in headings-adjacent body text, H3, and
# stripping content that has no place in a client-facing Word document (a
# horizontal rule, or a fenced code block like the Underwriter's trailing
# structured JSON block).
# ---------------------------------------------------------------------------

def test_h3_heading(tmp_path):
    doc = _build(tmp_path, "### Sub-subtitle\n")
    assert doc.paragraphs[0].text == "Sub-subtitle"
    assert doc.paragraphs[0].style.name == "Heading 3"


def test_bold_in_plain_paragraph_renders_as_a_bold_run(tmp_path):
    doc = _build(tmp_path, "**Verdict:** Approve.\n")
    paragraph = doc.paragraphs[0]
    assert paragraph.text == "Verdict: Approve."
    assert paragraph.runs[0].text == "Verdict:"
    assert paragraph.runs[0].bold is True
    assert paragraph.runs[1].bold in (False, None)


def test_italic_in_plain_paragraph_renders_as_an_italic_run(tmp_path):
    doc = _build(tmp_path, "*A note in italics.*\n")
    paragraph = doc.paragraphs[0]
    assert paragraph.text == "A note in italics."
    assert paragraph.runs[0].italic is True


def test_bold_in_bullet_list_renders_as_a_bold_run(tmp_path):
    doc = _build(tmp_path, "- **Label:** detail\n")
    paragraph = doc.paragraphs[0]
    assert paragraph.style.name == "List Bullet"
    assert paragraph.text == "Label: detail"
    assert paragraph.runs[0].bold is True


def test_horizontal_rule_is_skipped(tmp_path):
    doc = _build(tmp_path, "First.\n\n---\n\nSecond.\n")
    assert [p.text for p in doc.paragraphs] == ["First.", "Second."]


def test_fenced_code_block_is_skipped_entirely(tmp_path):
    markdown = (
        "Before the block.\n"
        "```json\n"
        '{"verdict": "APPROVED", "notes": null}\n'
        "```\n"
        "After the block.\n"
    )
    doc = _build(tmp_path, markdown)
    assert [p.text for p in doc.paragraphs] == ["Before the block.", "After the block."]
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "verdict" not in full_text
    assert "```" not in full_text
