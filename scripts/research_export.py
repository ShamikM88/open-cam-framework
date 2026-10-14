"""Standalone research-brief export, with sources and review-release disclosures."""

import os
import re
import sys
from datetime import date

from docx_builder import export_to_docx
from state_manager import read_state, resolve_date_str, sanitize_path_component, write_state

ATX_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+][ \t]+|\d+[.)][ \t]+)\S")
HTTP_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


def _today_date():
    """Return disclosure date; patchable so export tests are deterministic."""
    return date.today().isoformat()


def _markdown_structure(markdown):
    """Return Markdown lines, their non-fenced mask and ATX headings outside fences."""
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
    """Whether a Sources/References section outside fences contains a list item or URL."""
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
            if visible[index] and (LIST_ITEM_RE.match(lines[index]) or HTTP_URL_RE.search(lines[index])):
                return True
    return False


def _normalise_reason(reason, label):
    if reason is None:
        return None
    if not isinstance(reason, str):
        raise ValueError(f"{label} must be text.")
    collapsed = " ".join(reason.split())
    if not collapsed:
        raise ValueError(f"{label} must not be empty.")
    return collapsed


def _trailing_rejected_count(review_trail):
    """Count only the consecutive REJECTED entries at the end of the recorded trail."""
    count = 0
    for entry in reversed(review_trail or []):
        if not isinstance(entry, dict) or entry.get("verdict") != "REJECTED":
            break
        count += 1
    return count


def _insert_after_first_heading(markdown, paragraph):
    """Place the status disclosure directly after the document's first ATX heading."""
    lines, _visible, headings = _markdown_structure(markdown)
    if not headings:
        return paragraph + "\n\n" + markdown
    index = headings[0][0]
    return "\n".join(lines[:index + 1] + ["", paragraph, ""] + lines[index + 1:]).strip() + "\n"


def export_research_brief(
    company,
    proposal,
    brief_markdown,
    date_str=None,
    base_dir=None,
    analyst_override_no_sources=None,
    analyst_release_unapproved=None,
):
    """Export the brief, optionally disclosing an authorised release without sources or approval.

    The analyst-release flag records the consecutive trailing REJECTED pass count and reason
    in state.json, in one write after the DOCX has been written.
    """
    source_override_reason = _normalise_reason(
        analyst_override_no_sources, "Analyst override reason"
    )
    release_reason = _normalise_reason(
        analyst_release_unapproved, "Analyst release reason"
    )
    has_sources = _has_populated_sources_section(brief_markdown)
    if not has_sources and source_override_reason is None:
        raise ValueError("Research brief must contain a populated Sources or References section.")

    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str:
        date_str = sanitize_path_component(date_str, "date_str")

    date_str = resolve_date_str(company, proposal, date_str=date_str, base_dir=base_dir)
    release_date = _today_date() if release_reason is not None or not has_sources else None
    state = None
    passes = None
    if release_reason is not None:
        state = read_state(
            company, proposal, date_str=date_str, base_dir=base_dir, keys=("review_trail",)
        ) or {}
        passes = _trailing_rejected_count(state.get("review_trail") or [])
        status = (
            "Review status: Not approved by the independent Risk Reviewer. "
            f"{passes} review passes returned REJECTED. Released for manual analyst review "
            f"at the analyst's request on {release_date}: {release_reason}."
        )
        brief_markdown = _insert_after_first_heading(brief_markdown, status)

    if not has_sources:
        footnote = (
            "Footnote: No sources list is included in this brief. Exported under analyst override "
            f"on {release_date or _today_date()}: {source_override_reason}."
        )
        brief_markdown = brief_markdown.rstrip() + "\n\n" + footnote

    deals_root = os.path.join(base_dir, "deals") if base_dir else "deals"
    output_dir = os.path.join(deals_root, company, f"{proposal}_{date_str}")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{company}_{proposal}_Research_Brief.docx")
    export_to_docx(brief_markdown, output_path)

    if release_reason is not None:
        write_state(
            company,
            proposal,
            date_str=date_str,
            base_dir=base_dir,
            review_stopped_by_analyst={
                "passes": passes,
                "date": release_date,
                "reason": release_reason,
            },
        )
    return output_path


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Export a standalone research brief to .docx after verifying its Sources section; "
                    "explicit analyst-authorised disclosures can release a brief without sources or approval."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--brief", required=True, help="Path to the drafted research brief Markdown file")
    parser.add_argument(
        "--analyst-override-no-sources", metavar="REASON",
        help="Export without a populated Sources/References section only after explicit analyst authorisation; "
             "the reason is printed as the final DOCX paragraph.",
    )
    parser.add_argument(
        "--analyst-release-unapproved", metavar="REASON",
        help="Record an explicitly analyst-authorised release of a non-approved brief for manual review; "
             "the reason is disclosed near the top of the DOCX and in state.json.",
    )
    args = parser.parse_args(argv)
    try:
        with open(args.brief, encoding="utf-8") as file:
            brief_markdown = file.read()
        output_path = export_research_brief(
            args.company,
            args.proposal,
            brief_markdown,
            analyst_override_no_sources=args.analyst_override_no_sources,
            analyst_release_unapproved=args.analyst_release_unapproved,
        )
    except ValueError as exc:
        sys.exit(f"error: {exc}")
    print(f"Done! Research brief exported to {output_path}")


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    main()
