import re

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
FENCE_RE = re.compile(r"^\s*```")
HEADING_RE = re.compile(r"^(#{1,3}) ")
BULLET_RE = re.compile(r"^- ")
NUMBERED_RE = re.compile(r"^\d+\.\s")
INLINE_SPAN = r"\S(?:.*?\S)?"  # non-whitespace at both ends -- keeps a lone
# trailing/leading `*` used as a footnote marker (e.g. "Net Income* is...")
# from being misread as an emphasis delimiter, since a real emphasis span
# never opens/closes on whitespace.
INLINE_RE = re.compile(
    r"\*\*\*(" + INLINE_SPAN + r")\*\*\*"   # ***bold italic***
    r"|\*\*(" + INLINE_SPAN + r")\*\*"       # **bold**
    r"|\*(" + INLINE_SPAN + r")\*"           # *italic*
    r"|`(.+?)`"                               # `code`
    r"|\[([^\]]+)\]\(([^)]+)\)"               # [link text](url)
)

ALIGNMENTS = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}


def _is_table_row(line):
    return bool(TABLE_ROW_RE.match(line))


def _split_row(line):
    """Split a markdown table row into its cell strings, dropping the outer pipes."""
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def _is_separator_row(cells):
    return bool(cells) and all(SEPARATOR_CELL_RE.match(cell) for cell in cells)


def _column_alignment(separator_cell):
    left = separator_cell.startswith(":")
    right = separator_cell.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    return "left"


def _add_hyperlink_run(paragraph, url, text, bold=False):
    """Add a real, clickable Word hyperlink run -- python-docx has no
    high-level API for this, so it's built directly in OOXML: a
    w:hyperlink element referencing an external relationship on the
    paragraph's part, wrapping a w:r styled with the built-in "Hyperlink"
    character style (Word recognizes this style id even though this
    document never explicitly defines it in styles.xml).
    """
    part = paragraph.part
    r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)

    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    run_pr = OxmlElement("w:rPr")
    style = OxmlElement("w:rStyle")
    style.set(qn("w:val"), "Hyperlink")
    run_pr.append(style)
    if bold:
        run_pr.append(OxmlElement("w:b"))
    run.append(run_pr)

    text_el = OxmlElement("w:t")
    text_el.set(qn("xml:space"), "preserve")
    text_el.text = text
    run.append(text_el)

    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _add_inline_runs(paragraph, text, base_bold=False):
    """Split `text` on **bold**/*italic*/`code`/[link](url) markers and add
    one run (or, for a link, a real hyperlink) per span, preserving
    `base_bold` (e.g. a table header row) as the default for any plain-text
    span so callers don't have to re-apply it themselves.
    """
    pos = 0
    for match in INLINE_RE.finditer(text):
        if match.start() > pos:
            run = paragraph.add_run(text[pos:match.start()])
            run.bold = base_bold
        bold_italic_text, bold_text, italic_text, code_text, link_text, link_url = match.groups()
        if bold_italic_text is not None:
            run = paragraph.add_run(bold_italic_text)
            run.bold = True
            run.italic = True
        elif bold_text is not None:
            run = paragraph.add_run(bold_text)
            run.bold = True
        elif italic_text is not None:
            run = paragraph.add_run(italic_text)
            run.bold = base_bold
            run.italic = True
        elif code_text is not None:
            run = paragraph.add_run(code_text)
            run.bold = base_bold
            run.font.name = "Consolas"
        else:
            _add_hyperlink_run(paragraph, link_url, link_text, bold=base_bold)
        pos = match.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        run.bold = base_bold


def _set_cell_text(cell, text, alignment, bold=False):
    # A freshly added table cell's paragraph has no runs yet -- don't set
    # cell.text = "" first, since python-docx's text setter always calls
    # add_run() (even for ""), leaving a stray empty run at index 0 ahead of
    # the one we actually want.
    paragraph = cell.paragraphs[0]
    paragraph.alignment = ALIGNMENTS.get(alignment, WD_ALIGN_PARAGRAPH.LEFT)
    _add_inline_runs(paragraph, text, base_bold=bold)


