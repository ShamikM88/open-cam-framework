"""Draft-compliance checks shared between scripts/orchestrator.py (the
headless pipeline) and scripts/policy_check.py (a standalone CLI callable
from the /assemble and /review slash commands' Bash steps).

Dependency-free (no anthropic/docx/openpyxl imports), matching
template_resolver.py/state_manager.py/deal_export.py's pattern, so it's
callable from a Bash step -- or unit tested -- without pulling in heavy
dependencies. This is what lets the slash-command interface run the exact
same deterministic checks orchestrator.py's headless pipeline does, instead
of asking an LLM to reimplement this logic in prose (which would defeat the
point of code-enforced policy).
"""
import json
import re
import sys

# Canonical risk categories the Underwriter's structured output must cover
# (see agents/underwriter_agent.md's Structured Output guideline).
REQUIRED_RISK_TAXONOMY = [
    "Market", "Refinance", "Operational", "Concentration", "Key Man", "Financial", "Legal",
]

FENCED_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
CATEGORY_SUFFIX_RE = re.compile(r"\s+risk\s*$", re.IGNORECASE)


def normalize_category(category):
    """Fixed string transform (not semantic judgment): lowercase, strip a
    trailing "risk" suffix, trim whitespace -- so "Market Risk" and "Market"
    both normalize to "market" and compare equal without needing the
    Underwriter to match the canonical taxonomy's spelling exactly."""
    return CATEGORY_SUFFIX_RE.sub("", str(category).strip()).strip().lower()


def parse_underwriter_output(draft_text):
    """Extract the Underwriter's trailing structured JSON block
    ({"cp_ids_included": [...], "risk_categories_covered": {...},
    "reported_figures": {...}}) from its drafted CAM (see
    agents/underwriter_agent.md's Structured Output guideline). Scans
    matches in reverse and returns the first one that actually looks like
    this schema, for the same reason parse_verdict() (orchestrator.py)
    does: a block should always be trailing, but this is safer against an
    unrelated ```json block appearing earlier in the draft.

    Missing or malformed output degrades to an empty structure, which then
    fails every downstream policy check safely (as "nothing included" /
    "no category covered" / "nothing reported") rather than raising or
    silently skipping enforcement. This includes a field being present but
    the wrong *type* -- e.g. `risk_categories_covered` as a JSON list
    instead of an object -- not just a field being absent, since
    check_draft_compliance() assumes these exact types.
    """
    for match in reversed(list(FENCED_JSON_RE.finditer(draft_text or ""))):
        try:
            payload = json.loads(match.group(1))
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            print(f"[parse_underwriter_output] Skipping unparseable fenced JSON block: {e}",
                  file=sys.stderr)
            continue
        if isinstance(payload, dict) and (
            "cp_ids_included" in payload
            or "risk_categories_covered" in payload
            or "reported_figures" in payload
        ):
            cp_ids_included = payload.get("cp_ids_included")
            risk_categories_covered = payload.get("risk_categories_covered")
            reported_figures = payload.get("reported_figures")
            return {
                "cp_ids_included": cp_ids_included if isinstance(cp_ids_included, list) else [],
                "risk_categories_covered": (
                    risk_categories_covered if isinstance(risk_categories_covered, dict) else {}
                ),
                "reported_figures": reported_figures if isinstance(reported_figures, dict) else {},
            }
    return {"cp_ids_included": [], "risk_categories_covered": {}, "reported_figures": {}}


def compute_collateral_cover_pct(collateral):
    """Aggregate collateral cover %, matching the same
    collateral_value/exposure ratio spreading_builder.py's Collateral &
    Exposure sheet computes as an Excel formula -- expressed as a
    percentage (0-100 scale, matching how it's normally quoted in a CAM)
    rather than the workbook's raw 0-1 fraction.

    Returns None (rather than 0) when there's nothing to compute from, so
    it's simply omitted from the ground-truth figures instead of being
    compared against as if it were a real zero.
    """
    total_exposure = sum((asset.get("exposure") or 0) for asset in (collateral or []))
    total_collateral_value = sum((asset.get("collateral_value") or 0) for asset in (collateral or []))
    if not total_exposure:
        return None
    return (total_collateral_value / total_exposure) * 100


def ground_truth_figures(financials, ratios, collateral):
    """The complete set of figures the Underwriter is allowed to cite a
    number for, and what that number must actually be: every FY-Current
    ratio (dscr, gross_leverage, current_ratio, gearing,
    ebit_interest_cover, ebitda_interest_cover, plus the "EBIT/Interest"/
    "EBITDA/Interest" aliases) and subtotal (ebitda, tangible_net_worth,
    gross_profit, operating_profit, net_profit, profit_before_tax,
    total_debt, total_assets, total_liabilities, total_equity) already
    computed by evaluate_financial_model(), plus an aggregate
    collateral_cover_pct derived from the collateral list. A pure function
    of the same data already checkpointed to state.json -- never a new
    calculation the Underwriter couldn't already see.
    """
    current_ratios = (ratios or {}).get("FY-Current")
    current_financials = (financials or {}).get("FY-Current")

    truth = {}
    truth.update(current_ratios if isinstance(current_ratios, dict) else {})
    current_financials = dict(current_financials) if isinstance(current_financials, dict) else {}
    current_financials.pop("raw", None)  # a nested dict of raw inputs, not a figure itself
    truth.update(current_financials)

    collateral_cover_pct = compute_collateral_cover_pct(collateral)
    if collateral_cover_pct is not None:
        truth["collateral_cover_pct"] = collateral_cover_pct

    return truth


