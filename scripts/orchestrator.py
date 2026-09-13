import json
import os
import re
import sys
import argparse

from anthropic import Anthropic

from deal_export import export_deal
from spreading_builder import evaluate_financial_model
from state_manager import write_state, append_review_trail, state_path
from template_resolver import cam_template_path

MODEL = "claude-3-7-sonnet-20250219"
MAX_REVIEW_ITERATIONS = 3

VERDICT_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _default_client():
    return Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _load_json_file(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_multi_period_financials(financials_path, spread_path):
    """--spread takes precedence over --financials when both are given."""
    spread_data = _load_json_file(spread_path)
    if spread_data is not None:
        return spread_data
    return _load_json_file(financials_path)


def parse_verdict(response_text):
    """Extract the Risk Reviewer's fenced ```json {"verdict": ..., "notes": ...}```
    block from its response.

    Returns (verdict, notes) with verdict normalized to exactly "APPROVED" or
    "REJECTED". Falls back to ("REJECTED", response_text) if no such block is
    present, isn't valid JSON, or doesn't carry a recognized verdict -- an
    unparseable review must never be silently treated as an approval.
    """
    match = VERDICT_JSON_RE.search(response_text or "")
    if match:
        try:
            payload = json.loads(match.group(1))
            verdict = str(payload.get("verdict", "")).strip().upper()
            if verdict in ("APPROVED", "REJECTED"):
                return verdict, payload.get("notes")
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    return "REJECTED", response_text


def _build_grounding_context(company, proposal, pd_score, lgd_score, model_data, collateral_data):
    """The only place raw financials/ratios/collateral are injected into
    either agent's prompt -- both the Maker (draft + revision calls) and the
    Checker (audit call) receive exactly this block, so the Checker is
    auditing the draft against the same ground truth the Maker was given,
    not just against the draft's own internal consistency.
    """
    parts = [
        f"\nCompany: {company}",
        f"Proposal: {proposal}",
        f"PD: {pd_score}",
        f"LGD: {lgd_score}",
        "\nGrounded multi-period financials (from state.json -- the only "
        "source of truth for these figures; never invent or adjust them):",
        f"```json\n{json.dumps(model_data.get('financials', {}), indent=2)}\n```",
        "\nProgrammatically calculated ratios (from state.json -- re-verify "
        "the draft's stated figures against these; never recompute independently):",
        f"```json\n{json.dumps(model_data.get('ratios', {}), indent=2)}\n```",
    ]
    if collateral_data:
        parts.append("\nCollateral / exposure data (from state.json):")
        parts.append(f"```json\n{json.dumps(collateral_data, indent=2)}\n```")
    return "\n".join(parts)


def run_pipeline(company, proposal, pd_score, lgd_score, deal_type,
                  multi_period_financials=None, collateral_data=None,
                  client=None, max_iterations=MAX_REVIEW_ITERATIONS):
    client = client or _default_client()

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

    model_data = evaluate_financial_model(multi_period_financials or {})

    # Checkpoint the grounded figures before either agent is called: see
    # CLAUDE.md's "Context Window & State Management Protocol" -- these
    # numbers must exist on disk, not only in the prompts about to be sent.
    steps_completed = ["spread"] if multi_period_financials else []
    write_state(company, proposal, deal_type=deal_type,
                inputs={"pd": pd_score, "lgd": lgd_score},
                financials=model_data["financials"], ratios=model_data["ratios"],
                collateral=collateral_data or [],
                steps_completed=steps_completed)

    grounding_context = _build_grounding_context(
        company, proposal, pd_score, lgd_score, model_data, collateral_data,
    )

    print(f"[1/3] Underwriter Agent drafting CAM for {company}...")
    draft = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content":
                   f"{maker_prompt}\nStyle:\n{style_guide}\n{template_section}{grounding_context}"}]
    ).content[0].text
    write_state(company, proposal, deal_type=deal_type,
                steps_completed=steps_completed + ["draft"])

    deal_dir = os.path.dirname(state_path(company, proposal))
    os.makedirs(deal_dir, exist_ok=True)

    verdict, notes = "REJECTED", None
    approved_draft = None

    for iteration in range(1, max_iterations + 1):
        draft_path = os.path.join(deal_dir, f"draft_v{iteration}.md")
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(draft)

        print(f"[2/3] Risk Reviewer Agent auditing draft (iteration {iteration}/{max_iterations})...")
        audit_response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content":
                       f"{checker_prompt}\n{grounding_context}\nDraft to review:\n{draft}"}]
        ).content[0].text

        verdict, notes = parse_verdict(audit_response)
        append_review_trail(company, proposal, verdict=verdict, notes=notes,
                             deal_type=deal_type,
                             steps_completed=steps_completed + ["draft", "audit"])

        if verdict == "APPROVED":
            approved_draft = draft
            break

        if iteration < max_iterations:
            print(f"[Revise] Iteration {iteration} REJECTED: {notes}")
            draft = client.messages.create(
                model=MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content":
                           f"{maker_prompt}\nStyle:\n{style_guide}\n{template_section}{grounding_context}\n"
                           f"Previous draft:\n{draft}\n"
                           f"The Risk Reviewer rejected this draft. Address every point below "
                           f"and produce a revised, complete draft:\n{notes}"}]
            ).content[0].text

    if verdict != "APPROVED":
        write_state(company, proposal, deal_type=deal_type, review_verdict="REJECTED",
                    steps_completed=steps_completed + ["draft", "audit"])
        print(f"[FAILED] No APPROVED draft after {max_iterations} review iteration(s). "
              "Exiting without export.")
        sys.exit(1)

    print(f"[3/3] Exporting .docx and .xlsx files...")
    output_dir = export_deal(company, proposal, deal_type, approved_draft)
    write_state(company, proposal, deal_type=deal_type,
                draft_path=os.path.join(output_dir, f"{company}_{proposal}_CAM.docx"),
                steps_completed=steps_completed + ["draft", "audit", "export"])
    print(f"Done! Files generated in {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--pd", default="0.20%")
    parser.add_argument("--lgd", default="LGD 3 (15%)")
    parser.add_argument("--type", default="corporate_credit")
    parser.add_argument("--financials",
                         help="Path to a JSON file of multi-period raw financials: "
                              '{"FY-2": {...}, "FY-1": {...}, "FY-Current": {...}}')
    parser.add_argument("--spread",
                         help="Path to a JSON file in the same shape as --financials; "
                              "takes precedence over --financials if both are given")
    parser.add_argument("--collateral",
                         help="Path to a JSON file containing a flat list of collateral asset dicts")
    args = parser.parse_args()

    multi_period_financials = _load_multi_period_financials(args.financials, args.spread)
    collateral_data = _load_json_file(args.collateral)

    run_pipeline(args.company, args.proposal, args.pd, args.lgd, args.type,
                 multi_period_financials=multi_period_financials,
                 collateral_data=collateral_data)
