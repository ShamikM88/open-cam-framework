"""Deal-folder creation, template auto-save, and .docx/.xlsx export.

Pulled out of orchestrator.py so this logic -- which has no `anthropic`
dependency -- can run standalone as `python scripts/deal_export.py ...` from
a Claude Code slash command's Bash step (see .claude/commands/assemble.md),
where Claude itself has already produced the draft and just needs it turned
into deal output files. orchestrator.py's own (headless, API-key-based)
pipeline imports export_deal() directly instead of duplicating this logic.
"""
import os
import re
import sys
from datetime import datetime

from docx_builder import export_to_docx
from spreading_builder import FORWARD_PERIOD_KEYS, HISTORICAL_PERIOD_KEYS, SUPPLIED_RATIOS, SUPPLIED_ROW_LABELS, export_to_xlsx
from state_manager import (
    StateError, existing_state_date_str, read_state, resolve_export_date_str, sanitize_path_component,
    state_path, validate_iso_date_str,
)
from template_resolver import cam_template_path, local_cam_template_path

# The state.json keys export_deal() reads, with the kind each is held to. Deliberately looser than STATE_SHAPES for
# `financials`/`analyst_supplied_financials` ("object", not "periods"): a period whose value is not an object has always
# been treated as empty here (_raw_financials), and a non-list `collateral` or a non-object `downside_case` as absent
# (_collateral_data_from_state, _downside_financial_data_from_state); those documented tolerances are kept, so
# `collateral` is not validated at all, `downside_case` may be any non-object but an object's inner
# `financials`/`ratios` must be objects ("downside_lenient"), and the raw-figure keys only need to be objects.
# `financials_source` decides which raw store is read, so an unknown value is an error rather than silently
# "framework-computed".
STATE_KEYS_READ = {"financials": "object", "analyst_supplied_financials": "object", "downside_case": "downside_lenient",
                   "financials_source": "source", "group_basis": "group_basis"}


def _raw_financials(financials):
    """Reduce a `financials`-shaped dict (raw + evaluate_financial_model()'s
    computed subtotals per period, keyed under "raw" -- see
    spreading_builder.evaluate_financial_model) down to just the raw
    multi-period figures export_to_xlsx() needs to populate input cells.
    The computed subtotals aren't passed through: those live as Excel
    formulas in the workbook already. Shared by _financial_data_from_state()
    (state.json's own `financials`) and _downside_financial_data_from_state()
    (state.json's `downside_case`'s own `financials`) -- same shape, two
    different sources.
    """
    financials = financials or {}
    return {
        period: (data.get("raw", {}) if isinstance(data, dict) else {})
        for period, data in financials.items()
    }


def _financial_data_from_state(state):
    """Select exactly one raw-figure store per period for the workbook.

    Historical periods follow `financials_source`: analyst-supplied historical
    lines come from `analyst_supplied_financials`, otherwise from
    `financials[period]["raw"]`. Forward periods follow `forecast_source`:
    an analyst-supplied forecast keeps its existing supplied-store path, while
    a computed forecast reads `financials[period]["raw"]` even if the deal's
    historical basis is analyst-supplied (issue #249). Never blend stores
    within a period or fall back to the other store for a missing period.
    """
    if state.get("financials_source") == "analyst-supplied":
        analyst_supplied = state.get("analyst_supplied_financials")
        raw_by_period = dict(analyst_supplied) if isinstance(analyst_supplied, dict) else {}
        if state.get("forecast_source") != "analyst-supplied":
            computed = _raw_financials(state.get("financials"))
            for period in FORWARD_PERIOD_KEYS:
                raw_by_period.pop(period, None)
                if period in computed:
                    raw_by_period[period] = computed[period]
        return raw_by_period
    return _raw_financials(state.get("financials"))


def _downside_financial_data_from_state(state):
    """state.json's `downside_case` (see spreading_builder.evaluate_downside_case)
    is `{"financials": {...}, "ratios": {...}}` when stress_assumptions were
    ever supplied for this deal, `{}` otherwise -- either way, reduce its own
    `financials` the same way _financial_data_from_state() reduces the
    top-level one, so the exported workbook's FY+1/FY+2/FY+3 "(Downside)"
    columns (see issue #38) have something to populate from.
    """
    downside_case = state.get("downside_case")
    downside_financials = downside_case.get("financials") if isinstance(downside_case, dict) else None
    return _raw_financials(downside_financials)


