import os
import argparse
from datetime import datetime
from anthropic import Anthropic
from docx_builder import export_to_docx
from spreading_builder import export_to_xlsx
from template_resolver import cam_template_path, local_cam_template_path

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def run_pipeline(company, proposal, pd_score, lgd_score, deal_type):
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_dir = os.path.join("deals", company, f"{proposal}_{date_str}")
    os.makedirs(output_dir, exist_ok=True)

    with open("agents/underwriter_agent.md") as f: maker_prompt = f.read()
    with open("agents/risk_reviewer_agent.md") as f: checker_prompt = f.read()

    style_guide = ""
    if os.path.exists("config/style_guide.md"):
        with open("config/style_guide.md") as f: style_guide = f.read()

    # A calibration-derived override under templates/local/cam/ (see
    # scripts/calibrate.py) takes precedence over the shipped default under
    # templates/cam/; neither existing means this is a genuinely new type.
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

    print(f"[2/3] Risk Reviewer Agent auditing draft...")
    audit = client.messages.create(
        model="claude-3-7-sonnet-20250219",
        max_tokens=2000,
        messages=[{"role": "user", "content": f"{checker_prompt}\nDraft to review:\n{draft}"}]
    ).content[0].text

    if template_path is None:
        # First deal of a genuinely new type: no template (default or
        # override) exists yet. Save this draft's structure as a starting
        # point under templates/local/cam/ -- never under the shared,
        # git-tracked templates/cam/ -- since the draft contains this real
        # deal's actual company name and figures.
        new_template_path = local_cam_template_path(deal_type)
        os.makedirs(os.path.dirname(new_template_path), exist_ok=True)
        print(f"[Auto-Template] No template found for '{deal_type}' -- saving this draft's structure to {new_template_path}")
        with open(new_template_path, "w", encoding="utf-8") as f: f.write(draft)

    print(f"[3/3] Exporting .docx and .xlsx files...")
    docx_path = os.path.join(output_dir, f"{company}_{proposal}_CAM.docx")
    xlsx_path = os.path.join(output_dir, f"{company}_{proposal}_Spreading.xlsx")

    export_to_docx(draft, docx_path)
    export_to_xlsx(company, xlsx_path)
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
