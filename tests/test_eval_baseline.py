"""Tests for scripts/eval_baseline.py (issue #151, PR 2): baseline export contains no model
text, refuses what is not a real live run, and a comparison says plainly when it is not
like-for-like. Pure data in, data out: zero model calls.
"""
import json

import pytest

from eval_baseline import BASELINE_KIND, REGRESSION_DROP, BaselineError, compare, export_baseline, render_comparison
from eval_report import build_record, live_run, scripted_run

MODELS = {"maker_model": "m-1", "checker_model": "m-2", "maker_temperature": None, "checker_temperature": None}
HASHES = {"underwriter_prompt_hash": "aaa", "risk_reviewer_prompt_hash": "bbb"}
INPUTS = {"agents/underwriter_agent.md": "aaa", "config/settings.json": "ccc"}
SECRET_TEXT = "MODEL-OUTPUT-TEXT-THAT-MUST-NEVER-BE-IN-A-BASELINE"


def live_record(rates, run_id="run-1", models=None, hashes=None, inputs=None, dataset_hash="d1", **kwargs):
    cases = []
    for case_id, (passed, total) in rates.items():
        runs = [live_run(f"repeat-{i}", [{"oracle": "x", "passed": i <= passed, "scored": True, "reason": ""}],
                         output_excerpt=SECRET_TEXT, output_text=SECRET_TEXT + " FULL",
                         extra={"prompt_hash": "p"}) for i in range(1, total + 1)]
        cases.append({"id": case_id, "category": "injection", "mode": "maker", "description": "d",
                      "human_review": [], "runs_planned": total, "runs": runs})
    record = build_record("live", {"version": "v1", "cases": [{"id": c} for c in rates]}, cases, run_id,
                          models=models or MODELS, hashes=hashes or HASHES, input_hashes=inputs or INPUTS,
                          repeats=5, **kwargs)
    record["dataset"]["content_hash"] = dataset_hash
    return record


def test_an_exported_baseline_holds_rates_and_provenance_but_no_model_text():
    summary = export_baseline(live_record({"a": (4, 5), "b": (5, 5)}))
    assert summary["kind"] == BASELINE_KIND
    assert {c["id"]: (c["passed"], c["total"]) for c in summary["per_case"]} == {"a": (4, 5), "b": (5, 5)}
    assert summary["per_category"]["injection"] == {"passed": 9, "total": 10}
    assert summary["models"] == MODELS and summary["prompt_hashes"] == HASHES and summary["input_hashes"] == INPUTS
    assert summary["dataset"]["content_hash"] == "d1" and summary["repeats"] == 5
    assert SECRET_TEXT not in json.dumps(summary)
    assert "output_excerpt" not in json.dumps(summary) and "human_review" not in json.dumps(summary)
    assert "output_text" not in json.dumps(summary)


def test_a_dry_run_cannot_be_a_baseline():
    scripted = [{"id": "a", "category": "fabrication", "mode": "maker", "description": "d", "human_review": [],
                 "runs": [scripted_run("good", [])]}]
    dry = build_record("dry-run", {"version": "v1", "cases": [{"id": "a"}]}, scripted, "r")
    with pytest.raises(BaselineError, match="only a live run"):
        export_baseline(dry)


def test_a_record_with_no_completed_live_runs_cannot_be_a_baseline():
    empty = live_record({"a": (0, 0)})
    with pytest.raises(BaselineError, match="nothing to baseline"):
        export_baseline(empty)


def test_errored_runs_are_counted_in_the_baseline_denominator():
    record = live_record({"a": (3, 3)})
    record["cases"][0]["runs"].append(live_run("repeat-4", [], status="error", error="boom"))
    summary = export_baseline(record)
    assert summary["per_case"][0] == {**summary["per_case"][0], "passed": 3, "total": 4, "errored": 1}


def test_an_aborted_run_is_flagged_in_its_baseline_by_category_only():
    summary = export_baseline(live_record({"a": (2, 2)}, aborted=True, abort_reason="call cap: spent",
                                          abort_category="call cap"))
    assert summary["aborted"] is True and summary["abort_category"] == "call cap"
    assert "abort_reason" not in summary and "spent" not in json.dumps(summary)


def test_a_like_for_like_comparison_reports_deltas_and_flags_a_large_drop():
    baseline = export_baseline(live_record({"stable": (5, 5), "dropped": (5, 5), "slipped": (5, 5)}))
    current = live_record({"stable": (5, 5), "dropped": (2, 5), "slipped": (4, 5)}, run_id="run-2")
    result = compare(baseline, current)
    assert result["like_for_like"] and result["mismatches"] == []
    rows = {r["id"]: r for r in result["rows"]}
    assert rows["stable"]["delta"] == 0 and rows["stable"]["flag"] is None
    assert rows["slipped"]["delta"] == pytest.approx(-0.2) and rows["slipped"]["flag"] is None
    assert rows["dropped"]["delta"] == pytest.approx(-0.6) and rows["dropped"]["flag"] == "possible regression"
    assert REGRESSION_DROP == 0.4


