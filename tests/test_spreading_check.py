"""Tests for scripts/spreading_check.py: the standalone CLI wrapper that lets
/spread and /project's Bash steps run the same formula evaluation
orchestrator.py's headless pipeline does, instead of Claude recalculating
subtotals/ratios by hand in prose (see issue #98).

Tests call compute() directly rather than invoking the script as a
subprocess -- the __main__ block is a thin argparse+print wrapper around
compute(), matching test_policy_check.py's own stated rationale for doing
the same.
"""
import pytest

from spreading_check import compute
from state_manager import read_state, write_state


def test_compute_requires_either_fresh_or_already_on_file_financials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        compute("Acme Corp", "Fleet Loan")


def test_compute_writes_financials_ratios_and_marks_framework_computed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = compute("Acme Corp", "Fleet Loan",
                     multi_period_financials={"FY-Current": {"revenue": 1000, "cost_of_sales": 600}})

    assert state["financials_source"] == "framework-computed"
    assert state["financials"]["FY-Current"]["gross_profit"] == 400
    assert state["ratios"]["FY-Current"]["ebitda_interest_cover"] is None  # no interest_paid given
    assert state["multi_period_financials"] == {"FY-Current": {"revenue": 1000, "cost_of_sales": 600}}

    # Actually checkpointed to disk, not just returned in memory.
    on_disk = read_state("Acme Corp", "Fleet Loan")
    assert on_disk["financials"]["FY-Current"]["gross_profit"] == 400


def test_compute_includes_working_capital_days(tmp_path, monkeypatch):
    """Regression guard for issue #99 -- the primary gap this script closes
    alongside #98: the figures it writes must actually include Working
    Capital Days, not just the ratios that already existed before #99."""
    monkeypatch.chdir(tmp_path)
    state = compute("Acme Corp", "Fleet Loan", multi_period_financials={
        "FY-Current": {"revenue": 1000, "cost_of_sales": 600, "trade_debtors": 150,
                        "trade_creditors": 100, "stock": 80},
    })

    ratios = state["ratios"]["FY-Current"]
    assert ratios["trade_debtor_days"] == pytest.approx(54.75)
    assert ratios["working_capital_cycle_days"] is not None


def test_compute_merges_new_periods_without_erasing_existing_ones(tmp_path, monkeypatch):
    """The core correctness property this wrapper adds over a naive
    "write whatever evaluate_financial_model() returns" -- /project supplying
    forward years must not erase /spread's already-recorded historical
    periods, since write_state()'s own merge is a shallow dict.update()."""
    monkeypatch.chdir(tmp_path)
    compute("Acme Corp", "Fleet Loan", multi_period_financials={"FY-Current": {"revenue": 1000}})

    state = compute("Acme Corp", "Fleet Loan", multi_period_financials={"FY+1": {"revenue": 1100}})

    assert set(state["financials"].keys()) == {"FY-Current", "FY+1"}
    assert set(state["ratios"].keys()) == {"FY-Current", "FY+1"}
    assert set(state["multi_period_financials"].keys()) == {"FY-Current", "FY+1"}


