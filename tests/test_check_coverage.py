"""scripts/check_coverage.py and the coverage floors in pyproject.toml (issue #142).

The checker is the only thing standing between a coverage drop in `policy_engine.py` and a green build, so it is
tested both ways: it must pass a compliant report and fail every kind of non-compliant one (below the overall
floor, below a critical module's floor, a critical module missing, a malformed report or config). The floors
themselves are pinned: lowering one, or dropping a module from the critical list, fails a test and so needs a
reviewed change to this file as well as to pyproject.toml.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import check_coverage

REPO_ROOT = Path(__file__).resolve().parents[1]
CRITICAL = ["policy_engine", "policy_checks", "policy_check", "spreading_builder", "spreading_check",
            "state_manager", "deal_export", "docx_builder"]
THRESHOLDS = {"overall": 85.0, "critical": 90.0, "critical_modules": ["alpha", "beta"]}


def report(overall=95.0, **modules):
    """A coverage.py-JSON-shaped report with the given per-module percentages."""
    return {"totals": {"percent_covered": overall},
            "files": {f"scripts/{name}.py": {"summary": {"percent_covered": percent}}
                      for name, percent in modules.items()}}


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------

def test_a_compliant_report_passes():
    ok, lines = check_coverage.evaluate(report(alpha=95, beta=90.0, other=10), THRESHOLDS)
    assert ok and not any(line.startswith("FAIL") for line in lines)


@pytest.mark.parametrize("overall, expected", [(84.99, False), (85.0, True), (100, True)])
def test_the_overall_floor_is_inclusive(overall, expected):
    ok, _ = check_coverage.evaluate(report(overall, alpha=100, beta=100), THRESHOLDS)
    assert ok is expected


@pytest.mark.parametrize("percent, expected", [(89.99, False), (90.0, True)])
def test_the_critical_floor_is_inclusive_and_applies_to_each_module_alone(percent, expected):
    ok, lines = check_coverage.evaluate(report(100, alpha=100, beta=percent), THRESHOLDS)
    assert ok is expected
    if not expected:
        assert any("beta coverage 89.99%" in line for line in lines)


def test_a_high_overall_figure_cannot_hide_a_weak_critical_module():
    ok, lines = check_coverage.evaluate(report(99.5, alpha=100, beta=40, helpers=100), THRESHOLDS)
    assert not ok and any("beta" in line and "below" in line for line in lines)


def test_a_critical_module_missing_from_the_report_fails_rather_than_passes():
    ok, lines = check_coverage.evaluate(report(100, alpha=100), THRESHOLDS)
    assert not ok and any("beta was not measured" in line for line in lines)


def test_modules_are_matched_by_file_stem_on_either_path_style():
    parsed = check_coverage.module_percentages({"files": {
        "scripts\\alpha.py": {"summary": {"percent_covered": 91}},
        "C:/x/scripts/beta.py": {"summary": {"percent_covered": 92}},
        "scripts/notpython.txt": {"summary": {"percent_covered": 0}}}})
    assert parsed == {"alpha": 91.0, "beta": 92.0}


@pytest.mark.parametrize("entry", [{}, {"summary": {}}, {"summary": {"percent_covered": "high"}}, None])
def test_a_file_entry_without_a_percentage_is_an_error_not_a_zero(entry):
    with pytest.raises(ValueError, match=r"no summary.percent_covered for scripts/alpha.py"):
        check_coverage.module_percentages({"files": {"scripts/alpha.py": entry}})


def test_a_report_without_totals_is_an_error_not_a_pass():
    with pytest.raises(ValueError, match="totals.percent_covered"):
        check_coverage.evaluate({"files": {}}, THRESHOLDS)


# ---------------------------------------------------------------------------
# load_thresholds()
# ---------------------------------------------------------------------------

def write_config(tmp_path, body):
    path = tmp_path / "pyproject.toml"
    path.write_text(body, encoding="utf-8")
    return path


GOOD = '[tool.opencam.coverage]\noverall = 80\ncritical = 92.5\ncritical_modules = ["a", "b"]\n'


def test_thresholds_are_read_from_the_config(tmp_path):
    assert check_coverage.load_thresholds(write_config(tmp_path, GOOD)) == {
        "overall": 80.0, "critical": 92.5, "critical_modules": ["a", "b"]}


@pytest.mark.parametrize("body, message", [
    ("[tool.other]\nx = 1\n", "no \\[tool.opencam.coverage\\] table"),
    ('[tool.opencam.coverage]\noverall = 80\ncritical = 90\n', "missing: critical_modules"),
    ('[tool.opencam.coverage]\noverall = 0\ncritical = 90\ncritical_modules = ["a"]\n', "overall must be"),
    ('[tool.opencam.coverage]\noverall = 80\ncritical = 101\ncritical_modules = ["a"]\n', "critical must be"),
    ('[tool.opencam.coverage]\noverall = true\ncritical = 90\ncritical_modules = ["a"]\n', "overall must be"),
    ('[tool.opencam.coverage]\noverall = "80"\ncritical = 90\ncritical_modules = ["a"]\n', "overall must be"),
    ('[tool.opencam.coverage]\noverall = 80\ncritical = 90\ncritical_modules = []\n', "non-empty list"),
    ('[tool.opencam.coverage]\noverall = 80\ncritical = 90\ncritical_modules = ["a", "a"]\n', "distinct"),
    ('[tool.opencam.coverage]\noverall = 80\ncritical = 90\ncritical_modules = [1]\n', "non-empty list"),
])
def test_a_malformed_config_is_rejected_with_a_message(tmp_path, body, message):
    with pytest.raises(ValueError, match=message):
        check_coverage.load_thresholds(write_config(tmp_path, body))


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def run_main(tmp_path, coverage_report, config=GOOD, *extra):
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(coverage_report), encoding="utf-8")
    return check_coverage.main([str(report_path), "--config", str(write_config(tmp_path, config)), *extra])


def test_main_exits_zero_when_the_floors_are_met_and_one_when_not(tmp_path, capsys):
    assert run_main(tmp_path, report(95, a=95, b=95)) == 0
    assert "Coverage floors met." in capsys.readouterr().out
    assert run_main(tmp_path, report(95, a=95, b=50)) == 1
    out = capsys.readouterr().out
    assert "FAIL: b coverage 50.00%" in out and "Coverage floors met." not in out


def test_report_only_prints_the_table_and_never_fails(tmp_path, capsys):
    assert run_main(tmp_path, report(50, a=10, b=10), GOOD, "--report-only") == 0
    assert "FAIL: a coverage 10.00%" in capsys.readouterr().out


@pytest.mark.parametrize("make_report", [lambda p: p.write_text("not json", encoding="utf-8"),
                                         lambda p: p.write_text("[]", encoding="utf-8"),
                                         lambda p: None])
def test_an_unreadable_or_wrong_report_exits_two(tmp_path, capsys, make_report):
    path = tmp_path / "coverage.json"
    make_report(path)
    config = write_config(tmp_path, GOOD)
    assert check_coverage.main([str(path), "--config", str(config)]) == 2
    assert capsys.readouterr().err.startswith("error:")


# ---------------------------------------------------------------------------
# The real configuration
# ---------------------------------------------------------------------------

def test_the_real_floors_are_the_approved_ones():
    """Lowering a floor, or dropping a governance module from the list, must be a deliberate, reviewed change to
    this test as well as to pyproject.toml."""
    thresholds = check_coverage.load_thresholds()
    assert thresholds["overall"] >= 85 and thresholds["critical"] >= 90
    assert set(thresholds["critical_modules"]) >= set(CRITICAL)


def test_every_critical_module_exists_in_scripts():
    for module in check_coverage.load_thresholds()["critical_modules"]:
        assert (REPO_ROOT / "scripts" / f"{module}.py").is_file(), module


def test_the_changed_lines_floor_is_configured_for_diff_cover():
    tomllib = pytest.importorskip("tomllib")
    table = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["diff_cover"]
    assert table["fail_under"] >= 90 and table["compare_branch"] == "origin/main"


def test_the_checker_runs_against_a_real_coverage_report(tmp_path):
    """End to end with the real tool: measure one small module with coverage.py and check the JSON it writes."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "alpha.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    (tmp_path / "run.py").write_text("import sys\nsys.path.insert(0, 'scripts')\nimport alpha\nalpha.f(1)\n",
                                     encoding="utf-8")
    env_args = [sys.executable, "-m", "coverage"]
    subprocess.run([*env_args, "run", "--branch", "--source=scripts", "run.py"], cwd=tmp_path, check=True,
                   capture_output=True, timeout=120)
    subprocess.run([*env_args, "json", "-o", "coverage.json"], cwd=tmp_path, check=True, capture_output=True,
                   timeout=120)
    config = write_config(tmp_path, '[tool.opencam.coverage]\noverall = 85\ncritical = 90\n'
                                    'critical_modules = ["alpha"]\n')
    assert check_coverage.main([str(tmp_path / "coverage.json"), "--config", str(config)]) == 0
