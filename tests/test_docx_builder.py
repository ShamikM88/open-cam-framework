import struct
import zlib
from pathlib import Path

import pytest
import docx
import docx_builder
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from docx_builder import export_to_docx


def _build(tmp_path, markdown_text):
    out = tmp_path / "out.docx"
    export_to_docx(markdown_text, str(out))
    return docx.Document(str(out))


def _write_png(path, width, height):
    """A real, minimal solid-colour PNG generated in-test, so the repo
    carries no binary fixtures."""
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return str(path)


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


def test_xml_invalid_controls_are_replaced_in_prose_tables_and_link_targets(tmp_path, capsys):
    markdown = (
        "Revenue\x0c grew.\n\n"
        "| Metric | Value |\n"
        "| --- | --- |\n"
        "| Loss\x00 marker | 1.5x |\n\n"
        "See [source](https://synthetic.invalid/path\x0b).\n"
    )
    doc = _build(tmp_path, markdown)

    assert doc.paragraphs[0].text == "Revenue\uFFFD grew."
    assert [cell.text for cell in doc.tables[0].rows[1].cells] == ["Loss\uFFFD marker", "1.5x"]
    rel_targets = [
        rel.target_ref for rel in doc.part.rels.values()
        if rel.reltype.endswith("/hyperlink")
    ]
    assert len(rel_targets) == 1
    assert "\uFFFD" in rel_targets[0]
    warning = capsys.readouterr().err
    assert "Replaced 3 XML-invalid character(s)" in warning


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


def test_ragged_table_rows_preserve_extra_cells_and_warn(tmp_path, capsys):
    markdown = (
        "| A | B |\n"
        "| --- | --- |\n"
        "| 1 |\n"          # missing a column -> padded with a blank cell
        "| 2 | 3 | 4 |\n"  # extra column -> preserved by widening the table
    )
    doc = _build(tmp_path, markdown)
    table = doc.tables[0]
    assert len(table.columns) == 3
    assert [c.text for c in table.rows[1].cells] == ["1", "", ""]
    assert [c.text for c in table.rows[2].cells] == ["2", "3", "4"]
    assert "body row 2 has 3 cells but its header has 2" in capsys.readouterr().err


def test_escaped_pipe_stays_inside_one_table_cell(tmp_path):
    doc = _build(tmp_path, "| Metric | Value |\n| --- | --- |\n| A \\| B split | 1.5x |\n")
    assert [cell.text for cell in doc.tables[0].rows[1].cells] == ["A | B split", "1.5x"]


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


def test_h4_to_h6_headings_render_as_heading_3_without_literal_hashes(tmp_path):
    # Issue #110: python-docx tops out at Heading 3, so deeper levels are
    # clamped to it -- but must never leak "####" into the document text.
    doc = _build(tmp_path, "#### Four\n\n##### Five\n\n###### Six\n")
    assert [p.text for p in doc.paragraphs] == ["Four", "Five", "Six"]
    assert all(p.style.name == "Heading 3" for p in doc.paragraphs)


def test_h4_heading_ends_a_wrapped_paragraph(tmp_path):
    doc = _build(tmp_path, "Intro line one\nintro line two\n#### Sub-point\nBody text\n")
    assert [p.text for p in doc.paragraphs] == [
        "Intro line one intro line two", "Sub-point", "Body text",
    ]
    assert doc.paragraphs[1].style.name == "Heading 3"


def test_seven_hashes_or_no_space_is_not_a_heading(tmp_path):
    doc = _build(tmp_path, "####### Too deep\n\n#hashtag\n")
    assert [p.text for p in doc.paragraphs] == ["####### Too deep", "#hashtag"]
    assert not any(p.style.name.startswith("Heading") for p in doc.paragraphs)


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


@pytest.mark.parametrize("marker", ["---", "***", "___"])
def test_horizontal_rule_is_skipped(tmp_path, marker):
    doc = _build(tmp_path, f"First.\n\n{marker}\n\nSecond.\n")
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


