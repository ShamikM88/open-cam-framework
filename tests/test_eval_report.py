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


DATASET = {"version": "v1", "cases": [{"id": "a"}, {"id": "b"}]}


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
    assert record["dataset"]["version"] == "v1" and record["dataset"]["case_count"] == 2
    assert len(record["dataset"]["content_hash"]) == 16
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
    pack = render_review_pack(build_record("dry-run", {"version": "v1", "cases": [{"id": "c"}]}, [case("c", "fabrication", runs)], "r1"))
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


# ---------------------------------------------------------------------------
# Review fixes (independent review of the PR for #151).
# ---------------------------------------------------------------------------

def test_an_errored_live_run_is_never_a_pass_and_stays_in_the_denominator():
    ok = eval_report.live_run("r1", [{"oracle": "x", "passed": True, "scored": True, "reason": ""}])
    # An errored run is never a pass, even if the assertions it managed to record all passed.
    errored = eval_report.live_run("r2", [{"oracle": "x", "passed": True, "scored": True, "reason": ""}],
                                   status="error", error="APIConnectionError")
    unscored_only = eval_report.live_run("r3", [{"oracle": "x", "passed": True, "scored": False, "reason": ""}])
    assert ok["passed"] and not errored["passed"] and not unscored_only["passed"]
    assert pass_rate([ok, errored, unscored_only]) == {"passed": 1, "total": 3}


def test_a_scripted_run_with_no_scored_assertion_is_not_a_pass():
    assert scripted_run("good", [])["passed"] is False


def test_scripted_rows_show_the_outcome_they_are_meant_to_have():
    good = scripted_run("good", [{"oracle": "x", "passed": True, "scored": True, "reason": ""}], expected_pass=True)
    caught = scripted_run("bad", [{"oracle": "x", "passed": False, "scored": True, "reason": "boom"}],
                          expected_pass=False)
    missed = scripted_run("bad", [{"oracle": "x", "passed": True, "scored": True, "reason": ""}],
                          expected_pass=False)
    cases = [case("c", "fabrication", [good, caught, missed], self_check={"ok": False})]
    pack = render_review_pack(build_record("dry-run", {"version": "v1", "cases": [{"id": "c"}]}, cases, "r1"))
    assert "| good (scripted) | pass |" in pack
    assert "| bad (scripted) | caught (expected) |" in pack
    assert "| bad (scripted) | UNEXPECTED pass |" in pack


def test_an_excerpt_containing_a_json_fence_cannot_break_the_pack_fence():
    excerpt = "# Memo\n\n```json\n{\"a\": 1}\n```\n\nTrailing prose with # headings"
    cases = [case("c", "fabrication", [scripted_run("good", [], output_excerpt=excerpt)])]
    pack = render_review_pack(build_record("dry-run", {"version": "v1", "cases": [{"id": "c"}]}, cases, "r1"))
    lines = pack.splitlines()
    opening = next(i for i, line in enumerate(lines) if line.startswith("````text"))
    closing = next(i for i in range(opening + 1, len(lines)) if lines[i] == "````")
    inner = lines[opening + 1:closing]
    assert "```json" in inner and "Trailing prose with # headings" in inner[-1]


def test_an_unscored_observation_appears_in_the_human_review_section_not_as_a_failure():
    run = eval_report.live_run("repeat-1", [
        {"oracle": "verdict_is", "passed": True, "scored": True, "reason": "ok"},
        {"oracle": "canary_absent", "passed": False, "scored": False, "reason": "token appeared: ...quoted..."}])
    cases = [case("chk", "injection", [run], mode="checker")]
    pack = render_review_pack(build_record("live", {"version": "v1", "cases": [{"id": "chk"}]}, cases, "r1"))
    section_three = pack[pack.index("## 3."):]
    assert "unscored_ `canary_absent`: `token appeared" in section_three
    assert "| `chk` | repeat-1 (live) | pass | - |" in pack  # the unscored hit is not a failing oracle


def test_an_existing_run_directory_is_never_overwritten(tmp_path):
    record = _record_with_live_runs()
    write_results(record, out_root=str(tmp_path))
    with pytest.raises(ResultsPathError, match="already exists"):
        write_results(record, out_root=str(tmp_path))


def test_run_ids_have_sub_second_resolution():
    run_id = eval_report.new_run_id()
    assert len(run_id) == 22 and run_id.endswith("Z") and "T" in run_id


