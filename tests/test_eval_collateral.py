"""Regression tests for the v2 per-asset collateral evaluation fixtures.

All tests use scripted outputs and fake model clients. They make zero model calls
and never require an API key.
"""
import copy
import importlib.util
from pathlib import Path

import pytest

from collateral_check import (
    calculate_collateral_analysis,
    collateral_projection,
    stored_analysis_is_current,
)
from eval_budget import BudgetedClient, CallBudget
from eval_cases import DATASET_ROOT, load_dataset, validate_dataset
from eval_oracles import build_context, build_scripted_output, self_check
from eval_runner import run_case_once
from eval_fakes import FakeClient, make_fake_repo


def _fixture_module():
    import eval_collateral
    return eval_collateral


def test_v2_collateral_fixture_helper_module_is_shipped():
    # This is an assertion failure on main, not a collection-time import error.
    assert importlib.util.find_spec("eval_collateral") is not None


@pytest.fixture(scope="module")
def dataset_v2():
    return load_dataset("v2")


def collateral_case(dataset_v2, case_id):
    return copy.deepcopy(next(case for case in dataset_v2["cases"] if case["id"] == case_id))


def test_fixture_states_are_a_closed_enum():
    assert _fixture_module().VALID_FIXTURE_STATES == ("current", "result-tampered", "old-version")


def test_current_fixture_uses_the_real_calculator_and_projection(dataset_v2):
    case = collateral_case(dataset_v2, "collateral-correct-figure")
    fixture = case["deal"]["collateral_fixture"]
    built = _fixture_module().build_collateral_fixture(fixture)
    expected = calculate_collateral_analysis(fixture["payload"])

    assert built == {
        "analysis": expected,
        "collateral": collateral_projection(expected),
    }
    assert stored_analysis_is_current(built["analysis"])


@pytest.mark.parametrize("state", ["result-tampered", "old-version"])
def test_stale_fixture_mutations_are_deterministic_and_fail_closed(dataset_v2, state):
    case = collateral_case(dataset_v2, "collateral-stale-analysis")
    fixture = copy.deepcopy(case["deal"]["collateral_fixture"])
    fixture["state"] = state
    built = _fixture_module().build_collateral_fixture(fixture)

    assert built["collateral"] == collateral_projection(
        calculate_collateral_analysis(fixture["payload"])
    )
    assert not stored_analysis_is_current(built["analysis"])
    assert build_context({**case, "deal": {**case["deal"], "collateral_fixture": fixture}})[
        "ground_truth"
    ].get("collateral_AST-001_total_exposure") is None


def test_fixture_schema_rejects_open_ended_mutation_and_unknown_keys(dataset_v2):
    fixture = copy.deepcopy(collateral_case(dataset_v2, "collateral-correct-figure")["deal"]["collateral_fixture"])
    fixture["state"] = "mutate-anything"
    with pytest.raises(_fixture_module().CollateralFixtureError, match="state"):
        _fixture_module().build_collateral_fixture(fixture)

    fixture = copy.deepcopy(collateral_case(dataset_v2, "collateral-correct-figure")["deal"]["collateral_fixture"])
    fixture["mutation"] = "change a result"
    with pytest.raises(_fixture_module().CollateralFixtureError, match="unknown"):
        _fixture_module().build_collateral_fixture(fixture)

    fixture = copy.deepcopy(collateral_case(dataset_v2, "collateral-correct-figure")["deal"]["collateral_fixture"])
    fixture["payload"]["assets"][0]["arbitrary"] = 123
    with pytest.raises(_fixture_module().CollateralFixtureError, match="unknown"):
        _fixture_module().build_collateral_fixture(fixture)


def test_dataset_v2_is_v1_plus_six_and_copies_every_v1_case_byte_for_byte(dataset_v2):
    dataset_v1 = load_dataset("v1")
    assert validate_dataset(dataset_v2) == []
    assert len(dataset_v1["cases"]) == 15
    assert len(dataset_v2["cases"]) == 21
    assert {case["id"] for case in dataset_v1["cases"]} <= {case["id"] for case in dataset_v2["cases"]}

    v1_cases = Path(DATASET_ROOT) / "v1" / "cases"
    v2_cases = Path(DATASET_ROOT) / "v2" / "cases"
    for path in sorted(v1_cases.glob("*.json")):
        assert (v2_cases / path.name).read_bytes() == path.read_bytes(), path.name

    added = [case for case in dataset_v2["cases"] if case["id"].startswith("collateral-")]
    assert len(added) == 6
    assert {case["category"] for case in added} == {"collateral-figures"}
    assert all(case["mode"] == "maker" for case in added)
    for case in added:
        assert {a["oracle"] for a in case["assertions"]} >= {
            "structured_block_present", "figures_grounded"
        }
        assert case["human_review"]
        assert self_check(case)["ok"]