def test_non_json_fenced_block_renders_as_a_monospace_block(tmp_path):
    """Regression guard for issue #113: a fenced block *not* tagged `json`
    (e.g. a Unicode box-drawing ownership tree) must actually render in the
    document -- one paragraph per line, in Consolas, with whitespace
    (indentation) preserved exactly -- rather than being silently skipped
    the way the Underwriter's trailing ```json structured-output block is."""
    markdown = (
        "Before the tree.\n"
        "```\n"
        "Borrower Ltd (UK)\n"
        "└── Parent Holdings Ltd (UK) — 100%\n"
        "    └── Ultimate Parent SA (FR) — 100%\n"
        "```\n"
        "After the tree.\n"
    )
    doc = _build(tmp_path, markdown)
    texts = [p.text for p in doc.paragraphs]
    assert texts == [
        "Before the tree.",
        "Borrower Ltd (UK)",
        "└── Parent Holdings Ltd (UK) — 100%",
        "    └── Ultimate Parent SA (FR) — 100%",
        "After the tree.",
    ]
    # The three tree lines are paragraphs 1-3 (0 = "Before the tree.").
    for paragraph in doc.paragraphs[1:4]:
        assert paragraph.runs[0].font.name == "Consolas"


def test_monospace_block_lines_are_not_joined_like_a_normal_paragraph(tmp_path):
    """Each line of a non-json fenced block is its own paragraph -- unlike
    plain prose, consecutive lines must never be soft-wrap-joined into one
    paragraph, since that would destroy a tree diagram's line structure."""
    markdown = "```\nLine one\nLine two\nLine three\n```\n"
    doc = _build(tmp_path, markdown)
    assert [p.text for p in doc.paragraphs] == ["Line one", "Line two", "Line three"]


def test_json_tagged_fence_is_still_skipped_entirely(tmp_path):
    """Explicit regression guard distinguishing the two fence behaviors:
    only the ```json tag triggers the skip-for-export behavior; anything
    else renders. Uses an untagged fence immediately before a json-tagged
    one to confirm the tag check, not just fence position, is what decides
    this."""
    markdown = (
        "```\n"
        "RENDER ME\n"
        "```\n"
        "```json\n"
        '{"verdict": "APPROVED"}\n'
        "```\n"
    )
    doc = _build(tmp_path, markdown)
    texts = [p.text for p in doc.paragraphs]
    assert texts == ["RENDER ME"]


def test_unterminated_untagged_fence_does_not_swallow_the_trailing_json_block(tmp_path):
    """Regression guard (found in review of the #113 work): an untagged fence
    whose closing ``` was forgotten must not be "closed" by the opening line
    of the Underwriter's trailing ```json block -- that would render the
    narrative in between as monospace and leak the structured-output JSON
    body into the client-facing document. A tagged fence line can only open
    a block, never close one."""
    markdown = (
        "Intro.\n"
        "```\n"
        "[Borrower] (UK)\n"
        "Narrative that must stay normal prose.\n"
        "```json\n"
        '{"cp_ids_included": []}\n'
        "```\n"
    )
    doc = _build(tmp_path, markdown)
    texts = [p.text for p in doc.paragraphs]
    assert not any("cp_ids_included" in t for t in texts)
    narrative = next(p for p in doc.paragraphs if "Narrative that must stay normal prose." in p.text)
    assert narrative.runs[0].font.name != "Consolas"


def test_unterminated_fence_does_not_discard_the_rest_of_the_document(tmp_path):
    """A stray/odd ``` (e.g. from truncation) must not silently swallow
    every line through EOF -- only a genuinely closed fence gets skipped."""
    markdown = (
        "Before.\n"
        "```json\n"
        '{"unterminated": true\n'
        "## Section 2\n"
        "Real narrative content that must survive.\n"
    )
    doc = _build(tmp_path, markdown)
    texts = [p.text for p in doc.paragraphs]
    assert "Before." in texts
    assert "Section 2" in texts
    assert "Real narrative content that must survive." in texts


def test_footnote_style_trailing_asterisk_is_not_treated_as_italic(tmp_path):
    """A single `*` used as a footnote marker (common in financial
    narrative, e.g. "Net Income* is 5.2x... Note 1*") must not be
    misread as an emphasis delimiter -- that would silently eat both
    asterisks and italicize unrelated text in between."""
    doc = _build(tmp_path, "Net Income* is 5.2x Interest Expense, per Note 1*.\n")
    paragraph = doc.paragraphs[0]
    assert paragraph.text == "Net Income* is 5.2x Interest Expense, per Note 1*."
    assert all(not r.italic for r in paragraph.runs)


