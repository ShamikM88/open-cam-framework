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

import spreading_check
from spreading_check import compute, main, plan_fields
from numeric_validation import validate_period_objects
from state_manager import read_state, write_state


def test_validate_period_objects_rejects_a_non_object_period_map():
    with pytest.raises(ValueError, match="financial inputs must be an object keyed by period"):
        validate_period_objects(["FY+1"])


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


def test_plan_fields_refuses_historical_raw_years_on_an_analyst_supplied_forecast_deal_whatever_the_source_flag():
    """The analyst-supplied forecast guard stops a recomputed forward year and stamping the deal framework-computed.
    Since decision D3 (#271) a historical year is also refused when update_financials_source=False (what /project passes):
    the deal is analyst-supplied, so its history is never recomputed here."""
    existing = {"forecast_source": "analyst-supplied", "financials_source": "analyst-supplied"}
    historical = {"FY-Current": {"revenue": 1000, "cost_of_sales": 400}}
    with pytest.raises(ValueError, match="analyst-supplied.*historical"):
        plan_fields(existing, historical, update_financials_source=False)
    with pytest.raises(ValueError, match="would stamp the whole deal framework-computed"):
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
    with pytest.raises(SystemExit, match="error: Nothing to compute"):
        main(["--company", "Acme Corp", "--proposal", "Fleet Loan"])
    assert not (tmp_path / "deals").exists()


def test_main_with_a_missing_financials_file_fails_before_writing_anything(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="error: "):
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


def _analyst_supplied_deal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state(
        "Acme Corp", "Fleet Loan",
        financials_source="analyst-supplied",
        analyst_supplied_financials={"FY-Current": {"revenue": 5000, "cost_of_sales": 4001}},
        financials={"FY-Current": {"ebitda": 300, "gross_profit": 999}},
        ratios={"FY-Current": {"dscr": 1.3}},
        steps_completed=["spread"],
    )
    return next((tmp_path / "deals" / "Acme Corp").glob("Fleet Loan_*/state.json"))


@pytest.mark.parametrize("historical", [
    {"FY-Current": {"revenue": 5000, "cost_of_sales": 3000}},
    {"FY-1": {"revenue": 4000}},
    {"FY-2": {"revenue": 3500}, "FY+1": {"revenue": 5200, "cost_of_sales": 3100}},
], ids=["current", "prior-year", "historical-and-forward"])
def test_historical_input_on_an_analyst_supplied_deal_is_refused_whatever_the_source_flag_says(
    tmp_path, monkeypatch, historical,
):
    """Decision D3 (#271): --no-update-financials-source (what /project passes) must not let historical raw figures
    overwrite analyst-supplied subtotals or create a multi_period_financials store beside analyst_supplied_financials."""
    path = _analyst_supplied_deal(tmp_path, monkeypatch)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="analyst-supplied.*historical"):
        compute("Acme Corp", "Fleet Loan", multi_period_financials=historical, update_financials_source=False)

    assert path.read_bytes() == before
    assert "multi_period_financials" not in json.loads(before)


def test_forward_only_input_on_an_analyst_supplied_deal_is_still_allowed_with_no_update_financials_source(
    tmp_path, monkeypatch,
):
    path = _analyst_supplied_deal(tmp_path, monkeypatch)

    compute("Acme Corp", "Fleet Loan",
            multi_period_financials={"FY+1": {"revenue": 5200, "cost_of_sales": 3100}}, update_financials_source=False)

    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["financials_source"] == "analyst-supplied"
    assert state["financials"]["FY-Current"] == {"ebitda": 300, "gross_profit": 999}, "history is untouched"
    assert set(state["multi_period_financials"]) == {"FY+1"}

# ---------------------------------------------------------------------------
# Mutation-survivor contracts for plan_fields(), compute() and main().
# The assertions pin input refusal, exact user-facing error values, deep merge
# outcomes and the guarantee that rejected CLI inputs do not write state.
# ---------------------------------------------------------------------------