def test_the_dataset_hash_changes_when_a_case_changes_inside_a_version():
    base = {"version": "v1", "cases": [{"id": "a", "description": "one", "_file": "a.json"}]}
    edited = {"version": "v1", "cases": [{"id": "a", "description": "two", "_file": "a.json"}]}
    same_but_other_file_name = {"version": "v1", "cases": [{"id": "a", "description": "one", "_file": "x.json"}]}
    assert eval_report.dataset_hash(base) != eval_report.dataset_hash(edited)
    assert eval_report.dataset_hash(base) == eval_report.dataset_hash(same_but_other_file_name)


def test_non_ascii_model_text_is_preserved_and_a_lone_surrogate_cannot_abort_the_write(tmp_path):
    runs = [scripted_run("good", [], output_excerpt="DSCR ≥ 1.25x → ok \ud800 end")]
    record = build_record("dry-run", {"version": "v1", "cases": [{"id": "c"}]}, [case("c", "fabrication", runs)], "r9")
    out_dir = write_results(record, out_root=str(tmp_path))
    text = open(os.path.join(out_dir, "results.json"), encoding="utf-8").read()
    assert "≥" in text and "→" in text  # raw characters, not \u escapes
    assert open(os.path.join(out_dir, "review_pack.md"), encoding="utf-8").read()


def test_a_cross_drive_path_is_treated_as_outside_the_repo_not_a_crash(monkeypatch, tmp_path):
    def cross_drive(paths):
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(os.path, "commonpath", cross_drive)
    assert_results_dir_is_ignored(str(tmp_path / "elsewhere"))  # must not raise


