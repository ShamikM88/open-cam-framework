"""Deal-folder creation, template auto-save, and .docx/.xlsx export.

Pulled out of orchestrator.py so this logic -- which has no `anthropic`
dependency -- can run standalone as `python scripts/deal_export.py ...` from
a Claude Code slash command's Bash step (see .claude/commands/assemble.md),
where Claude itself has already produced the draft and just needs it turned
into deal output files. orchestrator.py's own (headless, API-key-based)
pipeline imports export_deal() directly instead of duplicating this logic.
"""
import os
from datetime import datetime

from docx_builder import export_to_docx
from spreading_builder import export_to_xlsx
from template_resolver import cam_template_path, local_cam_template_path


def export_deal(company, proposal, deal_type, draft_markdown, date_str=None, base_dir=None):
    """Create the dated deal folder, auto-save a new template if this deal
    type has none yet, and export the draft to .docx + .xlsx.

    `base_dir` lets tests run against a temp directory instead of the real
    project root; production callers leave it as None (paths relative to
    the current working directory, as before this was extracted).

    Returns the output directory path.
    """
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    deals_root = os.path.join(base_dir, "deals") if base_dir else "deals"
    output_dir = os.path.join(deals_root, company, f"{proposal}_{date_str}")
    os.makedirs(output_dir, exist_ok=True)

    if cam_template_path(deal_type, base_dir=base_dir) is None:
        # Genuinely new deal type: no template (override or default) exists
        # yet. Save this draft's structure as a starting point under
        # templates/local/cam/ -- never the shared, git-tracked
        # templates/cam/ -- since the draft contains this real deal's
        # actual company name and figures.
        new_template_path = local_cam_template_path(deal_type, base_dir=base_dir)
        os.makedirs(os.path.dirname(new_template_path), exist_ok=True)
        print(f"[Auto-Template] No template found for '{deal_type}' -- saving this draft's structure to {new_template_path}")
        with open(new_template_path, "w", encoding="utf-8") as f:
            f.write(draft_markdown)

    docx_path = os.path.join(output_dir, f"{company}_{proposal}_CAM.docx")
    xlsx_path = os.path.join(output_dir, f"{company}_{proposal}_Spreading.xlsx")

    export_to_docx(draft_markdown, docx_path)
    export_to_xlsx(company, xlsx_path)

    return output_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Create the deal output folder, auto-save a new CAM template if "
                     "this deal type has none yet, and export an already-drafted CAM "
                     "to .docx + .xlsx. Has no Anthropic dependency -- the draft is "
                     "expected to already exist as a file, written by whatever drafted "
                     "it (a Claude Code slash command, or orchestrator.py)."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--type", default="corporate_credit")
    parser.add_argument("--draft", required=True, help="Path to the drafted CAM Markdown file")
    args = parser.parse_args()

    with open(args.draft, encoding="utf-8") as f:
        draft_markdown = f.read()

    output_dir = export_deal(args.company, args.proposal, args.type, draft_markdown)
    print(f"Done! Files generated in {output_dir}")
