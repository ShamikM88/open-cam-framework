import os
import glob
from anthropic import Anthropic
from pypdf import PdfReader

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def run_calibration():
    text_content = ""
    for path in glob.glob("inputs/calibration_samples/*.pdf"):
        reader = PdfReader(path)
        for page in reader.pages:
            text_content += page.extract_text() + "\n"
            
    if not text_content:
        print("No sample PDFs found in inputs/calibration_samples/")
        return

    prompt = f"Analyze these sample CAMs and extract writing style, tone, and standard risk phrasing:\n{text_content[:12000]}"
    
    response = client.messages.create(
        model="claude-3-7-sonnet-20250219",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    with open("config/style_guide.md", "w") as f:
        f.write("# Calibrated Style Guide\n\n" + response.content[0].text)
        
    print(" Calibration complete. Created `config/style_guide.md`.")

if __name__ == "__main__":
    run_calibration()
