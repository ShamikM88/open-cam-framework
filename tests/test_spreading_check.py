"""Tests for scripts/spreading_check.py: the standalone CLI wrapper that lets
/spread and /project's Bash steps run the same formula evaluation
orchestrator.py's headless pipeline does, instead of Claude recalculating
subtotals/ratios by hand in prose (see issue #98).

Tests call compute() directly rather than invoking the script as a
subprocess -- the __main__ block is a thin argparse+print wrapper around
compute(), matching test_policy_check.py's own stated rationale for doing
the same.
"""
import json

import pytest

from spreading_check import compute, main, plan_fields
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


# ---------------------------------------------------------------------------
# plan_fields(): the two guards no other test reached (issue #219)
# ---------------------------------------------------------------------------

def test_plan_fields_refuses_a_call_that_adds_no_period_and_no_stress_even_when_the_deal_has_figures():
    """Nothing to compute is an error, not an empty success: the deal already has raw figures (so the "no figures on file"
    guard is not the one that fires), but this call supplies no period and no stress assumption, so computing would write
    nothing while looking as if it had worked."""
    existing = {"multi_period_financials": {"FY-Current": {"revenue": 1000, "cost_of_sales": 400}}}
    with pytest.raises(ValueError, match="Nothing to compute: --financials produced no new periods"):
        plan_fields(existing)


def test_plan_fields_takes_historical_raw_years_on_an_analyst_supplied_forecast_deal_only_without_relabelling_it():
    """The analyst-supplied forecast guard stops two things: a recomputed forward year, and stamping the deal
    framework-computed. A historical year passed with update_financials_source=False (what /project passes) does neither,
    so it is accepted and the basis label is left alone; the same call that would relabel the deal is refused."""
    existing = {"forecast_source": "analyst-supplied", "financials_source": "analyst-supplied"}
    historical = {"FY-Current": {"revenue": 1000, "cost_of_sales": 400}}
    fields = plan_fields(existing, historical, update_financials_source=False)
    assert "financials_source" not in fields, "the basis label is not rewritten"
    assert fields["financials"]["FY-Current"]["raw"]["revenue"] == 1000
    assert fields["multi_period_financials"] == historical
    with pytest.raises(ValueError, match="one deal never mixes the two modes"):
        plan_fields(existing, historical, update_financials_source=True)
    assert existing == {"forecast_source": "analyst-supplied", "financials_source": "analyst-supplied"}, "input untouched"


# ---------------------------------------------------------------------------
# main(): the command-line wrapper, called in-process (issue #182)
#
# tests/test_cli_subprocess.py still runs the real entry point as a child process; mutmut runs the selected tests in this
# process, so only calls like these can reach (and so kill mutants in) main() itself.
# ---------------------------------------------------------------------------

RAW_YEAR = {"revenue": 1000, "cost_of_sales": 400, "admin_expenses": 200}


