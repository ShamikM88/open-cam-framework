"""Each aggregation surface discloses limitations, including covenant non-results."""

import json

import committee_summary
import credit_aggregation as ca
import policy_engine
import openpyxl
from deal_export import workbook_inputs_from_state
from spreading_builder import export_to_xlsx
from test_credit_aggregation import COMPANY, DATE, PROPOSAL, _read_full, _state_file
from test_credit_aggregation_calc import B, O, P, calculate, change_journal, run, seed

LABEL = "credit aggregation, not accounting-reviewed"


def test_disclosure_follows_a9_order_and_includes_recorded_matching_refusals(tmp_path):
    c = run(tmp_path, change_journal(amount=19))
    state = _read_full(tmp_path)
    rows = ca.aggregation_sheet_rows(state)
    flat = "\n".join(" ".join(map(str, row)) for row in rows).lower()
    _, data = committee_summary.collect(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(tmp_path))
    summary = committee_summary.render_markdown(data).lower()
    for rendered in (flat, summary):
        phrases = (LABEL, "debt may be understated", "may not be available to the borrower", "are not verified")
        assert [rendered.index(p) for p in phrases] == sorted(rendered.index(p) for p in phrases)
        for phrase in ("refused journal", "j1", "amount", "qualifying cash", "restricted cash",
                       "unrestricted cash", O, "attestation", "total debt plus debt of left-out entities"):
            assert phrase in rendered
    assert c["refused_journals"][0]["journal_id"] == "J1"


def test_withheld_covenant_has_its_reason_label_and_limitations_even_if_stale(tmp_path):
    run(tmp_path)
    state = _read_full(tmp_path)
    state["covenants"] = [{"metric": "dscr", "type": "minimum", "threshold": 1.0, "basis": "consolidated"}]
    for stale in (False, True):
        result = policy_engine.evaluate_deal_policy(state)["covenant_results"][0]
        assert result["status"] == "UNRESOLVABLE" and result["actual"] is None
        assert result["view_label"] == LABEL
        assert "debt may be understated" in result["view_limitations"][0].lower()
        if not stale:
            assert "scheduled principal" in result["reason"]
        state["financials"][P]["raw"]["cash"] += 1


def test_aggregation_forward_disclosure_stays_unresolvable_and_has_no_borrower_fallback(tmp_path):
    run(tmp_path)
    state = _read_full(tmp_path)
    state["ratios"]["FY+1"] = {"gross_leverage": 0.1}
    state["covenants"] = [{"metric": "gross_leverage", "type": "maximum", "threshold": 1.0, "basis": "consolidated"}]
    result = policy_engine.evaluate_deal_policy(state)["forward_covenant_results"][0]
    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None
    assert result["view_label"] == LABEL and result["view_limitations"]


def test_actual_xlsx_and_covenant_keep_variant_separate(tmp_path):
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    state["financials"][P]["raw"]["other_income"] = 40
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    file["periods"][P]["attestations"][B].update(
        ebitda_intragroup="offset_other_income_only", unoffset_other_income_amount=10,
        schedule=[{"counterparty": O, "line": "other_income", "amount": 10}],
    )
    calculate(tmp_path, file)
    state = _read_full(tmp_path)
    metric = "gross_leverage_excluding_other_income"
    state["covenants"] = [{"metric": m, "type": "maximum", "threshold": 1.0, "basis": "consolidated"}
                          for m in (metric, "gross_leverage")]
    variant, ordinary = policy_engine.evaluate_deal_policy(state)["covenant_results"]
    assert variant["actual"] == 390 / 800 and "all other income" in variant["view_metric_label"]
    assert ordinary["status"] == "UNRESOLVABLE" and ordinary["actual"] is None
    path = tmp_path / "spreading.xlsx"
    export_to_xlsx(COMPANY, path, **workbook_inputs_from_state(state))
    book = openpyxl.load_workbook(path)
    assert book.sheetnames[-1] == "Credit aggregation"
    sheet_rows = list(book["Credit aggregation"].values)
    rows = {row[0]: row[1] for row in sheet_rows}
    assert LABEL in rows and rows[metric] == 390 / 800 and rows["gross_leverage"] is None
    assert ("Participant", "Legal entity", "Status", "Ownership (%)", "Post-elimination debt", "Cash", "EBITDA") in sheet_rows
    assert (B, COMPANY, "obligor", 100, 210, 50, 500) in sheet_rows