def _supplied_row_values(*period_dicts):
    """{workbook row label: value} for recorded subtotals and ratios that have a workbook row.

    A recorded null ratio is N/A; non-numeric values elsewhere are left blank.
    """
    values = {}
    for period_dict in period_dicts:
        for key, value in (period_dict if isinstance(period_dict, dict) else {}).items():
            label = SUPPLIED_ROW_LABELS.get(key)
            if label is None:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[label] = value
            elif value is None and key in SUPPLIED_RATIOS:
                values[label] = "N/A"
    return values


def _analyst_forecast_columns(state):
    """Return recorded analyst-supplied values and any forward-downside annotations.

    Historical periods use recorded values when financials_source is analyst-supplied;
    forward periods use recorded values when forecast_source is analyst-supplied.
    Supplied columns must not recalculate subtotals or ratios from incomplete raw lines.
    """
    historical_supplied = state.get("financials_source") == "analyst-supplied"
    forecast_supplied = state.get("forecast_source") == "analyst-supplied"
    if not historical_supplied and not forecast_supplied:
        return None, None, None, None

    financials = state.get("financials")
    financials = financials if isinstance(financials, dict) else {}
    ratios = state.get("ratios")
    ratios = ratios if isinstance(ratios, dict) else {}
    supplied = {}

    if historical_supplied:
        for period in HISTORICAL_PERIOD_KEYS:
            period_financials = financials.get(period)
            period_ratios = ratios.get(period)
            if isinstance(period_financials, dict) or isinstance(period_ratios, dict):
                supplied[period] = _supplied_row_values(period_financials, period_ratios)

    if forecast_supplied:
        for period in FORWARD_PERIOD_KEYS:
            period_financials = financials.get(period)
            period_ratios = ratios.get(period)
            if isinstance(period_financials, dict) or isinstance(period_ratios, dict):
                supplied[period] = _supplied_row_values(period_financials, period_ratios)
    else:
        return supplied or None, None, None, None

    downside = state.get("downside_case")
    downside = downside if isinstance(downside, dict) else {}
    basis = downside.get("basis") if isinstance(downside.get("basis"), dict) else {}
    downside_financials = downside.get("financials") if isinstance(downside.get("financials"), dict) else {}
    downside_ratios = downside.get("ratios") if isinstance(downside.get("ratios"), dict) else {}
    supplied_downside = {
        period: _supplied_row_values(downside_financials.get(period), downside_ratios.get(period))
        for period, how in basis.items() if how == "analyst-supplied" and period in FORWARD_PERIOD_KEYS
    }
    unavailable = downside.get("unavailable") if isinstance(downside.get("unavailable"), dict) else {}
    lines = state.get("analyst_supplied_financials")
    lines = lines if isinstance(lines, dict) else {}
    derived = {
        period: {name for name, value in (lines.get(period) if isinstance(lines.get(period), dict) else {}).items()
                 if isinstance(value, (int, float)) and not isinstance(value, bool)}
        for period, how in basis.items()
        if how != "analyst-supplied" and period in FORWARD_PERIOD_KEYS and period not in unavailable
    }
    return supplied or None, supplied_downside or None, {p for p in unavailable if p in FORWARD_PERIOD_KEYS}, derived

def _collateral_data_from_state(state):
    """state.json's `collateral` is expected to be the flat list of asset
    dicts export_to_xlsx() wants (see FIELD_LABELS / COLLATERAL_HEADERS in
    spreading_builder.py). Anything else (missing, or a different shape from
    another step's use of the same key) falls back to None, which
    export_to_xlsx() renders as a single blank placeholder row.
    """
    collateral = state.get("collateral")
    return collateral if isinstance(collateral, list) else None


