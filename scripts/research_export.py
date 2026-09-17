"""Standalone research-brief export: creates the dated deal folder (reusing
one that already exists for this company/proposal) and exports an
already-drafted research brief to .docx.

Kept separate from deal_export.py (rather than folding this into it)
deliberately -- a research-only deliverable (see /research) must never
trigger the full-CAM side effects deal_export.export_deal() does (template
auto-save, .xlsx spreading export), since no financials/collateral exist yet
for a deal that only ran /research.

No `anthropic` dependency, so it's callable two ways: imported directly, or
run standalone as `python scripts/research_export.py ...` from /research's
Bash step, since Claude has already produced the brief itself by that point
and just needs it turned into a deal output file.
"""
import os

from docx_builder import export_to_docx
from state_manager import resolve_date_str, sanitize_path_component


def export_research_brief(company, proposal, brief_markdown, date_str=None, base_dir=None):
    """Create the dated deal folder and export `brief_markdown` as
    <Company>_<Proposal>_Research_Brief.docx.

    Reuses an existing dated folder for this company/proposal when
    `date_str` isn't given (via state_manager.resolve_date_str(), the same
    auto-discovery /triage's and /commercial's own state.json writes rely
    on) rather than always defaulting to today -- the brief belongs
    alongside whatever state.json /research (or /triage + /commercial)
    already wrote for this deal, not in a fresh folder of its own.

    Returns the output path.
    """
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Export a standalone research brief (Go/No-Go screen + company/sector "
                     "research) to .docx. Has no Anthropic dependency -- the brief is expected "
                     "to already exist as a file, written by whatever drafted it (a Claude Code "
                     "slash command)."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--brief", required=True, help="Path to the drafted research brief Markdown file")
    args = parser.parse_args()

    with open(args.brief, encoding="utf-8") as f:
        brief_markdown = f.read()

    output_path = export_research_brief(args.company, args.proposal, brief_markdown)
    print(f"Done! Research brief exported to {output_path}")