def test_sc_plan_fields_refuses_analyst_supplied_forward_periods_with_exact_reason_and_no_mutation():
    existing = {
        "forecast_source": "analyst-supplied",
        "financials_source": "analyst-supplied",
        "multi_period_financials": {"FY-Current": {"revenue": 1000}},
    }
    incoming = {"FY+2": {"revenue": 1250}}
    existing_before = json.loads(json.dumps(existing))
    incoming_before = json.loads(json.dumps(incoming))

    with pytest.raises(ValueError) as caught:
        plan_fields(existing, incoming, update_financials_source=False)

    assert str(caught.value) == (
        "This deal's forecast (FY+1 to FY+3) was supplied by the analyst and is recorded as given; "
        "computing forward years from raw lines here would overwrite or blend it. Record forward-year "
        "changes with scripts/supplied_forecast.py."
    )
    assert existing == existing_before
    assert incoming == incoming_before


def test_sc_plan_fields_refuses_historical_framework_computation_over_supplied_forecast_with_exact_reason():
    existing = {"forecast_source": "analyst-supplied"}
    incoming = {"FY-Current": {"revenue": 1000}}

    with pytest.raises(ValueError) as caught:
        plan_fields(existing, incoming, update_financials_source=True)

    assert str(caught.value) == (
        "This deal's forecast was supplied by the analyst, so its basis is analyst-supplied for the whole "
        "deal; spreading historical years in framework-computed mode here would stamp the whole deal "
        "framework-computed over it. Record the historical years in /spread's analyst-supplied mode."
    )
    assert existing == {"forecast_source": "analyst-supplied"}
    assert incoming == {"FY-Current": {"revenue": 1000}}


def test_sc_plan_fields_lists_all_refused_historical_periods_in_sorted_order():
    existing = {"financials_source": "analyst-supplied"}
    incoming = {
        "FY-2": {"revenue": 700},
        "FY+1": {"revenue": 1100},
        "FY-1": {"revenue": 800},
    }

    with pytest.raises(ValueError) as caught:
        plan_fields(existing, incoming, update_financials_source=False)

    assert str(caught.value) == (
        "This deal is analyst-supplied; one deal never mixes the two modes, "
        "so historical periods (FY-1, FY-2) cannot be recomputed here. "
        "Use /spread's analyst-supplied mode to record financial figures."
    )
    assert existing == {"financials_source": "analyst-supplied"}
    assert incoming == {
        "FY-2": {"revenue": 700},
        "FY+1": {"revenue": 1100},
        "FY-1": {"revenue": 800},
    }


def test_sc_plan_fields_stress_only_call_without_a_forward_period_refuses_without_changing_input():
    existing = {
        "multi_period_financials": {"FY-Current": {"revenue": 1000}},
        "stress_assumptions": {"revenue_haircut_pct": 10},
    }
    stress = {"opex_increase_pct": 5}
    existing_before = json.loads(json.dumps(existing))
    stress_before = dict(stress)

    with pytest.raises(ValueError) as caught:
        plan_fields(existing, stress_assumptions=stress)

    assert str(caught.value) == (
        "Nothing to compute: --financials produced no new periods and no "
        "stress assumptions (existing or given) produced a downside case."
    )
    assert existing == existing_before
    assert stress == stress_before


def test_sc_plan_fields_preserves_untouched_computed_period_values_when_recomputing_one_period():
    existing = {
        "multi_period_financials": {
            "FY-1": {"revenue": 800, "cost_of_sales": 500},
            "FY-Current": {"revenue": 1000, "cost_of_sales": 600},
        },
        "financials": {
            "FY-1": {"revenue": 800, "gross_profit": 300, "raw": {"revenue": 800, "cost_of_sales": 500}},
            "FY-Current": {"revenue": 1000, "gross_profit": 400, "raw": {"revenue": 1000, "cost_of_sales": 600}},
        },
        "ratios": {"FY-1": {"dscr": 1.4}, "FY-Current": {"dscr": 1.6}},
    }

    fields = plan_fields(existing, {"FY-Current": {"revenue": 1050}})

    assert fields["multi_period_financials"] == {
        "FY-1": {"revenue": 800, "cost_of_sales": 500},
        "FY-Current": {"revenue": 1050, "cost_of_sales": 600},
    }
    assert fields["financials"]["FY-Current"]["raw"] == {"revenue": 1050, "cost_of_sales": 600}
    assert fields["financials"]["FY-Current"]["gross_profit"] == 450
    assert fields["financials"]["FY-1"] == existing["financials"]["FY-1"]
    assert fields["ratios"]["FY-1"] == {"dscr": 1.4}


