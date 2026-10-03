import json

import pytest

from check_test_count import check, main, parse_passed_count, read_badge_count


def _write_badge(tmp_path, passed):
    path = tmp_path / "test-count.json"
    path.write_text(json.dumps({"passed": passed}), encoding="utf-8")
    return path


def test_parse_passed_count_plain_summary():
    assert parse_passed_count("72 passed in 1.23s") == 72


def test_parse_passed_count_with_skips():
    assert parse_passed_count("428 passed, 2 skipped in 12.34s") == 428


def test_parse_passed_count_with_xfails():
    assert parse_passed_count("425 passed, 3 xfailed in 10.00s") == 425


def test_parse_passed_count_raises_when_no_passed_line_present():
    with pytest.raises(ValueError):
        parse_passed_count("3 failed in 1.20s")


def test_read_badge_count(tmp_path):
    badge_path = _write_badge(tmp_path, 430)
    assert read_badge_count(str(badge_path)) == 430


def test_check_reports_match_when_counts_agree(tmp_path):
    badge_path = _write_badge(tmp_path, 430)
    matches, actual, expected = check("430 passed in 16.27s", str(badge_path))
    assert matches is True
    assert actual == expected == 430


def test_check_reports_mismatch_when_counts_disagree(tmp_path):
    badge_path = _write_badge(tmp_path, 430)
    matches, actual, expected = check("429 passed in 16.27s", str(badge_path))
    assert matches is False
    assert actual == 429
    assert expected == 430


def test_main_exits_zero_and_prints_match_message_on_matching_count(tmp_path, capsys):
    badge_path = _write_badge(tmp_path, 430)
    output_file = tmp_path / "pytest_output.txt"
    output_file.write_text("430 passed in 16.27s", encoding="utf-8")

    exit_code = main([str(output_file), "--badge-path", str(badge_path)])

    assert exit_code == 0
    assert "matches" in capsys.readouterr().out


def test_main_exits_one_and_prints_mismatch_message_on_deliberate_mismatch(tmp_path, capsys):
    """Demonstrates the failure direction explicitly, not just the success
    path -- a deliberately wrong badge value must actually fail the check."""
    badge_path = _write_badge(tmp_path, 999)
    output_file = tmp_path / "pytest_output.txt"
    output_file.write_text("430 passed in 16.27s", encoding="utf-8")

    exit_code = main([str(output_file), "--badge-path", str(badge_path)])

    captured = capsys.readouterr().out
    assert exit_code == 1
    assert "mismatch" in captured
    assert "430" in captured and "999" in captured


# ---------------------------------------------------------------------------
# --write (issue #147): the one-command update, and the rule that CI never uses it
# ---------------------------------------------------------------------------

def _pytest_output(tmp_path, text):
    path = tmp_path / "pytest_output.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_write_rewrites_the_badge_to_the_reported_count(tmp_path, capsys):
    badge = _write_badge(tmp_path, 430)
    output = _pytest_output(tmp_path, "1078 passed, 2 skipped in 40.00s")
    assert main([str(output), "--badge-path", str(badge), "--write"]) == 0
    assert json.loads(badge.read_text(encoding="utf-8")) == {"passed": 1078}
    assert "430 -> 1078" in capsys.readouterr().out
    # the file is then valid for the ordinary check
    assert main([str(output), "--badge-path", str(badge)]) == 0


def test_write_keeps_the_files_canonical_shape(tmp_path):
    badge = _write_badge(tmp_path, 1)
    main([str(_pytest_output(tmp_path, "12 passed in 1s")), "--badge-path", str(badge), "--write"])
    assert badge.read_bytes() == b'{\n  "passed": 12\n}\n'  # two-space indent, LF, trailing newline


def test_write_is_a_noop_message_when_already_current(tmp_path, capsys):
    badge = _write_badge(tmp_path, 12)
    assert main([str(_pytest_output(tmp_path, "12 passed in 1s")), "--badge-path", str(badge), "--write"]) == 0
    assert "already says 12" in capsys.readouterr().out
    assert json.loads(badge.read_text(encoding="utf-8")) == {"passed": 12}
    assert badge.read_bytes() == b'{\n  "passed": 12\n}\n'  # rewritten in canonical form, same content


def test_write_creates_a_missing_or_unreadable_badge(tmp_path, capsys):
    missing = tmp_path / "sub" / "test-count.json"
    missing.parent.mkdir()
    output = _pytest_output(tmp_path, "7 passed in 1s")
    assert main([str(output), "--badge-path", str(missing), "--write"]) == 0
    assert json.loads(missing.read_text(encoding="utf-8")) == {"passed": 7}
    missing.write_text("not json at all", encoding="utf-8")
    assert main([str(output), "--badge-path", str(missing), "--write"]) == 0
    assert json.loads(missing.read_text(encoding="utf-8")) == {"passed": 7}


def test_write_refuses_output_with_no_passed_line_and_leaves_the_file_alone(tmp_path, capsys):
    badge = _write_badge(tmp_path, 430)
    before = badge.read_bytes()
    assert main([str(_pytest_output(tmp_path, "3 failed in 1.20s")), "--badge-path", str(badge), "--write"]) == 1
    assert badge.read_bytes() == before
    assert "Cannot compare test counts" in capsys.readouterr().out


def test_the_plain_check_still_fails_on_a_mismatch_and_points_at_write(tmp_path, capsys):
    badge = _write_badge(tmp_path, 999)
    output = _pytest_output(tmp_path, "430 passed in 1s")
    assert main([str(output), "--badge-path", str(badge)]) == 1
    assert json.loads(badge.read_text(encoding="utf-8")) == {"passed": 999}  # never auto-corrected
    assert "--write" in capsys.readouterr().out


def test_an_unreadable_badge_in_the_plain_check_is_a_clean_failure_not_a_traceback(tmp_path, capsys):
    badge = tmp_path / "test-count.json"
    badge.write_text("{}", encoding="utf-8")  # no "passed" key
    output = _pytest_output(tmp_path, "5 passed in 1s")
    try:
        code = main([str(output), "--badge-path", str(badge)])
    except KeyError:
        pytest.fail("a malformed badge should be reported, not raise KeyError")
    assert code == 1


def test_ci_only_runs_the_plain_check_and_never_write():
    """Governance rule: no automated step edits badges/test-count.json (no bot commits)."""
    from pathlib import Path
    workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    mentions = [line for f in workflows.glob("*.yml") for line in f.read_text(encoding="utf-8").splitlines()
                if "check_test_count" in line]
    assert mentions, "the CI count check is missing"
    assert not any("--write" in line for line in mentions)
