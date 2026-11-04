"""Stressed residual-realisation sensitivity (issue #549; register F-067).

Tests the deal-level ``stress_assumptions.rv_realisation_pct`` scenario: the
validator update that accepts the new key, the pure stressed computation
(residual-only stress, decision 12), and the report-only rule — stressed
figures appear in the calculator's JSON output when the stored deal carries
the assumption, and nothing derived is persisted (decision 13).

All figures are synthetic; no real borrower data.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import collateral_check
from collateral_check import (
    apply_rv_realisation_stress,
    calculate_collateral_analysis,
    stressed_collateral_value,
)
from numeric_validation import STRESS_KEYS, StressAssumptionsError, validate_stress_assumptions

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _asset(**overrides):
    """The #549 worked example: valuation 100,000, down 20%, RV 40%, collateral 60,000."""
    asset = {
        "asset_id": "AST-001",
        "asset_class": "Synthetic excavator",
        "valuation": 100000,
        "down_payment_pct": 0.20,
        "est_rv_pct": 0.40,
        "number_of_units": 1,
        "cap_or_model_value": 60000,
        "non_recovery": 0,
        "costs": 0,
        "lgd_pct": 0.15,
        "perfection_status": "Pending",
    }
    asset.update(overrides)
    return asset


def _payload(assets=None, mitigants=None):
    return {
        "assets": assets if assets is not None else [_asset()],
        "mitigants": mitigants if mitigants is not None else [],
    }


# ---------------------------------------------------------------------------
# The validator accepts the new key (decision 13) without changing the old ones
# ---------------------------------------------------------------------------

def test_the_new_key_is_a_recognised_stress_key():
    assert "rv_realisation_pct" in STRESS_KEYS


@pytest.mark.parametrize("value", [0, 50, 100, 0.5, 100.0])
def test_a_finite_realisation_from_zero_to_one_hundred_is_accepted(value):
    assert validate_stress_assumptions({"rv_realisation_pct": value}) == {"rv_realisation_pct": value}


@pytest.mark.parametrize("value", [-1, 101, -0.01, 100.01, float("nan"), float("inf"), "50", True, None, [50]])
def test_anything_else_is_refused(value):
    with pytest.raises(StressAssumptionsError, match="rv_realisation_pct"):
        validate_stress_assumptions({"rv_realisation_pct": value})


def test_a_sub_one_value_still_means_sub_one_percent(capsys):
    # The existing percent-key warning must keep firing for the new key:
    # 0.5 means 0.5 percent, not 50 percent realisation.
    validate_stress_assumptions({"rv_realisation_pct": 0.5})
    stderr = capsys.readouterr().err
    assert "rv_realisation_pct" in stderr and "between 0 and 1 percent" in stderr


def test_the_existing_keys_behave_exactly_as_before():
    # The new key must not change the old rules: revenue_haircut_pct stays
    # strictly below 100 and keeps its sub-1 warning; the others keep theirs.
    assert validate_stress_assumptions({"revenue_haircut_pct": 10}) == {"revenue_haircut_pct": 10}
    with pytest.raises(StressAssumptionsError, match="revenue_haircut_pct"):
        validate_stress_assumptions({"revenue_haircut_pct": 100})
    with pytest.raises(StressAssumptionsError, match="revenue_haircut_pct"):
        validate_stress_assumptions({"revenue_haircut_pct": -1})
    with pytest.raises(StressAssumptionsError, match="interest_rate_bump_bps"):
        validate_stress_assumptions({"interest_rate_bump_bps": "200"})


def test_an_absent_new_key_still_passes():
    assert validate_stress_assumptions({"revenue_haircut_pct": 10}) == {"revenue_haircut_pct": 10}
    assert validate_stress_assumptions({}) == {}


# ---------------------------------------------------------------------------
# The stressed computation: residual-only stress (decision 12)
# ---------------------------------------------------------------------------

