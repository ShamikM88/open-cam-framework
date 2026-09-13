import os
import argparse
from anthropic import Anthropic
from deal_export import export_deal
from state_manager import write_state, append_review_trail
from template_resolver import cam_template_path

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def run_pipeline(company, proposal, pd_score, lgd_score, deal_type):
    with open("agents/underwriter_agent.md") as f: maker_prompt = f.read()
    with open("agents/risk_reviewer_agent.md") as f: checker_prompt = f.read()

    style_guide = ""
    if os.path.exists("config/style_guide.md"):
        with open("config/style_guide.md") as f: style_guide = f.read()

    # A calibration-derived override under templates/local/cam/ (see
    # scripts/calibrate.py, or the /calibrate slash command) takes
    # precedence over the shipped default under templates/cam/; neither
    # existing means this is a genuinely new type.
    template_path = cam_template_path(deal_type)
    template_section = ""
    if template_path:
        with open(template_path, encoding="utf-8") as f:
            template_section = (
                "\nFollow this exact CAM template structure, filling in every "
                f"placeholder with grounded, sourced content:\n{f.read()}\n"
            )

    print(f"[1/3] Underwriter Agent drafting CAM for {company}...")
    draft = client.messages.create(
        model="claude-3-7-sonnet-20250219",
        max_tokens=4000,
        messages=[{"role": "user", "content": f"{maker_prompt}\nStyle:\n{style_guide}\n{template_section}Company: {company}\nProposal: {proposal}\nPD: {pd_score}\nLGD: {lgd_score}"}]
    ).content[0].text
    # Checkpoint after the draft: never rely on conversation/process memory
    # alone for a figure that already exists on disk (see CLAUDE.md's
    # "Context Window & State Management Protocol").
    write_state(company, proposal, deal_type=deal_type,
                inputs={"pd": pd_score, "lgd": lgd_score},
                steps_completed=["draft"])

    print(f"[2/3] Risk Reviewer Agent auditing draft...")
    audit = client.messages.create(
        model="claude-3-7-sonnet-20250219",
        max_tokens=2000,
        messages=[{"role": "user", "content": f"{checker_prompt}\nDraft to review:\n{draft}"}]
    ).content[0].text
    # append_review_trail (not write_state) so a re-run for the same deal
    # never clobbers a prior audit's verdict/notes -- orchestrator.py
    # doesn't loop today, but the schema stays consistent with /assemble's
    # /review loop, which does. Note isn't parsed out of `audit` separately
    # from the verdict here (see the review.md command for why: a naive
    # keyword match on freeform LLM text would be fragile) -- both fields
    # just hold the full audit response for now.
    append_review_trail(company, proposal, verdict=audit, notes=None,
                         deal_type=deal_type, steps_completed=["draft", "audit"])

    print(f"[3/3] Exporting .docx and .xlsx files...")
    output_dir = export_deal(company, proposal, deal_type, draft)
    write_state(company, proposal, deal_type=deal_type,
                draft_path=os.path.join(output_dir, f"{company}_{proposal}_CAM.docx"),
                steps_completed=["draft", "audit", "export"])
    print(f"Done! Files generated in {output_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--pd", default="0.20%")
    parser.add_argument("--lgd", default="LGD 3 (15%)")
    parser.add_argument("--type", default="corporate_credit")
    args = parser.parse_args()
    run_pipeline(args.company, args.proposal, args.pd, args.lgd, args.type)