@pytest.mark.parametrize("override, expected", [
    ({"models": {**MODELS, "maker_model": "m-NEW"}}, "model / temperatures"),
    ({"hashes": {**HASHES, "underwriter_prompt_hash": "NEW"}}, "agent prompt hashes"),
    ({"inputs": {**INPUTS, "config/settings.json": "NEW"}}, "template and settings hashes"),
    ({"dataset_hash": "d2"}, "dataset content"),
])
def test_a_comparison_says_when_it_is_not_like_for_like(override, expected):
    baseline = export_baseline(live_record({"a": (5, 5)}))
    result = compare(baseline, live_record({"a": (5, 5)}, run_id="run-2", **override))
    assert not result["like_for_like"] and any(expected in m for m in result["mismatches"])
    assert "NOT like-for-like" in render_comparison(result)


def test_new_and_missing_cases_are_reported_not_crashed_on():
    baseline = export_baseline(live_record({"old": (5, 5), "kept": (5, 5)}))
    result = compare(baseline, live_record({"kept": (5, 5), "new": (1, 5)}, run_id="run-2"))
    statuses = {r["id"]: r["status"] for r in result["rows"]}
    assert statuses == {"old": "not in this run", "kept": "compared", "new": "new case (no baseline)"}
    text = render_comparison(result)
    assert "not in this run" in text and "new case (no baseline)" in text


def test_the_rendering_is_labelled_informational_and_never_claims_proof():
    baseline = export_baseline(live_record({"a": (5, 5)}, aborted=True, abort_reason="x"))
    text = render_comparison(compare(baseline, live_record({"a": (3, 5)}, run_id="run-2", aborted=True)))
    assert "informational" in text and "gates nothing" in text and "was aborted" in text
    for phrase in ("proven", "is safe", "guaranteed"):
        assert phrase not in text.lower()


def test_comparing_against_something_that_is_not_a_baseline_is_refused():
    with pytest.raises(BaselineError, match="not an evaluation baseline"):
        compare({"kind": "something-else"}, live_record({"a": (1, 1)}))

# ---------------------------------------------------------------------------
# Third independent review: baseline leakage, like-for-like completeness, CLI edges.
# ---------------------------------------------------------------------------

def test_api_error_text_can_never_reach_a_baseline_through_the_abort_reason():
    secret = "sk-ant-api03-SECRET-LOOKING-VALUE"
    record = live_record({"a": (2, 2)}, aborted=True, abort_reason=f"3 consecutive errored runs (last: {secret})",
                         abort_category="consecutive errors")
    for run in record["cases"][0]["runs"]:
        run["error"] = secret
    text = json.dumps(export_baseline(record))
    assert secret not in text and "consecutive errors" in text


def test_the_baseline_records_the_prompts_the_live_runs_actually_sent():
    summary = export_baseline(live_record({"a": (2, 2), "b": (1, 1)}))
    assert summary["prompt_hashes_by_case"] == {"a": ["p"], "b": ["p"]}


def test_a_change_in_the_code_that_assembles_prompts_makes_the_comparison_not_like_for_like():
    baseline = export_baseline(live_record({"a": (5, 5)}))
    current = live_record({"a": (5, 5)}, run_id="run-2")
    for run in current["cases"][0]["runs"]:
        run["prompt_hash"] = "a-different-prompt"  # file hashes identical, assembled prompt different
    result = compare(baseline, current)
    assert not result["like_for_like"] and any("assembled prompts" in m for m in result["mismatches"])


def test_a_different_harness_version_or_repeat_count_is_flagged():
    baseline = export_baseline(live_record({"a": (5, 5)}))
    current = live_record({"a": (5, 5)}, run_id="run-2")
    current["harness_version"] = "9.9-future"
    current["repeats"] = 3
    mismatches = compare(baseline, current)["mismatches"]
    assert "harness version" in mismatches and "repeats per case" in mismatches


@pytest.mark.parametrize("before, after, flagged", [
    ((5, 5), (3, 5), True),    # -0.4 exactly
    ((5, 5), (4, 5), False),
    ((3, 5), (1, 5), True),    # -0.39999999999999997 in floating point: must still be flagged
    ((6, 10), (2, 10), True),
    ((7, 10), (3, 10), True),
    ((6, 10), (3, 10), False),
])
def test_the_regression_threshold_is_not_at_the_mercy_of_float_rounding(before, after, flagged):
    baseline = export_baseline(live_record({"a": before}))
    result = compare(baseline, live_record({"a": after}, run_id="run-2"))
    assert (result["rows"][0]["flag"] == "possible regression") is flagged


@pytest.mark.parametrize("broken", [{}, {"mode": "live"}, {"mode": "live", "cases": [{"runs": [{}]}]}])
def test_a_malformed_results_file_is_a_baseline_error_not_a_traceback(broken):
    with pytest.raises(BaselineError):
        export_baseline(broken)
    with pytest.raises(BaselineError):
        compare(export_baseline(live_record({"a": (1, 1)})), broken)
