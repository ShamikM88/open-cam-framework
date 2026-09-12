# OpenCAM Framework 🏦

An open-source, agentic credit underwriting framework powered by Python and Claude.

## Features
- 🤖 **Maker-Checker Architecture**: Dual-agent system (Underwriter & Risk Inspector).
- 🎨 **Style Calibration**: Programmatically matches your historical bank underwriting style.
- 📁 **Native Exports**: Generates `.docx` reports and `.xlsx` financial spreading sheets.
- 📂 **Structured Directory Outputs**: Auto-organizes deals under `deals/[Company]/[Proposal_Date]/`.

## Usage Guide
1. Install dependencies: `pip install -r requirements.txt`
2. Calibrate writing style: `python scripts/calibrate.py`
3. Run a deal:
   ```bash
   python scripts/orchestrator.py --company "Acme Corp" --proposal "Fleet Loan" --type "asset_finance"
