import os
import glob
import argparse
from anthropic import Anthropic
from pypdf import PdfReader
from template_resolver import local_cam_template_path

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

STYLE_PROMPT = (
    "Analyze these sample CAMs and extract writing style, tone, and standard "
    "risk phrasing:\n{text}"
)

TEMPLATE_PROMPT = (
    "Analyze the structure of these sample CAMs -- the section headings, the "
    "table columns, and the order information is presented in -- and produce "
    "a generic Markdown CAM template that mirrors that structure exactly.\n\n"
    "Critical: this template will be committed to a shared codebase, so it "
    "must contain ZERO real data from the samples. No real company names, "
    "people's names, dates, or figures. Replace every one of those with a "
    "bracketed [placeholder] describing what belongs there (e.g. "
    "[Borrower Legal Name], [Amount], [PD Grade]). Output only the Markdown "
    "template itself, nothing else.\n\nSample CAMs:\n{text}"
)


def _read_sample_text():
    text_content = ""
    for path in glob.glob("inputs/calibration_samples/*.pdf"):
        reader = PdfReader(path)
        for page in reader.pages:
            text_content += page.extract_text() + "\n"
    return text_content


def run_calibration(deal_type):
    text_content = _read_sample_text()
    if not text_content:
        print("No sample PDFs found in inputs/calibration_samples/")
        return

    print("[1/2] Extracting writing style and tone...")
    style_response = client.messages.create(
        model="claude-3-7-sonnet-20250219",
        max_tokens=3000,
        messages=[{"role": "user", "content": STYLE_PROMPT.format(text=text_content[:12000])}]
    )
    with open("config/style_guide.md", "w") as f:
        f.write("# Calibrated Style Guide\n\n" + style_response.content[0].text)
    print("Calibration complete. Created `config/style_guide.md`.")

    print(f"[2/2] Deriving a '{deal_type}' CAM template from your samples...")
    template_response = client.messages.create(
        model="claude-3-7-sonnet-20250219",
        max_tokens=3000,
        messages=[{"role": "user", "content": TEMPLATE_PROMPT.format(text=text_content[:12000])}]
    )
    template_path = local_cam_template_path(deal_type)
    os.makedirs(os.path.dirname(template_path), exist_ok=True)
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(template_response.content[0].text)
    print(f"Derived template written to {template_path} -- this overrides the "
          f"shipped default for '{deal_type}' deals until you remove it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--type", default="corporate_credit",
        help="Deal type these samples represent, e.g. corporate_credit, asset_finance "
             "(matches orchestrator.py's --type). Defaults to corporate_credit.",
    )
    args = parser.parse_args()
    run_calibration(args.type)
