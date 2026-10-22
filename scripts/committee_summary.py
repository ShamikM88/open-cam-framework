"""Committee summary: a short, deterministic meeting aid built from one deal's recorded state (issue #196).

For one exact dated deal folder it reads `state.json`, runs the deterministic policy check on that same state, and
writes `<Company>_<Proposal>_Committee_Summary.md` and `.docx` beside it. Everything printed is a value the record
already holds (an unrecorded term says "not recorded"): there is no new calculation, no judgement, no verdict and no
recommendation, and it is not a substitute for the CAM, `/review` or a credit decision. The policy check is run
without a draft, so draft-level audit findings are "not assessed" here; `/review` owns that.

Writes only those two report files (each through a temporary file and an atomic replace); never `state.json`,
`steps_completed`, `review_trail`, a draft, the source manifest or a template. No `anthropic` dependency. Exit
status: 0 written, 1 unusable input or state (one `error:` line on stderr), 2 a usage error.
"""
import json
import os
import re
import sys
import tempfile

from docx_builder import export_to_docx
from policy_check import STATE_KEYS_READ, compute_from_state
from source_manifest import missing_saved_sources, read_manifest, sources_declared
from spreading_builder import PERIOD_KEYS
from state_manager import (
    StateError, find_deal, read_state, required_steps_completed, sanitize_path_component, state_path,
    validate_iso_date_str, validate_state,
)

NOT_RECORDED = "not recorded"
TRACKED_STEPS = ("triage", "spread", "commercial", "collateral", "project", "review", "assemble")
HARD_PREREQUISITE_STEPS = ("spread",)

GROUP_DISCLOSURE = ("Credit aggregation of consolidated accounts supplied by the borrower; "
                    "not a statutory consolidation prepared by this framework.")

LIMITS = (
    "This summary quotes recorded values and the deterministic policy check. It makes no credit decision, "
    "recommendation or approval, and it does not replace the CAM, `/review` or the formal audit.",
    "Draft-level audit findings (condition completeness, risk taxonomy, narrative accuracy) are not assessed here: "
    "the policy check ran on the recorded state without a draft.",
    "Anything shown as \"not recorded\" is absent from `state.json`; it is not evidence that the term does not exist.",
    "A saved source in the manifest is not proof that it supports a claim.",
)


# What the Word builder reads as inline markup (**bold**, *italic*, `code`, [text](url) links), the angle brackets a
# Markdown renderer would read as an HTML tag in the .md file, and underscore emphasis (_x_, __x__) in that file. An
# underscore only emphasises when it opens a word and a matching one closes it, so snake_case names are left alone.
INLINE_MARKUP = re.compile(
    r"[*`\[\]<>]|(?<![A-Za-z0-9])_{1,2}[^\s_](?:[^_]*[^\s_])?_{1,2}(?![A-Za-z0-9])")


def _cell(value):
    """A recorded value as stored (None is N/A), on one line, safe to put in a table cell, a bullet or a label.

    Text a record could use to break the layout or to pass itself off as markup is neutralised: `|` becomes `/`,
    runs of white space and line breaks become one space, and a value containing inline markup characters is shown as a code span so it
    reads literally (a backtick inside it becomes an apostrophe, because a code span cannot contain one).
    """
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = " ".join(str(value).replace("|", "/").split()) or NOT_RECORDED
    if INLINE_MARKUP.search(text):
        text = "`" + text.replace("`", "'") + "`"
    return text


