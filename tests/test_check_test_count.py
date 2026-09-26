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