def _json_file(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(json.dumps(content), encoding="utf-8")
    return str(path)


def _run_main(capsys, *argv):
    main(list(argv))
    out = capsys.readouterr()
    assert out.err == ""
    return json.loads(out.out)


def test_main_recomputes_prints_the_four_keys_and_checkpoints_the_same_figures(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    financials = _json_file(tmp_path, "fin.json", {"FY-Current": RAW_YEAR})

    printed = _run_main(capsys, "--company", "Acme Corp", "--proposal", "Fleet Loan", "--financials", financials)

    assert list(printed) == ["financials", "ratios", "downside_case", "financials_source"]
    assert printed["financials"]["FY-Current"]["gross_profit"] == 600
    assert printed["financials"]["FY-Current"]["raw"] == RAW_YEAR
    assert printed["downside_case"] is None, "no stress assumption, so no downside case"
    assert printed["financials_source"] == "framework-computed", "the default stamps the basis"
    state = read_state("Acme Corp", "Fleet Loan")
    assert state["financials"] == printed["financials"] and state["ratios"] == printed["ratios"]
    assert state["multi_period_financials"] == {"FY-Current": RAW_YEAR}


def test_main_reads_the_stress_assumptions_file_and_prints_the_downside_case(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    financials = _json_file(tmp_path, "fin.json", {"FY+1": RAW_YEAR})
    stress = _json_file(tmp_path, "stress.json", {"revenue_haircut_pct": 10})

    printed = _run_main(capsys, "--company", "Acme Corp", "--proposal", "Fleet Loan", "--financials", financials,
                        "--stress-assumptions", stress)

    assert printed["downside_case"]["financials"]["FY+1"]["raw"]["revenue"] == 900
    assert read_state("Acme Corp", "Fleet Loan")["stress_assumptions"] == {"revenue_haircut_pct": 10}


def test_main_accepts_stress_assumptions_alone_for_a_deal_that_already_has_figures(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", multi_period_financials={"FY+1": RAW_YEAR})
    stress = _json_file(tmp_path, "stress.json", {"revenue_haircut_pct": 20})

    printed = _run_main(capsys, "--company", "Acme Corp", "--proposal", "Fleet Loan", "--stress-assumptions", stress)

    assert printed["downside_case"]["financials"]["FY+1"]["raw"]["revenue"] == 800


def test_main_no_update_financials_source_leaves_the_basis_label_as_it_was(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", financials_source="analyst-supplied")
    financials = _json_file(tmp_path, "fin.json", {"FY+1": RAW_YEAR})

    printed = _run_main(capsys, "--company", "Acme Corp", "--proposal", "Fleet Loan", "--financials", financials,
                        "--no-update-financials-source")

    assert printed["financials_source"] == "analyst-supplied"
    assert read_state("Acme Corp", "Fleet Loan")["financials_source"] == "analyst-supplied"


def test_main_without_the_flag_does_stamp_the_basis_on_a_deal_that_had_none(tmp_path, monkeypatch, capsys):
    """The flag's default is the stamping behaviour: only the flag turns it off."""
    monkeypatch.chdir(tmp_path)
    financials = _json_file(tmp_path, "fin.json", {"FY-Current": RAW_YEAR})
    off = _run_main(capsys, "--company", "A", "--proposal", "P", "--financials", financials, "--no-update-financials-source")
    on = _run_main(capsys, "--company", "B", "--proposal", "P", "--financials", financials)
    assert off["financials_source"] is None and on["financials_source"] == "framework-computed"


def test_main_turns_a_state_error_into_one_error_line_and_changes_nothing(tmp_path, monkeypatch, capsys):
    """A state written by a newer framework is never downgraded: one `error:` line as the exit message, the file untouched."""
    monkeypatch.chdir(tmp_path)
    write_state("Acme Corp", "Fleet Loan", multi_period_financials={"FY-Current": RAW_YEAR})
    state_file = next((tmp_path / "deals" / "Acme Corp").glob("Fleet Loan_*/state.json"))
    newer = json.loads(state_file.read_text(encoding="utf-8"))
    newer["schema_version"] = "9.0.0"
    state_file.write_text(json.dumps(newer), encoding="utf-8")
    before = state_file.read_bytes()
    financials = _json_file(tmp_path, "fin.json", {"FY-Current": dict(RAW_YEAR, revenue=2000)})

    with pytest.raises(SystemExit) as caught:
        main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--financials", financials])

    message = str(caught.value.code)
    assert message.startswith("error: ") and "9.0.0" in message and "\n" not in message
    assert capsys.readouterr().out == "", "no result is printed for a refused call"
    assert state_file.read_bytes() == before


def test_main_with_nothing_to_compute_writes_no_state(tmp_path, monkeypatch):
    """No --financials and nothing on file is refused (as compute() does), and no deal folder is created for it."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="Nothing to compute"):
        main(["--company", "Acme Corp", "--proposal", "Fleet Loan"])
    assert not (tmp_path / "deals").exists()


def test_main_with_a_missing_financials_file_fails_before_writing_anything(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--financials", str(tmp_path / "nope.json")])
    assert not (tmp_path / "deals").exists()


def test_main_reads_its_input_files_as_utf8(tmp_path, monkeypatch, capsys):
    """A period label or a note with a non-ASCII character must not depend on the platform's default encoding."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "fin.json"
    path.write_text(json.dumps({"FY-Current": dict(RAW_YEAR, note="£ \u2014 \u00e9")}, ensure_ascii=False), encoding="utf-8")
    printed = _run_main(capsys, "--company", "Acme Corp", "--proposal", "Fleet Loan", "--financials", str(path))
    assert printed["financials"]["FY-Current"]["raw"]["note"] == "£ \u2014 \u00e9"


@pytest.mark.parametrize("argv, missing", [
    ([], "--company"),
    (["--company", "A"], "--proposal"),
])
def test_main_requires_company_and_proposal(capsys, argv, missing):
    with pytest.raises(SystemExit) as caught:
        main(argv)
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "usage:" in err and missing in err


def test_main_rejects_an_unknown_flag_and_help_lists_every_flag(capsys):
    with pytest.raises(SystemExit) as bad:
        main(["--company", "A", "--proposal", "P", "--nope"])
    assert bad.value.code == 2 and "unrecognized arguments: --nope" in capsys.readouterr().err
    with pytest.raises(SystemExit) as helped:
        main(["--help"])
    assert helped.value.code == 0
    text = capsys.readouterr().out
    for flag in ("--company", "--proposal", "--financials", "--stress-assumptions", "--no-update-financials-source"):
        assert flag in text, flag


def test_compute_refuses_framework_mode_for_analyst_supplied_financials_without_writing_state(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    write_state(
        "Acme Corp", "Fleet Loan",
        financials_source="analyst-supplied",
        financials_source_note="Synthetic institution convention.",
        analyst_supplied_financials={"FY-Current": {"revenue": 5000, "cost_of_sales": 3000}},
        financials={"FY-Current": {"raw": {"revenue": 5000, "cost_of_sales": 3000}}},
    )
    state_path = next((tmp_path / "deals" / "Acme Corp").glob("Fleet Loan_*/state.json"))
    before = state_path.read_bytes()

    with pytest.raises(ValueError, match="one deal never mixes the two modes"):
        compute(
            "Acme Corp", "Fleet Loan",
            multi_period_financials={
                "FY-Current": {"revenue": 5100, "cost_of_sales": 3050, "admin_expenses": 1000},
            },
        )

    assert state_path.read_bytes() == before
