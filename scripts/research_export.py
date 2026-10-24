"""Standalone research-brief export, with a sources-list acceptance gate.

Kept separate from deal_export.py: a research-only deliverable must never
trigger the full-CAM side effects (template auto-save and workbook export).
"""
import os
import re
import sys
from datetime import date

from docx_builder import check_exportable, export_to_docx
from draft_lint import lint_draft
from state_manager import read_state, resolve_date_str, sanitize_path_component, write_state


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


def _normalise_release_reason(reason):
    if reason is None:
        return None
    if not isinstance(reason, str):
        raise ValueError("Analyst release reason must be text.")
    collapsed = " ".join(reason.split())
    if not collapsed:
        raise ValueError("Analyst release reason must not be empty.")
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
    company, proposal, brief_markdown, date_str=None, base_dir=None,
    analyst_override_no_sources=None, analyst_release_unapproved=None,
):
    """Export a research brief as DOCX, refusing an empty/missing sources section.

    The override is a disclosed exception for cases where an analyst authorised
    export without sources. Its reason is not persisted in state; it appears as
    the final paragraph of the document.
    """
    override_reason = _normalise_override_reason(analyst_override_no_sources)
    release_reason = _normalise_release_reason(analyst_release_unapproved)
    findings = lint_draft(brief_markdown, document_kind="research-brief")
    if findings:
        raise ValueError("Draft lint rejected research brief: " + "; ".join(finding["message"] for finding in findings))
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
    release_date = None
    passes = None
    if release_reason is not None:
        release_date = _today_date()
        state = read_state(
            company, proposal, date_str=date_str, base_dir=base_dir, keys=("review_trail",)
        ) or {}
        earlier_releases = state.get("review_stopped_by_analyst", [])
        if not isinstance(earlier_releases, list):
            raise ValueError("state.json: review_stopped_by_analyst must be a list of release records.")
        passes = _trailing_rejected_count(state.get("review_trail") or [])
        if passes == 0:
            raise ValueError(
                "Cannot release an unapproved brief: the deal's review_trail has no trailing REJECTED entry "
                "(no review yet, or the latest verdict is not REJECTED)."
            )
        status = (
            "Review status: Not approved by the independent Risk Reviewer. "
            f"{passes} review {'pass' if passes == 1 else 'passes'} returned REJECTED. Released for manual analyst review "
            f"at the analyst's request on {release_date}: {release_reason}."
        )
        brief_markdown = _insert_after_first_heading(brief_markdown, status)
    # The text that will be rendered, including any exporter-added disclosure, must be exportable before the first write.
    check_exportable(brief_markdown)
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
            # Append-only: every release adds one record and none is ever cleared or overwritten.
            review_stopped_by_analyst=[
                *earlier_releases,
                {"date": release_date, "passes": passes, "reason": release_reason},
            ],
        )
    return output_path


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Export a standalone research brief to .docx after verifying that it has a populated "
                    "Sources or References section; explicit analyst-authorised disclosures can release a brief without sources or approval."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--brief", required=True, help="Path to the drafted research brief Markdown file")
    parser.add_argument(
        "--analyst-override-no-sources", nargs="?", const="", default=None, metavar="REASON",
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
        with open(args.brief, encoding="utf-8") as f:
            brief_markdown = f.read()
        output_path = export_research_brief(
            args.company, args.proposal, brief_markdown,
            analyst_override_no_sources=args.analyst_override_no_sources,
            analyst_release_unapproved=args.analyst_release_unapproved,
        )
    except (OSError, ValueError) as exc:
        sys.exit(f"error: {exc}")
    print(f"Done! Research brief exported to {output_path}")


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    main()
