"""Subprocess contract for predictable no-work and JSON-input errors in spreading_check."""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
HOME_VARIABLES = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME")


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


def _assert_one_error(result):
    assert result.returncode == 1
    assert result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1
    assert result.stderr.startswith("error:")
    assert "Traceback" not in result.stderr


def test_nothing_to_compute_on_a_new_deal_is_one_controlled_error(tmp_path):
    result = _run_spreading_check(tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility")

    _assert_one_error(result)
    assert "Nothing to compute: no --financials given" in result.stderr
    assert not (tmp_path / "deals").exists()


def test_nothing_to_compute_for_an_existing_deal_is_one_controlled_error_and_leaves_state_unchanged(tmp_path):
    folder = tmp_path / "deals" / "Synthetic Co" / "Synthetic Facility_2026-01-01"
    folder.mkdir(parents=True)
    state_path = folder / "state.json"
    state_path.write_text(json.dumps({
        "company": "Synthetic Co",
        "proposal": "Synthetic Facility",
        "date": "2026-01-01",
        "multi_period_financials": {"FY-Current": {"revenue": 1000, "cost_of_sales": 600}},
    }), encoding="utf-8")
    before = state_path.read_bytes()

    result = _run_spreading_check(tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility")

    _assert_one_error(result)
    assert "Nothing to compute: --financials produced no new periods" in result.stderr
    assert state_path.read_bytes() == before


def test_malformed_financials_json_is_one_controlled_error(tmp_path):
    source = tmp_path / "bad-financials.json"
    source.write_text('{"FY-Current":{', encoding="utf-8")

    result = _run_spreading_check(
        tmp_path, "--company", "Synthetic Co", "--proposal", "Synthetic Facility", "--financials", source,
    )

    _assert_one_error(result)
    assert "invalid JSON in" in result.stderr and "bad-financials.json" in result.stderr
    assert not (tmp_path / "deals").exists()
