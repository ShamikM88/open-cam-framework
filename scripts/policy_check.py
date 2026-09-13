"""Standalone CLI: compute a deal's policy_state and, if a draft is given,
check it for compliance -- callable from a Claude Code slash command's Bash
step (see .claude/commands/assemble.md, review.md), where Claude itself is
orchestrating the draft/review and needs Python's deterministic answer
rather than reimplementing covenant/CP/taxonomy/narrative-accuracy checks
in prose. This is what gives the slash-command interface the same
code-enforced governance orchestrator.py's headless pipeline already has.

No `anthropic` dependency, matching deal_export.py/template_resolver.py/
state_manager.py's pattern.
"""
import argparse
import json

from policy_checks import check_draft_compliance, ground_truth_figures
from policy_engine import evaluate_deal_policy
from state_manager import read_state


def compute(company, proposal, draft_path=None):
    """Returns {"policy_state", "reasons", "compliant"}.

    Call without `draft_path` before a draft exists (e.g. /assemble
    building its drafting context) to get `policy_state` -- required CPs,
    covenant results, security gaps -- with `reasons` limited to
    deal-structural issues (covenant/security) that don't depend on a draft.
    Call with `draft_path` (e.g. /review auditing a finished draft) to also
    run the CP-completeness, risk-taxonomy, and narrative-accuracy checks
    against that draft's trailing structured JSON block.
    """
    state = read_state(company, proposal) or {}
    financials = state.get("financials") or {}
    ratios = state.get("ratios") or {}
    collateral = state.get("collateral") or []

    policy_state = evaluate_deal_policy({
        "ratios": ratios,
        "collateral": collateral,
        "covenants": state.get("covenants") or [],
        "security_package": state.get("security_package") or [],
        "guarantees": state.get("guarantees") or [],
    })

    draft_text = None
    if draft_path:
        with open(draft_path, encoding="utf-8") as f:
            draft_text = f.read()

    reasons = check_draft_compliance(
        draft_text, policy_state, ground_truth_figures(financials, ratios, collateral),
    )

    return {
        "policy_state": policy_state,
        "reasons": reasons,
        "compliant": len(reasons) == 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compute a deal's deterministic policy_state (required CPs, "
                     "covenant results, security gaps) and, if --draft is given, "
                     "check that draft's structured output for compliance. Prints "
                     "JSON to stdout. No Anthropic dependency -- callable from a "
                     "slash command's Bash step."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--draft", help="Path to a drafted CAM Markdown file to check for compliance")
    args = parser.parse_args()

    print(json.dumps(compute(args.company, args.proposal, draft_path=args.draft), indent=2))
