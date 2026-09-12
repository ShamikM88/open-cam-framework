import re

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH

TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
CODE_RE = re.compile(r"`(.+?)`")

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


def _clean_inline_markdown(text):
    text = BOLD_RE.sub(r"\1", text)
    text = CODE_RE.sub(r"\1", text)
    return text


def _set_cell_text(cell, text, alignment, bold=False):
    cell.text = _clean_inline_markdown(text)
    for paragraph in cell.paragraphs:
        paragraph.alignment = ALIGNMENTS.get(alignment, WD_ALIGN_PARAGRAPH.LEFT)
        for run in paragraph.runs:
            run.bold = bold


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


def export_to_docx(markdown_text, output_path):
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
        elif line.startswith("- "):
            doc.add_paragraph(line[2:], style="List Bullet")
            i += 1
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
                doc.add_paragraph(line)
            i += 1

    doc.save(output_path)