def test_the_stressed_collateral_value_matches_the_corrected_worked_example():
    # Residual part = min(F-053 collateral value 60,000, F-050 RV exposure 40,000)
    # = 40,000; non-residual part = 20,000. At 50% realisation:
    # stressed collateral = 20,000 + 50% x 40,000 = 40,000 (not 30,000).
    analysis = calculate_collateral_analysis(_payload())
    results = analysis["assets"][0]["results"]
    assert stressed_collateral_value(results, 50) == pytest.approx(40000)
    # Full realisation is the base case; no realisation leaves only the
    # non-residual part.
    assert stressed_collateral_value(results, 100) == pytest.approx(60000)
    assert stressed_collateral_value(results, 0) == pytest.approx(20000)


def test_the_stressed_asset_figures_reuse_the_unchanged_exposures():
    # F-051 total exposure and F-052 net exposure are the unchanged
    # denominators; only F-053, F-054, F-055 and F-066 have stressed variants.
    analysis = calculate_collateral_analysis(_payload())
    stressed = apply_rv_realisation_stress(analysis, 50)
    asset = stressed["assets"][0]
    assert asset["asset_id"] == "AST-001"
    assert asset["stressed_collateral_value"] == pytest.approx(40000)
    assert asset["stressed_coverage_pct"] == pytest.approx(0.5)
    assert asset["stressed_uncovered_risk"] == pytest.approx(40000)
    assert asset["stressed_net_uncovered_risk"] == pytest.approx(40000)
    assert set(asset) == {
        "asset_id", "stressed_collateral_value", "stressed_coverage_pct",
        "stressed_uncovered_risk", "stressed_net_uncovered_risk",
    }


def test_the_stressed_deal_figures_sum_the_asset_figures():
    payload = _payload(assets=[
        _asset(asset_id="A"),
        _asset(asset_id="B", valuation=50000, cap_or_model_value=45000, est_rv_pct=0.0),
    ])
    analysis = calculate_collateral_analysis(payload)
    stressed = apply_rv_realisation_stress(analysis, 50)
    # A: collateral 60,000 (RV exposure 40,000 -> residual 40,000) -> 40,000.
    # B: collateral 45,000, no RV -> residual 0 -> 45,000 unchanged.
    assert stressed["assets"][0]["stressed_collateral_value"] == pytest.approx(40000)
    assert stressed["assets"][1]["stressed_collateral_value"] == pytest.approx(45000)
    results = stressed["results"]
    assert results["stressed_total_collateral_value"] == pytest.approx(85000)
    assert results["stressed_coverage_pct"] == pytest.approx(85000 / 120000)
    # A's stressed collateral 40,000 against its 80,000 exposure leaves 40,000
    # uncovered; B is fully covered (45,000 against 40,000).
    assert results["stressed_gross_uncovered_risk"] == pytest.approx(40000)
    assert results["stressed_net_uncovered_risk"] == pytest.approx(40000)


def test_stressed_net_uncovered_risk_accounts_for_mitigants():
    payload = _payload(mitigants=[{"amount": 30000, "eligible_for_netting": True}])
    analysis = calculate_collateral_analysis(payload)
    stressed = apply_rv_realisation_stress(analysis, 50)
    # Net exposure 50,000 (80,000 - 30,000 pro rata on the single asset);
    # stressed collateral 40,000 -> stressed net uncovered 10,000.
    asset = stressed["assets"][0]
    assert asset["stressed_net_uncovered_risk"] == pytest.approx(10000)
    assert stressed["results"]["stressed_net_uncovered_risk"] == pytest.approx(10000)


def test_a_stressed_collateral_value_never_goes_negative():
    analysis = calculate_collateral_analysis(_payload(
        assets=[_asset(cap_or_model_value=10000, non_recovery=0, costs=0)]))
    # Residual part = min(10,000, 40,000) = 10,000, all residual; at 0%
    # realisation the stressed value is 0, never negative.
    asset = apply_rv_realisation_stress(analysis, 0)["assets"][0]
    assert asset["stressed_collateral_value"] == 0
    assert asset["stressed_uncovered_risk"] == pytest.approx(80000)


