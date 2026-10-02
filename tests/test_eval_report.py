"""Tests for scripts/eval_report.py (issue #151): result records, the three
kept-apart result types, the review pack wording, and the rule that results
are only ever written under a git-ignored directory. Zero model calls.
"""
import json
import os
import shutil
import subprocess

import pytest

import eval_report
from eval_report import (
    DISCLAIMER,
    REPO_ROOT,
    ResultsPathError,
    assert_results_dir_is_ignored,
    build_record,
    pass_rate,
    render_review_pack,
    scripted_run,
    summarize,
    write_results,
)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def live_run(label, passed):
    return {"label": label, "kind": "live", "passed": passed, "assertions": [
        {"oracle": "canary_absent", "passed": passed, "reason": "canary absent" if passed else "canary appeared"}]}


def case(case_id, category, runs, **extra):
    return {"id": case_id, "category": category, "mode": "maker", "description": "d",
            "human_review": ["Was it sensible?"], "runs": runs, **extra}


DATASET = {"version": "v1", "cases": [{}, {}]}


def test_pass_rate_counts_only_live_runs():
    runs = [live_run("r1", True), live_run("r2", False), live_run("r3", True),
            scripted_run("good", [{"oracle": "x", "passed": True, "reason": ""}])]
    assert pass_rate(runs) == {"passed": 2, "total": 3}
    assert pass_rate([scripted_run("good", [])]) == {"passed": 0, "total": 0}


def test_summary_aggregates_by_category_and_counts_self_checks():
    cases = [
        case("a", "injection", [live_run("1", True), live_run("2", True)]),
        case("b", "injection", [live_run("1", False)]),
        case("c", "fabrication", [scripted_run("good", [])], self_check={"ok": True}),
    ]
    summary = summarize(cases)
    assert summary["observed_pass_rate_by_category"]["injection"] == {"passed": 2, "total": 3}
    assert summary["observed_pass_rate_by_category"]["fabrication"] == {"passed": 0, "total": 0}
    assert summary["scripted_self_checks"] == {"cases": 1, "ok": 1}


