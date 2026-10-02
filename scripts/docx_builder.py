import os
import re
import sys

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
FENCE_RE = re.compile(r"^\s*```")
FENCE_OPEN_RE = re.compile(r"^\s*```(\S*)")
IMAGE_RE = re.compile(r"^\s*!\[([^\]]*)\]\((.+?)\)\s*$")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}
URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
HEADING_RE = re.compile(r"^(#{1,6}) ")
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


def _add_monospace_block(doc, code_lines):
    """Render each line of a non-`json` fenced code block as its own
    paragraph in a monospace font (Consolas, matching the existing
    inline-code font choice) -- unlike every other block type here, these
    lines are never joined into a soft-wrapped paragraph and never run
    through _add_inline_runs()'s markdown handling, since a preformatted
    block (e.g. a Unicode box-drawing ownership tree, see issue #113) is
    meant to render exactly as given, whitespace and all. Paragraph
    spacing is zeroed so consecutive lines read as one tight block rather
    than a stack of normally-spaced paragraphs.
    """
    for code_line in code_lines:
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        run = paragraph.add_run(code_line)
        run.font.name = "Consolas"


def _add_image(doc, alt, path):
    """Embed the image at `path` as its own block, scaled down (never up) to
    fit the page's text width, with `alt` as its accessibility description
    and, when non-empty, an italic caption beneath it (see issue #114).

    A reference that can't be honoured -- a remote URL (this module has no
    network access by design), a missing file, an unsupported or unreadable
    image -- never crashes the export and is never silently dropped: it
    becomes a visible placeholder paragraph in the document plus a stderr
    warning. A silently missing chart in a committee paper is worse than a
    visible gap, and raising would lose the whole export over one image.
    """
    problem = None
    if URL_SCHEME_RE.match(path):
        problem = "remote URLs aren't supported"
    elif os.path.splitext(path)[1].lower() not in IMAGE_EXTENSIONS:
        problem = "not a supported image type (png/jpg/gif/bmp)"
    elif not os.path.isfile(path):
        problem = "file not found"

    shape = None
    if problem is None:
        paragraphs_before = len(doc.paragraphs)
        try:
            shape = doc.add_picture(path)
        except Exception as e:  # python-docx raises several unrelated types for a corrupt image
            detail = str(e) or type(e).__name__
            problem = f"could not be read as an image ({detail})"
            # add_picture() creates its paragraph before it parses the file, so
            # a failure leaves an empty one behind -- drop it.
            for stray in doc.paragraphs[paragraphs_before:]:
                stray._element.getparent().remove(stray._element)

    if problem is not None:
        print(f"[docx_builder] Image not embedded ({problem}): {path}", file=sys.stderr)
        doc.add_paragraph(f"[Image not embedded: {alt or path} -- {problem}]")
        return

    section = doc.sections[0]
    max_width = section.page_width - section.left_margin - section.right_margin
    if shape.width > max_width:
        ratio = max_width / shape.width
        shape.height = int(shape.height * ratio)
        shape.width = int(max_width)

    if alt:
        shape._inline.docPr.set("descr", alt)
        caption = doc.add_paragraph()
        caption.add_run(alt).italic = True


def _find_closing_fence(lines, open_idx):
    """Index of the bare ``` line that closes the fence opened at
    `open_idx`, or None if there isn't one. A fence line carrying a tag
    (```json) can only ever *open* a block, never close one (as in
    CommonMark): otherwise an unterminated untagged fence -- e.g. an
    ownership tree whose closing ``` was forgotten -- would be "closed" by
    the opening line of the Underwriter's trailing ```json block, rendering
    the narrative in between as monospace and leaking that block's JSON body
    into the client-facing document.
    """
    for k in range(open_idx + 1, len(lines)):
        if FENCE_RE.match(lines[k]):
            return None if FENCE_OPEN_RE.match(lines[k]).group(1) else k
    return None


def _is_block_boundary(line):
    """True when `line` starts (or is) a different block -- a heading,
    bullet, table row, horizontal rule, fenced code block, image, or a blank
    line -- and so must never be absorbed into a preceding plain-text
    paragraph.

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
        or IMAGE_RE.match(line)
        or HR_RE.match(line)
        or _is_table_row(line)
    )


def export_to_docx(markdown_text, output_path):
    """Render `markdown_text` as a .docx at `output_path`.

    Follows normal Markdown paragraph semantics: a single newline inside a
    block is a soft wrap (joined into one continuous paragraph, via
    _is_block_boundary()'s lookahead below), not a paragraph break -- only
    a blank line, or the start of a new block (heading/bullet/numbered
    item/table row/fence/image/HR), starts a new one. This means source text
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

        heading = HEADING_RE.match(line)
        if heading:
            hashes = heading.group(1)
            # python-docx's default style set tops out at Heading 3 (issue
            # #110), so H4-H6 render as Heading 3 rather than leaking the
            # literal "####" into the document.
            doc.add_heading(line[len(hashes) + 1:], level=min(len(hashes), 3))
            i += 1
        elif FENCE_RE.match(line):
            # A fenced code block is one of two things in practice: the
            # Underwriter's trailing ```json structured-output block (needed
            # for policy_checks.py's regex parsing but never meant for a
            # client-facing CAM -- skipped wholesale, not dumped as body
            # paragraphs) or a preformatted monospace block meant to actually
            # appear in the document (e.g. a Unicode box-drawing ownership
            # tree, see issue #113) -- rendered as-is via
            # _add_monospace_block(). The ```json tag is what distinguishes
            # them. Look ahead for an actual closing fence first either way:
            # an unterminated one (a stray/odd ``` from truncation) must not
            # silently discard every line through EOF, so only treat this as
            # a real fenced block when a real closing fence exists --
            # otherwise treat this line as a lone stray marker and keep
            # processing normally.
            close_idx = _find_closing_fence(lines, i)
            if close_idx is not None:
                tag_match = FENCE_OPEN_RE.match(line)
                tag = tag_match.group(1).lower() if tag_match else ""
                if tag != "json":
                    _add_monospace_block(doc, lines[i + 1:close_idx])
                i = close_idx + 1
            else:
                i += 1
        elif HR_RE.match(line):
            i += 1  # a markdown horizontal rule has no meaningful docx equivalent here
        elif IMAGE_RE.match(line):
            image_match = IMAGE_RE.match(line)
            _add_image(doc, image_match.group(1).strip(), image_match.group(2).strip())
            i += 1
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