def export_deal(company, proposal, deal_type, draft_markdown, date_str=None, base_dir=None):
    """Create the dated deal folder, auto-save a new template if this deal
    type has none yet, and export the draft to .docx + .xlsx.

    `base_dir` lets tests run against a temp directory instead of the real
    project root; production callers leave it as None (paths relative to
    the current working directory, as before this was extracted).

    `date_str` defaults to today when not given -- right for a brand-new deal,
    wrong for one already in progress, whose output must land next to its own
    state.json/sources/ however many days it has taken (issue #97). Callers
    with a deal in progress must pass the date themselves, resolved via
    state_manager.resolve_date_str(), as orchestrator.py and this module's
    own CLI do.

    Returns the output directory path.
    """
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    deal_type = sanitize_path_component(deal_type, "deal_type")
    date_str = sanitize_path_component(date_str, "date_str") if date_str else None
    state_date_str = date_str

    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    deals_root = os.path.join(base_dir, "deals") if base_dir else "deals"
    output_dir = os.path.join(deals_root, company, f"{proposal}_{date_str}")

    # An explicit date selects both the output folder and its state.json.
    # Bare library calls still auto-discover existing state, while keeping
    # their historical default of writing output to today's folder. Read
    # (and validate) BEFORE anything is created: unusable state (issue #171)
    # must not leave an empty output folder or auto-saved template behind.
    if state_date_str and not os.path.isfile(state_path(company, proposal, date_str=state_date_str, base_dir=base_dir)):
        # Decision D1 (#248): an explicit date that names a folder with no state.json is an error when the deal has state
        # in another dated folder -- otherwise the workbook would come out blank. A deal with no state anywhere keeps
        # exporting into the named folder.
        elsewhere = existing_state_date_str(company, proposal, base_dir=base_dir)
        if elsewhere:
            raise StateError(
                f"no state.json in the dated folder {proposal}_{state_date_str}, but this deal has state in "
                f"{proposal}_{elsewhere}; name that date, or leave the date out; nothing was written.")
    state = read_state(
        company, proposal, date_str=state_date_str, base_dir=base_dir, keys=STATE_KEYS_READ,
    ) or {}
    financial_data = _financial_data_from_state(state)
    downside_financial_data = _downside_financial_data_from_state(state)
    collateral_data = _collateral_data_from_state(state)
    group_basis = state.get("group_basis")
    supplied_values, supplied_downside_values, blank_downside_periods, derived_downside_fields = \
        _analyst_forecast_columns(state)

    os.makedirs(output_dir, exist_ok=True)

    if cam_template_path(deal_type, base_dir=base_dir) is None:
        # Genuinely new deal type: no template (override or default) exists
        # yet. Save this draft's structure as a starting point under
        # templates/local/cam/ -- never the shared, git-tracked
        # templates/cam/ -- since the draft contains this real deal's
        # actual company name and figures.
        new_template_path = local_cam_template_path(deal_type, base_dir=base_dir)
        os.makedirs(os.path.dirname(new_template_path), exist_ok=True)
        print(f"[Auto-Template] No template found for '{deal_type}' -- saving this draft's structure to {new_template_path}")
        with open(new_template_path, "w", encoding="utf-8") as f:
            f.write(draft_markdown)

    docx_path = os.path.join(output_dir, f"{company}_{proposal}_CAM.docx")
    xlsx_path = os.path.join(output_dir, f"{company}_{proposal}_Spreading.xlsx")

    export_to_docx(draft_markdown, docx_path)
    export_to_xlsx(company, xlsx_path, financial_data=financial_data or None,
                    collateral_data=collateral_data,
                    downside_financial_data=downside_financial_data or None,
                    supplied_values=supplied_values, supplied_downside_values=supplied_downside_values,
                    blank_downside_periods=blank_downside_periods or None,
                    derived_downside_fields=derived_downside_fields or None, group_basis=group_basis)

    return output_dir


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Create the deal output folder, auto-save a new CAM template if "
                     "this deal type has none yet, and export an already-drafted CAM "
                     "to .docx + .xlsx. Has no Anthropic dependency -- the draft is "
                     "expected to already exist as a file, written by whatever drafted "
                     "it (a Claude Code slash command, or orchestrator.py)."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--type", default="corporate_credit")
    parser.add_argument("--draft", required=True, help="Path to the drafted CAM Markdown file")
    parser.add_argument(
        "--date-str", default=None,
        help="YYYY-MM-DD suffix of the deal's dated folder to export into. Defaults to "
             "this deal's existing dated folder if one exists (so a multi-day deal's "
             ".docx/.xlsx land next to its own state.json/sources/ -- issue #97), "
             "else today's date for a brand-new deal.",
    )
    args = parser.parse_args(argv)
    try:
        if args.date_str is not None:
            validate_iso_date_str(args.date_str, field_name="--date-str")
        with open(args.draft, encoding="utf-8") as f:
            draft_markdown = f.read()

        # Reject unsafe names before folder discovery; export_deal() repeats the
        # validation at its own boundary.
        company = sanitize_path_component(args.company, "company")
        proposal = sanitize_path_component(args.proposal, "proposal")
        date_str = resolve_export_date_str(company, proposal, date_str=args.date_str)
        output_dir = export_deal(company, proposal, args.type, draft_markdown, date_str=date_str)
    except (OSError, ValueError) as exc:
        sys.exit(f"error: {exc}")
    print(f"Done! Files generated in {output_dir}")


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    main()
