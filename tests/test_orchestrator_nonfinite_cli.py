"""Headless CLI contract for non-finite financial/stress inputs (issue #244)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
NON_FINITE_TOKENS = ("NaN", "Infinity", "-Infinity")


def _run_orchestrator(tmp_path, *args):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("ANTHROPIC")}
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "orchestrator.py"), *map(str, args)],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def _assert_controlled_error(result, *expected_fragments):
    assert result.returncode == 1
    assert result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1
    assert result.stderr.startswith("error:")
    assert "Traceback" not in result.stderr
    for fragment in expected_fragments:
        assert fragment in result.stderr


@pytest.mark.parametrize("token", NON_FINITE_TOKENS, ids=["nan", "positive-infinity", "negative-infinity"])
def test_headless_cli_rejects_non_finite_financials_before_model_or_state(tmp_path, token):
    financials = tmp_path / "financials.json"
    financials.write_text('{"FY-Current":{"revenue":' + token + ',"cost_of_sales":600}}', encoding="utf-8")

    result = _run_orchestrator(tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility",
                               "--financials", financials)

    _assert_controlled_error(result, "financial inputs", "FY-Current", "revenue")
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("token", NON_FINITE_TOKENS, ids=["nan", "positive-infinity", "negative-infinity"])
def test_headless_cli_rejects_non_finite_stress_before_model_or_state(tmp_path, token):
    financials = tmp_path / "financials.json"
    financials.write_text('{"FY+1":{"revenue":1000,"interest_paid":10}}', encoding="utf-8")
    stress = tmp_path / "stress.json"
    stress.write_text('{"revenue_haircut_pct":' + token + '}', encoding="utf-8")

    result = _run_orchestrator(tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility",
                               "--financials", financials, "--stress-assumptions", stress)

    _assert_controlled_error(result, "stress assumptions", "revenue_haircut_pct")
    assert not (tmp_path / "deals").exists()


COLLATERAL_TOKENS = (*NON_FINITE_TOKENS, "1e999")

COLLATERAL_MONETARY_FIELDS = ("exposure", "cap_value", "non_recovery", "costs", "collateral_value")


@pytest.mark.parametrize("token", COLLATERAL_TOKENS, ids=["nan", "positive-infinity", "negative-infinity", "overflow-1e999"])
def test_headless_cli_rejects_non_finite_collateral_before_model_or_state(tmp_path, token):
    """Decision D4 (#244): collateral figures feed the cover ratio, so they get the same guard as financials."""
    collateral = tmp_path / "collateral.json"
    collateral.write_text('[{"asset_class":"HGV","exposure":' + token + ',"collateral_value":80}]', encoding="utf-8")

    result = _run_orchestrator(tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility",
                               "--collateral", collateral)

    _assert_controlled_error(result, "collateral", "exposure")
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("field", COLLATERAL_MONETARY_FIELDS)
def test_headless_cli_rejects_negative_collateral_before_model_or_state(tmp_path, field):
    """Decision 5 on #266 (issue #378): a negative on any monetary collateral field is
    refused at input with one `error:` line, exit 1, nothing written."""
    asset = {"asset_class": "HGV", "exposure": 100, "cap_value": 0, "non_recovery": 0, "costs": 0,
             "collateral_value": 80, "number_of_units": 2, "perfection_status": "Perfected"}
    asset[field] = -1
    collateral = tmp_path / "collateral.json"
    collateral.write_text(json.dumps([asset]), encoding="utf-8")

    result = _run_orchestrator(tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility",
                               "--collateral", collateral)

    _assert_controlled_error(result, "collateral", field, "-1")
    assert not (tmp_path / "deals").exists()