def _add_table(doc, header_cells, alignments, body_rows):
    table = doc.add_table(rows=1, cols=len(header_cells))
    table.style = "Table Grid"
    table.autofit = True

    for idx, text in enumerate(header_cells):
        _set_cell_text(table.rows[0].cells[idx], text, alignments[idx], bold=True)

    for row_cells in body_rows:
        row = table.add_row()
        for idx in range(len(row.cells)):
            text = row_cells[idx] if idx < len(row_cells) else ""
            alignment = alignments[idx] if idx < len(alignments) else "left"
            _set_cell_text(row.cells[idx], text, alignment)

    return table


def _is_block_boundary(line):
    """True when `line` starts (or is) a different block -- a heading,
    bullet, table row, horizontal rule, fenced code block, or a blank line
    -- and so must never be absorbed into a preceding plain-text paragraph.

    Used to find where a soft-wrapped paragraph ends: Markdown (like every
    other renderer -- browsers, GitHub, Word itself) treats a single
    newline inside a block as a soft wrap, not a paragraph break -- only a
    blank line (or the start of a new block) ends the paragraph. Line-by-
    line source text wrapped at some column width must still join back
    into one continuous paragraph, not fragment into one Word paragraph
    per source line.
    """
    return (
        not line.strip()
        or HEADING_RE.match(line)
        or BULLET_RE.match(line)
        or NUMBERED_RE.match(line)
        or FENCE_RE.match(line)
        or HR_RE.match(line)
        or _is_table_row(line)
    )


def export_to_docx(markdown_text, output_path):
    """Render `markdown_text` as a .docx at `output_path`.

    Follows normal Markdown paragraph semantics: a single newline inside a
    block is a soft wrap (joined into one continuous paragraph, via
    _is_block_boundary()'s lookahead below), not a paragraph break -- only
    a blank line, or the start of a new block (heading/bullet/numbered
    item/table row/fence/HR), starts a new one. This means source text
    that lists several distinct fields as consecutive plain lines (e.g.
    "**Company:** X\\n**Date:** Y") will render as one run-on paragraph,
    not as separate lines -- authors of Markdown destined for this
    function (a /research brief, a /assemble CAM draft) should use a
    bullet list or genuinely blank-line-separated paragraphs for anything
    meant to read as distinct lines/fields.
    """
    doc = docx.Document()
    lines = markdown_text.split("\n")
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        if line.startswith("# "):
            doc.add_heading(line[2:], level=1)
            i += 1
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
            i += 1
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=3)
            i += 1
        elif FENCE_RE.match(line):
            # A fenced code block (e.g. the Underwriter's trailing structured
            # JSON block, needed for policy_checks.py's regex parsing but
            # never meant for a client-facing CAM) -- skip it wholesale
            # rather than dumping raw code/JSON as body paragraphs. Look
            # ahead for an actual closing fence first: an unterminated one
            # (a stray/odd ``` from truncation) must not silently discard
            # every line through EOF, so only skip the block when a real
            # closing fence exists -- otherwise treat this line as a lone
            # stray marker and keep processing normally.
            close_idx = None
            for k in range(i + 1, n):
                if FENCE_RE.match(lines[k]):
                    close_idx = k
                    break
            i = close_idx + 1 if close_idx is not None else i + 1
        elif HR_RE.match(line):
            i += 1  # a markdown horizontal rule has no meaningful docx equivalent here
        elif BULLET_RE.match(line):
            para_lines = [line[2:].strip()]
            j = i + 1
            while j < n and not _is_block_boundary(lines[j]):
                para_lines.append(lines[j].strip())
                j += 1
            paragraph = doc.add_paragraph(style="List Bullet")
            _add_inline_runs(paragraph, " ".join(para_lines))
            i = j
        elif _is_table_row(line) and i + 1 < n and _is_separator_row(_split_row(lines[i + 1])):
            header_cells = _split_row(line)
            alignments = [_column_alignment(c) for c in _split_row(lines[i + 1])]
            body_rows = []
            j = i + 2
            while j < n and _is_table_row(lines[j]):
                body_rows.append(_split_row(lines[j]))
                j += 1
            _add_table(doc, header_cells, alignments, body_rows)
            i = j
        else:
            if line.strip():
                para_lines = [line.strip()]
                j = i + 1
                while j < n and not _is_block_boundary(lines[j]):
                    para_lines.append(lines[j].strip())
                    j += 1
                paragraph = doc.add_paragraph()
                _add_inline_runs(paragraph, " ".join(para_lines))
                i = j
            else:
                i += 1

    doc.save(output_path)