def _table(headers, rows):
    lines = ["| " + " | ".join(_cell(header) for header in headers) + " |",
             "| " + " | ".join(":---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return lines


def _covenant_row(result, columns):
    """A result's values for `columns`; a reason that is absent (None) reads "-", not N/A."""
    return [(result.get(column) or "-") if column == "reason" else result.get(column) for column in columns]


def _ordered_periods(*period_maps):
    present = {period for mapping in period_maps if isinstance(mapping, dict) for period in mapping}
    return [period for period in PERIOD_KEYS if period in present]


def _scalar_rows(by_period, periods, skip=()):
    """Rows [name, value per period] for every recorded scalar name, in first-seen order."""
    names = []
    for period in periods:
        values = by_period.get(period)
        for name, value in (values.items() if isinstance(values, dict) else ()):
            if name not in names and name not in skip and not isinstance(value, (dict, list)):
                names.append(name)
    rows = []
    for name in names:
        row = [name]
        for period in periods:
            values = by_period.get(period)
            row.append(values.get(name, NOT_RECORDED) if isinstance(values, dict) else NOT_RECORDED)
        rows.append(row)
    return rows


def _records_table(records, preferred):
    """A table of a list of dicts: the preferred columns that occur first, then any others, scalar values only."""
    records = [record for record in records if isinstance(record, dict)]
    columns = [column for column in preferred if any(column in record for record in records)]
    for record in records:
        for column, value in record.items():
            if column not in columns and not isinstance(value, (dict, list)):
                columns.append(column)
    return _table(columns, [[record.get(column, NOT_RECORDED) for column in columns] for record in records])


def collect(company, proposal, date_str=None, base_dir=None):
    """Read the exact dated state and gather everything the summary prints. Returns (folder, summary data)."""
    company = sanitize_path_component(company, "company")
    proposal = sanitize_path_component(proposal, "proposal")
    if date_str is not None:
        validate_iso_date_str(date_str, field_name="--date-str")
        if not os.path.isfile(state_path(company, proposal, date_str=date_str, base_dir=base_dir)):
            raise StateError(f"no state.json in the dated folder {proposal}_{date_str}; nothing to summarise")
    else:
        found = find_deal(company, proposal, base_dir=base_dir)
        if not found["record_found"]:
            raise StateError(f"no existing state.json for company {company!r} and proposal {proposal!r}")
        date_str = found["date"]
    path = state_path(company, proposal, date_str=date_str, base_dir=base_dir)
    state = read_state(company, proposal, date_str=date_str, base_dir=base_dir, keys=STATE_KEYS_READ) or {}
    validate_state(state, keys=("group_basis",), path=path)
    manifest = read_manifest(company, proposal, date_str=date_str, base_dir=base_dir)
    data = {
        "company": company, "proposal": proposal, "date": date_str, "state": state,
        "policy": compute_from_state(state)["policy_state"], "manifest_count": len(manifest),
        "sources_declared": sources_declared(state), "sources_missing": missing_saved_sources(state, manifest),
    }
    return os.path.dirname(path), data


def render_markdown(data):
    """The summary as Markdown, from the data `collect()` gathered. Pure: no file or state access."""
    state, policy = data["state"], data["policy"]
    steps = state.get("steps_completed") if isinstance(state.get("steps_completed"), list) else []
    lines = [f"# Committee summary: {_cell(data['company'])} / {_cell(data['proposal'])}", ""]

    source = state.get("financials_source")
    basis = _cell(source) if source else f"{NOT_RECORDED} (read as framework-computed)"
    if state.get("forecast_source"):
        basis += f"; forecast: {_cell(state['forecast_source'])}"
    header = [
        ("Record", f"{data['proposal']}_{data['date']}"),
        ("Deal type", state.get("deal_type") or NOT_RECORDED),
        ("Financial basis", basis),
        ("Policy check", "deal-structural checks on the recorded state; draft-level findings not assessed"),
    ]
    lines += [f"- **{label}:** {_cell(value)}" for label, value in header]
    if state.get("financials_source_note"):
        lines.append(f"- **Basis note:** {_cell(state['financials_source_note'])}")
    lines += ["", "This summary is built only from the recorded state and the deterministic policy check. It is not a "
              "credit decision, recommendation or approval.", ""]

    group_basis = state.get("group_basis")
    if isinstance(group_basis, dict):
        lines += ["## Group accounts basis", "", GROUP_DISCLOSURE, ""]
        lines += [f"- **{label}:** {_cell(group_basis.get(key))}" for label, key in (
            ("Reporting entity", "reporting_entity"), ("Entities covered", "entities_covered"),
            ("Common period end", "period_end"), ("Periods covered", "periods"),
            ("Figures basis", "figures_basis"), ("Perimeter confirmed on", "perimeter_confirmed_on"))]
        lines.append("")

    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    lines += ["## Facility and key terms", ""]
    lines += [f"- **{_cell(key)}:** {_cell(value)}" for key, value in inputs.items()]
    lines += [f"- **Facility amount, tenor and pricing:** {NOT_RECORDED} in state.json", ""]
    guarantees = state.get("guarantees") if isinstance(state.get("guarantees"), list) else []
    if guarantees:
        lines += ["**Guarantees**", ""] + _records_table(guarantees, ("provider", "type", "amount")) + [""]
    else:
        lines += ["No guarantees are recorded.", ""]

    financials = state.get("financials") if isinstance(state.get("financials"), dict) else {}
    ratios = state.get("ratios") if isinstance(state.get("ratios"), dict) else {}
    periods = _ordered_periods(financials, ratios)
    lines += ["## Financial snapshot", ""]
    if periods:
        figure_rows = _scalar_rows(financials, periods)
        ratio_rows = _scalar_rows(ratios, periods)
        if figure_rows:
            lines += ["**Recorded subtotals**", ""] + _table(["Item"] + periods, figure_rows) + [""]
        if ratio_rows:
            lines += ["**Recorded ratios** (N/A: not meaningful, for example a non-positive denominator)", ""]
            lines += _table(["Ratio"] + periods, ratio_rows) + [""]
    else:
        lines += ["No financial figures or ratios are recorded.", ""]

    covenant_columns = ["metric", "type", "threshold", "actual", "status", "headroom_pct", "reason"]
    lines += ["## Covenants", ""]
    results = policy["covenant_results"]
    if results:
        lines += ["**Current (FY-Current)**", ""]
        lines += _table(covenant_columns, [_covenant_row(r, covenant_columns) for r in results]) + [""]
    else:
        lines += ["No covenants are recorded.", ""]
    forward = policy["forward_covenant_results"]
    if forward:
        columns = ["year", "forward_id"] + covenant_columns
        lines += ["**Forward years** (disclosure only; they do not decide the policy check)", ""]
        lines += _table(columns, [_covenant_row(r, columns) for r in forward]) + [""]
    breaches = policy["downside_covenant_breaches"]
    if breaches:
        columns = ["year", "metric", "base_actual", "downside_actual", "threshold", "downside_status", "reason"]
        lines += ["**Downside breaches** (base case passes, stressed case does not)", ""]
        lines += _table(columns, [_covenant_row(r, columns) for r in breaches]) + [""]
    elif isinstance(state.get("downside_case"), dict) and state["downside_case"]:
        lines += ["No downside breach is reported for the recorded downside case.", ""]
    else:
        lines += ["No downside case is recorded; that is not evidence that downside risk is absent.", ""]

    lines += ["## Conditions precedent and subsequent", ""]
    for heading, key, id_key in (("Conditions precedent", "required_conditions_precedent", "cp_id"),
                                 ("Conditions subsequent", "required_conditions_subsequent", "cs_id")):
        lines += [f"**{heading}**", ""]
        lines += [f"- {_cell(item.get(id_key))}: {_cell(item.get('text'))}" for item in policy[key]] or ["None."]
        lines.append("")

    collateral = state.get("collateral") if isinstance(state.get("collateral"), list) else []
    package = state.get("security_package") if isinstance(state.get("security_package"), list) else []
    lines += ["## Collateral and security", ""]
    if collateral:
        lines += ["**Assets**", ""] + _records_table(collateral, ("asset_id",)) + [""]
    else:
        lines += ["No collateral is recorded.", ""]
    if package:
        lines += ["**Security package**", ""]
        lines += _records_table(package, ("secures_asset_id", "perfection_status", "ranking")) + [""]
    lines += ["**Security gaps reported by the policy check**", ""]
    lines += [f"- {_cell(gap)}" for gap in policy["security_gaps"]] or ["None reported."]
    lines.append("")

    lines += ["## Open gaps", ""]
    missing_required = required_steps_completed(steps, HARD_PREREQUISITE_STEPS)
    not_done = [step for step in TRACKED_STEPS if step not in steps]
    lines.append("- **Required step missing:** " + (", ".join(missing_required) if missing_required else "none"))
    lines.append("- **Steps not recorded as completed:** " + (", ".join(not_done) if not_done else "none"))
    unresolved = [r for r in results + forward if r.get("status") == "UNRESOLVABLE"]
    lines.append(f"- **UNRESOLVABLE covenant results:** {len(unresolved)}")
    if data["sources_declared"] == "unknown":
        sources_note = "; the citation declaration is unknown, so the saved-source check could not be completed"
    elif data["sources_missing"]:
        sources_note = "; declared citations have no saved source"
    else:
        sources_note = ""
    lines.append(f"- **Saved sources:** {data['manifest_count']} in the manifest; citations declared: "
                 f"{data['sources_declared']}{sources_note}")
    lines += ["", "## Recorded review status", ""]
    trail = state.get("review_trail") if isinstance(state.get("review_trail"), list) else []
    last = trail[-1] if trail and isinstance(trail[-1], dict) else {}
    lines.append(f"- **Latest recorded verdict:** {_cell(state.get('review_verdict') or NOT_RECORDED)} "
                 "(a historical record, not a current approval)")
    lines.append(f"- **Recorded review iterations:** {len(trail)}"
                 + (f"; latest {_cell(last.get('verdict'))} at {_cell(last.get('timestamp'))}" if last else ""))
    lines += ["", "## Limits and items needing human review", ""]
    lines += [f"- {limit}" for limit in LIMITS]
    return "\n".join(lines) + "\n"


def _replace_atomically(path, write):
    """Write to a temporary file beside `path`, then replace it, so a failure never leaves a half-written report."""
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".committee_summary_", suffix=".tmp")
    os.close(handle)
    try:
        write(temporary)
        os.replace(temporary, path)
    except BaseException:
        os.remove(temporary)
        raise


def generate(company, proposal, date_str=None, base_dir=None):
    """Write the Markdown and Word summaries into the deal's dated folder. Returns the JSON-ready report."""
    folder, data = collect(company, proposal, date_str=date_str, base_dir=base_dir)
    markdown = render_markdown(data)
    stem = os.path.join(folder, f"{data['company']}_{data['proposal']}_Committee_Summary")
    markdown_path, docx_path = stem + ".md", stem + ".docx"

    def write_markdown(temporary):
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown)

    _replace_atomically(markdown_path, write_markdown)
    try:
        _replace_atomically(docx_path, lambda temporary: export_to_docx(markdown, temporary))
    except OSError as exc:
        raise OSError(f"the Markdown summary was written to {markdown_path} but the Word summary failed: {exc}") from exc
    return {
        "status": "written", "selected_folder": folder, "markdown_path": markdown_path, "docx_path": docx_path,
        "included_sections": [line[3:] for line in markdown.splitlines() if line.startswith("## ")],
        "policy_scope": "deal-structural checks on the recorded state; draft-level findings not assessed",
    }


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Write a short committee summary (Markdown and Word) for one deal from its recorded state and "
                    "the deterministic policy check. Quotes recorded values only; makes no credit decision. Has no "
                    "Anthropic dependency."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument(
        "--date-str", default=None,
        help="YYYY-MM-DD suffix of the deal's dated folder. Defaults to the deal's newest dated folder that has a "
             "state.json.")
    args = parser.parse_args(argv)
    try:
        report = generate(args.company, args.proposal, date_str=args.date_str)
    except (OSError, ValueError) as exc:
        sys.exit(f"error: {exc}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    main()
