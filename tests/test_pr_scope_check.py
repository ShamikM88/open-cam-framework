"""Tests for the deterministic, advisory PR scope report (issue #456)."""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "pr_scope_check.py"


@pytest.fixture
def checker():
    spec = importlib.util.spec_from_file_location("pr_scope_check_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_declared_scope_matches_paths_and_reports_out_of_scope(checker):
    report = checker.build_report(
        ["README.md", "docs/contributing.md", "docs/testing.md"],
        ["docs/**/*.md"],
    )
    assert "IN_SCOPE ordinary: 2" in report
    assert "OUT_OF_SCOPE ordinary: 1" in report
    assert "IN_SCOPE docs/contributing.md" in report
    assert "OUT_OF_SCOPE README.md" in report
    assert "Changed files: 3" in report


def test_protected_paths_are_reported_with_and_without_declaration(checker):
    files = [
        "agents/underwriter_agent.md", ".github/workflows/ci.yml", "pyproject.toml",
        "config/settings.json", "badges/test-count.json", ".claude/settings.json",
        "diff_cover_critical.toml",
    ]
    scope = [
        "agents/*.md", "pyproject.toml", "config/**", "badges/test-count.json",
        ".claude/**", "diff_cover_critical.toml",
    ]
    report = checker.build_report(files, scope)
    assert "PROTECTED_DECLARED: 6" in report
    assert "PROTECTED_UNDECLARED: 1" in report
    assert "PROTECTED_DECLARED agents/underwriter_agent.md" in report
    assert "PROTECTED_UNDECLARED .github/workflows/ci.yml" in report
    assert "OUT_OF_SCOPE ordinary: 0" in report


@pytest.mark.parametrize("body", ["", "summary only\n", "## Declared file scope\n<!-- no patterns -->\n"])
def test_missing_and_empty_scope_are_advisory_not_failures(checker, tmp_path, capsys, body):
    changed = tmp_path / "changed-files.nul"
    changed.write_bytes(b"README.md\0")
    scope = tmp_path / "scope-body.md"
    scope.write_text(body, encoding="utf-8")
    code = checker.main(["--changed-files", str(changed), "--scope-body", str(scope)])
    output = capsys.readouterr()
    assert code == 0
    assert "SCOPE NOT DECLARED" in output.out
    assert "OUT_OF_SCOPE README.md" in output.out
    assert "error:" not in output.err


def test_duplicate_scope_heading_and_malformed_patterns_return_two(checker):
    hostile = "malformed shell: $(touch /tmp/not-run)"
    with pytest.raises(checker.ScopeInputError):
        checker.parse_scope_body(
            "## Declared file scope\n- `docs/*.md`\n" +
            "## Declared file scope\n- `**`\n" + hostile
        )
    with pytest.raises(checker.ScopeInputError):
        checker.parse_scope_body("## Declared file scope\n- `docs/[abc.md`\n")


def test_single_star_does_not_cross_a_directory(checker):
    assert checker.matches_glob("src/a.py", "src/*.py")
    assert not checker.matches_glob("src/nested/a.py", "src/*.py")


def test_double_star_matches_zero_or_more_components(checker):
    assert checker.matches_glob("tests/test_x.py", "tests/**/*.py")
    assert checker.matches_glob("tests/unit/test_x.py", "tests/**/*.py")
    assert checker.matches_glob("x/y/z.py", "**/*.py")
    assert checker.matches_glob("one.py", "**/*.py")


def test_directory_glob_matches_descendant_files(checker):
    assert checker.matches_glob("docs/guide.md", "docs/")
    assert checker.matches_glob("docs/nested/guide.md", "docs/")
    assert not checker.matches_glob("docs", "docs/")


def test_character_classes_match_within_a_single_path_component(checker):
    assert checker.matches_glob("tests/test_a.py", "tests/test_[ab].py")
    assert checker.matches_glob("tests/test_b.py", "tests/test_[ab].py")
    assert not checker.matches_glob("tests/test_c.py", "tests/test_[ab].py")
    with pytest.raises(checker.ScopeInputError):
        checker.matches_glob("tests/test_a.py", "tests/test_[ab.py")


def test_case_sensitive_matching_is_deterministic(checker):
    paths = ["docs/z.md", "docs/A.md", "README.md", "docs/a.md"]
    first = checker.build_report(paths, ["docs/*.md"])
    second = checker.build_report(list(reversed(paths)), ["docs/*.md"])
    assert first == second
    assert "OUT_OF_SCOPE README.md" in first
    assert first.index("IN_SCOPE docs/A.md") < first.index("IN_SCOPE docs/a.md") < first.index("IN_SCOPE docs/z.md")
    assert not checker.matches_glob("Docs/a.md", "docs/*.md")


def test_windows_separators_leading_dot_and_repeated_slashes_normalize(checker):
    assert checker.normalize_path(r".\\docs\\\\./unit//readme.md") == "docs/unit/readme.md"
    assert checker.normalize_path(r"docs\\sub\\file.py") == "docs/sub/file.py"
    assert checker.normalize_path(r"docs//./sub/file.py", pattern=True) == "docs/sub/file.py"


@pytest.mark.parametrize("path", [
    "/etc/passwd", r"\\\\server\\share\\file", r"C:\\repo\\file.py",
    "C:relative.py", "../escape.py", "docs/../escape.py", "docs/..\\escape.py",
    "docs/bad\nname.py", "docs/bad\x07name.py",
])
def test_absolute_drive_unc_parent_and_control_paths_are_rejected(checker, path):
    with pytest.raises(checker.ScopeInputError):
        checker.normalize_path(path)


def test_hostile_pr_body_is_data_not_shell_or_summary_markup(checker):
    body = (
        "A free-form paragraph with <img src=x onerror=alert(1)> and `[link](javascript:alert(1))`.\n"
        "## Declared file scope\n"
        "- `**`\n"
        "## Later section\n"
        "This shell-shaped text must stay inert: $(touch /tmp/not-run); `--write`; `# heading`.\n"
    )
    patterns = checker.parse_scope_body(body)
    assert patterns == ["**"]
    report = checker.build_report(["docs/<img>.md", "http://example.invalid/a.md"], patterns)
    assert "SCOPE NOT DECLARED" not in report
    assert "BROAD_SCOPE: 1" in report
    assert "BROAD_SCOPE \\*\\*" in report
    assert "<img" not in report
    assert "javascript:alert" not in report
    assert "http://example.invalid" not in report


def test_scope_line_that_tries_to_widen_itself_is_malformed_without_echoing_body(checker, tmp_path, capsys):
    changed = tmp_path / "changed-files.nul"
    changed.write_bytes(b"docs/a.md\0")
    body = tmp_path / "scope-body.md"
    raw = "- `docs/*.md` # widen with `**` and $(touch /tmp/not-run)"
    body.write_text("## Declared file scope\n" + raw + "\n", encoding="utf-8")
    code = checker.main(["--changed-files", str(changed), "--scope-body", str(body)])
    captured = capsys.readouterr()
    assert code == 2
    assert "error:" in captured.err
    assert raw not in captured.out + captured.err


def test_nul_changed_file_list_accepts_spaces_and_escaped_punctuation(checker, tmp_path):
    changed = tmp_path / "changed-files.nul"
    changed.write_bytes(b"docs/a file [draft].md\0README.md\0")
    body = tmp_path / "scope-body.md"
    body.write_text("## Declared file scope\n- `docs/*.md`\n", encoding="utf-8")
    code = checker.main(["--changed-files", str(changed), "--scope-body", str(body)])
    assert code == 0
    report = checker.build_report(["docs/a file [draft].md", "README.md"], ["docs/*.md"])
    assert "IN_SCOPE ordinary: 1" in report
    assert "OUT_OF_SCOPE ordinary: 1" in report
    assert "docs/a file \\[draft\\]\.md" not in report or "docs/a file \\[draft\\].md" in report


def test_missing_input_and_invalid_utf8_return_two(checker, tmp_path, capsys):
    missing = tmp_path / "missing.nul"
    body = tmp_path / "scope-body.md"
    body.write_text("", encoding="utf-8")
    code = checker.main(["--changed-files", str(missing), "--scope-body", str(body)])
    assert code == 2
    assert "error:" in capsys.readouterr().err

    invalid = tmp_path / "invalid.nul"
    invalid.write_bytes(b"docs/\xff.md\0")
    code = checker.main(["--changed-files", str(invalid), "--scope-body", str(body)])
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_summary_file_receives_the_same_report_as_stdout(checker, tmp_path, capsys):
    changed = tmp_path / "changed-files.nul"
    changed.write_bytes(b"docs/a.md\0")
    body = tmp_path / "scope-body.md"
    body.write_text("## Declared file scope\n- `docs/*.md`\n", encoding="utf-8")
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    code = checker.main([
        "--changed-files", str(changed), "--scope-body", str(body), "--summary-file", str(summary)
    ])
    captured = capsys.readouterr()
    assert code == 0
    assert summary.read_text(encoding="utf-8") == captured.out


def test_large_diff_reports_exact_counts_and_caps_paths_at_one_hundred(checker):
    paths = [f"docs/file-{index:03d}.md" for index in range(105)]
    report = checker.build_report(paths, ["docs/**/*.md"])
    rows = [line for line in report.splitlines() if line.startswith("IN_SCOPE ")]
    assert len(rows) == 100
    assert "IN_SCOPE ordinary: 105" in report
    assert "5 more paths omitted" in report


def test_changed_file_list_requires_nul_delimiters_and_unique_file_paths(checker, tmp_path):
    bad = tmp_path / "bad.nul"
    bad.write_bytes(b"docs/a.md\0docs/a.md\0")
    with pytest.raises(checker.ScopeInputError):
        checker.read_changed_files(bad)
    bad.write_bytes(b"docs/a.md")
    with pytest.raises(checker.ScopeInputError):
        checker.read_changed_files(bad)


def test_main_block_only_configures_stdio_when_run_as_a_script():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":\n    from textio import configure_stdio\n    configure_stdio()' in source
    assert "import anthropic" not in source
    assert "urllib" not in source and "requests" not in source