def values_match(reported_value, actual_value, relative_tolerance=0.005):
    """A fixed 0.5% relative tolerance, applied consistently to every
    metric (ratios and currency amounts alike) rather than picking a
    different rule per metric. Guards the zero case explicitly since a
    relative tolerance is undefined against an actual value of exactly 0.
    """
    try:
        reported_value = float(reported_value)
        actual_value = float(actual_value)
    except (TypeError, ValueError):
        return False
    if actual_value == 0:
        return abs(reported_value) < 1e-9
    return abs(reported_value - actual_value) / abs(actual_value) <= relative_tolerance


def check_reported_figures(reported_figures, ground_truth_figures_dict):
    """Compare every figure the Underwriter's structured output declares
    against the actual computed ground truth. A key with no corresponding
    computed value is flagged UNRESOLVABLE_REPORTED_FIGURE -- the same
    fallback discipline policy_engine.py's covenant-metric check uses --
    rather than silently ignored, and any mismatch beyond tolerance is
    reported with both the reported and computed values so the revision
    prompt can point the Underwriter at exactly what to fix.
    """
    reasons = []
    for metric, reported_value in (reported_figures or {}).items():
        if metric not in ground_truth_figures_dict:
            reasons.append(
                f"UNRESOLVABLE_REPORTED_FIGURE: '{metric}' is not a recognized computed "
                f"figure (reported value: {reported_value})."
            )
            continue
        actual_value = ground_truth_figures_dict[metric]
        if not values_match(reported_value, actual_value):
            reasons.append(
                f"Narrative/Ground-Truth Mismatch: reported {metric} {reported_value} "
                f"vs computed {actual_value}"
            )
    return reasons


def check_draft_compliance(draft_text, policy_state, ground_truth_figures_dict):
    """The single source of truth for "why would this draft be
    code-enforced-REJECTED": covenant FAIL/UNRESOLVABLE, any security gap,
    and -- only when a draft is actually being audited -- missing required
    CPs, missing/malformed risk-taxonomy coverage, and narrative/ground-
    truth figure mismatches.

    `draft_text=None` means "no draft to audit yet" (e.g. /assemble calling
    this before drafting, just to obtain policy_state for its own drafting
    context): covenant/security reasons -- which depend only on the deal's
    own recorded structure, not on any draft -- are still returned, but the
    CP/taxonomy/reported-figures checks are skipped entirely rather than
    reporting every required CP as "missing" against a draft that doesn't
    exist yet. Passing `draft_text=""` (an empty but real draft) does run
    those checks, and will report everything as missing/malformed, which is
    correct: an empty draft really is missing all of it.

    Returns a plain list of human-readable reason strings, empty if fully
    compliant. Callers decide what a non-empty list means for their own
    verdict (see orchestrator.py's _apply_deterministic_policy_checks(),
    which folds these into the same `notes` field an LLM-originated
    rejection uses -- and .claude/commands/review.md, which is told to do
    the same in prose).
    """
    reasons = []

    if draft_text is not None:
        underwriter_output = parse_underwriter_output(draft_text)

        cp_ids_included = set(underwriter_output["cp_ids_included"])
        for cp in policy_state.get("required_conditions_precedent", []):
            if cp["cp_id"] not in cp_ids_included:
                reasons.append(f"Missing Required CP {cp['cp_id']}: {cp['text']}")

        covered_by_normalized_key = {
            normalize_category(key): value
            for key, value in underwriter_output["risk_categories_covered"].items()
        }
        for category in REQUIRED_RISK_TAXONOMY:
            entry = covered_by_normalized_key.get(normalize_category(category))
            raw_status = entry.get("status") if isinstance(entry, dict) else None
            status = str(raw_status).strip().lower() if raw_status is not None else None
            justification = entry.get("justification") if isinstance(entry, dict) else None
            malformed = status not in ("covered", "not_applicable") or (
                status == "not_applicable" and not str(justification or "").strip()
            )
            if malformed:
                reasons.append(f"Missing or malformed Risk Category: {category}")

    for result in policy_state.get("covenant_results", []):
        if result["status"] in ("FAIL", "UNRESOLVABLE"):
            reasons.append(
                f"Covenant {result['status']}: {result['metric']} "
                f"({result['type']} {result['threshold']}, actual {result['actual']})"
            )

    reasons.extend(policy_state.get("security_gaps", []))

    if draft_text is not None:
        reasons.extend(
            check_reported_figures(underwriter_output["reported_figures"], ground_truth_figures_dict)
        )

    return reasons