def test_zero_total_exposure_makes_stressed_coverage_na():
    analysis = calculate_collateral_analysis(_payload(
        assets=[_asset(valuation=0, down_payment_pct=0.0, est_rv_pct=0.0, cap_or_model_value=0)]))
    stressed = apply_rv_realisation_stress(analysis, 50)
    assert stressed["assets"][0]["stressed_coverage_pct"] is None
    assert stressed["results"]["stressed_coverage_pct"] is None


def test_the_stress_object_names_the_assumption_it_used():
    analysis = calculate_collateral_analysis(_payload())
    stressed = apply_rv_realisation_stress(analysis, 50)
    assert stressed["rv_realisation_pct"] == 50


# ---------------------------------------------------------------------------
# The CLI: report-only output (decision 13)
# ---------------------------------------------------------------------------

def _child_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("ANTHROPIC")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_cli(*args, cwd, payload_text=None):
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "collateral_check.py"), *map(str, args)],
        cwd=cwd, env=_child_env(), capture_output=True, text=True, encoding="utf-8", timeout=120,
        input=payload_text,
    )


@pytest.fixture
def workdir(tmp_path):
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


def _seed_state(workdir, fields):
    code = (
        f"import sys; sys.path.insert(0, {str(SCRIPTS_DIR)!r}); from state_manager import write_state; "
        f"write_state('Acme', 'Loan', **{fields!r})"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=workdir, env=_child_env(), capture_output=True, text=True, timeout=60, check=True,
    )


def test_no_stressed_output_without_the_assumption(workdir):
    payload = workdir / "payload.json"
    payload.write_text(json.dumps(_payload()), encoding="utf-8")
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", payload, cwd=workdir)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "stressed" not in output


def test_a_stored_assumption_adds_the_stressed_figures_to_the_output_only(workdir):
    _seed_state(workdir, {"stress_assumptions": {"rv_realisation_pct": 50}})
    payload = workdir / "payload.json"
    payload.write_text(json.dumps(_payload()), encoding="utf-8")
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", payload, cwd=workdir)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    stressed = output["stressed"]
    assert stressed["rv_realisation_pct"] == 50
    assert stressed["assets"][0]["stressed_collateral_value"] == pytest.approx(40000)
    assert stressed["results"]["stressed_gross_uncovered_risk"] == pytest.approx(40000)

    # Report-only: nothing derived is persisted. The stored analysis has no
    # stressed figures, and the stored assumption is untouched.
    state = json.loads(next(workdir.glob("deals/Acme/Loan_*/state.json")).read_text(encoding="utf-8"))
    assert "stressed" not in state["collateral_analysis"]
    assert state["stress_assumptions"] == {"rv_realisation_pct": 50}
    assert "stressed" not in json.dumps(state["collateral_analysis"])
    assert "rv_realisation_pct" not in json.dumps(state["collateral_analysis"])


def test_an_out_of_range_stored_assumption_is_refused(workdir):
    _seed_state(workdir, {"stress_assumptions": {"rv_realisation_pct": 150}})
    payload = workdir / "payload.json"
    payload.write_text(json.dumps(_payload()), encoding="utf-8")
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", payload, cwd=workdir)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert result.stderr.count("\n") == 1


def test_a_non_finite_stored_assumption_is_refused(workdir):
    _seed_state(workdir, {"stress_assumptions": {"rv_realisation_pct": "half"}})
    payload = workdir / "payload.json"
    payload.write_text(json.dumps(_payload()), encoding="utf-8")
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", payload, cwd=workdir)
    assert result.returncode == 1
    assert result.stderr.startswith("error: ")
    assert result.stderr.count("\n") == 1