def test_triple_asterisk_renders_as_a_single_bold_italic_run(tmp_path):
    doc = _build(tmp_path, "***Critical:*** breach detected\n")
    paragraph = doc.paragraphs[0]
    assert paragraph.text == "Critical: breach detected"
    assert paragraph.runs[0].text == "Critical:"
    assert paragraph.runs[0].bold is True
    assert paragraph.runs[0].italic is True
    assert "*" not in paragraph.text


# ---------------------------------------------------------------------------
# Soft-wrapped source lines must join into one continuous Word paragraph
# (matching normal Markdown semantics -- a single newline is a soft wrap,
# only a blank line or a new block starts a fresh paragraph), not fragment
# into one Word paragraph per source line.
# ---------------------------------------------------------------------------

def test_wrapped_plain_paragraph_joins_into_one_paragraph(tmp_path):
    markdown = "This sentence was wrapped\nacross three separate\nsource lines.\n"
    doc = _build(tmp_path, markdown)
    assert len(doc.paragraphs) == 1
    assert doc.paragraphs[0].text == "This sentence was wrapped across three separate source lines."


def test_blank_line_still_separates_wrapped_paragraphs(tmp_path):
    markdown = "First para line one\nfirst para line two.\n\nSecond para line one\nsecond para line two.\n"
    doc = _build(tmp_path, markdown)
    assert [p.text for p in doc.paragraphs] == [
        "First para line one first para line two.",
        "Second para line one second para line two.",
    ]


def test_heading_ends_a_wrapped_paragraph(tmp_path):
    markdown = "Body text that wraps\nonto a second line.\n## Next Section\n"
    doc = _build(tmp_path, markdown)
    assert doc.paragraphs[0].text == "Body text that wraps onto a second line."
    assert doc.paragraphs[1].text == "Next Section"
    assert doc.paragraphs[1].style.name == "Heading 2"


def test_wrapped_bullet_item_joins_into_one_bullet(tmp_path):
    markdown = "- A bullet whose text wraps\n  onto a continuation line.\n"
    doc = _build(tmp_path, markdown)
    assert len(doc.paragraphs) == 1
    assert doc.paragraphs[0].text == "A bullet whose text wraps onto a continuation line."
    assert doc.paragraphs[0].style.name == "List Bullet"


def test_two_wrapped_bullets_stay_separate(tmp_path):
    markdown = "- First bullet wraps\n  onto a second line.\n- Second bullet, single line.\n"
    doc = _build(tmp_path, markdown)
    assert [p.text for p in doc.paragraphs] == [
        "First bullet wraps onto a second line.",
        "Second bullet, single line.",
    ]


def test_numbered_list_items_are_not_merged_together(tmp_path):
    """A numbered-list marker ('1. ', '2. ', ...) must still end the
    previous item's paragraph even though it isn't a bullet/heading/table
    row -- otherwise every item in the list collapses into one paragraph."""
    markdown = (
        "1. First item, short.\n"
        "2. Second item wraps\n"
        "   onto a continuation line.\n"
        "3. Third item, short.\n"
    )
    doc = _build(tmp_path, markdown)
    assert [p.text for p in doc.paragraphs] == [
        "1. First item, short.",
        "2. Second item wraps onto a continuation line.",
        "3. Third item, short.",
    ]


def test_table_still_ends_a_preceding_wrapped_paragraph(tmp_path):
    markdown = "Intro text that wraps\nonto a second line.\n" + MARKDOWN_TABLE
    doc = _build(tmp_path, markdown)
    assert doc.paragraphs[0].text == "Intro text that wraps onto a second line."
    assert len(doc.tables) == 1


# ---------------------------------------------------------------------------
# Markdown links ([text](url)) must render as real, clickable Word
# hyperlinks -- not pass through as literal bracket/paren text.
# ---------------------------------------------------------------------------

def _hyperlink_targets(doc, paragraph):
    """The external relationship URL(s) referenced by every w:hyperlink
    element in `paragraph`, resolved via the document part's relationship
    table (python-docx has no high-level API for reading hyperlinks back)."""
    rels = paragraph.part.rels
    return [
        rels[el.get(qn("r:id"))].target_ref
        for el in paragraph._p.findall(qn("w:hyperlink"))
    ]


