"""Each aggregation surface discloses limitations, including covenant non-results."""

import committee_summary
import credit_aggregation as ca
import policy_engine
from test_credit_aggregation import COMPANY, DATE, PROPOSAL, _read_full
from test_credit_aggregation_calc import O, P, change_journal, run

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
