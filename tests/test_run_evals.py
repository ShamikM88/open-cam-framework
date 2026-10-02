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
    assert "Nothing to do" in capsys.readouterr().err


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

# ---------------------------------------------------------------------------
# Review fixes (independent review of the PR for #151).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flags", [["--max-calls", "0"], ["--max-calls", "-5"], ["--repeats", "0"],
                                   ["--repeats", "-1"]])
def test_non_positive_limits_are_rejected_by_the_parser_without_a_traceback(flags, tmp_path, capsys):
    with pytest.raises(SystemExit) as raised:
        run_evals.main(["--dry-run", "--out", str(tmp_path), *flags])
    assert raised.value.code == 2
    assert "must be at least 1" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_an_unsound_case_makes_the_dry_run_exit_non_zero(tmp_path, monkeypatch, capsys):
    real = run_evals.self_check

    def broken(case):
        outcome = real(case)
        outcome["good_all_pass"] = False
        outcome["ok"] = False
        return outcome

    monkeypatch.setattr(run_evals, "self_check", broken)
    assert run_evals.main(["--dry-run", "--out", str(tmp_path)]) == 1
    assert "NOT SOUND" in capsys.readouterr().out


def test_a_traversing_dataset_version_is_refused_cleanly(capsys):
    assert run_evals.main(["--validate", "--dataset", "../x"]) == 1
    assert "invalid dataset version" in capsys.readouterr().err


def test_the_cli_works_from_any_working_directory(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPTS_DIR / "run_evals.py"), "--validate"],
                            capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "15 cases, all valid" in result.stdout


@pytest.mark.parametrize("flag", ["--validate", "--list"])
def test_read_only_modes_leave_the_whole_evals_tree_untouched(flag):
    def tree():
        return sorted(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "evals").rglob("*"))

    before = tree()
    run_evals.main([flag])
    assert tree() == before

# ---------------------------------------------------------------------------
# PR 2: the --live, --export-baseline and --compare commands. Every client is a fake:
# these tests make zero model calls and need no API key.
# ---------------------------------------------------------------------------

def _fake_client_factory(monkeypatch, respond):
    from eval_fakes import FakeClient
    client = FakeClient(respond)
    monkeypatch.setattr(run_evals, "make_client", lambda: client)
    return client


def _forbid_client(monkeypatch):
    def boom():
        raise AssertionError("the client must not be built (and no key touched) on this path")
    monkeypatch.setattr(run_evals, "make_client", boom)


LIVE = ["--live", "--cases", "fab-no-financials", "--repeats", "1", "--yes"]


def test_live_refuses_a_plan_over_the_cap_before_touching_the_key(monkeypatch, tmp_path, capsys):
    _forbid_client(monkeypatch)
    code = run_evals.main(["--live", "--yes", "--repeats", "20", "--out", str(tmp_path)])
    assert code == 2 and "Refused" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("answer", ["no", "", "y", "YES please"])
def test_live_needs_an_explicit_yes(monkeypatch, tmp_path, capsys, answer):
    _forbid_client(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *_: answer)
    code = run_evals.main(["--live", "--cases", "fab-no-financials", "--repeats", "1", "--out", str(tmp_path)])
    assert code == 2 and "Not confirmed" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_live_with_no_terminal_to_confirm_runs_nothing(monkeypatch, tmp_path, capsys):
    _forbid_client(monkeypatch)

    def eof(*_):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert run_evals.main(["--live", "--cases", "fab-no-financials", "--repeats", "1", "--out", str(tmp_path)]) == 2


