"""Standalone CLI: run spreading_builder.py's formula evaluation against raw
line items a /spread or /project session gathered, and checkpoint the
computed financials/ratios/downside_case to this deal's state.json --
callable from a Claude Code slash command's Bash step, the same role
policy_check.py plays for policy_engine.py/policy_checks.py (see issue #98).

Before this script existed, /spread and /project had Claude calculate every
subtotal/ratio itself in prose -- duplicating evaluate_financial_model()'s
formulas by hand, with no code-enforced backstop against an arithmetic slip,
unlike the headless orchestrator.py path, which always calls this same
function directly. This closes that gap for the primary slash-command
interface too, without either command reimplementing the formulas in prose.

Analyst-supplied mode (financials_source == "analyst-supplied") never calls
this script -- that path deliberately records the analyst's own figures
exactly as given, without independent recomputation (see issue #55).

No `anthropic` dependency, matching policy_check.py/deal_export.py's pattern.
"""
import argparse
import json

from spreading_builder import evaluate_downside_case, evaluate_financial_model
from state_manager import read_state, write_state


def compute(company, proposal, multi_period_financials=None, stress_assumptions=None,
            update_financials_source=True):
    """Recompute financials/ratios (and, when a downside case is derivable,
    downside_case) from raw line items and checkpoint the result to this
    deal's state.json.

    `multi_period_financials` -- raw figures for whichever periods this call
    was just given (e.g. /spread's historical "FY-2"/"FY-1"/"FY-Current", or
    /project's forward "FY+1"-"FY+3") -- is merged into whatever periods this
    deal's state.json already has on file, never a blind replace: /spread and
    /project each supply only the periods they're responsible for, in
    separate calls, and write_state()'s own merge is shallow (a bare
    `financials={...}` would otherwise silently erase whatever periods the
    other command already wrote). The same merge-not-replace treatment
    applies to the computed `financials`/`ratios` dicts themselves.

    The merge is two levels deep: a period given this call is merged field-by
    -field into that period's own already-recorded raw dict, not substituted
    for it wholesale. Without this, re-supplying a period to correct a single
    figure (e.g. just `revenue`) without re-stating every other raw field
    would silently discard every other previously-recorded field for that
    period -- and recompute its subtotals/ratios from the now-incomplete raw
    data without any warning. A field actually given this call always wins
    (overwrites whatever was recorded before it); a field simply not
    mentioned this call is preserved, never reset to 0.

    `update_financials_source` gates whether a fresh computation stamps
    `financials_source: "framework-computed"` -- pass `False` from /project's
    own call. `financials_source` is a whole-deal flag, not per-period (see
    .claude/commands/spread.md): if /spread's analyst-supplied mode already
    set it for this deal's historical periods, /project later supplying
    *forward-year* figures through this same script must not silently flip
    it back to "framework-computed" -- the historicals were never
    independently recomputed, so the deal's audit-guarantee caveat (Guideline
    9) still applies regardless of what /project just added. Only /spread
    (the command that actually owns this mode decision) passes the default
    `True`.

    `stress_assumptions`, if not given at all, falls back to whatever this
    deal already has on file -- so a fresh /spread run (financials-only)
    still recomputes `downside_case` against the *new* base data using the
    already-confirmed stress assumptions, matching orchestrator.py's own
    "recompute whenever either input changes" behavior. When fresh
    `stress_assumptions` *is* given, it's merged key-by-key over whatever's
    already on file, not substituted wholesale -- /project's own prose
    deliberately tells the analyst to omit a shock key entirely when it
    isn't being changed this run ("each independently defaults to no shock"),
    so a fresh dict that only re-confirms one shock (e.g. a revised
    `revenue_haircut_pct`) must not silently drop an already-confirmed
    `opex_increase_pct`/`interest_rate_bump_bps` it didn't mention.

    Raises ValueError if neither fresh `multi_period_financials` nor any
    already on file exist -- nothing to compute.

    Returns the full merged state dict (same contract as
    state_manager.write_state()).
    """
    existing_state = read_state(company, proposal) or {}
    existing_multi_period = existing_state.get("multi_period_financials") or {}

    merged_multi_period = dict(existing_multi_period)
    touched_periods = {}
    if multi_period_financials:
        for period, raw in multi_period_financials.items():
            merged_period = dict(existing_multi_period.get(period) or {})
            merged_period.update(raw or {})
            merged_multi_period[period] = merged_period
            touched_periods[period] = merged_period

    if not merged_multi_period:
        raise ValueError(
            "Nothing to compute: no --financials given, and this deal has no "
            "multi_period_financials already on file."
        )

    fields = {}
    if touched_periods:
        # Computed from the merged (not fresh-only) per-period raw dicts --
        # see the docstring above -- so a partial correction's subtotals/
        # ratios are derived from the period's complete recorded figures,
        # not just whichever fields this particular call happened to supply.
        model_data = evaluate_financial_model(touched_periods)

        merged_financials = dict(existing_state.get("financials") or {})
        merged_financials.update(model_data["financials"])
        merged_ratios = dict(existing_state.get("ratios") or {})
        merged_ratios.update(model_data["ratios"])

        fields["financials"] = merged_financials
        fields["ratios"] = merged_ratios
        fields["multi_period_financials"] = merged_multi_period
        if update_financials_source:
            fields["financials_source"] = "framework-computed"

    existing_stress_assumptions = existing_state.get("stress_assumptions") or {}
    merged_stress_assumptions = dict(existing_stress_assumptions)
    if stress_assumptions:
        merged_stress_assumptions.update(stress_assumptions)
    stress_assumptions = merged_stress_assumptions
    if stress_assumptions:
        downside_case = evaluate_downside_case(merged_multi_period, stress_assumptions)
        if downside_case.get("financials") or downside_case.get("ratios"):
            fields["downside_case"] = downside_case
            fields["stress_assumptions"] = stress_assumptions

    if not fields:
        raise ValueError(
            "Nothing to compute: --financials produced no new periods and no "
            "stress assumptions (existing or given) produced a downside case."
        )

    return write_state(company, proposal, **fields)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Recompute financials/ratios/downside_case from raw line items and "
                     "checkpoint them to state.json -- callable from /spread's or /project's "
                     "own Bash step so the slash-command interface gets the same "
                     "code-enforced formula evaluation orchestrator.py's headless pipeline "
                     "already has, instead of Claude recalculating by hand in prose."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--financials",
                         help='Path to a JSON file: {"FY-2": {...}, "FY-1": {...}, '
                              '"FY-Current": {...}, "FY+1": {...}, ...} -- raw line items per '
                              "period (only the periods this call is supplying), in the schema "
                              "documented by spreading_builder.py's FIELD_LABELS")
    parser.add_argument("--stress-assumptions",
                         help='Path to a JSON file: {"revenue_haircut_pct": ..., '
                              '"opex_increase_pct": ..., "interest_rate_bump_bps": ...} -- all '
                              "optional within it, defaulting to no shock")
    parser.add_argument("--no-update-financials-source", dest="update_financials_source",
                         action="store_false",
                         help="Don't stamp financials_source: \"framework-computed\" on this "
                              "call -- pass this from /project's own call, since "
                              "financials_source is a whole-deal flag /spread owns the decision "
                              "for, not something /project's forward-year figures should ever "
                              "silently flip (e.g. away from an already-set \"analyst-supplied\")")
    args = parser.parse_args(argv)

    multi_period_financials = None
    if args.financials:
        with open(args.financials, encoding="utf-8") as f:
            multi_period_financials = json.load(f)

    stress_assumptions = None
    if args.stress_assumptions:
        with open(args.stress_assumptions, encoding="utf-8") as f:
            stress_assumptions = json.load(f)

    state = compute(args.company, args.proposal, multi_period_financials, stress_assumptions,
                     update_financials_source=args.update_financials_source)
    print(json.dumps({
        "financials": state.get("financials"),
        "ratios": state.get("ratios"),
        "downside_case": state.get("downside_case"),
        "financials_source": state.get("financials_source"),
    }, indent=2))


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    main()