def test_markdown_link_renders_as_a_real_hyperlink(tmp_path):
    doc = _build(tmp_path, "See [Companies House](https://example.com/company/123) for details.\n")
    paragraph = doc.paragraphs[0]
    assert paragraph.text == "See Companies House for details."
    assert "[" not in paragraph.text and "(" not in paragraph.text
    assert _hyperlink_targets(doc, paragraph) == ["https://example.com/company/123"]


def test_markdown_link_in_a_bullet_item_renders_as_a_hyperlink(tmp_path):
    doc = _build(tmp_path, "- [Source A](https://example.com/a)\n- [Source B](https://example.com/b)\n")
    assert [p.text for p in doc.paragraphs] == ["Source A", "Source B"]
    assert _hyperlink_targets(doc, doc.paragraphs[0]) == ["https://example.com/a"]
    assert _hyperlink_targets(doc, doc.paragraphs[1]) == ["https://example.com/b"]


def test_multiple_links_in_one_paragraph(tmp_path):
    doc = _build(tmp_path, "See [A](https://example.com/a) and [B](https://example.com/b).\n")
    paragraph = doc.paragraphs[0]
    assert paragraph.text == "See A and B."
    assert _hyperlink_targets(doc, paragraph) == ["https://example.com/a", "https://example.com/b"]


def test_mailto_link_renders_as_a_real_hyperlink(tmp_path):
    doc = _build(tmp_path, "Contact [the team](mailto:credit@example.invalid).\n")
    assert _hyperlink_targets(doc, doc.paragraphs[0]) == ["mailto:credit@example.invalid"]


@pytest.mark.parametrize("target", [
    "file://synthetic-host/share/extract.pdf",
    r"\\synthetic-host\share\annex.docx",
    "ms-word:ofe|u|https://synthetic.invalid/x.docx",
    "javascript:synthetic.invalid",
    "http://[::1",  # urlsplit raises ValueError for an unterminated IPv6 literal
])
def test_unsupported_hyperlink_targets_are_plain_text_with_warning(tmp_path, capsys, target):
    doc = _build(tmp_path, f"See [source]({target}).\n")
    paragraph = doc.paragraphs[0]

    assert paragraph.text == f"See source ({target})."
    assert _hyperlink_targets(doc, paragraph) == []
    assert target in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Image embedding (issue #114): a standalone `![alt](path)` line becomes an
# embedded picture. Before this, such a line rendered as a stray "!" plus a
# hyperlink pointing at a file path.
# ---------------------------------------------------------------------------

def test_image_line_embeds_a_picture_with_caption_and_alt_text(tmp_path):
    png = _write_png(tmp_path / "chart.png", 40, 20)
    doc = _build(tmp_path, f"![Sector growth projection]({png})\n")

    assert len(doc.inline_shapes) == 1
    descr = doc.inline_shapes[0]._inline.docPr.get("descr")
    assert descr == "Sector growth projection"
    texts = [p.text for p in doc.paragraphs]
    assert "Sector growth projection" in texts
    assert not any(t.startswith("!") for t in texts)
    caption = next(p for p in doc.paragraphs if p.text == "Sector growth projection")
    assert caption.runs[0].italic is True


def test_image_with_empty_alt_text_has_no_caption(tmp_path):
    png = _write_png(tmp_path / "chart.png", 40, 20)
    doc = _build(tmp_path, f"![]({png})\n")

    assert len(doc.inline_shapes) == 1
    assert [p.text for p in doc.paragraphs] == [""]  # only the picture's own paragraph


def test_wide_image_is_scaled_down_to_the_page_text_width_keeping_aspect_ratio(tmp_path):
    png = _write_png(tmp_path / "wide.png", 2000, 100)
    doc = _build(tmp_path, f"![wide]({png})\n")

    section = doc.sections[0]
    max_width = section.page_width - section.left_margin - section.right_margin
    shape = doc.inline_shapes[0]
    assert shape.width <= max_width
    assert abs(shape.width / shape.height - 20) < 0.5  # 2000x100 -> 20:1 preserved


def test_small_image_is_never_scaled_up(tmp_path):
    png = _write_png(tmp_path / "small.png", 40, 20)
    doc = _build(tmp_path, f"![small]({png})\n")

    native = docx.Document()
    expected = native.add_picture(png).width
    assert doc.inline_shapes[0].width == expected


def test_image_line_ends_a_wrapped_paragraph(tmp_path):
    png = _write_png(tmp_path / "chart.png", 40, 20)
    doc = _build(tmp_path, f"First line of prose\n![fig]({png})\nSecond bit of prose\n")

    texts = [p.text for p in doc.paragraphs]
    assert "First line of prose" in texts
    assert "Second bit of prose" in texts
    assert len(doc.inline_shapes) == 1


def test_missing_image_file_becomes_a_visible_placeholder_and_a_warning(tmp_path, capsys):
    doc = _build(tmp_path, f"![Stock chart]({tmp_path / 'nope.png'})\nAfter.\n")

    assert len(doc.inline_shapes) == 0
    texts = [p.text for p in doc.paragraphs]
    assert any("Image not embedded: Stock chart" in t and "file not found" in t for t in texts)
    assert "After." in texts  # the rest of the document still exports
    assert "file not found" in capsys.readouterr().err


def test_remote_url_is_not_fetched_and_becomes_a_placeholder(tmp_path):
    doc = _build(tmp_path, "![Chart](https://example.com/chart.png)\n")

    assert len(doc.inline_shapes) == 0
    assert any("remote URLs aren't supported" in p.text for p in doc.paragraphs)


def test_unsupported_image_extension_becomes_a_placeholder(tmp_path):
    svg = tmp_path / "tree.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    doc = _build(tmp_path, f"![Tree]({svg})\n")

    assert len(doc.inline_shapes) == 0
    assert any("not a supported image type" in p.text for p in doc.paragraphs)


def test_corrupt_image_file_does_not_crash_the_export(tmp_path):
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"this is not a png")
    doc = _build(tmp_path, f"![Broken]({bad})\nStill here.\n")

    assert len(doc.inline_shapes) == 0
    texts = [p.text for p in doc.paragraphs]
    assert any("could not be read as an image" in t for t in texts)
    assert "Still here." in texts
    assert "" not in texts  # no stray empty paragraph left behind by the failed insert
    assert not any(t.endswith("()]") for t in texts)  # the reason is never an empty "()"


