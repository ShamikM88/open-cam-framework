"""CLI contract for non-finite financial/stress inputs (issue #244)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
HOME_VARIABLES = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
NON_FINITE_TOKENS = ("NaN", "Infinity", "-Infinity")


def _run_spreading_check(tmp_path, *args):
    home = tmp_path / "isolated-home"
    home.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("ANTHROPIC")}
    env.update(dict.fromkeys(HOME_VARIABLES, str(home)))
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "spreading_check.py"), *map(str, args)],
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
def test_non_finite_financial_json_is_rejected_before_creating_deal_state(tmp_path, token):
    financials = tmp_path / "financials.json"
    financials.write_text(
        '{"FY-Current":{"revenue":' + token + ',"cost_of_sales":600,"long_term_debt":200,"interest_paid":10}}',
        encoding="utf-8",
    )

    result = _run_spreading_check(
        tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility", "--financials", financials,
    )

    _assert_controlled_error(result, "financial inputs", "FY-Current", "revenue")
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("token", NON_FINITE_TOKENS, ids=["nan", "positive-infinity", "negative-infinity"])
def test_non_finite_stress_json_is_rejected_before_creating_deal_state(tmp_path, token):
    financials = tmp_path / "financials.json"
    financials.write_text('{"FY+1":{"revenue":1000,"interest_paid":10}}', encoding="utf-8")
    stress = tmp_path / "stress.json"
    stress.write_text('{"revenue_haircut_pct":' + token + '}', encoding="utf-8")

    result = _run_spreading_check(
        tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility",
        "--financials", financials, "--stress-assumptions", stress,
    )

    _assert_controlled_error(result, "stress assumptions", "revenue_haircut_pct")
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("token", NON_FINITE_TOKENS, ids=["nan", "positive-infinity", "negative-infinity"])
def test_non_finite_financial_update_leaves_existing_state_byte_identical(tmp_path, token):
    deal_dir = tmp_path / "deals" / "Synthetic Co" / "Synthetic Facility_2026-01-01"
    deal_dir.mkdir(parents=True)
    state_path = deal_dir / "state.json"
    state_path.write_text(json.dumps({
        "company": "Synthetic Co",
        "proposal": "Synthetic Facility",
        "date": "2026-01-01",
        "multi_period_financials": {"FY-Current": {"revenue": 1000, "cost_of_sales": 600}},
        "financials": {},
        "ratios": {},
    }), encoding="utf-8")
    before = state_path.read_bytes()
    financials = tmp_path / "financials.json"
    financials.write_text('{"FY-Current":{"revenue":' + token + '}}', encoding="utf-8")

    result = _run_spreading_check(
        tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility", "--financials", financials,
    )

    _assert_controlled_error(result, "financial inputs", "FY-Current", "revenue")
    assert state_path.read_bytes() == before
