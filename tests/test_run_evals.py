"""Tests for scripts/run_evals.py (issue #151, PR 1): the CLI makes zero model
calls, refuses an unaffordable plan, writes only to a git-ignored directory, and
is not wired into the pipeline or CI. Zero model calls.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import run_evals

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def test_validate_succeeds_on_the_shipped_dataset(capsys):
    assert run_evals.main(["--validate"]) == 0
    assert "15 cases, all valid" in capsys.readouterr().out


def test_list_prints_every_case(capsys):
    assert run_evals.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "inj-source-block-obfuscated" in out and "chk-clean-control" in out


def test_dry_run_plans_then_self_checks_and_writes_results(tmp_path, capsys):
    code = run_evals.main(["--dry-run", "--out", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "15 cases x 5 repeats = 75 live model calls (cap 80" in out
    assert "makes 0 calls" in out and "15 / 15 cases sound" in out
    run_dir = next(tmp_path.iterdir())
    record = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert record["mode"] == "dry-run" and record["usage"]["calls"] == 0
    assert record["planned_live_calls"] == 75 and record["call_cap"] == 80
    assert len(record["cases"]) == 15
    assert all(c["self_check"]["ok"] for c in record["cases"])
    assert (run_dir / "review_pack.md").is_file()


def test_the_plan_is_printed_before_anything_is_written(tmp_path, capsys, monkeypatch):
    order = []
    monkeypatch.setattr(run_evals, "write_results", lambda *a, **k: order.append("write") or str(tmp_path))
    real_print = print
    monkeypatch.setattr("builtins.print", lambda *a, **k: (order.append("print:" + str(a[0])[:5]), real_print(*a, **k)))
    run_evals.main(["--dry-run"])
    assert order.index("print:Plan:") < order.index("write")


def test_a_plan_over_the_cap_is_refused_before_any_work(tmp_path, capsys):
    code = run_evals.main(["--dry-run", "--repeats", "20", "--out", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == 2 and "Refused" in err and "300 model calls" in err
    assert list(tmp_path.iterdir()) == []  # nothing written


def test_a_cap_above_the_absolute_ceiling_is_refused(tmp_path, capsys):
    code = run_evals.main(["--dry-run", "--max-calls", "251", "--out", str(tmp_path)])
    assert code == 2 and "absolute ceiling" in capsys.readouterr().err


def test_running_with_no_mode_flag_does_nothing_and_exits_non_zero(capsys):
    assert run_evals.main([]) == 2
    assert "live evaluation is not implemented" in capsys.readouterr().err


def test_an_invalid_dataset_fails_loudly(tmp_path, monkeypatch, capsys):
    cases = tmp_path / "v1" / "cases"
    cases.mkdir(parents=True)
    (tmp_path / "v1" / "dataset.json").write_text(json.dumps({"version": "v1"}), encoding="utf-8")
    (cases / "bad.json").write_text(json.dumps({"id": "bad"}), encoding="utf-8")
    monkeypatch.setattr("eval_cases.DATASET_ROOT", str(tmp_path))

    assert run_evals.main(["--validate"]) == 1
    assert "INVALID" in capsys.readouterr().err


def test_dry_run_never_touches_the_anthropic_client(tmp_path, monkeypatch):
    import anthropic

    def boom(*args, **kwargs):
        raise AssertionError("the dry run must not construct an Anthropic client")

    monkeypatch.setattr(anthropic, "Anthropic", boom)
    assert run_evals.main(["--dry-run", "--out", str(tmp_path)]) == 0


def test_the_harness_modules_import_without_anthropic():
    code = (f"import sys; sys.path.insert(0, {str(SCRIPTS_DIR)!r}); "
            "import eval_budget, eval_cases, eval_oracles, run_evals; "
            "assert 'anthropic' not in sys.modules, 'harness core imported anthropic'")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_harness_is_not_wired_into_the_pipeline_or_ci():
    for name in ("scripts/orchestrator.py", "tests/conftest.py", ".github/workflows/ci.yml"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "run_evals" not in text and "eval_cases" not in text and "eval_oracles" not in text, name


def test_the_cli_runs_as_a_real_script_and_survives_a_cp1252_pipe(tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONUTF8", None)
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "run_evals.py"), "--dry-run", "--out", str(tmp_path)],
        capture_output=True, env=env, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "15 / 15 cases sound" in result.stdout.decode("utf-8")


@pytest.mark.parametrize("flag", ["--validate", "--list"])
def test_read_only_modes_write_nothing(flag, tmp_path):
    before = sorted(p.name for p in REPO_ROOT.glob("evals/*"))
    run_evals.main([flag])
    assert sorted(p.name for p in REPO_ROOT.glob("evals/*")) == before
