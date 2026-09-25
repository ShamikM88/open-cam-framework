"""Draft-compliance checks used by scripts/orchestrator.py (the headless
pipeline). Factored out into its own module -- rather than left inline in
orchestrator.py -- specifically so a future standalone CLI can wrap it and
be called from the /assemble and /review slash commands' Bash steps, the
same way deal_export.py/state_manager.py/template_resolver.py already are
(not yet wired up as of this module's introduction).

Dependency-free (no anthropic/docx/openpyxl imports), matching those
modules' pattern, so it's callable from a Bash step -- or unit tested --
without pulling in heavy dependencies. This is what will let the
slash-command interface run the exact same deterministic checks
orchestrator.py's headless pipeline does, instead of asking an LLM to
reimplement this logic in prose (which would defeat the point of
code-enforced policy).
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
    ({"cp_ids_included": [...], "cs_ids_included": [...],
    "risk_categories_covered": {...}, "reported_figures": {...},
    "downside_breaches_acknowledged": [...], "sources": [...],
    "financials_source_disclosed": true, "credit_policy_considered": true})
    from its drafted CAM (see
    agents/underwriter_agent.md's Structured Output guideline). Scans
    matches in reverse and returns the first one that actually looks like
    this schema, for the same reason parse_verdict() (orchestrator.py)
    does: a block should always be trailing, but this is safer against an
    unrelated ```json block appearing earlier in the draft.

    Missing or malformed output degrades to an empty structure, which then
    fails every downstream policy check safely (as "nothing included" /
    "no category covered" / "nothing reported" / "nothing acknowledged" /
    "no sources cited" / "not disclosed") rather than raising or silently
    skipping enforcement. This includes a field being present but the wrong
    *type* -- e.g. `risk_categories_covered` as a JSON list instead of an
    object -- not just a field being absent, since check_draft_compliance()
    assumes these exact types.
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
            or "cs_ids_included" in payload
            or "risk_categories_covered" in payload
            or "reported_figures" in payload
            or "downside_breaches_acknowledged" in payload
            or "sources" in payload
            or "financials_source_disclosed" in payload
            or "credit_policy_considered" in payload
        ):
            cp_ids_included = payload.get("cp_ids_included")
            cs_ids_included = payload.get("cs_ids_included")
            risk_categories_covered = payload.get("risk_categories_covered")
            reported_figures = payload.get("reported_figures")
            downside_breaches_acknowledged = payload.get("downside_breaches_acknowledged")
            sources = payload.get("sources")
            return {
                "cp_ids_included": cp_ids_included if isinstance(cp_ids_included, list) else [],
                "cs_ids_included": cs_ids_included if isinstance(cs_ids_included, list) else [],
                "risk_categories_covered": (
                    risk_categories_covered if isinstance(risk_categories_covered, dict) else {}
                ),
                "reported_figures": reported_figures if isinstance(reported_figures, dict) else {},
                "downside_breaches_acknowledged": (
                    downside_breaches_acknowledged if isinstance(downside_breaches_acknowledged, list) else []
                ),
                "sources": sources if isinstance(sources, list) else [],
                "financials_source_disclosed": payload.get("financials_source_disclosed") is True,
                "credit_policy_considered": payload.get("credit_policy_considered") is True,
            }
    return {
        "cp_ids_included": [], "cs_ids_included": [], "risk_categories_covered": {},
        "reported_figures": {}, "downside_breaches_acknowledged": [], "sources": [],
        "financials_source_disclosed": False, "credit_policy_considered": False,
    }


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


def ground_truth_figures(financials, ratios, collateral, downside_case=None):
    """The complete set of figures the Underwriter is allowed to cite a
    number for, and what that number must actually be: every FY-Current
    ratio (dscr, gross_leverage, net_debt_to_ebitda, current_ratio, gearing,
    ebit_interest_cover, ebitda_interest_cover, fcf_conversion_pct, plus the
    "EBIT/Interest"/"EBITDA/Interest" aliases) and subtotal (ebitda,
    tangible_net_worth, gross_profit, operating_profit, net_profit,
    profit_before_tax, fcf, total_debt, total_assets, total_liabilities,
    total_equity) already computed by evaluate_financial_model() -- this
    docstring is descriptive, not a closed allowlist: every key
    evaluate_financial_model() returns flows through automatically, so a
    future new ratio/subtotal needs no change here -- plus an aggregate
    collateral_cover_pct derived from the collateral list, plus -- if
    `downside_case` is given -- every downside (stressed forward-year)
    ratio/subtotal, keyed as `f"{metric}_{period}_downside"` (e.g.
    `"dscr_FY+2_downside"`) so it can never collide with a base-case key
    above. A pure function of the same data already checkpointed to
    state.json -- never a new calculation the Underwriter couldn't already
    see.

    A ratio evaluate_financial_model() left as `None` (e.g. a debt-free
    company's DSCR -- a zero denominator makes the ratio undefined, not
    zero) is excluded here entirely, the same way collateral_cover_pct is
    only added when it's resolvable. If it weren't, the key would still be
    "in" ground truth with value None, and any reported figure for it would
    fail values_match() against None every single time (float(None) always
    raises) -- an unconditional, unfixable "Narrative/Ground-Truth
    Mismatch" for a metric that's legitimately undefined, rather than the
    correct outcome: it's simply not a figure this deal has a number for.
    The same exclusion applies to downside figures below, for the same
    reason.
    """
    current_ratios = (ratios or {}).get("FY-Current")
    current_financials = (financials or {}).get("FY-Current")

    truth = {}
    truth.update({
        k: v for k, v in (current_ratios if isinstance(current_ratios, dict) else {}).items()
        if v is not None
    })
    current_financials = dict(current_financials) if isinstance(current_financials, dict) else {}
    current_financials.pop("raw", None)  # a nested dict of raw inputs, not a figure itself
    truth.update({k: v for k, v in current_financials.items() if v is not None})

    collateral_cover_pct = compute_collateral_cover_pct(collateral)
    if collateral_cover_pct is not None:
        truth["collateral_cover_pct"] = collateral_cover_pct

    if downside_case:
        downside_ratios = downside_case.get("ratios") or {}
        downside_financials = downside_case.get("financials") or {}

        for period, period_ratios in downside_ratios.items():
            if not isinstance(period_ratios, dict):
                continue
            for metric, value in period_ratios.items():
                if value is not None:
                    truth[f"{metric}_{period}_downside"] = value

        for period, period_financials in downside_financials.items():
            if not isinstance(period_financials, dict):
                continue
            for metric, value in period_financials.items():
                if metric == "raw" or value is None:
                    continue
                truth[f"{metric}_{period}_downside"] = value

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


def check_draft_compliance(draft_text, policy_state, ground_truth_figures_dict, financials_source=None,
                            credit_policy_present=None):
    """The single source of truth for "why would this draft be
    code-enforced-REJECTED": covenant FAIL/UNRESOLVABLE, any security gap,
    and -- only when a draft is actually being audited -- missing required
    CPs, missing required CSs, missing/malformed risk-taxonomy coverage,
    undisclosed downside covenant breaches, narrative/ground-truth figure
    mismatches (base-case and downside alike), and an undisclosed
    analyst-supplied spreading caveat (see below).

    `financials_source` is this deal's state.json field of the same name
    (`"analyst-supplied"`, `"framework-computed"`, or `None`/absent --
    everything except `"analyst-supplied"` is treated identically, matching
    agents/underwriter_agent.md's Guideline 9). Only when it's exactly
    `"analyst-supplied"` does the Underwriter's own self-declared
    `financials_source_disclosed` get checked -- self-declared and only
    verified for presence, not wording, exactly like `sources` above;
    matches Guideline 9's own "the CAM must carry an explicit caveat"
    requirement with the same code-enforcement discipline every other
    Guideline 5 field already has.

    `credit_policy_present` is a fork-wide fact (does `config/credit_policy.md`
    exist, per `/calibrate-policy`), not deal-specific state -- callers
    resolve it themselves (see policy_check.py/orchestrator.py). When
    truthy, this only checks that the Underwriter's own self-declared
    `credit_policy_considered` is present -- a floor, not a correctness
    check (see agents/underwriter_agent.md's Guideline 10). Whether the
    draft actually *complies* with the policy document is the Risk
    Reviewer's own independent, qualitative audit responsibility (see
    agents/risk_reviewer_agent.md's Audit Checklist item 5) -- that
    judgment isn't and can't be code-enforced the way a numeric threshold
    or an exact-ID match can.

    A downside covenant breach itself (policy_state's
    `downside_covenant_breaches`) never forces a reason on its own -- a
    deal can be sound with disclosed downside risk; see
    policy_engine.evaluate_deal_policy(). What's enforced here is
    disclosure: every breach_id must appear in the Underwriter's own
    `downside_breaches_acknowledged` list, or it's treated exactly like any
    other omission -- the same unified rejection path as a missing CP.

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

        cs_ids_included = set(underwriter_output["cs_ids_included"])
        for cs in policy_state.get("required_conditions_subsequent", []):
            if cs["cs_id"] not in cs_ids_included:
                reasons.append(f"Missing Required Condition Subsequent {cs['cs_id']}: {cs['text']}")

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

        acknowledged_breach_ids = set(underwriter_output["downside_breaches_acknowledged"])
        for breach in policy_state.get("downside_covenant_breaches", []):
            if breach["breach_id"] not in acknowledged_breach_ids:
                reasons.append(
                    f"Undisclosed Downside Breach {breach['breach_id']}: {breach['metric']} "
                    f"breaches threshold in {breach['year']} under stress but is not "
                    "addressed in the draft."
                )

        # A lighter-weight companion to the CP/taxonomy/figure checks above:
        # this doesn't verify a citation's *accuracy* (a narrative claim like
        # "founded in 1990" can't be checked the way a numeric ratio can),
        # only that the Underwriter declared at least one source for the
        # narrative claims (company history, management, market/competitive)
        # its own Grounding guideline requires it to cite. An empty list
        # means either no sources were actually used (a real grounding gap)
        # or they were used but never declared -- both are worth catching.
        if not [s for s in underwriter_output["sources"] if str(s).strip()]:
            reasons.append(
                "Missing Narrative Sources: no citation sources were declared for this "
                "draft's narrative claims (company history, management, market/competitive) "
                "-- see agents/underwriter_agent.md's Structured Output guideline."
            )

        # Guideline 9 requires a visible caveat in the CAM whenever this
        # deal's spreading was analyst-supplied rather than independently
        # recomputed -- a real, deliberate reduction in audit guarantee that
        # must never go unnoticed. Only checked for this one specific
        # financials_source value: "framework-computed" and absent/None are
        # both the normal case and need no disclosure at all.
        if financials_source == "analyst-supplied" and not underwriter_output["financials_source_disclosed"]:
            reasons.append(
                "Missing Analyst-Supplied Spreading Disclosure: this deal's financials_source "
                "is 'analyst-supplied', but the draft did not declare "
                "financials_source_disclosed=true -- see agents/underwriter_agent.md's "
                "Guideline 9 (the CAM's Financial Analysis section must carry an explicit "
                "caveat when spreading wasn't independently recomputed by the framework)."
            )

        # Guideline 10 requires the Underwriter to at least declare it
        # considered this fork's calibrated credit policy whenever one
        # exists -- a floor-level presence check only, exactly like
        # financials_source_disclosed above. Whether the draft actually
        # *complies* with the policy is never checked here -- that's the
        # Risk Reviewer's own independent, qualitative audit (Audit
        # Checklist item 5), not something this deterministic layer can
        # judge.
        if credit_policy_present and not underwriter_output["credit_policy_considered"]:
            reasons.append(
                "Missing Credit Policy Consideration: this fork has a calibrated institutional "
                "credit policy (config/credit_policy.md), but the draft did not declare "
                "credit_policy_considered=true -- see agents/underwriter_agent.md's "
                "Guideline 10."
            )

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
