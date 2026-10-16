"""Standalone research-brief export, with a sources-list acceptance gate.

Kept separate from deal_export.py: a research-only deliverable must never
trigger the full-CAM side effects (template auto-save and workbook export).
"""
import os
import re
import sys
from datetime import date

from docx_builder import export_to_docx
from state_manager import resolve_date_str, sanitize_path_component


ATX_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+][ \t]+|\d+[.)][ \t]+)\S")
HTTP_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


def _today_date():
    """Return the override footnote date; patchable for deterministic tests."""
    return date.today().isoformat()


def _markdown_structure(markdown):
    """Return source lines, their non-fenced mask, and ATX headings outside fences."""
    lines = markdown.splitlines()
    visible = [True] * len(lines)
    headings = []
    fence_char = None
    fence_length = 0

    for index, line in enumerate(lines):
        if fence_char is not None:
            visible[index] = False
            close_re = re.compile(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*$")
            if close_re.match(line):
                fence_char = None
                fence_length = 0
            continue

        fence_match = FENCE_OPEN_RE.match(line)
        if fence_match and not (fence_match.group(1)[0] == "`" and "`" in fence_match.group(2)):
            visible[index] = False
            fence_char = fence_match.group(1)[0]
            fence_length = len(fence_match.group(1))
            continue

        heading_match = ATX_HEADING_RE.match(line)
        if heading_match:
            raw_title = heading_match.group(2) or ""
            title = re.sub(r"#+[ \t]*$", "", raw_title).strip()
            headings.append((index, len(heading_match.group(1)), title))

    return lines, visible, headings


def _has_populated_sources_section(markdown):
    """Whether a Sources/References ATX section contains a list item or HTTP(S) URL.

    Fenced code blocks neither introduce headings nor count as source-list entries.
    A section ends at the next heading of the same or a higher level.
    """
    lines, visible, headings = _markdown_structure(markdown)
    for heading_index, level, title in headings:
        if title.casefold() not in {"sources", "references"}:
            continue

        end = len(lines)
        for next_index, next_level, _ in headings:
            if next_index > heading_index and next_level <= level:
                end = next_index
                break

        for index in range(heading_index + 1, end):
            if not visible[index]:
                continue
            if LIST_ITEM_RE.match(lines[index]) or HTTP_URL_RE.search(lines[index]):
                return True
    return False


def _normalise_override_reason(reason):
    if reason is None:
        return None
    if not isinstance(reason, str):
        raise ValueError("Analyst override reason must be text.")
    collapsed = " ".join(reason.split())
    if not collapsed:
        raise ValueError("Analyst override reason must not be empty.")
    return collapsed


def export_research_brief(
    company, proposal, brief_markdown, date_str=None, base_dir=None,
    analyst_override_no_sources=None,
):
    """Export a research brief as DOCX, refusing an empty/missing sources section.

    The override is a disclosed exception for cases where an analyst authorised
    export without sources. Its reason is not persisted in state; it appears as
    the final paragraph of the document.
    """
    override_reason = _normalise_override_reason(analyst_override_no_sources)
    has_sources = _has_populated_sources_section(brief_markdown)
    if not has_sources:
        if override_reason is None:
            raise ValueError("Research brief must contain a populated Sources or References section.")
        footnote = (
            f"Footnote: No sources list is included in this brief. Exported under analyst override "
            f"on {_today_date()}: {override_reason}."
        )
        brief_markdown = brief_markdown.rstrip() + "\n\n" + footnote

    # Validate the content before resolving paths or creating any output directory.
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str:
        date_str = sanitize_path_component(date_str, "date_str")

    date_str = resolve_date_str(company, proposal, date_str=date_str, base_dir=base_dir)
    deals_root = os.path.join(base_dir, "deals") if base_dir else "deals"
    output_dir = os.path.join(deals_root, company, f"{proposal}_{date_str}")
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, f"{company}_{proposal}_Research_Brief.docx")
    export_to_docx(brief_markdown, output_path)
    return output_path


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Export a standalone research brief to .docx after verifying that it has a populated "
                    "Sources or References section. A disclosed analyst override can permit omission."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--brief", required=True, help="Path to the drafted research brief Markdown file")
    parser.add_argument(
        "--analyst-override-no-sources", nargs="?", const="", default=None, metavar="REASON",
        help="Export without a populated Sources/References section only after explicit analyst authorisation; "
             "the reason is printed as the final DOCX paragraph.",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.brief, encoding="utf-8") as f:
            brief_markdown = f.read()
        output_path = export_research_brief(
            args.company, args.proposal, brief_markdown,
            analyst_override_no_sources=args.analyst_override_no_sources,
        )
    except ValueError as exc:
        sys.exit(f"error: {exc}")
    print(f"Done! Research brief exported to {output_path}")


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    main()