# ---------------------------------------------------------------------------
# Unsupported Markdown constructs are deliberately characterized rather than
# silently implying that the exporter supports the full Markdown language.
# Issue #277 documents this narrower subset; the exporter itself is unchanged.
# ---------------------------------------------------------------------------

def test_star_bullets_are_characterized_as_plain_text(tmp_path):
    doc = _build(tmp_path, "* Star bullet one\n* Star bullet two\n")

    assert len(doc.paragraphs) == 1
    assert doc.paragraphs[0].text == "* Star bullet one * Star bullet two"
    assert doc.paragraphs[0].style.name == "Normal"


def test_nested_bullets_are_characterized_as_merged_parent_text(tmp_path):
    doc = _build(tmp_path, "- Top level\n  - Nested level\n")

    assert len(doc.paragraphs) == 1
    assert doc.paragraphs[0].text == "Top level - Nested level"
    assert doc.paragraphs[0].style.name == "List Bullet"


def test_inline_markdown_in_heading_is_characterized_as_literal_text(tmp_path):
    doc = _build(tmp_path, "## Risk **Mitigants**\n")

    assert len(doc.paragraphs) == 1
    assert doc.paragraphs[0].text == "Risk **Mitigants**"
    assert doc.paragraphs[0].style.name == "Heading 2"


# ---------------------------------------------------------------------------
# Issue #408: behaviour-focused checks for surviving docx_builder mutants.
# All inputs and paths here are synthetic; Word output is reopened before its
# observable text, styles, formatting, OOXML hyperlinks, or table layout is
# asserted.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("codepoint", "kept"),
    [
        (0x08, False),
        (0x09, True),
        (0x0A, True),
        (0x0B, False),
        (0x0C, False),
        (0x0D, True),
        (0x0E, False),
        (0x1F, False),
        (0x20, True),
        (0x21, True),
        (0xD7FE, True),
        (0xD7FF, True),
        (0xD800, False),
        (0xDFFE, False),
        (0xDFFF, False),
        (0xE000, True),
        (0xE001, True),
        (0xFFFC, True),
        (0xFFFD, True),
        (0xFFFE, False),
        (0xFFFF, False),
        (0x10000, True),
        (0x10001, True),
        (0x10FFFE, True),
        (0x10FFFF, True),
    ],
)
def test_408_xml_sanitizer_keeps_and_replaces_each_range_boundary(
    codepoint, kept, capsys
):
    character = chr(codepoint)
    expected = character if kept else "\uFFFD"

    assert docx_builder._sanitize_xml_invalid_characters(character) == expected

    warning = (
        ""
        if kept
        else "[docx_builder] Replaced 1 XML-invalid character(s) with "
        "U+FFFD before export.\n"
    )
    assert capsys.readouterr().err == warning


