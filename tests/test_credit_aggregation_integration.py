"""Integration of the credit aggregation through the real freshness check (issue #472, CG587-1 to CG587-4).

Nothing here stubs `aggregation_period_status`: a deal is recorded through the real B2a path, a flat `computed`
object is added by hand (B2b does not exist yet), and the policy check, summary, workbook rows and the public
read paths are exercised on it.
"""
import copy
import json

import pytest

import committee_summary
import credit_aggregation
import deal_export
import export_readback
import policy_check
from state_manager import StateError

from test_credit_aggregation import (  # noqa: E402 (sibling test module providing the B2a fixtures)
    COMPANY, DATE, PROPOSAL, _read_full, _record, _seed_two_entity_deal, _state_file,
)

COMPUTED = {"financials": {"ebitda": 500.0, "total_debt": 900.0}, "ratios": {"gross_leverage": 1.8}}
COVENANT = {"metric": "gross_leverage", "type": "maximum", "threshold": 3.0, "basis": "consolidated"}


def _write(tmp_path, state):
    _state_file(tmp_path).write_text(json.dumps(state, indent=2), encoding="utf-8")


@pytest.fixture
def deal(tmp_path):
    """A recorded, fresh, calculated aggregation with a consolidated covenant, as state.json holds it."""
    _seed_two_entity_deal(tmp_path)
    _record(tmp_path)
    state = _read_full(tmp_path)
    state["credit_aggregation"]["FY-Current"]["computed"] = copy.deepcopy(COMPUTED)
    state["covenants"] = [dict(COVENANT)]
    state["ratios"] = {"FY-Current": {"gross_leverage": 5.0}}
    _write(tmp_path, state)
    return tmp_path


def test_the_policy_check_entry_point_routes_a_consolidated_covenant_to_a_fresh_aggregation(deal):
    result = policy_check.compute_from_state(_read_full(deal))["policy_state"]["covenant_results"][0]
    assert result["status"] == "PASS" and result["actual"] == 1.8
    assert result["view_source"] == "credit_aggregation" and result["input_fingerprint"]


def test_a_changed_input_makes_the_policy_check_covenant_unresolvable_not_borrower_based(deal):
    state = _read_full(deal)
    state["financials"]["FY-Current"]["raw"]["cash"] += 1     # a covered source changes: the fingerprint is stale
    result = policy_check.compute_from_state(state)["policy_state"]["covenant_results"][0]
    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None
    assert "fingerprint" in result["reason"] or "no longer" in result["reason"]


def test_the_summary_and_workbook_rows_show_figures_only_while_the_period_is_current(deal):
    state = _read_full(deal)
    rows = credit_aggregation.aggregation_sheet_rows(state)
    assert any(row[:1] == ["gross_leverage"] and row[1:] == [1.8] for row in rows)
    state["financials"]["FY-Current"]["raw"]["cash"] += 1
    stale_rows = credit_aggregation.aggregation_sheet_rows(state)
    assert not any(row[:1] == ["gross_leverage"] for row in stale_rows)
    assert any(row[:1] == ["Status"] and row[1] == "stale" for row in stale_rows)


def test_a_nested_computed_object_is_not_current_and_is_refused_by_the_validator(deal):
    state = _read_full(deal)
    state["credit_aggregation"]["FY-Current"]["computed"] = {
        "financials": {"FY-Current": {"ebitda": 99}}, "ratios": {"FY-Current": {"dscr": 1.1}}}
    status = credit_aggregation.aggregation_period_status(state, "FY-Current")
    assert status["status"] == "invalid"
    with pytest.raises(StateError, match="flat finite number"):
        from state_manager import validate_state
        validate_state(state, keys=("credit_aggregation",))


@pytest.mark.parametrize("bad", [True, float("inf"), "1.8", [1.8]])
def test_computed_values_must_be_flat_finite_numbers(deal, bad):
    state = _read_full(deal)
    state["credit_aggregation"]["FY-Current"]["computed"]["ratios"]["gross_leverage"] = bad
    assert credit_aggregation.aggregation_period_status(state, "FY-Current")["status"] == "invalid"


@pytest.mark.parametrize("corrupt", ["corrupt", ["x"], {"FY-Current": "corrupt"}])
def test_a_malformed_credit_aggregation_is_refused_by_the_summary_export_and_readback(deal, corrupt):
    state = _read_full(deal)
    state["credit_aggregation"] = corrupt
    _write(deal, state)
    base = str(deal)
    with pytest.raises(StateError, match="credit_aggregation"):
        committee_summary.collect(COMPANY, PROPOSAL, date_str=DATE, base_dir=base)
    with pytest.raises(StateError, match="credit_aggregation"):
        deal_export.export_deal(COMPANY, PROPOSAL, "loan", "# Draft\n", date_str=DATE, base_dir=base)
    with pytest.raises(StateError, match="credit_aggregation"):
        export_readback.verify_deal(COMPANY, PROPOSAL, date_str=DATE, base_dir=base)


def test_the_committee_summary_collection_path_renders_the_real_status(deal):
    _, data = committee_summary.collect(COMPANY, PROPOSAL, date_str=DATE, base_dir=str(deal))
    text = committee_summary.render_markdown(data)
    assert "## Credit aggregation (not a consolidation)" in text and "1.8" in text
    assert data["policy"]["covenant_results"][0]["view_source"] == "credit_aggregation"