@pytest.mark.parametrize(("shock", "message"), [
    ({"revenue_haircut_pct": True},
     "stress assumptions: revenue_haircut_pct must be a finite JSON number; found True"),
    ({"opex_increase_pct": "5"},
     "stress assumptions: opex_increase_pct must be a finite JSON number; found '5'"),
    ({"revenue_haircut_pct": -1},
     "stress assumptions: revenue_haircut_pct must be non-negative; found -1"),
    ({"revenue_haircut_pct": 100},
     "stress assumptions: revenue_haircut_pct must be less than 100; found 100"),
    ({"interest_rate_bump_bps": float("inf")},
     "non-finite numeric value at stress assumptions['interest_rate_bump_bps']: inf"),
], ids=["boolean-is-not-a-number", "numeric-string-is-not-a-number", "negative-shock",
        "hundred-percent-haircut", "nonfinite-shock"])
def test_sc_compute_refuses_invalid_stress_values_before_creating_deal_state(tmp_path, monkeypatch, shock, message):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError) as caught:
        compute("Synthetic Co", "Credit Facility", stress_assumptions=shock)

    assert str(caught.value) == message
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize(("raw_value", "expected_repr"), [
    ("1000", "'1000'"),
    (True, "True"),
    (float("inf"), "inf"),
], ids=["numeric-string", "boolean", "infinity"])
def test_sc_main_refuses_invalid_recognized_raw_values_with_one_error_and_no_write(
    tmp_path, monkeypatch, capsys, raw_value, expected_repr,
):
    monkeypatch.chdir(tmp_path)
    financials = _json_file(tmp_path, "financials.json", {"FY+1": {"revenue": raw_value}})

    with pytest.raises(SystemExit) as caught:
        main(["--company", "Synthetic Co", "--proposal", "Credit Facility", "--financials", financials])

    assert caught.value.code == (
        "error: financial inputs: period 'FY+1', field 'revenue' must be a finite JSON number or null; "
        f"found {expected_repr}"
    )
    # In-process sys.exit(string) prints nothing until interpreter exit; subprocess tests cover real-CLI one-line error/exit-1.
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize(("period_value", "type_name"), [
    ("not a period object", "str"),
    (["revenue", 1000], "list"),
    (1000, "int"),
    (None, "NoneType"),
], ids=["string-period", "list-period", "scalar-period", "null-period"])
def test_sc_main_refuses_non_object_period_values_with_one_error_and_no_write(
    tmp_path, monkeypatch, capsys, period_value, type_name,
):
    monkeypatch.chdir(tmp_path)
    financials = _json_file(tmp_path, "financials.json", {"FY+1": period_value})

    with pytest.raises(SystemExit) as caught:
        main(["--company", "Synthetic Co", "--proposal", "Credit Facility", "--financials", financials])

    assert caught.value.code == (
        f"error: financial inputs: period 'FY+1' must be an object of line items; found {type_name}"
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize(("flag", "payload"), [
    ("--financials", ["FY+1", {"revenue": 1000}]),
    ("--stress-assumptions", ["revenue_haircut_pct", 10]),
], ids=["financial-root-list", "stress-root-list"])
def test_sc_main_refuses_non_object_json_roots_with_one_error_and_no_write(
    tmp_path, monkeypatch, capsys, flag, payload,
):
    monkeypatch.chdir(tmp_path)
    input_file = _json_file(tmp_path, "input.json", payload)

    with pytest.raises(SystemExit) as caught:
        main(["--company", "Synthetic Co", "--proposal", "Credit Facility", flag, input_file])

    assert caught.value.code == f"error: {input_file} must contain a JSON object, found list"
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize(("shock", "message"), [
    ({"revenue_haircut_pct": True},
     "error: stress assumptions: revenue_haircut_pct must be a finite JSON number; found True"),
    ({"opex_increase_pct": "5"},
     "error: stress assumptions: opex_increase_pct must be a finite JSON number; found '5'"),
    ({"opex_increase_pct": -1},
     "error: stress assumptions: opex_increase_pct must be non-negative; found -1"),
    ({"revenue_haircut_pct": 100},
     "error: stress assumptions: revenue_haircut_pct must be less than 100; found 100"),
], ids=["boolean", "string", "negative", "upper-bound"])
def test_sc_main_refuses_invalid_stress_json_with_one_error_and_no_write(
    tmp_path, monkeypatch, capsys, shock, message,
):
    monkeypatch.chdir(tmp_path)
    stress = _json_file(tmp_path, "stress.json", shock)

    with pytest.raises(SystemExit) as caught:
        main([
            "--company", "Synthetic Co", "--proposal", "Credit Facility",
            "--stress-assumptions", stress,
        ])

    assert caught.value.code == message
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert not (tmp_path / "deals").exists()


def test_sc_main_reports_a_missing_stress_file_as_one_error_without_writing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    missing = str(tmp_path / "missing-stress.json")

    with pytest.raises(SystemExit) as caught:
        main([
            "--company", "Synthetic Co", "--proposal", "Credit Facility",
            "--stress-assumptions", missing,
        ])

    assert caught.value.code.startswith(f"error: cannot read {missing}: ")
    assert "\n" not in caught.value.code
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert not (tmp_path / "deals").exists()



# Additional main()/plan_fields() mutation-survivor contracts.
def test_main_requires_company_when_proposal_is_present(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(["--proposal", "Synthetic Facility"])
    assert caught.value.code == 2
    assert "usage:" in capsys.readouterr().err
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("flag", ["--financials", "--stress-assumptions"])
def test_main_reads_financials_and_stress_files_as_utf8_with_cp1252_default_open(
    tmp_path, monkeypatch, capsys, cp1252_default_open, flag,
):
    monkeypatch.chdir(tmp_path)
    financials = tmp_path / "financials.json"
    financials.write_text(
        json.dumps({"FY+1": dict(RAW_YEAR, note="£")}, ensure_ascii=False),
        encoding="utf-8",
    )
    stress = tmp_path / "stress.json"
    stress.write_text(
        json.dumps({"revenue_haircut_pct": 10, "note": "£"}, ensure_ascii=False),
        encoding="utf-8",
    )

    args = ["--company", "Synthetic Co", "--proposal", "Credit Facility",
            "--financials", str(financials), "--stress-assumptions", str(stress)]
    printed = _run_main(capsys, *args)
    state = read_state("Synthetic Co", "Credit Facility")
    if flag == "--financials":
        assert printed["financials"]["FY+1"]["raw"]["note"] == "£"
    else:
        assert state["stress_assumptions"]["note"] == "£"


@pytest.mark.parametrize("flag", ["--financials", "--stress-assumptions"])
def test_main_reports_non_utf8_input_path_and_error_prefix(
    tmp_path, monkeypatch, capsys, cp1252_default_open, flag,
):
    monkeypatch.chdir(tmp_path)
    bad_file = tmp_path / ("financials.bin" if flag == "--financials" else "stress.bin")
    bad_file.write_bytes(b"\xff")
    with pytest.raises(SystemExit) as caught:
        main(["--company", "Synthetic Co", "--proposal", "Credit Facility", flag, str(bad_file)])
    assert caught.value.code.startswith(f"error: {bad_file} is not UTF-8 text: ")
    assert str(bad_file) in caught.value.code
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("flag", ["--financials", "--stress-assumptions"])
def test_main_reports_invalid_json_input_path_and_error_prefix(
    tmp_path, monkeypatch, capsys, flag,
):
    monkeypatch.chdir(tmp_path)
    bad_file = tmp_path / ("financials.json" if flag == "--financials" else "stress.json")
    bad_file.write_text("{ not valid JSON", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["--company", "Synthetic Co", "--proposal", "Credit Facility", flag, str(bad_file)])
    assert caught.value.code.startswith(f"error: invalid JSON in {bad_file}: ")
    assert str(bad_file) in caught.value.code
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize(
    ("flag", "payload", "expected"),
    [
        (
            "--financials",
            '{"FY+1":{"unknown_line_item":NaN}}',
            "error: non-finite numeric value at financial inputs['FY+1']['unknown_line_item']: nan",
        ),
        (
            "--stress-assumptions",
            '{"revenue_haircut_pct":NaN}',
            "error: non-finite numeric value at stress assumptions['revenue_haircut_pct']: nan",
        ),
    ],
    ids=["unknown-financial-line-item-nonfinite", "nonfinite-stress-assumption"],
)
def test_main_names_the_financial_or_stress_validator_path_and_does_not_write_state(
    tmp_path, monkeypatch, capsys, flag, payload, expected,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        spreading_check,
        "compute",
        lambda *args, **kwargs: {
            "financials": None, "ratios": None, "downside_case": None, "financials_source": None,
        },
    )
    path = tmp_path / "input.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["--company", "Synthetic Co", "--proposal", "Credit Facility", flag, str(path)])
    assert caught.value.code == expected
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "deals").exists()


_PRINTED_STATE = {
    "financials": {"FY-Current": {"gross_profit": 600}},
    "ratios": {"FY-Current": {"dscr": 1.25}},
    "downside_case": None,
    "financials_source": "framework-computed",
}


def test_main_prints_one_json_object_with_exactly_the_four_state_keys(monkeypatch, capsys):
    monkeypatch.setattr(spreading_check, "compute", lambda *args, **kwargs: _PRINTED_STATE)

    main(["--company", "Synthetic Co", "--proposal", "Credit Facility"])

    # Parsed, so this test does not depend on member order or whitespace.
    assert json.loads(capsys.readouterr().out) == _PRINTED_STATE


def test_main_output_format_is_two_space_indented_json_with_one_trailing_newline(monkeypatch, capsys):
    # The exact text is pinned on purpose: docs/cli-reference.md states that the output is two-space-indented JSON
    # ending in one newline, and a reviewed change to that format should have to update this test and that sentence.
    # Consumers parse the JSON and must not rely on member order; this test is what keeps the formatting
    # mutants (`indent=2` changed or dropped) from surviving in the weekly mutation run.
    monkeypatch.setattr(spreading_check, "compute", lambda *args, **kwargs: _PRINTED_STATE)

    main(["--company", "Synthetic Co", "--proposal", "Credit Facility"])

    assert capsys.readouterr().out == json.dumps(_PRINTED_STATE, indent=2) + "\n"


def test_sc_plan_fields_defaults_to_stamping_framework_computed_basis_when_unset():
    fields = plan_fields(
        {},
        {"FY-Current": {"revenue": 1000, "cost_of_sales": 400}},
    )
    assert fields["financials_source"] == "framework-computed"


def test_sc_plan_fields_period_object_error_names_financial_inputs():
    with pytest.raises(ValueError) as caught:
        plan_fields({}, {"FY-Current": "not a period object"})
    assert str(caught.value) == (
        "financial inputs: period 'FY-Current' must be an object of line items; found str"
    )


def test_sc_plan_fields_historical_period_phrase_is_absent_without_historical_periods():
    with pytest.raises(ValueError) as caught:
        plan_fields(
            {"financials_source": "analyst-supplied"},
            {"FY+1": {"revenue": 1200, "cost_of_sales": 500}},
        )
    assert str(caught.value) == (
        "This deal is analyst-supplied; one deal never mixes the two modes. "
        "Use /spread's analyst-supplied mode to record financial figures."
    )


def test_sc_plan_fields_nothing_to_compute_message_is_exact():
    with pytest.raises(ValueError) as caught:
        plan_fields({})
    assert str(caught.value) == (
        "Nothing to compute: no --financials given, and this deal has no "
        "multi_period_financials already on file."
    )


@pytest.mark.parametrize(
    ("downside_case", "expected_stored"),
    [
        ({"financials": {"FY+1": {"ebitda": 10}}, "ratios": {}}, True),
        ({"financials": {}, "ratios": {"FY+1": {"dscr": 1.2}}}, True),
        (
            {"financials": {"FY+1": {"ebitda": 10}}, "ratios": {"FY+1": {"dscr": 1.2}}},
            True,
        ),
        ({"financials": {}, "ratios": {}}, False),
    ],
    ids=["financials-only", "ratios-only", "both", "neither"],
)
def test_sc_plan_fields_stores_downside_when_either_financials_or_ratios_exist(
    monkeypatch, downside_case, expected_stored,
):
    monkeypatch.setattr("spreading_check.evaluate_downside_case", lambda *args, **kwargs: downside_case)
    fields = plan_fields(
        {},
        {"FY+1": {"revenue": 1000, "cost_of_sales": 400}},
        {"revenue_haircut_pct": 10},
    )
    assert ("downside_case" in fields) is expected_stored
    assert ("stress_assumptions" in fields) is expected_stored
    if expected_stored:
        assert fields["downside_case"] == downside_case


def test_main_names_financial_inputs_in_raw_line_item_validation_error(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "financials.json"
    path.write_text('{"FY+1":{"revenue":"not a number"}}', encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main([
            "--company", "Synthetic Co", "--proposal", "Credit Facility",
            "--financials", str(path),
        ])
    assert caught.value.code == (
        "error: financial inputs: period 'FY+1', field 'revenue' must be a finite JSON number or null; "
        "found 'not a number'"
    )
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "deals").exists()
