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
import os

from policy_checks import check_draft_compliance, ground_truth_figures
from policy_engine import evaluate_deal_policy
from state_manager import read_state


def compute(company, proposal, draft_path=None):
    """Returns {"policy_state", "reasons", "compliant", "cam_data_present"}.

    Call without `draft_path` before a draft exists (e.g. /assemble
    building its drafting context) to get `policy_state` -- required CPs,
    covenant results, security gaps -- with `reasons` limited to
    deal-structural issues (covenant/security) that don't depend on a draft.
    Call with `draft_path` (e.g. /review auditing a finished draft) to also
    run the CP-completeness, risk-taxonomy, narrative-accuracy, (when this
    deal's own `financials_source` is `"analyst-supplied"`) caveat-
    disclosure, and (when this fork has a calibrated `config/credit_policy.md`
    -- see /calibrate-policy) credit-policy-consideration checks against
    that draft's trailing structured JSON block.

    `cam_data_present` (issue #87) is `False` for a research-only deal
    (see /research) -- one whose state.json has never had `financials`,
    `ratios`, `covenants`, or `security_package` written to it, because it
    never ran `/spread`. This is a defense-in-depth signal, not the primary
    mechanism: /review's own --research-brief mode already knows not to
    call this function at all for a research brief (see review.md's
    "Code-enforced check" guard). Without this flag, a caller that invoked
    this function against a research-only deal anyway would have no way to
    tell "compliant": true apart from a real, verified CAM -- every CP/
    covenant/taxonomy/figure check would simply find nothing applicable to
    flag, not because the brief is actually sound, but because none of
    that data exists yet for this deal.
    """
    state = read_state(company, proposal) or {}
    financials = state.get("financials") or {}
    ratios = state.get("ratios") or {}
    collateral = state.get("collateral") or []
    covenants = state.get("covenants") or []
    security_package = state.get("security_package") or []
    downside_case = state.get("downside_case") or {}
    financials_source = state.get("financials_source")
    cam_data_present = bool(financials or ratios or covenants or security_package)
    # Fork-wide fact, not deal-specific state.json data -- same reasoning
    # as orchestrator.py's own read of config/style_guide.md.
    credit_policy_present = os.path.exists("config/credit_policy.md")

    policy_state = evaluate_deal_policy({
        "ratios": ratios,
        "collateral": collateral,
        "covenants": covenants,
        "security_package": security_package,
        "guarantees": state.get("guarantees") or [],
        "downside_case": downside_case,
    })

    draft_text = None
    if draft_path:
        with open(draft_path, encoding="utf-8") as f:
            draft_text = f.read()

    reasons = check_draft_compliance(
        draft_text, policy_state,
        ground_truth_figures(financials, ratios, collateral, downside_case),
        financials_source=financials_source,
        credit_policy_present=credit_policy_present,
    )

    return {
        "policy_state": policy_state,
        "reasons": reasons,
        "compliant": len(reasons) == 0,
        "cam_data_present": cam_data_present,
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