def test_408_split_row_preserves_unpadded_cell_edges_in_saved_docx(tmp_path):
    markdown = "|Name|Amount|\n|---|---:|\n|Alpha|42|\n"
    doc = _build(tmp_path, markdown)

    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert [cell.text for cell in table.rows[0].cells] == ["Name", "Amount"]
    assert [cell.text for cell in table.rows[1].cells] == ["Alpha", "42"]


def test_408_split_row_keeps_an_escaped_pipe_at_the_end_of_a_cell():
    assert docx_builder._split_row("|A\\||") == ["A|"]


def test_408_split_row_does_not_index_past_a_terminal_backslash():
    assert docx_builder._split_row("|tail\\|") == ["tail\\"]


def test_408_closing_fence_can_be_the_line_immediately_after_opening():
    assert docx_builder._find_closing_fence(["```", "```"], 0) == 1


def test_408_empty_list_is_not_a_table_separator_row():
    assert docx_builder._is_separator_row([]) is False
    assert docx_builder._is_separator_row(["---", ":---:"]) is True


def test_408_hyperlink_run_keeps_style_bold_target_and_boundary_spaces(
    tmp_path,
):
    url = "https://example.invalid/path"
    doc = _build(tmp_path, f"| [ link ]({url}) |\n| --- |\n")
    cell = doc.tables[0].rows[0].cells[0]
    paragraph = cell.paragraphs[0]

    assert paragraph.text == " link "
    assert paragraph.style.name == "Normal"
    hyperlink = paragraph._p.findall(qn("w:hyperlink"))[0]
    rel_id = hyperlink.get(qn("r:id"))
    assert doc.part.rels[rel_id].target_ref == url

    hyperlink_run = hyperlink.find(qn("w:r"))
    assert hyperlink_run is not None
    run_properties = hyperlink_run.find(qn("w:rPr"))
    assert run_properties is not None
    style = run_properties.find(qn("w:rStyle"))
    assert style is not None
    assert style.get(qn("w:val")) == "Hyperlink"
    assert run_properties.find(qn("w:b")) is not None
    text_element = hyperlink_run.find(qn("w:t"))
    assert text_element is not None
    assert text_element.text == " link "
    assert text_element.get(qn("xml:space")) == "preserve"


def test_408_inline_formats_keep_header_bold_and_code_font_after_readback(
    tmp_path, capsys
):
    url = "ftp://example.invalid/resource"
    markdown = (
        "|plain **bold** *italic* `code` [linked](https://example.invalid/ok) "
        f"[unsupported]({url})|\n| --- |\n"
    )
    doc = _build(tmp_path, markdown)
    paragraph = doc.tables[0].rows[0].cells[0].paragraphs[0]

    assert paragraph.text == (
        "plain bold italic code linked "
        f"unsupported ({url})"
    )
    assert paragraph.style.name == "Normal"
    runs_by_text = {run.text: run for run in paragraph.runs}
    assert runs_by_text["plain "].bold is True
    assert runs_by_text["bold"].bold is True
    assert runs_by_text["italic"].bold is True
    assert runs_by_text["italic"].italic is True
    assert runs_by_text["code"].bold is True
    assert runs_by_text["code"].font.name == "Consolas"
    assert runs_by_text[f"unsupported ({url})"].bold is True
    assert _hyperlink_targets(doc, paragraph) == ["https://example.invalid/ok"]

    assert capsys.readouterr().err == (
        "[docx_builder] Unsupported hyperlink target rendered as text: "
        f"{url}\n"
    )


def test_408_equal_width_table_rows_do_not_emit_a_ragged_row_warning(
    tmp_path, capsys
):
    doc = _build(tmp_path, "|A|B|\n|---|---|\n|one|two|\n")

    assert [cell.text for cell in doc.tables[0].rows[1].cells] == ["one", "two"]
    assert capsys.readouterr().err == ""


def test_408_table_autofit_stays_enabled_after_docx_round_trip(tmp_path):
    doc = _build(tmp_path, "|A|B|\n|---|---|\n|one|two|\n")

    assert doc.tables[0].autofit is True