def _make_dir_link(link, target):
    """A symlink, or on Windows a junction; None if neither can be created here."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        return done.returncode == 0
    return False


@needs_git
def test_a_link_pointing_into_a_tracked_directory_is_judged_by_where_it_leads(tmp_path):
    link = tmp_path / "innocent-looking"
    if not _make_dir_link(link, os.path.join(REPO_ROOT, "scripts")):
        pytest.skip("cannot create a symlink or junction in this environment")
    with pytest.raises(ResultsPathError, match="not git-ignored"):
        assert_results_dir_is_ignored(str(link))
    with pytest.raises(ResultsPathError):
        write_results(_record_with_live_runs(), out_root=str(link))
    assert not any(name.startswith("2") for name in os.listdir(os.path.join(REPO_ROOT, "scripts")))


@needs_git
def test_the_files_actually_written_are_the_ones_probed(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    # ignores results.json but NOT review_pack.md: the write must be refused.
    (tmp_path / ".gitignore").write_text("evals/results/*/results.json\n", encoding="utf-8")
    with pytest.raises(ResultsPathError, match="review_pack.md"):
        write_results(_record_with_live_runs(), out_root=str(tmp_path / "evals" / "results"),
                      repo_root=str(tmp_path))
    assert not (tmp_path / "evals" / "results" / "20260101T000000Z").exists()


@needs_git
def test_a_default_dry_run_writes_only_into_the_ignored_results_dir_and_no_tracked_file_changes():
    import run_evals

    def status():
        return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=REPO_ROOT,
                              capture_output=True, text=True).stdout

    results_root = os.path.join(REPO_ROOT, "evals", "results")
    existed = os.path.isdir(results_root)
    before_runs = set(os.listdir(results_root)) if existed else set()
    before = status()
    try:
        assert run_evals.main(["--dry-run"]) == 0
        new_runs = set(os.listdir(results_root)) - before_runs
        assert len(new_runs) == 1  # it wrote under the default directory...
        assert status() == before  # ...and git sees nothing new or changed anywhere
    finally:
        for name in set(os.listdir(results_root)) - before_runs if os.path.isdir(results_root) else ():
            shutil.rmtree(os.path.join(results_root, name))
        if not existed and os.path.isdir(results_root) and not os.listdir(results_root):
            os.rmdir(results_root)

# ---------------------------------------------------------------------------
# Second independent review of the PR for #151: hostile text, atomic writes, git failures.
# ---------------------------------------------------------------------------

HOSTILE = "bad | cell\n## FAKE HEADING\n| forged | row |\n<script>alert(1)</script> [click](http://evil.example) `tick`"


def test_model_text_cannot_forge_table_rows_headings_or_markup_in_the_pack():
    run = {"label": "repeat-1", "kind": "live", "passed": False, "status": "ok", "output_excerpt": "",
           "assertions": [{"oracle": "figures_grounded", "passed": False, "scored": True, "reason": HOSTILE}]}
    pack = render_review_pack(build_record("live", {"version": "v1", "cases": [{"id": "c"}]},
                                           [case("c", "fabrication", [run])], "r1"))
    lines = pack.splitlines()
    assert not any(line.startswith("## FAKE") or line.startswith("| forged") for line in lines)
    (row,) = [line for line in lines if line.startswith("| `c` | repeat-1")]
    assert "\\|" in row and row.count("\n") == 0
    # the hostile text survives only as inert inline code inside that single row
    assert "<script>" in row and "`` " not in row.split("| FAIL |")[0]


def test_an_error_message_is_also_neutralised_in_the_pack():
    run = eval_report.live_run("repeat-1", [], status="error", error=HOSTILE)
    pack = render_review_pack(build_record("live", {"version": "v1", "cases": [{"id": "c"}]},
                                           [case("c", "fabrication", [run])], "r1"))
    assert not any(line.startswith("## FAKE") or line.startswith("| forged") for line in pack.splitlines())
    assert "ERROR `" in pack


def test_an_unscored_observations_reason_is_neutralised_too():
    run = eval_report.live_run("repeat-1", [{"oracle": "canary_absent", "passed": False, "scored": False,
                                             "reason": HOSTILE}])
    pack = render_review_pack(build_record("live", {"version": "v1", "cases": [{"id": "c"}]},
                                           [case("c", "injection", [run])], "r1"))
    assert not any(line.startswith("## FAKE") for line in pack.splitlines())


def test_the_excerpt_limit_fits_a_makers_whole_output_and_clip_is_exact_at_the_boundary():
    assert eval_report.EXCERPT_LIMIT >= 16000  # max_tokens=4000 is about 16k characters
    limit = eval_report.EXCERPT_LIMIT
    assert eval_report.clip("x" * limit) == "x" * limit  # exactly at the limit: untouched
    clipped = eval_report.clip("x" * (limit + 1))
    assert clipped.startswith("x" * limit) and "truncated, 1 more characters" in clipped


def test_a_failure_while_rendering_leaves_no_partial_files(tmp_path, monkeypatch):
    def explode(record):
        raise RuntimeError("render failed")

    monkeypatch.setattr(eval_report, "render_review_pack", explode)
    with pytest.raises(RuntimeError):
        write_results(_record_with_live_runs(), out_root=str(tmp_path))
    run_dir = tmp_path / "20260101T000000Z"
    assert not run_dir.exists() or os.listdir(run_dir) == []  # no results.json, no .tmp leftovers


class _Completed:
    def __init__(self, returncode, stderr=b""):
        self.returncode, self.stderr = returncode, stderr


def _inside_repo_path():
    return os.path.join(REPO_ROOT, "evals", "results", "r1")


def test_a_git_failure_fails_closed_with_its_own_message(monkeypatch):
    monkeypatch.setattr(eval_report.subprocess, "run", lambda *a, **k: _Completed(128, b"fatal: not a git repository"))
    with pytest.raises(ResultsPathError, match="cannot verify .*exited 128.*not a git repository"):
        assert_results_dir_is_ignored(_inside_repo_path())


def test_a_missing_git_binary_fails_closed(monkeypatch):
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(eval_report.subprocess, "run", no_git)
    with pytest.raises(ResultsPathError, match="git unavailable"):
        assert_results_dir_is_ignored(_inside_repo_path())


def test_only_git_exit_codes_zero_and_one_are_understood(monkeypatch):
    monkeypatch.setattr(eval_report.subprocess, "run", lambda *a, **k: _Completed(0))
    assert_results_dir_is_ignored(_inside_repo_path())  # ignored: allowed
    monkeypatch.setattr(eval_report.subprocess, "run", lambda *a, **k: _Completed(1))
    with pytest.raises(ResultsPathError, match="not git-ignored"):
        assert_results_dir_is_ignored(_inside_repo_path())


def test_the_dataset_hash_covers_every_field_including_the_canary():
    base = {"version": "v1", "cases": [{"id": "a", "canary": {"token": "CANARY-AAAAAAAA"}}]}
    edited = {"version": "v1", "cases": [{"id": "a", "canary": {"token": "CANARY-BBBBBBBB"}}]}
    assert eval_report.dataset_hash(base) != eval_report.dataset_hash(edited)


def test_the_scripted_self_check_count_counts_only_sound_cases():
    cases = [case("a", "fabrication", [], self_check={"ok": True}), case("b", "fabrication", [], self_check={"ok": False}),
             case("c", "fabrication", [], self_check={"ok": True})]
    assert summarize(cases)["scripted_self_checks"] == {"cases": 3, "ok": 2}


def test_an_errored_live_run_has_its_own_label_in_the_pack():
    run = eval_report.live_run("repeat-1", [], status="error", error="boom")
    pack = render_review_pack(build_record("live", {"version": "v1", "cases": [{"id": "c"}]},
                                           [case("c", "fabrication", [run])], "r1"))
    assert "| repeat-1 (live) | ERROR `boom` |" in pack