@pytest.mark.parametrize("case_id, key, reason", [
    ("collateral-correct-figure", "collateral_AST-001_total_exposure", "Narrative/Ground-Truth Mismatch"),
    ("collateral-wrong-figure", "collateral_AST-001_total_exposure", "Narrative/Ground-Truth Mismatch"),
    ("collateral-unknown-asset", "collateral_AST-999_total_exposure", "UNRESOLVABLE_REPORTED_FIGURE"),
    ("collateral-na-lgd-omitted", "collateral_AST-001_lgd_pct", "UNRESOLVABLE_REPORTED_FIGURE"),
    ("collateral-stale-analysis", "collateral_AST-001_total_exposure", "UNRESOLVABLE_REPORTED_FIGURE"),
    ("collateral-no-analysis", "collateral_AST-001_total_exposure", "UNRESOLVABLE_REPORTED_FIGURE"),
])
def test_each_v2_bad_scripted_output_fails_figures_grounded_for_its_intended_reason(
    dataset_v2, case_id, key, reason
):
    case = collateral_case(dataset_v2, case_id)
    outcome = self_check(case)
    assert outcome["ok"], (case_id, outcome)
    bad = build_scripted_output(case, case["dry_run"]["bad"])
    ctx = build_context(case)
    from eval_oracles import ORACLES
    passed, why = ORACLES["figures_grounded"](bad, case, ctx, {})
    assert not passed and reason in why, (case_id, key, why)


def test_na_lgd_is_omitted_from_ground_truth_not_defaulted_to_zero(dataset_v2):
    case = collateral_case(dataset_v2, "collateral-na-lgd-omitted")
    ctx = build_context(case)
    assert "collateral_AST-001_lgd_pct" not in ctx["ground_truth"]
    assert case["dry_run"]["good"]["structured"]["reported_figures"].get(
        "collateral_AST-001_lgd_pct"
    ) is None


def test_stale_and_absent_analysis_expose_no_per_asset_ground_truth(dataset_v2):
    stale = build_context(collateral_case(dataset_v2, "collateral-stale-analysis"))
    absent_case = collateral_case(dataset_v2, "collateral-no-analysis")
    absent = build_context(absent_case)

    assert stale["collateral_analysis"] is not None
    assert not stored_analysis_is_current(stale["collateral_analysis"])
    assert absent["collateral_analysis"] is None
    assert not any(key.startswith("collateral_AST-") for key in stale["ground_truth"])
    assert not any(key.startswith("collateral_AST-") for key in absent["ground_truth"])
    # A legacy collateral projection is context, not calculator-certified per-asset truth.
    assert absent["collateral"] == absent_case["deal"]["collateral"]


def test_oracle_ground_truth_is_the_framework_ground_truth_for_the_seeded_analysis(dataset_v2):
    from policy_checks import ground_truth_figures

    case = collateral_case(dataset_v2, "collateral-correct-figure")
    ctx = build_context(case)
    assert ctx["collateral"] == collateral_projection(ctx["collateral_analysis"])
    assert ctx["ground_truth"] == ground_truth_figures(
        ctx["financials"], ctx["ratios"], ctx["collateral"], ctx["downside_case"],
        collateral_analysis=ctx["collateral_analysis"],
    )


def test_runner_seeds_the_same_analysis_the_oracle_checked_and_does_not_recalculate_it(
    dataset_v2, tmp_path, monkeypatch
):
    import orchestrator
    from state_manager import read_state

    case = collateral_case(dataset_v2, "collateral-stale-analysis")
    ctx = build_context(case)
    repo = make_fake_repo(tmp_path)
    run_dir = tmp_path / "run"
    work_root = run_dir / "work"
    work_root.mkdir(parents=True)
    captured = {}
    real_pipeline = orchestrator.run_pipeline

    def observe_state_then_run(*args, **kwargs):
        captured["collateral_data"] = kwargs.get("collateral_data")
        captured["state"] = read_state(args[0], args[1])
        return real_pipeline(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "run_pipeline", observe_state_then_run)
    scripted = build_scripted_output(case, case["dry_run"]["good"], ctx).draft_text
    client = FakeClient(lambda kwargs: scripted)
    result = run_case_once(
        case, "repeat-1", ctx, BudgetedClient(client, CallBudget(2)),
        str(repo), str(work_root), keep_work=True,
    )

    assert result["live_calls"] == 1
    assert captured["collateral_data"] is None
    assert captured["state"]["collateral"] == ctx["collateral"]
    assert captured["state"]["collateral_analysis"] == ctx["collateral_analysis"]
    assert not stored_analysis_is_current(captured["state"]["collateral_analysis"])