def test_408_table_alignment_defaults_left_when_separator_is_shorter(
    tmp_path,
):
    doc = _build(tmp_path, "|First|Second|\n|---|\n|one|two|\n")
    table = doc.tables[0]

    assert len(table.columns) == 2
    for row in table.rows:
        assert row.cells[0].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.LEFT
        assert row.cells[1].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.LEFT


def test_408_table_without_body_rows_still_exports_as_a_table(tmp_path):
    doc = _build(tmp_path, "|A|B|\n|---|---|")

    assert len(doc.tables) == 1
    assert [cell.text for cell in doc.tables[0].rows[0].cells] == ["A", "B"]


def test_408_table_body_ending_at_eof_preserves_all_cells(tmp_path):
    doc = _build(tmp_path, "|A|B|\n|---|---|\n|one|two|")

    assert len(doc.tables) == 1
    assert [cell.text for cell in doc.tables[0].rows[0].cells] == ["A", "B"]
    assert [cell.text for cell in doc.tables[0].rows[1].cells] == ["one", "two"]


def test_408_single_table_row_without_separator_remains_plain_text(tmp_path):
    row = "| lone | row |"
    doc = _build(tmp_path, row)

    assert doc.tables == []
    assert [paragraph.text for paragraph in doc.paragraphs] == [row]


def test_408_heading_does_not_skip_the_paragraph_after_it(tmp_path):
    doc = _build(tmp_path, "## Heading\nFollowing sentence.\n")

    assert [paragraph.text for paragraph in doc.paragraphs] == [
        "Heading",
        "Following sentence.",
    ]
    assert [paragraph.style.name for paragraph in doc.paragraphs] == [
        "Heading 2",
        "Normal",
    ]


def test_408_horizontal_rule_does_not_skip_the_following_paragraph(tmp_path):
    doc = _build(tmp_path, "Before.\n---\nAfter.\n")

    assert [paragraph.text for paragraph in doc.paragraphs] == [
        "Before.",
        "After.",
    ]
    assert [paragraph.style.name for paragraph in doc.paragraphs] == [
        "Normal",
        "Normal",
    ]


def test_408_unclosed_fence_marker_does_not_skip_the_line_after_it(tmp_path):
    doc = _build(tmp_path, "```\nFirst.\n\nSecond.\n")

    assert [paragraph.text for paragraph in doc.paragraphs] == ["First.", "Second."]


def test_408_bullet_at_eof_without_newline_is_exported(tmp_path):
    doc = _build(tmp_path, "- final bullet")

    assert [paragraph.text for paragraph in doc.paragraphs] == ["final bullet"]
    assert [paragraph.style.name for paragraph in doc.paragraphs] == [
        "List Bullet"
    ]


def test_408_plain_paragraph_at_eof_without_newline_is_exported(tmp_path):
    doc = _build(tmp_path, "Plain final line")

    assert [paragraph.text for paragraph in doc.paragraphs] == [
        "Plain final line"
    ]
    assert [paragraph.style.name for paragraph in doc.paragraphs] == ["Normal"]


def test_408_monospace_block_has_zero_spacing_and_consolas_after_readback(
    tmp_path,
):
    doc = _build(tmp_path, "```\n  tree  \nbranch\n```\n")

    assert [paragraph.text for paragraph in doc.paragraphs] == [
        "  tree  ",
        "branch",
    ]
    for paragraph in doc.paragraphs:
        assert paragraph.style.name == "Normal"
        assert paragraph.paragraph_format.space_before == Pt(0)
        assert paragraph.paragraph_format.space_after == Pt(0)
        assert len(paragraph.runs) == 1
        assert paragraph.runs[0].font.name == "Consolas"


def test_408_unknown_cell_alignment_defaults_to_explicit_left_after_readback(
    tmp_path,
):
    document = docx.Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    docx_builder._set_cell_text(cell, "Synthetic cell", "not-an-alignment")
    output_path = tmp_path / "unknown-alignment.docx"
    document.save(output_path)

    reopened = docx.Document(output_path)
    paragraph = reopened.tables[0].cell(0, 0).paragraphs[0]
    assert paragraph.text == "Synthetic cell"
    assert paragraph.alignment == WD_ALIGN_PARAGRAPH.LEFT