def test_record_carries_the_provenance_fields_the_design_requires():
    record = build_record("dry-run", DATASET, [], "20260101T000000Z",
                          models={"maker_model": "m1", "checker_model": "m2"},
                          hashes={"underwriter_prompt_hash": "abc"}, planned_live_calls=75, call_cap=80)
    assert record["mode"] == "dry-run" and record["run_id"] == "20260101T000000Z"
    assert record["dataset"] == {"version": "v1", "case_count": 2}
    assert record["models"] == {"maker_model": "m1", "checker_model": "m2"}
    assert record["prompt_hashes"] == {"underwriter_prompt_hash": "abc"}
    assert (record["planned_live_calls"], record["call_cap"]) == (75, 80)
    assert record["usage"] == {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    assert record["harness_version"] and record["timestamp"] and record["disclaimer"] == DISCLAIMER


def test_prompt_hashes_match_the_provenance_helper():
    from orchestrator import _content_hash
    from textio import read_text
    hashes = eval_report.prompt_hashes()
    assert hashes["underwriter_prompt_hash"] == _content_hash(
        read_text(os.path.join(REPO_ROOT, "agents", "underwriter_agent.md")))
    assert set(hashes) == {"underwriter_prompt_hash", "risk_reviewer_prompt_hash"}


def _record_with_live_runs():
    cases = [case("inj-1", "injection", [live_run("repeat-1", True), live_run("repeat-2", False)],
                  description="Injection case")]
    return build_record("live", {"version": "v1", "cases": cases}, cases, "20260101T000000Z",
                        models={"maker_model": "m1", "checker_model": "m2"}, call_cap=80,
                        usage={"calls": 2, "input_tokens": 5, "output_tokens": 9})


def test_review_pack_keeps_the_three_result_types_in_separate_sections():
    pack = render_review_pack(_record_with_live_runs())
    one, two, three = (pack.index(h) for h in (
        "## 1. Deterministic oracle results", "## 2. Observed pass rates (live runs)",
        "## 3. Human-review observations"))
    assert one < two < three
    assert "| `inj-1` | repeat-2 (live) | FAIL |" in pack
    assert "injection | 1 / 2" in pack and "`inj-1`: 1 / 2" in pack
    assert "- [ ] Was it sensible?" in pack and "No pass/fail" in pack


def test_review_pack_never_claims_a_result_is_proven_or_safe():
    pack = render_review_pack(_record_with_live_runs())
    assert DISCLAIMER in pack
    lowered = pack.lower()
    for phrase in ("proven safe", "is safe", "guaranteed", "100% safe", "passes safety"):
        assert phrase not in lowered
    assert "observed" in lowered


def test_a_dry_run_pack_says_no_model_was_called_and_reports_no_pass_rates():
    cases = [case("c", "fabrication", [scripted_run("good", [{"oracle": "x", "passed": True, "reason": ""}]),
                                       scripted_run("bad", [{"oracle": "x", "passed": False, "reason": "caught"}])],
                  self_check={"ok": True})]
    pack = render_review_pack(build_record("dry-run", {"version": "v1", "cases": cases}, cases, "r1"))
    assert "no model was called" in pack
    assert "No live runs in this record" in pack
    assert "Scripted self-checks" in pack


def test_output_excerpts_appear_beside_the_questions_in_the_human_review_section():
    runs = [scripted_run("good", [], output_excerpt="EXCERPT-TEXT")]
    pack = render_review_pack(build_record("dry-run", {"version": "v1", "cases": [1]}, [case("c", "fabrication", runs)], "r1"))
    assert pack.index("## 3.") < pack.index("EXCERPT-TEXT")


def test_write_results_writes_both_files_outside_the_repo(tmp_path):
    record = _record_with_live_runs()
    out_dir = write_results(record, out_root=str(tmp_path))
    assert os.path.basename(out_dir) == record["run_id"]
    saved = json.loads(open(os.path.join(out_dir, "results.json"), encoding="utf-8").read())
    assert saved["run_id"] == record["run_id"] and saved["summary"] == record["summary"]
    assert "Evaluation review pack" in open(os.path.join(out_dir, "review_pack.md"), encoding="utf-8").read()


@needs_git
def test_results_are_refused_in_a_tracked_location_inside_the_repo():
    with pytest.raises(ResultsPathError, match="not git-ignored"):
        assert_results_dir_is_ignored(os.path.join(REPO_ROOT, "scripts", "results"))
    with pytest.raises(ResultsPathError):
        write_results(_record_with_live_runs(), out_root=os.path.join(REPO_ROOT, "tests", "out"))
    assert not os.path.exists(os.path.join(REPO_ROOT, "tests", "out"))


@needs_git
def test_the_default_results_directory_is_git_ignored():
    default = os.path.join(REPO_ROOT, "evals", "results")
    assert_results_dir_is_ignored(default)  # must not raise
    probe = subprocess.run(["git", "check-ignore", "-q", os.path.join(default, "x", "results.json")],
                           cwd=REPO_ROOT)
    assert probe.returncode == 0


@needs_git
def test_writing_results_never_changes_a_tracked_file(tmp_path):
    def status():
        return subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=REPO_ROOT,
                              capture_output=True, text=True).stdout
    before = status()
    write_results(_record_with_live_runs(), out_root=str(tmp_path))
    assert status() == before


@needs_git
def test_ignore_check_works_before_the_results_directory_exists(tmp_path):
    """Regression: `git check-ignore evals/results` does not match the rule
    `evals/results/` while that directory is missing, which made the first real
    run refuse to write anywhere. A tmp repo proves the behaviour in isolation."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("evals/results/\n", encoding="utf-8")
    assert not (tmp_path / "evals" / "results").exists()

    assert_results_dir_is_ignored(str(tmp_path / "evals" / "results"), repo_root=str(tmp_path))
    with pytest.raises(ResultsPathError):
        assert_results_dir_is_ignored(str(tmp_path / "evals" / "other"), repo_root=str(tmp_path))
    out_dir = write_results(_record_with_live_runs(), out_root=str(tmp_path / "evals" / "results"),
                            repo_root=str(tmp_path))
    assert os.path.isfile(os.path.join(out_dir, "results.json"))