def test_compute_recomputes_a_period_given_again_rather_than_duplicating(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    compute("Acme Corp", "Fleet Loan", multi_period_financials={"FY-Current": {"revenue": 1000}})
    state = compute("Acme Corp", "Fleet Loan",
                     multi_period_financials={"FY-Current": {"revenue": 2000}})  # corrected figure

    assert state["financials"]["FY-Current"]["raw"]["revenue"] == 2000
    assert len(state["financials"]) == 1


def test_compute_merges_a_partial_period_correction_field_by_field(tmp_path, monkeypatch):
    """Regression guard: correcting just one raw field for an already-
    recorded period must not silently discard every other field already on
    file for that period -- the merge must be two levels deep (period, then
    field), not a whole-period replace."""
    monkeypatch.chdir(tmp_path)
    compute("Acme Corp", "Fleet Loan",
            multi_period_financials={"FY-Current": {"revenue": 1000, "cost_of_sales": 600}})

    state = compute("Acme Corp", "Fleet Loan",
                     multi_period_financials={"FY-Current": {"revenue": 1050}})  # only revenue corrected

    raw = state["multi_period_financials"]["FY-Current"]
    assert raw == {"revenue": 1050, "cost_of_sales": 600}
    assert state["financials"]["FY-Current"]["gross_profit"] == 450  # 1050 - 600, not 1050 - 0


def test_compute_derives_downside_case_when_stress_assumptions_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = compute(
        "Acme Corp", "Fleet Loan",
        multi_period_financials={"FY+1": {"revenue": 1000, "interest_paid": 50,
                                           "long_term_debt": 200}},
        stress_assumptions={"revenue_haircut_pct": 10},
    )

    assert state["stress_assumptions"] == {"revenue_haircut_pct": 10}
    assert state["downside_case"]["financials"]["FY+1"]["raw"]["revenue"] == 900


def test_compute_reuses_stress_assumptions_already_on_file(tmp_path, monkeypatch):
    """A fresh /spread run (financials only, no --stress-assumptions) must
    still recompute downside_case against the new base data using whatever
    stress assumptions an earlier /project run already confirmed -- matching
    orchestrator.py's own "recompute whenever either input changes"
    behavior."""
    monkeypatch.chdir(tmp_path)
    compute("Acme Corp", "Fleet Loan",
            multi_period_financials={"FY+1": {"revenue": 1000}},
            stress_assumptions={"revenue_haircut_pct": 20})

    state = compute("Acme Corp", "Fleet Loan",
                     multi_period_financials={"FY+1": {"revenue": 2000}})  # revised base case

    assert state["downside_case"]["financials"]["FY+1"]["raw"]["revenue"] == 1600


def test_compute_merges_a_partial_stress_assumptions_correction_key_by_key(tmp_path, monkeypatch):
    """Regression guard: /project's own prose tells the analyst to omit a
    shock key entirely when it isn't being changed this run ("each
    independently defaults to no shock") -- a fresh dict that only
    re-confirms one shock must not silently drop an already-confirmed one it
    didn't mention."""
    monkeypatch.chdir(tmp_path)
    compute("Acme Corp", "Fleet Loan",
            multi_period_financials={"FY+1": {"revenue": 1000}},
            stress_assumptions={"revenue_haircut_pct": 10, "opex_increase_pct": 5})

    state = compute("Acme Corp", "Fleet Loan",
                     stress_assumptions={"revenue_haircut_pct": 15})  # only this one revised

    assert state["stress_assumptions"] == {"revenue_haircut_pct": 15, "opex_increase_pct": 5}


def test_compute_omits_downside_case_when_no_forward_period_exists_yet(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = compute("Acme Corp", "Fleet Loan",
                     multi_period_financials={"FY-Current": {"revenue": 1000}},
                     stress_assumptions={"revenue_haircut_pct": 10})

    assert "downside_case" not in state


def test_compute_does_not_flip_analyst_supplied_flag_when_update_financials_source_is_false(
    tmp_path, monkeypatch,
):
    """Regression guard: /project supplying forward-year figures on a deal
    whose historicals were recorded via /spread's analyst-supplied mode must
    never silently flip the deal's financials_source back to
    "framework-computed" -- the historicals were never independently
    recomputed, so Guideline 9's caveat requirement still applies regardless
    of what /project just added. /project's own call always passes
    update_financials_source=False for exactly this reason."""
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", financials_source="analyst-supplied",
                financials={"FY-Current": {"raw": {}}})

    state = compute("Acme Corp", "Fleet Loan",
                     multi_period_financials={"FY+1": {"revenue": 1000}},
                     update_financials_source=False)

    assert state["financials_source"] == "analyst-supplied"
    assert "FY+1" in state["financials"]  # the forward-year data still gets written


def test_compute_sets_framework_computed_by_default(tmp_path, monkeypatch):
    """Positive counterpart -- /spread's own call (update_financials_source
    left at its True default) must still set the flag as before."""
    monkeypatch.chdir(tmp_path)
    state = compute("Acme Corp", "Fleet Loan",
                     multi_period_financials={"FY-Current": {"revenue": 1000}})

    assert state["financials_source"] == "framework-computed"


def test_compute_preexisting_multi_period_financials_allows_stress_only_call(tmp_path, monkeypatch):
    """/project supplying stress assumptions in a later call than the one
    that supplied the forward-year base case (e.g. the analyst decides on
    stress scenarios after first capturing the budget) must still find that
    base case on file and derive a downside case from it."""
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", multi_period_financials={"FY+1": {"revenue": 1000}})

    state = compute("Acme Corp", "Fleet Loan", stress_assumptions={"revenue_haircut_pct": 10})

    assert state["downside_case"]["financials"]["FY+1"]["raw"]["revenue"] == 900
