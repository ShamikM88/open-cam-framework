# OpenCAM Framework 🏦

OpenCAM is an open-source, prompt-engineered framework designed to standardize and automate Commercial & Asset Finance Credit Assessment Memorandums (CAMs) using Large Language Models (LLMs).

## Directory Structure
- `config/system_instructions.md`: Master underwriting instructions and operational rules.
- `config/skills_registry.md`: Slash command workflows (`/triage`, `/spread`, `/commercial`, `/collateral`, `/assemble`).
- `templates/`: Production-ready Markdown templates for Corporate Credit and Asset Finance.

## Quickstart Guide
1. Create a Project inside **Claude.ai** (or Custom GPT).
2. Combine `config/system_instructions.md` and `config/skills_registry.md` into the **Custom Instructions** field.
3. Upload the files in `templates/` to **Project Knowledge**.
4. Upload deal documents into a new chat and execute `/triage` ➔ `/spread` ➔ `/commercial` ➔ `/collateral` ➔ `/assemble`.