def test_408_missing_image_warning_and_placeholder_are_exact(tmp_path, capsys):
    missing_path = tmp_path / "missing-chart.png"
    doc = _build(tmp_path, f"![Synthetic chart]({missing_path})\nAfter.\n")

    assert len(doc.inline_shapes) == 0
    assert [paragraph.text for paragraph in doc.paragraphs] == [
        "[Image not embedded: Synthetic chart -- file not found]",
        "After.",
    ]
    assert capsys.readouterr().err == (
        f"[docx_builder] Image not embedded (file not found): {missing_path}\n"
    )


def test_408_exact_width_image_is_not_visibly_changed_at_scaling_boundary(
    tmp_path,
):
    # The default Letter page has a 6-inch text width; a 432-pixel PNG
    # without a DPI chunk is interpreted at 72 dpi (432 / 72 = 6.0 inches).
    png = _write_png(tmp_path / "exact-width.png", 432, 1)
    doc = _build(tmp_path, f"![Boundary width]({png})\n")

    section = doc.sections[0]
    max_width = section.page_width - section.left_margin - section.right_margin
    assert doc.inline_shapes[0].width == max_width
    assert doc.inline_shapes[0]._inline.docPr.get("descr") == "Boundary width"


# ---------------------------------------------------------------------------
# Issue #439: HTML comments in CAM guidance are not client-facing content.
# ---------------------------------------------------------------------------

def test_html_comments_are_removed_from_export_and_surrounding_text_survives(tmp_path):
    markdown = (
        "Before <!-- one-line secret --> after.\n\n"
        "Left <!-- multi-line secret\n"
        "second secret line\n"
        "third secret line --> right.\n\n"
        "<!-- standalone secret -->\n"
        "Visible text.\n"
    )
    doc = _build(tmp_path, markdown)
    exported = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    flattened = " ".join(exported.split())

    assert "secret" not in exported
    assert "<!--" not in exported
    assert "Before after." in flattened
    assert "Left right." in flattened
    assert "Visible text." in flattened


def test_html_comment_inside_fenced_code_block_is_preserved_as_code(tmp_path):
    markdown = "```\n<!-- this is an example, not a comment to strip -->\n```\n"
    doc = _build(tmp_path, markdown)

    assert [paragraph.text for paragraph in doc.paragraphs] == [
        "<!-- this is an example, not a comment to strip -->",
    ]


def test_a_bare_comment_close_marker_in_ordinary_text_is_not_a_comment(tmp_path):
    doc = _build(tmp_path, "Literal marker --> remains visible.\n")

    assert [paragraph.text for paragraph in doc.paragraphs] == ["Literal marker --> remains visible."]


def test_html_comments_are_removed_from_crlf_markdown(tmp_path):
    markdown = (
        "Before\r\n"
        "<!-- Guidance: first line\r\n"
        "second secret line -->\r\n"
        "After <!-- one-line secret --> text.\r\n"
    )
    doc = _build(tmp_path, markdown)
    exported = "\n".join(paragraph.text for paragraph in doc.paragraphs)

    assert "secret" not in exported and "Guidance" not in exported and "<!--" not in exported
    assert "Before" in exported and "After" in exported and "text." in exported


def test_unterminated_html_comment_refuses_export_without_writing_docx(tmp_path):
    output = tmp_path / "unterminated.docx"

    with pytest.raises(ValueError, match="Unterminated HTML comment"):
        export_to_docx("Visible text.\n<!-- comment never closes\nsecret text", str(output))

    assert not output.exists()


def test_shipped_cam_guidance_uses_html_comments_and_blank_human_signature():
    root = Path(__file__).resolve().parents[1]
    expected_guidance_counts = {
        "asset_finance_cam.md": 9,
        "corporate_credit_cam.md": 5,
    }
    signature = "**Credit Analyst:** ________ **Date:** ________"
    signature_guidance = "<!-- Guidance: The drafter never fills this line. -->"

    for name, expected_count in expected_guidance_counts.items():
        lines = (root / "templates" / "cam" / name).read_text(encoding="utf-8").splitlines()
        guidance = [line for line in lines if line.startswith("<!-- Guidance: ") and line.endswith(" -->")]
        assert len(guidance) == expected_count + 1, name
        assert signature in lines, name
        signature_index = lines.index(signature)
        assert lines[signature_index + 1] == signature_guidance, name
