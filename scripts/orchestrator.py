import hashlib
import json
import os
import sys
import argparse

from anthropic import Anthropic

from deal_export import export_deal
from policy_checks import (
    FENCED_JSON_RE,
    REQUIRED_RISK_TAXONOMY,
    check_draft_compliance,
    compute_collateral_cover_pct as _compute_collateral_cover_pct,
    ground_truth_figures as _ground_truth_figures,
    normalize_category as _normalize_category,
    parse_underwriter_output,
    values_match as _values_match,
    check_reported_figures as _check_reported_figures,
)
from policy_engine import evaluate_deal_policy
from spreading_builder import evaluate_downside_case, evaluate_financial_model
from state_manager import write_state, append_review_trail, read_state, state_path, resolve_date_str
from template_resolver import cam_template_path

def _load_settings():
    """config/settings.json's full contents -- the documented single
    source of truth per CLAUDE.md for the Maker/Checker model and
    temperature configuration below, so neither can silently drift from a
    hardcoded constant. Falls back to {} if the config file is missing or
    malformed rather than raising here -- _resolve_maker_checker_config()
    below is the one that decides whether an empty/incomplete result is
    actually fatal (it is, for a missing maker_model); other settings this
    file may also hold (max_tokens, output_directory, ...) are read
    elsewhere with their own fallback handling and shouldn't be blocked by
    a maker_model-specific failure mode living in this shared loader.
    """
    try:
        with open("config/settings.json", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _resolve_maker_checker_config():
    """The Underwriter ("Maker") and Risk Reviewer ("Checker") are
    independently configurable model/temperature knobs -- institutional
    governance expects independent review to use a structurally different
    model/config, not just a different prompt, to avoid the two sharing the
    same blind spots. Without an explicit "checker_model"/"*_temperature"
    in config/settings.json, the Checker still defaults to the same model
    as the Maker and no temperature override is applied (this framework
    can't invent a second model on its own), but the two are independent
    settings rather than one hardcoded constant shared by every call.

    Reads config/settings.json fresh on every call (matching e.g.
    template_resolver.cam_template_path()'s own read-on-every-call
    convention) rather than freezing the result at import time, both for
    consistency with the rest of this codebase and so a config change is
    picked up by the very next deal run without needing a process restart.

    Raises RuntimeError if "maker_model" isn't set (missing key, missing
    file, or malformed JSON -- all indistinguishable from the caller's
    perspective: there's no model to draft with). config/settings.json is
    checked into the repo with maker_model already set, so a fork gets a
    working config automatically -- a missing value here means the
    checked-in config was deleted, corrupted, or deliberately edited to
    remove it, not "a fresh install that hasn't configured it yet". A
    silent hardcoded fallback would let a real deal draft on an
    unconfigured, untracked model with no indication anything was
    wrong -- exactly the config-drift failure mode issue #34 was about.
    """
    settings = _load_settings()
    maker_model = settings.get("maker_model")
    if not maker_model:
        raise RuntimeError(
            "config/settings.json is missing 'maker_model' (the file may also be "
            "missing or malformed). This framework requires an explicit maker_model "
            "-- see CLAUDE.md's Execution scripts section -- rather than silently "
            "drafting a deal on an unconfigured default model."
        )
    return {
        "maker_model": maker_model,
        "checker_model": settings.get("checker_model") or maker_model,
        "maker_temperature": settings.get("maker_temperature"),
        "checker_temperature": settings.get("checker_temperature"),
    }


MAX_REVIEW_ITERATIONS = 3


def _completion_kwargs(model, temperature):
    """kwargs for client.messages.create() -- temperature is only included
    when actually configured, so omitting it from config/settings.json
    preserves the Anthropic API's own default rather than this module
    silently picking one."""
    kwargs = {"model": model}
    if temperature is not None:
        kwargs["temperature"] = temperature
    return kwargs


def _content_hash(text):
    """Short, stable fingerprint of a prompt file's content at the moment
    it was used to draft/audit a deal -- see model_provenance below."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


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


def _add_step(steps, step):
    """Append `step` to `steps` if it isn't already there -- steps_completed
    must only ever grow, matching the slash commands' own "Append X to
    steps_completed if it isn't already there" convention."""
    return steps if step in steps else steps + [step]


def parse_verdict(response_text):
    """Extract the Risk Reviewer's fenced ```json {"verdict": ..., "notes": ...}```
    block from its response.

    risk_reviewer_agent.md asks for this block "at the very end", but the
    same prompt also hands the reviewer several other ```json blocks (the
    grounding context's financials/ratios/collateral/policy data -- see
    _build_grounding_context()) that it may legitimately quote back while
    explaining a discrepancy. Scanning matches in reverse order and taking
    the last one that actually carries a recognized verdict key -- rather
    than just the first fenced block in the response -- avoids mistaking an
    echoed input block for the real verdict.

    Returns (verdict, notes) with verdict normalized to exactly "APPROVED" or
    "REJECTED". Falls back to ("REJECTED", response_text) if no block carries
    a recognized verdict at all -- an unparseable review must never be
    silently treated as an approval.
    """
    response_text = response_text or ""
    for match in reversed(list(FENCED_JSON_RE.finditer(response_text))):
        try:
            payload = json.loads(match.group(1))
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            print(f"[parse_verdict] Skipping unparseable fenced JSON block: {e}", file=sys.stderr)
            continue
        verdict = str(payload.get("verdict", "")).strip().upper()
        if verdict in ("APPROVED", "REJECTED"):
            return verdict, payload.get("notes")
    return "REJECTED", response_text


def _apply_deterministic_policy_checks(verdict, notes, draft_text, policy_state, ground_truth_figures,
                                        financials_source=None, credit_policy_present=None):
    """Code-enforced overlay on top of the Risk Reviewer's own (qualitative)
    verdict: covenant/security/CP/taxonomy/narrative-accuracy/analyst-
    supplied-disclosure/credit-policy-consideration compliance is checked
    exactly, every time (via policy_checks.check_draft_compliance(), also
    usable from the /assemble and /review slash commands' own Bash-invoked
    checks via scripts/policy_check.py), and can only ever move a verdict
    from APPROVED to REJECTED -- never the reverse.

    Every reason found here is folded into the same `notes` string
    append_review_trail() already records an LLM-originated rejection
    under (never a separate/parallel field), so a code-enforced rejection
    re-prompts the Underwriter through the exact same revision path as a
    Reviewer-originated one.
    """
    reasons = check_draft_compliance(draft_text, policy_state, ground_truth_figures,
                                      financials_source=financials_source,
                                      credit_policy_present=credit_policy_present)

    if not reasons:
        return verdict, notes

    combined_notes = "\n".join(([str(notes)] if notes else []) + reasons)
    return "REJECTED", combined_notes


def _build_grounding_context(company, proposal, pd_score, lgd_score, model_data,
                              collateral_data, policy_state=None, downside_case=None,
                              financials_source=None, credit_policy=None,
                              financials_source_note=None, credit_policy_notes=None):
    """The only place raw financials/ratios/collateral/policy data are
    injected into either agent's prompt -- both the Maker (draft + revision
    calls) and the Checker (audit call) receive exactly this block, so the
    Checker is auditing the draft against the same ground truth the Maker
    was given, not just against the draft's own internal consistency.

    `model_data`'s financials/ratios include forward periods ("FY+1"-
    "FY+3") exactly like historical ones when the deal has them -- this is
    the base case. `downside_case` (if given) is the separate, explicitly
    stressed version of those same forward periods (see
    spreading_builder.evaluate_downside_case()) -- never mixed into the
    base-case financials/ratios above, so the Underwriter can never
    mistake one for the other.

    `financials_source` mirrors state.json's own field of the same name.
    Unlike the slash-command interface, where agents/underwriter_agent.md's
    Guideline 9 can just say "check state.json's financials_source field"
    because the model reads that file directly, this headless pipeline
    never hands state.json itself to the model -- only this prompt. So when
    it's `"analyst-supplied"`, that fact is spelled out explicitly below;
    without this, the Underwriter/Risk Reviewer running headless would have
    no way to know the caveat Guideline 9 requires is even needed.

    `credit_policy` (if given) is this fork's own calibrated institutional
    credit policy -- see /calibrate-policy and config/credit_policy.md. It's
    a fork-wide fact, not deal-specific, so unlike `financials_source` it's
    never persisted to state.json; the caller just reads the file fresh
    (mirroring config/style_guide.md's own read pattern) and passes its
    text straight through here. Deliberately appended into this SHARED
    parts list -- not a Maker-only interpolation like style_guide/
    template_section -- so both the Underwriter (advisory, Guideline 10)
    and the Risk Reviewer (mandatory, Audit Checklist item 4) receive it
    automatically from the one grounding_context object both already reuse,
    with no separate Checker-specific injection point needed.

    `financials_source_note` (issue #58) is a persisted, analyst-confirmed
    description of *what* convention made this deal analyst-supplied (e.g.
    "Depreciation embedded in Cost of Goods Sold") -- see scripts/
    conventions.py and /spread's "Check for a persisted convention" step.
    Unlike `credit_policy`, this genuinely is deal-scoped state (it's
    resolved once per deal at /spread time and copied into that deal's own
    state.json as `financials_source_note`, exactly like `financials_source`
    itself), not a fresh fork-wide file read on every call -- the headless
    pipeline can inherit it from `existing_state` even though it can never
    originate analyst-supplied mode on its own (see run_pipeline()'s own
    financials_source resolution). Only meaningful when `financials_source`
    is `"analyst-supplied"`; ignored otherwise.

    `credit_policy_notes` (issue #58) is this fork's own accumulated,
    analyst-confirmed corrections to how specific `config/credit_policy.md`
    clauses have been interpreted -- see /review's "Persisting a
    policy-interpretation correction" step. Fork-wide, not deal-specific,
    exactly like `credit_policy` itself -- same read-fresh-every-call
    pattern, same shared-parts-list placement so both agents receive it
    with no separate Checker-specific injection point needed.
    """
    parts = [
        f"\nCompany: {company}",
        f"Proposal: {proposal}",
        f"PD: {pd_score}",
        f"LGD: {lgd_score}",
    ]
    if financials_source == "analyst-supplied":
        parts.append(
            "\nThis deal's financials_source is \"analyst-supplied\": the figures/ratios below "
            "were recorded exactly as the analyst provided them from their own institution's "
            "spreading template, not independently recomputed by this framework from raw line "
            "items. Per Guideline 9, the Financial Analysis section must carry an explicit, "
            "visible caveat disclosing this, and your structured output must set "
            "financials_source_disclosed: true once you have -- a code-enforced check rejects "
            "the draft otherwise."
            + (f" The confirmed convention: {financials_source_note}. Cite this verbatim in "
               "your caveat." if financials_source_note else "")
        )
    if credit_policy:
        parts.append(
            "\nInstitutional Credit Policy (this fork's own calibrated lending "
            "criteria, required mitigants, structuring norms, and risk appetite "
            "boundaries -- see /calibrate-policy). Underwriter: draft with this in "
            "mind per Guideline 10 and set credit_policy_considered: true in your "
            "structured output once you have. Risk Reviewer: independently verify "
            "the draft against this per Audit Checklist item 4 and flag any "
            "violation as a REJECTED-worthy finding, regardless of what the "
            "Underwriter declared:"
        )
        parts.append(f"```\n{credit_policy}\n```")
    if credit_policy_notes:
        parts.append(
            "\nCredit Policy Interpretation Notes (analyst-confirmed corrections to how "
            "specific policy clauses have been interpreted in past deals -- see /review. "
            "Apply these interpretations rather than re-flagging an already-resolved point; "
            "never extend a note beyond what it explicitly covers):"
        )
        parts.append(f"```\n{credit_policy_notes}\n```")
    parts += [
        "\nGrounded multi-period financials -- historical and forward-year "
        "base case alike (from state.json -- the only source of truth for "
        "these figures; never invent, extrapolate, or adjust them):",
        f"```json\n{json.dumps(model_data.get('financials', {}), indent=2)}\n```",
        "\nProgrammatically calculated ratios, base case, every period "
        "supplied (from state.json -- re-verify the draft's stated figures "
        "against these; never recompute independently):",
        f"```json\n{json.dumps(model_data.get('ratios', {}), indent=2)}\n```",
    ]
    if collateral_data:
        parts.append("\nCollateral / exposure data (from state.json):")
        parts.append(f"```json\n{json.dumps(collateral_data, indent=2)}\n```")
    if downside_case and (downside_case.get("financials") or downside_case.get("ratios")):
        parts.append(
            "\nDownside (stressed) forward-year financials and ratios (from "
            "state.json -- already computed by applying this deal's "
            "stress_assumptions to the base case above; synthesize these in "
            "your Projections & Sensitivities section, never recompute or "
            "invent a stress impact yourself. Cite any of these figures in "
            "your structured output's `reported_figures` under "
            "`{metric}_{period}_downside` keys, e.g. \"dscr_FY+2_downside\"):"
        )
        parts.append(f"```json\n{json.dumps(downside_case, indent=2)}\n```")
    if policy_state:
        parts.append(
            "\nRequired Conditions Precedent (from policy_state -- render each one's "
            "`text` as prose in the CAM's Conditions Precedent section, and report "
            "inclusion in your structured output by `cp_id` only; a code-level check "
            "rejects the draft if any required cp_id is missing):"
        )
        parts.append(
            f"```json\n{json.dumps(policy_state.get('required_conditions_precedent', []), indent=2)}\n```"
        )
        if policy_state.get("required_conditions_subsequent"):
            parts.append(
                "\nRequired Conditions Subsequent (from policy_state -- post-drawdown, "
                "ongoing monitoring obligations, as opposed to the pre-drawdown Conditions "
                "Precedent above; render each one's `text` as prose in the CAM's Conditions "
                "Precedent & Subsequent section, and report inclusion in your structured "
                "output by `cs_id` only; a code-level check rejects the draft if any "
                "required cs_id is missing):"
            )
            parts.append(
                f"```json\n{json.dumps(policy_state['required_conditions_subsequent'], indent=2)}\n```"
            )
        parts.append(
            "\nCovenant compliance results (from policy_state -- already computed; "
            "narrate the headroom or breach in your commentary, do not recompute):"
        )
        parts.append(f"```json\n{json.dumps(policy_state.get('covenant_results', []), indent=2)}\n```")
        if policy_state.get("security_gaps"):
            parts.append("\nSecurity/collateral gaps identified (from policy_state):")
            parts.append(f"```json\n{json.dumps(policy_state['security_gaps'], indent=2)}\n```")
        if policy_state.get("downside_covenant_breaches"):
            parts.append(
                "\nDownside covenant breaches under stress (from policy_state -- a "
                "covenant that PASSes in the base case but FAILs in the downside "
                "case for a given year. This does not force rejection on its own, "
                "but every breach_id below must be named, with its year/metric/"
                "shortfall and a proposed structural mitigant, in your Projections "
                "& Sensitivities section, and included in your structured output's "
                "`downside_breaches_acknowledged` -- a code-level check rejects the "
                "draft if any breach_id here is left undisclosed):"
            )
            parts.append(f"```json\n{json.dumps(policy_state['downside_covenant_breaches'], indent=2)}\n```")
    return "\n".join(parts)


def run_pipeline(company, proposal, pd_score, lgd_score, deal_type,
                  multi_period_financials=None, collateral_data=None, stress_assumptions=None,
                  client=None, max_iterations=MAX_REVIEW_ITERATIONS, new_review=False):
    client = client or _default_client()

    with open("agents/underwriter_agent.md") as f: maker_prompt = f.read()
    with open("agents/risk_reviewer_agent.md") as f: checker_prompt = f.read()

    maker_checker_config = _resolve_maker_checker_config()

    # Which model and which exact version of each prompt actually produced
    # this deal's draft/audit -- state.json records schema_version already,
    # but not this. If either the model or a prompt file changes later, a
    # historical deal otherwise has no way to identify what generated it (a
    # model-risk-management gap: SR 11-7 / PRA SS1/23-style expectations).
    model_provenance = {
        "maker_model": maker_checker_config["maker_model"],
        "checker_model": maker_checker_config["checker_model"],
        "underwriter_prompt_hash": _content_hash(maker_prompt),
        "risk_reviewer_prompt_hash": _content_hash(checker_prompt),
    }

    style_guide = ""
    if os.path.exists("config/style_guide.md"):
        with open("config/style_guide.md") as f: style_guide = f.read()

    # Fork-wide fact, not deal-specific state.json data -- see
    # _build_grounding_context()'s own docstring for why this is derived
    # fresh here rather than threaded through write_state().
    credit_policy_present = os.path.exists("config/credit_policy.md")
    credit_policy = ""
    if credit_policy_present:
        with open("config/credit_policy.md") as f: credit_policy = f.read()

    credit_policy_notes = ""
    if os.path.exists("config/credit_policy_notes.md"):
        with open("config/credit_policy_notes.md") as f: credit_policy_notes = f.read()

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

    # Never blindly overwrite what an earlier run of this pipeline (or an
    # earlier slash-command step, if this deal was previously advanced that
    # way) already checkpointed: only replace financials/ratios/collateral
    # when this call was actually given new data for them, and only ever
    # *add* to steps_completed, never reset it -- see state_manager
    # .write_state()'s documented shallow-merge contract and CLAUDE.md's
    # re-hydration rule.
    # Resolved once, here, and threaded through every read_state()/
    # write_state()/append_review_trail()/state_path() call below via
    # date_str -- never via new_review=new_review again -- so every call
    # in this run is guaranteed to agree on which dated folder, with no
    # second call site able to silently forget the flag and fall back to
    # auto-discovery (see resolve_date_str()'s own docstring).
    date_str = resolve_date_str(company, proposal, new_review=new_review)
    existing_state = read_state(company, proposal, date_str=date_str) or {}
    steps_completed = list(existing_state.get("steps_completed", []))

    if multi_period_financials:
        model_data = evaluate_financial_model(multi_period_financials)
        financials, ratios = model_data["financials"], model_data["ratios"]
        steps_completed = _add_step(steps_completed, "spread")
        # This call just independently recomputed every ratio from raw
        # line items -- "analyst-supplied" (see #55's /spread alternative
        # mode) only ever comes from a hand-edited state.json, never from
        # this headless recomputation path, so a fresh --financials/
        # --spread run always resets financials_source back to the
        # framework-computed default even if a prior slash-command step
        # had flagged the deal analyst-supplied.
        financials_source = "framework-computed"
        # Same reasoning -- a stale note from a prior analyst-supplied run
        # must not linger, even though it's currently inert (the caveat
        # trigger below already gates on financials_source itself).
        financials_source_note = ""
    else:
        financials = existing_state.get("financials", {})
        ratios = existing_state.get("ratios", {})
        financials_source = existing_state.get("financials_source", "framework-computed")
        # Unlike financials_source itself, this headless pipeline can never
        # *originate* a convention note (see scripts/conventions.py's own
        # docstring -- there's no analyst here to confirm one), but it can
        # legitimately *inherit* one an earlier interactive /spread step
        # already confirmed and checkpointed to this deal's state.json.
        financials_source_note = existing_state.get("financials_source_note", "")

    collateral = collateral_data if collateral_data else existing_state.get("collateral", [])

    # Downside (stressed) forward-year case. The raw forward-year base-case
    # financials and the stress_assumptions to apply to them don't have to
    # be re-supplied together on every call -- each independently falls
    # back to whatever this deal already had checkpointed, exactly like
    # `financials`/`ratios` above. This matters because the two are re-run
    # together whenever EITHER changes: recomputing on every call that has
    # both available (fresh or cached) keeps `downside_case` from ever
    # silently drifting out of sync with the `financials`/`ratios` actually
    # in effect this run -- e.g. a revised --financials file recomputes the
    # downside case against the *new* base data even if --stress-assumptions
    # isn't repeated, and a new --stress-assumptions file alone recomputes
    # it against the same cached base data `financials`/`ratios` were
    # already reusing. Historical periods in `multi_period_financials` are
    # never shocked -- see evaluate_downside_case().
    multi_period_financials_for_downside = (
        multi_period_financials or existing_state.get("multi_period_financials")
    )
    stress_assumptions_to_persist = stress_assumptions or existing_state.get("stress_assumptions", {})
    if multi_period_financials_for_downside and stress_assumptions_to_persist:
        downside_case = evaluate_downside_case(
            multi_period_financials_for_downside, stress_assumptions_to_persist,
        )
    else:
        downside_case = existing_state.get("downside_case", {})
    multi_period_financials_to_persist = (
        multi_period_financials_for_downside or existing_state.get("multi_period_financials", {})
    )

    # Covenants/security/guarantees have no dedicated CLI flags yet -- they
    # come from whatever this deal's state.json already carries (e.g. hand-
    # edited, or written by a future slash-command step). policy_state is
    # a pure function of these plus the freshly (re)computed ratios/
    # collateral/downside_case above, so it's recomputed every run rather
    # than cached.
    policy_state = evaluate_deal_policy({
        "ratios": ratios,
        "collateral": collateral,
        "covenants": existing_state.get("covenants", []),
        "security_package": existing_state.get("security_package", []),
        "guarantees": existing_state.get("guarantees", []),
        "downside_case": downside_case,
    })
    # Same inputs the loop below already holds fixed across iterations
    # (financials/ratios/collateral/downside_case don't change draft-to-draft
    # within one run), so this is computed once and reused rather than
    # per-iteration.
    ground_truth_figures = _ground_truth_figures(financials, ratios, collateral, downside_case)

    # Checkpoint the grounded figures before either agent is called: see
    # CLAUDE.md's "Context Window & State Management Protocol" -- these
    # numbers must exist on disk, not only in the prompts about to be sent.
    write_state(company, proposal, deal_type=deal_type, date_str=date_str,
                inputs={"pd": pd_score, "lgd": lgd_score},
                financials=financials, ratios=ratios, collateral=collateral,
                financials_source=financials_source, financials_source_note=financials_source_note,
                downside_case=downside_case, stress_assumptions=stress_assumptions_to_persist,
                multi_period_financials=multi_period_financials_to_persist,
                policy_state=policy_state, steps_completed=steps_completed,
                model_provenance=model_provenance)

    grounding_context = _build_grounding_context(
        company, proposal, pd_score, lgd_score,
        {"financials": financials, "ratios": ratios}, collateral, policy_state, downside_case,
        financials_source=financials_source,
        credit_policy=credit_policy,
        financials_source_note=financials_source_note,
        credit_policy_notes=credit_policy_notes,
    )

    print(f"[1/3] Underwriter Agent drafting CAM for {company}...")
    draft = client.messages.create(
        **_completion_kwargs(maker_checker_config["maker_model"], maker_checker_config["maker_temperature"]),
        max_tokens=4000,
        messages=[{"role": "user", "content":
                   f"{maker_prompt}\nStyle:\n{style_guide}\n{template_section}{grounding_context}"}]
    ).content[0].text
    steps_completed = _add_step(steps_completed, "draft")
    write_state(company, proposal, deal_type=deal_type, date_str=date_str, steps_completed=steps_completed,
                financials_source=financials_source, model_provenance=model_provenance)

    deal_dir = os.path.dirname(state_path(company, proposal, date_str=date_str))
    os.makedirs(deal_dir, exist_ok=True)

    verdict, notes = "REJECTED", None
    approved_draft = None

    for iteration in range(1, max_iterations + 1):
        draft_path = os.path.join(deal_dir, f"draft_v{iteration}.md")
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(draft)

        print(f"[2/3] Risk Reviewer Agent auditing draft (iteration {iteration}/{max_iterations})...")
        audit_response = client.messages.create(
            **_completion_kwargs(maker_checker_config["checker_model"], maker_checker_config["checker_temperature"]),
            max_tokens=2000,
            messages=[{"role": "user", "content":
                       f"{checker_prompt}\n{grounding_context}\nDraft to review:\n{draft}"}]
        ).content[0].text

        verdict, notes = parse_verdict(audit_response)
        verdict, notes = _apply_deterministic_policy_checks(
            verdict, notes, draft, policy_state, ground_truth_figures,
            financials_source=financials_source,
            credit_policy_present=credit_policy_present,
        )
        steps_completed = _add_step(steps_completed, "audit")
        append_review_trail(company, proposal, verdict=verdict, notes=notes,
                             deal_type=deal_type, steps_completed=steps_completed,
                             date_str=date_str, model_provenance=model_provenance)

        if verdict == "APPROVED":
            approved_draft = draft
            break

        if iteration < max_iterations:
            print(f"[Revise] Iteration {iteration} REJECTED: {notes}")
            draft = client.messages.create(
                **_completion_kwargs(maker_checker_config["maker_model"], maker_checker_config["maker_temperature"]),
                max_tokens=4000,
                messages=[{"role": "user", "content":
                           f"{maker_prompt}\nStyle:\n{style_guide}\n{template_section}{grounding_context}\n"
                           f"Previous draft:\n{draft}\n"
                           f"The Risk Reviewer rejected this draft. Address every point below "
                           f"and produce a revised, complete draft:\n{notes}"}]
            ).content[0].text

    if verdict != "APPROVED":
        write_state(company, proposal, deal_type=deal_type, review_verdict="REJECTED",
                    date_str=date_str, steps_completed=steps_completed,
                    financials_source=financials_source, model_provenance=model_provenance)
        print(f"[FAILED] No APPROVED draft after {max_iterations} review iteration(s). "
              "Exiting without export.")
        sys.exit(1)

    print(f"[3/3] Exporting .docx and .xlsx files...")
    output_dir = export_deal(company, proposal, deal_type, approved_draft)
    steps_completed = _add_step(steps_completed, "export")
    write_state(company, proposal, deal_type=deal_type, date_str=date_str,
                draft_path=os.path.join(output_dir, f"{company}_{proposal}_CAM.docx"),
                steps_completed=steps_completed, model_provenance=model_provenance)
    print(f"Done! Files generated in {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--pd", default="0.20%")
    parser.add_argument("--lgd", default="LGD 3 (15%)")
    parser.add_argument("--type", default="corporate_credit")
    parser.add_argument("--financials",
                         help="Path to a JSON file of multi-period raw financials, dict-keyed by "
                              "period label -- historical ('FY-2', 'FY-1', 'FY-Current') and/or "
                              "forward ('FY+1', 'FY+2', 'FY+3'), any subset of either, same "
                              "granular schema for all of them: "
                              '{"FY-2": {...}, "FY-1": {...}, "FY-Current": {...}, "FY+1": {...}}. '
                              "Forward-year figures are grounded, user-supplied input (e.g. "
                              "management's own forecast) exactly like historical ones -- never "
                              "derived or extrapolated by this tool.")
    parser.add_argument("--spread",
                         help="Path to a JSON file in the same shape as --financials; "
                              "takes precedence over --financials if both are given")
    parser.add_argument("--collateral",
                         help="Path to a JSON file containing a flat list of collateral asset dicts")
    parser.add_argument("--stress-assumptions",
                         help="Path to a JSON file of deterministic downside-case shocks applied "
                              "to --financials/--spread's FY+1-FY+3 entries only (historical "
                              "periods are never shocked): "
                              '{"revenue_haircut_pct": 10, "interest_rate_bump_bps": 200, '
                              '"opex_increase_pct": 5}. opex_increase_pct affects admin_expenses '
                              "only, never cost_of_sales. Ignored if --financials/--spread has no "
                              "forward-year entries.")
    parser.add_argument("--new-review", action="store_true",
                         help="Force a fresh dated folder for this --company/--proposal instead "
                              "of resuming the most recent existing one -- use this for a new "
                              "annual review of a deal that already has a prior dated folder, so "
                              "it never silently inherits last year's PD/LGD, financials, or "
                              "policy_state. Without this flag, a --company/--proposal that "
                              "already has a dated folder always resumes the most recent one.")
    args = parser.parse_args()

    multi_period_financials = _load_multi_period_financials(args.financials, args.spread)
    collateral_data = _load_json_file(args.collateral)
    stress_assumptions = _load_json_file(args.stress_assumptions)

    run_pipeline(args.company, args.proposal, args.pd, args.lgd, args.type,
                 multi_period_financials=multi_period_financials,
                 collateral_data=collateral_data,
                 stress_assumptions=stress_assumptions,
                 new_review=args.new_review)