def test_a_missing_api_key_is_a_clean_error_and_nothing_is_written(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = run_evals.main([*LIVE, "--out", str(tmp_path)])
    assert code == 2 and "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_unknown_case_ids_are_refused_and_duplicates_collapse(monkeypatch, tmp_path, capsys):
    _forbid_client(monkeypatch)
    assert run_evals.main(["--live", "--yes", "--cases", "no-such-case", "--out", str(tmp_path)]) == 1
    assert "unknown case id" in capsys.readouterr().err
    capsys.readouterr()
    client = _fake_client_factory(monkeypatch, lambda kw: "x")
    run_evals.main(["--live", "--yes", "--cases", "fab-no-financials,fab-no-financials", "--repeats", "2",
                    "--out", str(tmp_path)])
    assert "1 cases x 2 repeats = 2 LIVE" in capsys.readouterr().out and len(client.calls) == 2


def test_a_live_run_writes_results_review_pack_and_incremental_runs(monkeypatch, tmp_path, capsys):
    from eval_fakes import dataset_case, good_maker_text
    case = dataset_case("fab-no-financials")
    client = _fake_client_factory(monkeypatch, lambda kw: good_maker_text(case))

    assert run_evals.main([*LIVE, "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out

    (run_dir,) = list(tmp_path.iterdir())
    assert sorted(p.name for p in run_dir.iterdir()) == ["results.json", "review_pack.md", "runs.jsonl"]
    record = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert record["mode"] == "live" and record["usage"]["calls"] == 1 and len(client.calls) == 1
    assert record["cases"][0]["runs"][0]["passed"] and not record["aborted"]
    assert "Observed pass rates" in out and "not a proof of safety" in out
    assert len((run_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_keep_work_keeps_the_isolated_directories_inside_the_results_dir(monkeypatch, tmp_path):
    from eval_fakes import dataset_case, good_maker_text
    case = dataset_case("fab-no-financials")
    _fake_client_factory(monkeypatch, lambda kw: good_maker_text(case))
    run_evals.main([*LIVE, "--keep-work", "--out", str(tmp_path)])
    (run_dir,) = list(tmp_path.iterdir())
    kept = list((run_dir / "work").glob("fab-no-financials-*"))
    assert len(kept) == 1, "--keep-work should keep the per-run isolated directory"
    assert (kept[0] / "agents").is_dir() and not (kept[0] / "templates" / "local").exists()


def test_an_aborted_live_run_exits_non_zero_and_says_why(monkeypatch, tmp_path, capsys):
    def respond(kw):
        raise RuntimeError("API down")

    _fake_client_factory(monkeypatch, respond)
    code = run_evals.main(["--live", "--cases", "fab-no-financials", "--repeats", "5", "--yes", "--out", str(tmp_path)])
    assert code == 1 and "ABORTED early (consecutive errors): 3 consecutive errored runs" in capsys.readouterr().err


def test_live_flags_are_mutually_exclusive_with_the_other_modes():
    with pytest.raises(SystemExit) as raised:
        run_evals.main(["--live", "--dry-run"])
    assert raised.value.code == 2


def _live_results(monkeypatch, tmp_path, name="a"):
    from eval_fakes import dataset_case, good_maker_text
    case = dataset_case("fab-no-financials")
    _fake_client_factory(monkeypatch, lambda kw: good_maker_text(case))
    out = tmp_path / name
    assert run_evals.main([*LIVE, "--out", str(out)]) == 0
    (run_dir,) = list(out.iterdir())
    return run_dir / "results.json"


def test_export_baseline_writes_beside_the_results_and_refuses_to_overwrite(monkeypatch, tmp_path, capsys):
    results = _live_results(monkeypatch, tmp_path)
    capsys.readouterr()
    assert run_evals.main(["--export-baseline", str(results)]) == 0
    assert "copy it by hand to evals/baselines/" in capsys.readouterr().out
    summary = json.loads((results.parent / "baseline_summary.json").read_text(encoding="utf-8"))
    assert summary["kind"] == "evaluation-baseline-summary" and summary["per_case"][0]["total"] == 1
    assert run_evals.main(["--export-baseline", str(results)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_export_baseline_refuses_a_dry_run_record(tmp_path, capsys):
    assert run_evals.main(["--dry-run", "--out", str(tmp_path)]) == 0
    (run_dir,) = list(tmp_path.iterdir())
    capsys.readouterr()
    assert run_evals.main(["--export-baseline", str(run_dir / "results.json")]) == 1
    assert "only a live run" in capsys.readouterr().err


def test_export_baseline_reports_a_missing_file_cleanly(tmp_path, capsys):
    assert run_evals.main(["--export-baseline", str(tmp_path / "nope.json")]) == 1
    assert "Error:" in capsys.readouterr().err


def test_compare_prints_a_like_for_like_comparison(monkeypatch, tmp_path, capsys):
    first = _live_results(monkeypatch, tmp_path, "first")
    second = _live_results(monkeypatch, tmp_path, "second")
    run_evals.main(["--export-baseline", str(first)])
    capsys.readouterr()

    assert run_evals.main(["--compare", str(first.parent / "baseline_summary.json"), str(second)]) == 0
    out = capsys.readouterr().out
    assert "fab-no-financials" in out and "gates nothing" in out
    # Same model/prompts/dataset, so this is like-for-like (case content hash unchanged).
    assert "Like-for-like" in out


def test_compare_refuses_a_non_baseline_file(monkeypatch, tmp_path, capsys):
    results = _live_results(monkeypatch, tmp_path)
    capsys.readouterr()
    assert run_evals.main(["--compare", str(results), str(results)]) == 1
    assert "not an evaluation baseline" in capsys.readouterr().err


def test_a_live_run_into_the_default_results_dir_changes_no_tracked_file(monkeypatch):
    import shutil
    import subprocess
    from eval_fakes import dataset_case, good_maker_text

    def status():
        return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=REPO_ROOT,
                              capture_output=True, text=True).stdout

    if shutil.which("git") is None:
        pytest.skip("git not available")
    case = dataset_case("fab-no-financials")
    _fake_client_factory(monkeypatch, lambda kw: good_maker_text(case))
    root = REPO_ROOT / "evals" / "results"
    existed = root.is_dir()
    before_runs = set(p.name for p in root.iterdir()) if existed else set()
    before = status()
    try:
        assert run_evals.main(LIVE) == 0
        assert status() == before  # git sees nothing new or changed
    finally:
        if root.is_dir():
            for entry in root.iterdir():
                if entry.name not in before_runs:
                    shutil.rmtree(entry)
            if not existed and not any(root.iterdir()):
                root.rmdir()

# ---------------------------------------------------------------------------
# Third independent review: --cases, relative --out, Ctrl-C, aborted packs, the export guard.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cases", ["", ",", " , ,"])
def test_cases_that_names_nothing_is_refused_not_read_as_everything(monkeypatch, tmp_path, capsys, cases):
    _forbid_client(monkeypatch)
    assert run_evals.main(["--live", "--yes", "--cases", cases, "--out", str(tmp_path)]) == 1
    assert "names no case ids" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_a_relative_out_directory_works_end_to_end(monkeypatch, tmp_path):
    from eval_fakes import dataset_case, good_maker_text
    case = dataset_case("fab-no-financials")
    _fake_client_factory(monkeypatch, lambda kw: good_maker_text(case))
    monkeypatch.chdir(tmp_path)
    assert run_evals.main([*LIVE, "--out", "relout"]) == 0
    (run_dir,) = list((tmp_path / "relout").iterdir())
    assert (run_dir / "results.json").is_file() and not (run_dir / "work").exists()


def test_ctrl_c_still_writes_the_record_and_exits_non_zero(monkeypatch, tmp_path, capsys):
    from eval_fakes import dataset_case, good_maker_text
    case = dataset_case("fab-no-financials")
    calls = {"n": 0}

    def respond(kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return good_maker_text(case)

    _fake_client_factory(monkeypatch, respond)
    code = run_evals.main(["--live", "--cases", "fab-no-financials", "--repeats", "4", "--yes", "--out", str(tmp_path)])
    assert code == 1 and "ABORTED early (interrupted)" in capsys.readouterr().err
    (run_dir,) = list(tmp_path.iterdir())
    record = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert record["aborted"] and record["abort_category"] == "interrupted" and len(record["cases"][0]["runs"]) == 1


def test_an_aborted_pack_says_so_and_lists_the_unreached_cases(monkeypatch, tmp_path):
    from eval_fakes import dataset_case
    _fake_client_factory(monkeypatch, lambda kw: (_ for _ in ()).throw(RuntimeError("down")))
    run_evals.main(["--live", "--cases", "fab-no-financials,fab-single-period", "--repeats", "5", "--yes",
                    "--out", str(tmp_path)])
    (run_dir,) = list(tmp_path.iterdir())
    pack = (run_dir / "review_pack.md").read_text(encoding="utf-8")
    assert "ABORTED EARLY (consecutive errors)" in pack
    assert "- `fab-single-period`: not reached" in pack
    assert "(only 3 of 5 planned runs happened)" in pack  # a pass rate is never shown without its denominator
    assert dataset_case("fab-single-period")["id"] in pack


def test_a_truncated_live_output_is_flagged_in_the_pack_and_the_run_details_are_shown(monkeypatch, tmp_path):
    from eval_fakes import FakeClient, dataset_case

    class Truncating(FakeClient):
        def _create(self, **kwargs):
            response = super()._create(**kwargs)
            response.stop_reason = "max_tokens"
            return response

    case = dataset_case("fab-no-financials")
    client = Truncating(lambda kw: "a draft cut off before its structured block", input_tokens=900, output_tokens=4000)
    monkeypatch.setattr(run_evals, "make_client", lambda: client)
    run_evals.main([*LIVE, "--out", str(tmp_path)])
    (run_dir,) = list(tmp_path.iterdir())
    pack = (run_dir / "review_pack.md").read_text(encoding="utf-8")
    assert "OUTPUT TRUNCATED at max_tokens" in pack and "900 in / 4000 out, max_tokens" in pack
    assert case["id"] in pack


def test_export_baseline_refuses_a_results_file_in_a_tracked_directory(monkeypatch, tmp_path, capsys):
    import shutil
    from eval_fakes import REPO_ROOT as repo_root
    results = _live_results(monkeypatch, tmp_path)
    probe_dir = repo_root / "tests" / "_baseline_guard_probe"
    try:
        probe_dir.mkdir()
        shutil.copyfile(results, probe_dir / "results.json")
        capsys.readouterr()
        code = run_evals.main(["--export-baseline", str(probe_dir / "results.json")])
        assert code == 1 and "not git-ignored" in capsys.readouterr().err
        assert not (probe_dir / "baseline_summary.json").exists()
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def test_a_malformed_results_file_is_a_clean_error_on_the_command_line(tmp_path, capsys):
    bad = tmp_path / "results.json"
    bad.write_text(json.dumps({"mode": "live", "cases": [{"runs": [{}]}]}), encoding="utf-8")
    assert run_evals.main(["--export-baseline", str(bad)]) == 1
    assert "Error:" in capsys.readouterr().err and not (tmp_path / "baseline_summary.json").exists()


def test_the_baseline_file_is_opened_exclusively_even_if_it_appears_after_the_existence_check(
        monkeypatch, tmp_path, capsys):
    results = _live_results(monkeypatch, tmp_path)
    target = results.parent / "baseline_summary.json"
    target.write_text("{}", encoding="utf-8")
    real_exists = os.path.exists
    monkeypatch.setattr(run_evals.os.path, "exists",
                        lambda p: False if os.path.abspath(p) == str(target) else real_exists(p))
    code = run_evals.main(["--export-baseline", str(results)])
    assert code == 1 and "File exists" in capsys.readouterr().err  # a clean error, not a traceback
    assert target.read_text(encoding="utf-8") == "{}"
