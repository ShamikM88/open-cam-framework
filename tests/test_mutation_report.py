"""scripts/mutation_report.py: the per-module mutation score and completeness report (issue #146).

The input is synthetic text in the shape of `mutmut results --all true` (one `module.function__mutmut_N: status`
line per mutant), so none of this needs mutmut, Linux or a mutation run.
"""
import json

import pytest

import mutation_report
from mutation_report import load_config, parse_results, render_markdown, summarize

CONFIG = {"modules": ["alpha", "beta", "gamma"], "critical": ["alpha", "beta"], "target_critical": None}


def lines(module, function, statuses):
    return "\n".join(f"    {module}.{function}__mutmut_{i}: {status}" for i, status in enumerate(statuses, 1))


def results_text(*blocks):
    return "\n".join(blocks) + "\n"


FULL = results_text(
    lines("alpha", "x_compute", ["killed"] * 8 + ["survived", "timeout"]),            # 9/10 = 90.0%
    lines("beta", "x_main", ["killed"] * 3 + ["no tests"] * 1 + ["survived"] * 2),     # 3/6 = 50.0%
    lines("gamma", "xǁHelperǁrun", ["killed"] * 4),
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_parse_results_reads_module_function_number_and_status():
    entries = parse_results("    policy_engine.x__slugify__mutmut_3: survived\n"
                            "    state_manager.xǁ_FileLockǁ__enter____mutmut_12: no tests\n"
                            "    docx_builder.x_export_to_docx__mutmut_7: timeout\n"
                            "    spreading_builder.x_a__mutmut_1: check was interrupted by user\n"
                            "    spreading_builder.x_b__mutmut_2: not checked\n"
                            "    spreading_builder.x_c__mutmut_3: some new status\n")
    assert entries == [
        ("policy_engine", "_slugify", 3, "survived"),
        ("state_manager", "_FileLock.__enter__", 12, "no_tests"),
        ("docx_builder", "export_to_docx", 7, "timeout"),
        ("spreading_builder", "a", 1, "interrupted"),
        ("spreading_builder", "b", 2, "not_checked"),
        ("spreading_builder", "c", 3, "unknown"),
    ]


def test_parse_results_skips_everything_that_is_not_a_mutant_line():
    text = "Running mutation testing\n\nnot a mutant line\n    alpha.x_f__mutmut_1: killed\n  trailing: text\n"
    assert parse_results(text) == [("alpha", "f", 1, "killed")]
    assert parse_results("") == []


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

def test_per_module_scores_count_killed_and_timed_out_against_all_non_skipped_mutants():
    summary = summarize(parse_results(FULL), CONFIG)
    alpha, beta, gamma = (summary["modules"][m] for m in ("alpha", "beta", "gamma"))
    assert (alpha["total"], alpha["killed"], alpha["timeout"], alpha["survived"], alpha["score"]) == (10, 8, 1, 1, 90.0)
    assert (beta["total"], beta["no_tests"], beta["score"]) == (6, 1, 50.0)
    assert gamma["score"] == 100.0
    total = summary["total"]
    assert (total["total"], total["killed"], total["timeout"], total["survived"], total["no_tests"]) == (20, 15, 1, 3, 1)
    assert total["score"] == 80.0


def test_a_mutant_no_test_reaches_counts_against_the_score_but_is_also_shown_excluded():
    row = summarize(parse_results(FULL), CONFIG)["modules"]["beta"]
    assert row["score"] == 50.0 and row["score_excluding_no_tests"] == 60.0     # 3 of 6, and 3 of the 5 reached


def test_skipped_mutants_leave_the_denominator():
    text = results_text(lines("alpha", "x_f", ["killed", "skipped", "skipped", "survived"]))
    assert summarize(parse_results(text), CONFIG)["modules"]["alpha"]["score"] == 50.0


def test_a_module_with_no_mutants_has_no_score():
    row = summarize(parse_results(lines("alpha", "x_f", ["killed"])), CONFIG)["modules"]["beta"]
    assert row["total"] == 0 and row["score"] is None and row["score_excluding_no_tests"] is None


def test_modules_are_reported_in_configured_order_and_marked_critical_or_supporting():
    summary = summarize(parse_results(FULL), CONFIG)
    assert list(summary["modules"]) == ["alpha", "beta", "gamma"]
    assert [summary["modules"][m]["critical"] for m in summary["modules"]] == [True, True, False]


def test_a_target_marks_each_critical_module_met_or_below_and_leaves_the_rest_alone():
    config = dict(CONFIG, target_critical=60.0)
    modules = summarize(parse_results(FULL), config)["modules"]
    assert modules["alpha"]["meets_target"] is True and modules["beta"]["meets_target"] is False
    assert modules["gamma"]["meets_target"] is None                                   # not a critical module
    assert summarize(parse_results(FULL), CONFIG)["modules"]["alpha"]["meets_target"] is None   # no target set
    empty = summarize(parse_results(lines("alpha", "x_f", ["killed"])), config)["modules"]["beta"]
    assert empty["meets_target"] is None                                              # nothing to compare


# ---------------------------------------------------------------------------
# Completeness: an incomplete report must not be readable as a good one
# ---------------------------------------------------------------------------

def test_a_complete_run_has_no_warnings():
    assert summarize(parse_results(FULL), CONFIG)["warnings"] == []


def test_an_empty_listing_warns():
    assert summarize([], CONFIG)["warnings"][0] == "the results file lists no mutants at all"


def test_a_survivors_only_listing_is_recognised_and_its_scores_flagged():
    text = results_text(lines("alpha", "x_f", ["survived", "survived"]), lines("beta", "x_g", ["no tests"]))
    warnings = summarize(parse_results(text), CONFIG)["warnings"]
    assert any("survivors-only" in w and "--all true" in w for w in warnings)


def test_a_configured_module_with_no_mutants_warns_by_name():
    text = results_text(lines("alpha", "x_f", ["killed"]), lines("beta", "x_g", ["killed"]))
    assert summarize(parse_results(text), CONFIG)["warnings"] == [
        "gamma: configured for mutation but no mutant was listed for it"]


def test_a_module_whose_mutants_mostly_have_no_test_warns():
    text = results_text(lines("alpha", "x_f", ["killed"]), lines("beta", "x_g", ["no tests"] * 3 + ["killed"]),
                        lines("gamma", "x_h", ["killed"]))
    assert summarize(parse_results(text), CONFIG)["warnings"] == [
        "beta: 3 of 4 mutants have no test that reaches them"]
    exactly_half = results_text(lines("alpha", "x_f", ["killed"]), lines("beta", "x_g", ["no tests", "killed"]),
                                lines("gamma", "x_h", ["killed"]))
    assert summarize(parse_results(exactly_half), CONFIG)["warnings"] == []          # more than half, not half


def test_unchecked_interrupted_and_unknown_mutants_make_the_run_incomplete():
    text = results_text(FULL, lines("alpha", "x_z", ["not checked", "check was interrupted by user", "mystery"]))
    warnings = summarize(parse_results(text), CONFIG)["warnings"]
    assert any("1 mutants were never checked and 1 were interrupted" in w for w in warnings)
    assert any("1 mutants have a status this report does not know" in w for w in warnings)


def test_mutants_listed_for_a_module_outside_the_configuration_are_reported_not_ignored():
    summary = summarize(parse_results(results_text(FULL, lines("delta", "x_f", ["killed"]))), CONFIG)
    assert "delta" in summary["modules"] and summary["modules"]["delta"]["critical"] is False
    assert any(w.startswith("delta: mutants listed for a module that is not configured") for w in summary["warnings"])


# ---------------------------------------------------------------------------
# Survivor groups, rendering, JSON
# ---------------------------------------------------------------------------

def test_survivor_groups_are_ranked_by_size_and_exclude_killed_mutants():
    text = results_text(lines("alpha", "x_big", ["survived"] * 3 + ["killed"]), lines("alpha", "x_small", ["survived"]),
                        lines("beta", "x_cli", ["no tests"] * 2 + ["killed"]), lines("gamma", "x_f", ["killed"]))
    groups = summarize(parse_results(text), CONFIG)["survivor_groups"]
    assert groups == [
        {"module": "alpha", "function": "big", "status": "survived", "count": 3},
        {"module": "beta", "function": "cli", "status": "no_tests", "count": 2},
        {"module": "alpha", "function": "small", "status": "survived", "count": 1},
    ]


def test_the_markdown_report_has_a_row_per_module_the_total_and_the_survivor_table():
    report = render_markdown(summarize(parse_results(FULL), CONFIG), top=2)
    assert "| alpha | critical | 10 | 8 | 1 | 1 | 0 | 90.0% | 90.0% |" in report
    assert "| gamma | supporting | 4 | 4 | 0 | 0 | 0 | 100.0% | 100.0% |" in report
    assert "| **All** | | 20 | 15 | 1 | 3 | 1 | **80.0%** | 84.2% |" in report
    assert "none set" in report and "diagnostic, not a merge gate" in report
    assert "Report completeness: every configured module has mutants" in report
    assert "Largest groups of survivors (top 2 of 3)" in report and "| alpha.compute | survived | 1 |" in report


def test_the_markdown_report_lists_warnings_and_shows_the_target_column_only_when_set():
    config = dict(CONFIG, target_critical=60.0)
    report = render_markdown(summarize(parse_results(lines("alpha", "x_f", ["killed", "survived"])), config))
    assert "Target for critical modules: 60.0%" in report and "| Target |" in report
    assert "| alpha | critical | 2 | 1 | 0 | 1 | 0 | 50.0% | 50.0% | below |" in report
    assert "### Report completeness warnings" in report and "- beta: configured for mutation but no mutant" in report
    assert "| Target |" not in render_markdown(summarize(parse_results(FULL), CONFIG))


def test_top_limits_the_survivor_table():
    text = results_text(*(lines("alpha", f"x_f{i}", ["survived"]) for i in range(5)), lines("beta", "x_g", ["killed"]),
                        lines("gamma", "x_h", ["killed"]))
    assert "top 3 of 5" in render_markdown(summarize(parse_results(text), CONFIG), top=3)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def write_config(tmp_path, body):
    path = tmp_path / "pyproject.toml"
    path.write_text(body, encoding="utf-8")
    return path


GOOD_CONFIG = ('[tool.mutmut]\nonly_mutate = ["src/alpha.py", "src/beta.py", "src/gamma.py"]\n'
               '[tool.opencam.coverage]\ncritical_modules = ["alpha", "beta"]\n')


def test_load_config_reads_the_module_lists(tmp_path):
    assert load_config(write_config(tmp_path, GOOD_CONFIG)) == CONFIG
    with_target = load_config(write_config(tmp_path, GOOD_CONFIG + "[tool.opencam.mutation]\ntarget_critical = 75\n"))
    assert with_target["target_critical"] == 75.0


@pytest.mark.parametrize("body, message", [
    ("[tool.opencam.coverage]\ncritical_modules = [\"a\"]\n", "only_mutate"),
    ("[tool.mutmut]\nonly_mutate = []\n[tool.opencam.coverage]\ncritical_modules = [\"a\"]\n", "only_mutate"),
    ("[tool.mutmut]\nonly_mutate = [\"scripts/a.py\"]\n[tool.opencam.coverage]\ncritical_modules = [\"a\"]\n",
     "src/<module>.py"),
    ("[tool.mutmut]\nonly_mutate = [3]\n[tool.opencam.coverage]\ncritical_modules = [\"a\"]\n", "src/<module>.py"),
    ("[tool.mutmut]\nonly_mutate = [\"src/a.py\"]\n", "critical_modules"),
    ("[tool.mutmut]\nonly_mutate = [\"src/a.py\"]\n[tool.opencam.coverage]\ncritical_modules = [\"b\"]\n",
     "not mutated"),
    (GOOD_CONFIG + "[tool.opencam.mutation]\ntarget_critical = 0\n", "target_critical"),
    (GOOD_CONFIG + "[tool.opencam.mutation]\ntarget_critical = 101\n", "target_critical"),
    (GOOD_CONFIG + "[tool.opencam.mutation]\ntarget_critical = true\n", "target_critical"),
    (GOOD_CONFIG + "[tool.opencam.mutation]\ntarget_critical = \"80\"\n", "target_critical"),
])
def test_load_config_rejects_a_bad_configuration(tmp_path, body, message):
    with pytest.raises(ValueError, match=message):
        load_config(write_config(tmp_path, body))


def test_the_repositorys_own_configuration_loads_and_covers_the_governance_modules_plus_calibrate():
    config = load_config()
    assert set(config["critical"]) <= set(config["modules"])
    assert set(config["modules"]) - set(config["critical"]) == {"calibrate"}      # the supporting module (#146)
    assert config["target_critical"] is None            # not set: decided from a full run's numbers, reviewed


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------

def test_main_prints_the_report_and_writes_json(tmp_path, capsys):
    results = tmp_path / "all.txt"
    results.write_text(FULL, encoding="utf-8")
    out = tmp_path / "report.json"
    assert mutation_report.main([str(results), "--config", str(write_config(tmp_path, GOOD_CONFIG)),
                                 "--json", str(out)]) == 0
    assert "| **All** | | 20 | 15 | 1 | 3 | 1 | **80.0%** |" in capsys.readouterr().out
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total"]["score"] == 80.0 and data["modules"]["alpha"]["score"] == 90.0


def test_main_exits_zero_even_when_the_report_is_incomplete_or_the_scores_are_poor(tmp_path, capsys):
    results = tmp_path / "all.txt"
    results.write_text(lines("alpha", "x_f", ["survived"] * 5), encoding="utf-8")
    assert mutation_report.main([str(results), "--config", str(write_config(tmp_path, GOOD_CONFIG))]) == 0
    assert "survivors-only" in capsys.readouterr().out                      # diagnostic: warned, never failed


def test_main_exits_two_for_an_unreadable_results_file_or_configuration(tmp_path, capsys):
    config = write_config(tmp_path, GOOD_CONFIG)
    assert mutation_report.main([str(tmp_path / "missing.txt"), "--config", str(config)]) == 2
    assert "missing.txt" in capsys.readouterr().err
    results = tmp_path / "all.txt"
    results.write_text(FULL, encoding="utf-8")
    assert mutation_report.main([str(results), "--config", str(tmp_path / "nope.toml")]) == 2
    assert mutation_report.main([str(results), "--config", str(write_config(tmp_path, "[tool.mutmut]\n"))]) == 2
    assert "only_mutate" in capsys.readouterr().err


def test_timeouts_alone_do_not_make_a_survivors_only_listing_look_complete():
    """`mutmut results` (without --all) lists survivors, no-test mutants and timeouts, but never a killed one."""
    text = results_text(lines("alpha", "x_f", ["survived", "timeout"]), lines("beta", "x_g", ["no tests"]))
    assert any("survivors-only" in w for w in summarize(parse_results(text), CONFIG)["warnings"])


def test_a_score_exactly_at_the_target_meets_it():
    text = results_text(lines("alpha", "x_f", ["killed", "survived"]))
    assert summarize(parse_results(text), dict(CONFIG, target_critical=50.0))["modules"]["alpha"]["meets_target"] is True
    assert summarize(parse_results(text), dict(CONFIG, target_critical=50.1))["modules"]["alpha"]["meets_target"] is False
