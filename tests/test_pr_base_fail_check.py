"""Tests for the advisory new-tests-on-base check (issue #475, PR 1 of 3).

The check is a reviewer aid, never a gate: it selects the test functions and Test* class methods a
pull request adds or changes (AST comparison, decorators included), overlays the PR's changed
tests/ paths onto a worktree of the base commit, runs exactly those tests there, and reports one of
RED_ON_BASE, PASSES_ON_BASE, CANNOT_RUN, NO_TESTS_SELECTED with the failure kind named. Every
verdict exits 0; only a broken check exits 2, and it must never look like a clean result.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "pr_base_fail_check.py"


@pytest.fixture
def checker():
    spec = importlib.util.spec_from_file_location("pr_base_fail_check_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd, *args):
    """Run git the way the suite's other synthetic-repository tests do (no inherited GIT_ config)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True, capture_output=True,
                   env=env, timeout=60)


class SyntheticRepo:
    """A real git repository with a base commit and a head commit, built from a file tree."""

    def __init__(self, root):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        _git(self.root, "init", "-q")
        self._commit_number = 0

    def write(self, path, content):
        target = self.root / path
        if isinstance(content, bytes):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def commit(self):
        self._commit_number += 1
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", f"commit {self._commit_number}")
        return self.sha("HEAD")

    def sha(self, ref):
        result = subprocess.run(["git", "rev-parse", ref], cwd=self.root, capture_output=True,
                                text=True, check=True, timeout=30)
        return result.stdout.strip()

    def changed_files_list(self, base, head):
        result = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
            cwd=self.root, capture_output=True, check=True, timeout=30)
        return result.stdout

    def run(self, base, head, extra=()):
        """Run the script under test against this repository, as CI would (cwd = the checkout)."""
        listing = self.root / "changed-files.nul"
        listing.write_bytes(self.changed_files_list(base, head))
        summary = self.root / "summary.md"
        argv = ["--base-sha", base, "--head-sha", head, "--changed-files", str(listing),
                "--summary-file", str(summary), *extra]
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *argv], cwd=self.root, capture_output=True, text=True,
            encoding="utf-8", timeout=300, stdin=subprocess.DEVNULL)
        return result, summary


BASE_APP = "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a + b\n"  # subtract is broken on base


# ---------------------------------------------------------------------------
# Selection: AST comparison of top-level test functions and Test* class methods
# ---------------------------------------------------------------------------

def test_new_and_changed_top_level_functions_are_selected(checker):
    head = "def test_new():\n    assert True\n\n\ndef test_same():\n    assert 1\n\n\ndef helper():\n    pass\n"
    base = "def test_same():\n    assert 1\n\n\ndef test_moved():\n    assert 2\n\n\ndef helper():\n    return 1\n"
    selected, _ = checker.select_from_sources(base, head)
    assert selected == [(None, "test_new")]


def test_a_changed_body_or_parametrize_decorator_selects_the_function(checker):
    base = "import pytest\n\n\ndef test_one():\n    assert 1\n"
    body_changed = "import pytest\n\n\ndef test_one():\n    assert 2\n"
    selected_body = checker.select_from_sources(base, body_changed)[0]
    assert (None, "test_one") in selected_body

    parametrized = ("import pytest\n\n\n@pytest.mark.parametrize('x', [1, 2])\ndef test_one(x):\n"
                    "    assert x\n")
    selected_param = checker.select_from_sources(base, parametrized)[0]
    assert (None, "test_one") in selected_param


def test_class_test_methods_are_selected_when_new_or_changed(checker):
    """PX475-1 (approved): Test* class methods count, like tests/test_wiki_publish.py's."""
    head = ("class TestPublish:\n    def test_new(self):\n        assert True\n\n"
            "    def test_same(self):\n        assert 1\n\n\nclass NotATest:\n    def test_ignored(self):\n"
            "        assert 1\n")
    base = ("class TestPublish:\n    def test_same(self):\n        assert 1\n\n"
            "    def test_old(self):\n        assert 2\n")
    selected, _ = checker.select_from_sources(base, head)
    assert selected == [("TestPublish", "test_new")]


def test_a_changed_class_method_body_selects_it(checker):
    base = "class TestMath:\n    def test_subtract(self):\n        assert subtract(2, 1) == 1\n"
    head = "class TestMath:\n    def test_subtract(self):\n        assert subtract(2, 1) == 0\n"
    selected, _ = checker.select_from_sources(base, head)
    assert selected == [("TestMath", "test_subtract")]


def test_a_new_file_selects_everything_and_an_unparseable_base_is_all_new(checker):
    head = "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n"
    selected, _ = checker.select_from_sources(None, head)
    assert selected == [(None, "test_a"), (None, "test_b")]
    selected, _ = checker.select_from_sources("def broken(:\n", head)
    assert selected == [(None, "test_a"), (None, "test_b")]


def test_an_unparseable_head_file_is_reported_not_crashed(checker):
    selected, unparseable = checker.select_from_sources("def test_a():\n    assert 1\n",
                                                       "def test_a(:\n")
    assert selected == [] and unparseable is True


def test_edited_non_test_code_selects_nothing(checker):
    """The approved rule: an edit confined to non-test code (a helper, module-level code) selects
    nothing, even inside a changed tests/ file."""
    base = "TOTAL = 1\n\n\ndef test_one():\n    assert TOTAL\n\n\ndef helper():\n    return 1\n"
    head = "TOTAL = 2\n\n\ndef test_one():\n    assert TOTAL\n\n\ndef helper():\n    return 2\n"
    selected, _ = checker.select_from_sources(base, head)
    assert selected == []


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_shas_must_be_forty_hex_characters(checker):
    for base, head in (("", "0" * 40), ("0" * 40, "HEAD"), ("X" * 40, "0" * 40)):
        with pytest.raises(checker.BaseFailInputError):
            checker.parse_shas(base, head)
    assert checker.parse_shas("0" * 40, "1" * 40) == ("0" * 40, "1" * 40)


def test_changed_file_list_is_validated_like_the_scope_checks(checker, tmp_path):
    empty = tmp_path / "empty.nul"
    empty.write_bytes(b"")
    assert checker.read_changed_files(empty) == []

    good = tmp_path / "good.nul"
    good.write_bytes(b"tests/test_a.py\0README.md\0")
    assert checker.read_changed_files(good) == ["tests/test_a.py", "README.md"]

    for name, payload in (("nonul", b"tests/test_a.py"),
                         ("nonutf8", b"tests/\xff\xfe.py\0"),
                         ("absolute", b"/etc/passwd\0"),
                         ("parent", b"tests/../escape.py\0"),
                         ("duplicate", b"tests/test_a.py\0tests/test_a.py\0")):
        bad = tmp_path / f"{name}.nul"
        bad.write_bytes(payload)
        with pytest.raises(checker.BaseFailInputError):
            checker.read_changed_files(bad)


# ---------------------------------------------------------------------------
# JUnit classification: verdicts with the failure kind named
# ---------------------------------------------------------------------------

def _junit(cases, errors=0, suites=()):
    """A minimal pytest junit XML: cases are (classname, name, child) with child in {None, failure, error};
    extra raw suites are appended inside the single root element."""
    body = "".join(
        f'<testcase classname="{classname}" name="{name}">'
        + ("" if child is None else f"<{child} type=\"{child}\">boom</{child}>")
        + "</testcase>"
        for classname, name, child in cases)
    return ("<testsuites>"
            f'<testsuite name="suite" tests="{len(cases)}" failures="0" errors="{errors}" '
            f'time="0.01">{body}</testsuite>'
            + "".join(s.decode("utf-8") for s in suites)
            + "</testsuites>").encode("utf-8")


def test_assertion_failures_are_red_with_the_kind_named(checker):
    selected = {"tests/test_x.py": [(None, "test_new")]}
    xml = _junit([("tests.test_x", "test_new", "failure")])
    rows = checker.classify_junit(xml, selected)
    assert rows == [{"id": "tests/test_x.py::test_new", "runs": 1, "failed": 1, "passed": 0,
                     "kind": "assertion", "category": "RED_ON_BASE"}]


def test_parametrized_runs_are_counted_per_function(checker):
    selected = {"tests/test_x.py": [(None, "test_new")]}
    xml = _junit([("tests.test_x", "test_new[1]", "failure"), ("tests.test_x", "test_new[2]", "failure"),
                  ("tests.test_x", "test_new[3]", None), ("tests.test_x", "test_new[4]", None)])
    rows = checker.classify_junit(xml, selected)
    assert rows[0]["runs"] == 4 and rows[0]["failed"] == 2 and rows[0]["passed"] == 2
    assert rows[0]["category"] == "RED_ON_BASE"


def test_a_collection_error_is_cannot_run_with_the_import_kind(checker):
    selected = {"tests/test_x.py": [(None, "test_new")], "tests/test_y.py": [(None, "test_other")]}
    xml = _junit([], errors=1, suites=(
        b'<testsuite name="suite2"><error type="pytest.errors">ERROR tests/test_x.py\n'
        b"ModuleNotFoundError: No module named 'helpers'</error></testsuite>",))
    rows = checker.classify_junit(xml, selected)
    by_id = {row["id"]: row for row in rows}
    assert by_id["tests/test_x.py::test_new"]["category"] == "CANNOT_RUN"
    assert by_id["tests/test_x.py::test_new"]["kind"] == "import"
    assert by_id["tests/test_y.py::test_other"]["category"] == "CANNOT_RUN"
    assert by_id["tests/test_y.py::test_other"]["kind"] == "no-result"


def test_a_collection_error_naming_a_windows_path_is_still_the_import_kind(checker):
    """CL531-5: pytest on Windows names the failing file with backslashes; the match must not depend on `/`."""
    selected = {"tests/test_x.py": [(None, "test_new")]}
    xml = _junit([], errors=1, suites=(
        b'<testsuite name="suite2"><error type="pytest.errors">ERROR collecting tests\\test_x.py\n'
        b"ImportError while importing test module 'D:\\a\\repo\\tests\\test_x.py'</error></testsuite>",))
    rows = checker.classify_junit(xml, selected)
    assert rows[0]["category"] == "CANNOT_RUN" and rows[0]["kind"] == "import"


def test_class_methods_are_classified_under_their_class(checker):
    selected = {"tests/test_x.py": [("TestMath", "test_new")]}
    xml = _junit([("tests.test_x.TestMath", "test_new", "failure")])
    rows = checker.classify_junit(xml, selected)
    assert rows[0]["id"] == "tests/test_x.py::TestMath::test_new"
    assert rows[0]["category"] == "RED_ON_BASE" and rows[0]["kind"] == "assertion"


def test_pr_verdict_precedence(checker):
    def row(category, **kwargs):
        return {"id": "tests/t.py::test_a", "runs": 1, "failed": 0, "passed": 1,
                "kind": "assertion", "category": category, **kwargs}

    assert checker.pr_verdict([]) == "NO_TESTS_SELECTED"
    assert checker.pr_verdict([row("PASSES_ON_BASE")]) == "PASSES_ON_BASE"
    assert checker.pr_verdict([row("RED_ON_BASE")]) == "RED_ON_BASE"
    assert checker.pr_verdict([row("RED_ON_BASE"), row("CANNOT_RUN")]) == "RED_ON_BASE"
    assert checker.pr_verdict([row("PASSES_ON_BASE"), row("CANNOT_RUN")]) == "CANNOT_RUN"
    assert checker.pr_verdict([row("CANNOT_RUN")]) == "CANNOT_RUN"


# ---------------------------------------------------------------------------
# The report: summary format, escaping, append, row cap
# ---------------------------------------------------------------------------

def _row(**overrides):
    row = {"id": "tests/test_x.py::test_new", "runs": 6, "failed": 6, "passed": 0,
           "kind": "assertion", "category": "RED_ON_BASE"}
    row.update(overrides)
    return row


def test_the_report_carries_the_verdict_the_table_and_the_legend(checker):
    report = checker.build_report([_row()], [])
    assert "### New tests on the base commit (advisory; not a required check)" in report
    assert "Verdict: RED_ON_BASE — 1 selected test function, 1 failed on the base commit (kind: assertion)." in report
    assert "| Test function | Runs | Failed | Passed | Kind |" in report
    assert "| :--- | ---: | ---: | ---: | :--- |" in report
    assert "| tests/test_x.py::test_new | 6 | 6 | 0 | assertion |" in report
    for legend in ("RED_ON_BASE:", "PASSES_ON_BASE:", "CANNOT_RUN:", "NO_TESTS_SELECTED:"):
        assert legend in report


def test_each_verdict_has_its_own_summary_line(checker):
    passing = checker.build_report([_row(category="PASSES_ON_BASE", failed=0, passed=6)], [])
    assert "Verdict: PASSES_ON_BASE — 1 selected test function, all passed on the base commit." in passing
    cannot = checker.build_report([_row(category="CANNOT_RUN", runs=0, failed=0, passed=0,
                                       kind="import")], [])
    assert "Verdict: CANNOT_RUN — 1 selected test function, 1 could not run on the base commit (kind: import)." in cannot
    none = checker.build_report([], [])
    assert "Verdict: NO_TESTS_SELECTED — the PR adds or changes no test function." in none
    assert "| Test function" not in none


def test_the_table_is_capped_and_the_omission_counted(checker):
    rows = [_row(id=f"tests/test_x.py::test_n{i}") for i in range(105)]
    report = checker.build_report(rows, [])
    assert report.count("| tests/test_x.py::test_n") == 100
    assert "5 more rows omitted" in report


def test_paths_are_escaped_as_markdown_not_links(checker):
    report = checker.build_report([_row(id="tests/we`ird|name.py::test_a")], [])
    assert "we\\`ird\\|name" in report


def test_unparseable_files_are_noted(checker):
    report = checker.build_report([], ["tests/broken.py"])
    assert "tests/broken.py could not be parsed at the head commit" in report


# ---------------------------------------------------------------------------
# End to end on synthetic git repositories: every verdict exits 0
# ---------------------------------------------------------------------------

def _base_repo(tmp_path):
    repo = SyntheticRepo(tmp_path / "repo")
    repo.write("app.py", BASE_APP)
    repo.write("tests/test_old.py", "def test_old():\n    assert 1\n")
    repo.write("tests/fixtures/data.json", '{"v": 1}\n')
    return repo, repo.commit()


def test_a_failing_new_test_is_red_on_base(tmp_path):
    repo, base = _base_repo(tmp_path)
    repo.write("app.py", "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n")
    repo.write("tests/test_new.py", "from app import subtract\n\n\ndef test_subtract():\n    assert subtract(2, 1) == 1\n")
    head = repo.commit()
    result, summary = repo.run(base, head)
    assert result.returncode == 0, result.stderr
    assert "Verdict: RED_ON_BASE — 1 selected test function, 1 failed on the base commit (kind: assertion)." in result.stdout
    assert "| tests/test_new.py::test_subtract | 1 | 1 | 0 | assertion |" in result.stdout
    assert "Verdict: RED_ON_BASE" in summary.read_text(encoding="utf-8")


def test_a_passing_new_test_is_passses_on_base_and_summary_only(tmp_path):
    repo, base = _base_repo(tmp_path)
    repo.write("tests/test_new.py", "from app import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    head = repo.commit()
    result, _ = repo.run(base, head)
    assert result.returncode == 0, result.stderr
    assert "Verdict: PASSES_ON_BASE" in result.stdout


def test_a_test_importing_what_base_lacks_is_cannot_run(tmp_path):
    repo, base = _base_repo(tmp_path)
    repo.write("helpers_new.py", "VALUE = 1\n")
    repo.write("tests/test_new.py", "from helpers_new import VALUE\n\n\ndef test_value():\n    assert VALUE\n")
    head = repo.commit()
    result, _ = repo.run(base, head)
    assert result.returncode == 0, result.stderr
    assert "Verdict: CANNOT_RUN" in result.stdout
    assert "kind: import" in result.stdout


def test_a_changed_fixture_is_overlaid_on_the_base_worktree(tmp_path):
    """The head fixture (v=2) is overlaid, so the new test passes on base; without the overlay it
    would read the base fixture (v=1) and fail."""
    repo, base = _base_repo(tmp_path)
    repo.write("tests/fixtures/data.json", '{"v": 2}\n')
    repo.write("tests/test_fixture.py",
               'import json\nfrom pathlib import Path\n\n\ndef test_value():\n'
               '    data = json.loads(Path("tests/fixtures/data.json").read_text(encoding="utf-8"))\n'
               '    assert data["v"] == 2\n')
    head = repo.commit()
    result, _ = repo.run(base, head)
    assert result.returncode == 0, result.stderr
    assert "Verdict: PASSES_ON_BASE" in result.stdout
    assert "tests/test_fixture.py::test_value" in result.stdout


def test_class_based_tests_are_selected_end_to_end(tmp_path):
    """PX475-1: a wiki_publish-style class test file is selected and run on the base."""
    repo, base = _base_repo(tmp_path)
    repo.write("tests/test_class_based.py",
               "from app import add, subtract\n\n\nclass TestMath:\n"
               "    def test_add(self):\n        assert add(2, 2) == 4\n\n"
               "    def test_subtract(self):\n        assert subtract(2, 1) == 1\n")
    head = repo.commit()
    result, _ = repo.run(base, head)
    assert result.returncode == 0, result.stderr
    assert "Verdict: RED_ON_BASE" in result.stdout
    assert "| tests/test_class_based.py::TestMath::test_subtract | 1 | 1 | 0 | assertion |" in result.stdout


def test_a_deleted_test_file_and_non_test_edits_select_nothing(tmp_path):
    repo, base = _base_repo(tmp_path)
    (repo.root / "tests/test_old.py").unlink()
    head = repo.commit()
    result, _ = repo.run(base, head)
    assert result.returncode == 0, result.stderr
    assert "Verdict: NO_TESTS_SELECTED" in result.stdout

    repo2 = SyntheticRepo(tmp_path / "repo2")
    repo2.write("app.py", BASE_APP)
    repo2.write("tests/test_helpers.py", "def helper():\n    return 1\n\n\ndef test_one():\n    assert helper()\n")
    base2 = repo2.commit()
    repo2.write("tests/test_helpers.py", "def helper():\n    return 2\n\n\ndef test_one():\n    assert helper()\n")
    head2 = repo2.commit()
    result2, _ = repo2.run(base2, head2)
    assert result2.returncode == 0, result2.stderr
    assert "Verdict: NO_TESTS_SELECTED" in result2.stdout


def test_paths_outside_tests_are_never_overlaid_or_selected(tmp_path):
    repo, base = _base_repo(tmp_path)
    repo.write("docs/note.md", "# changed outside tests\n")
    repo.write("scripts/thing.py", "X = 1\n")
    repo.write("tests/test_new.py", "def test_new():\n    assert 1\n")
    head = repo.commit()
    result, _ = repo.run(base, head)
    assert result.returncode == 0, result.stderr
    assert "Verdict: PASSES_ON_BASE" in result.stdout
    assert (repo.root / "scripts/thing.py").exists()      # the head tree is untouched; only the report is read


def test_the_report_appends_to_an_existing_summary(tmp_path):
    repo, base = _base_repo(tmp_path)
    repo.write("tests/test_new.py", "def test_new():\n    assert 1\n")
    head = repo.commit()
    listing = repo.root / "changed-files.nul"
    listing.write_bytes(repo.changed_files_list(base, head))
    summary = repo.root / "summary.md"
    summary.write_text("an earlier step's summary\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head,
         "--changed-files", str(listing), "--summary-file", str(summary)],
        cwd=repo.root, capture_output=True, text=True, encoding="utf-8", timeout=300,
        stdin=subprocess.DEVNULL)
    written = summary.read_text(encoding="utf-8")
    assert written.startswith("an earlier step's summary\n")
    assert "Verdict: PASSES_ON_BASE" in written


# ---------------------------------------------------------------------------
# A broken check exits 2 and never looks like a clean result
# ---------------------------------------------------------------------------

def test_a_head_sha_that_is_not_head_exits_two(tmp_path):
    repo, base = _base_repo(tmp_path)
    repo.write("tests/test_new.py", "def test_new():\n    assert 1\n")
    head = repo.commit()
    other = "e" * 40  # a well-formed SHA that is not HEAD
    listing = repo.root / "changed-files.nul"
    listing.write_bytes(repo.changed_files_list(base, head))
    summary = repo.root / "summary.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", other,
         "--changed-files", str(listing), "--summary-file", str(summary)],
        cwd=repo.root, capture_output=True, text=True, encoding="utf-8", timeout=120,
        stdin=subprocess.DEVNULL)
    assert result.returncode == 2
    assert "error:" in result.stderr and "Verdict:" not in result.stdout
    assert "CHECK MALFUNCTION" in summary.read_text(encoding="utf-8")


def test_malformed_input_exits_two_without_a_verdict(tmp_path):
    repo, base = _base_repo(tmp_path)
    repo.write("tests/test_new.py", "def test_new():\n    assert 1\n")
    head = repo.commit()
    for name, payload in (("bad-sha", None), ("nonul", b"tests/test_new.py")):
        listing = repo.root / "changed-files.nul"
        if payload is None:
            argv = ["--base-sha", "nothex", "--head-sha", head, "--changed-files", str(listing)]
        else:
            listing.write_bytes(payload)
            argv = ["--base-sha", base, "--head-sha", head, "--changed-files", str(listing)]
        result = subprocess.run([sys.executable, str(SCRIPT), *argv], cwd=repo.root,
                                capture_output=True, text=True, encoding="utf-8", timeout=120,
                                stdin=subprocess.DEVNULL)
        assert result.returncode == 2, name
        assert "error:" in result.stderr and "Verdict:" not in result.stdout


def test_a_git_failure_exits_two(tmp_path):
    repo, base = _base_repo(tmp_path)
    repo.write("tests/test_new.py", "def test_new():\n    assert 1\n")
    head = repo.commit()                       # the changed path must exist at the head to be selected
    bogus = "f" * 40                           # a well-formed SHA that does not exist in the repository
    listing = repo.root / "changed-files.nul"
    listing.write_bytes(b"tests/test_new.py\0")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--base-sha", bogus, "--head-sha", repo.sha("HEAD"),
         "--changed-files", str(listing)], cwd=repo.root, capture_output=True, text=True,
        encoding="utf-8", timeout=120, stdin=subprocess.DEVNULL)
    assert result.returncode == 2
    assert "error:" in result.stderr and "Verdict:" not in result.stdout


def test_only_a_pytest_internal_error_is_a_malfunction_by_exit_code(checker):
    """Exit 4 with a JUnit file is a result — pytest exits 4 for a selected node id whose file
    cannot be collected on the base (#453's case), and the JUnit carries the collection error. Only
    pytest's internal error (3) is a broken check by exit code alone; a usage error that wrote no
    JUnit at all is caught by the missing-file check in the runner."""
    assert checker.pytest_exit_is_malfunction(3)
    for code in (0, 1, 2, 4, 5):
        assert not checker.pytest_exit_is_malfunction(code)


# ---------------------------------------------------------------------------
# Review fixes (CL531-2, CL531-3, CL531-4)
# ---------------------------------------------------------------------------

def test_an_unparseable_file_alone_never_runs_pytest_and_is_cannot_run(tmp_path, monkeypatch, checker, capsys):
    """CL531-2/CL531-4: with nothing selected, pytest must not be started with no node ids (that would run
    the base's whole suite), and the verdict is CANNOT_RUN, not NO_TESTS_SELECTED."""
    repo, base = _base_repo(tmp_path)
    repo.write("tests/helper_x.py", "def broken(:\n")
    head = repo.commit()
    listing = repo.root / "changed-files.nul"
    listing.write_bytes(repo.changed_files_list(base, head))
    monkeypatch.chdir(repo.root)

    def must_not_run(*args, **kwargs):
        raise AssertionError("pytest must not run when no test function is selected")

    monkeypatch.setattr(checker, "run_selected_tests", must_not_run)
    code = checker.main(["--base-sha", base, "--head-sha", head, "--changed-files", str(listing)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Verdict: CANNOT_RUN — 1 changed test file could not be parsed at the head commit (kind: unparseable)." in out
    assert "NO_TESTS_SELECTED — the PR adds" not in out
    assert "tests/helper_x.py could not be parsed at the head commit" in out


def test_an_unparseable_file_beside_a_passing_test_is_not_an_all_clear(checker):
    rows = [{"id": "tests/test_a.py::test_a", "runs": 1, "failed": 0, "passed": 1,
             "kind": "assertion", "category": "PASSES_ON_BASE"}]
    assert checker.pr_verdict(rows, ["tests/broken.py"]) == "CANNOT_RUN"
    assert checker.pr_verdict(rows) == "PASSES_ON_BASE"
    red = [{**rows[0], "failed": 1, "passed": 0, "category": "RED_ON_BASE"}]
    assert checker.pr_verdict(red, ["tests/broken.py"]) == "RED_ON_BASE"
    line = checker.build_report(rows, ["tests/broken.py"])
    assert ("Verdict: CANNOT_RUN — 1 selected test function, 0 could not run on the base commit; "
            "1 changed test file could not be parsed at the head commit (kind: unparseable).") in line


def test_a_skipped_new_test_is_not_reported_as_passed_on_base(tmp_path):
    """CL531-3: a test that is skipped on the base never executed; it is neither red nor green evidence."""
    repo, base = _base_repo(tmp_path)
    repo.write("tests/test_new.py",
               "import pytest\n\n\n@pytest.mark.skip(reason='not run')\ndef test_skipped():\n    assert False\n")
    head = repo.commit()
    result, _ = repo.run(base, head)
    assert result.returncode == 0, result.stderr
    assert "Verdict: CANNOT_RUN" in result.stdout and "kind: skipped" in result.stdout
    assert "PASSES_ON_BASE" not in result.stdout.split("RED_ON_BASE:")[0]


def test_skipped_cases_of_a_parametrized_test_do_not_count_as_passed(tmp_path):
    repo, base = _base_repo(tmp_path)
    repo.write("tests/test_new.py",
               "import pytest\n\n\n@pytest.mark.parametrize('value', [1, pytest.param(2, marks=pytest.mark.skip)])\n"
               "def test_values(value):\n    assert value\n")
    head = repo.commit()
    result, _ = repo.run(base, head)
    assert result.returncode == 0, result.stderr
    assert "Verdict: PASSES_ON_BASE — 1 selected test function, all passed on the base commit." in result.stdout
    assert "| tests/test_new.py::test_values | 2 | 0 | 1 | assertion |" in result.stdout


# ---------------------------------------------------------------------------
# In-process coverage of the input guards and the broken-check paths (the changed-line floor)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["tests/x\x00.py", "tests/x\u202e.py", "/etc/passwd", "C:/x.py", "../x.py",
                                   "tests/../../x.py", "tests/", ".", "", None, 5])
def test_unsafe_changed_file_paths_are_refused(checker, value):
    with pytest.raises(checker.BaseFailInputError):
        checker.normalize_path(value)


def test_a_path_is_normalised_the_same_way_on_every_platform(checker):
    assert checker.normalize_path("tests\\sub\\.\\test_a.py") == "tests/sub/test_a.py"
    assert checker.normalize_path("tests//test_a.py") == "tests/test_a.py"


def test_a_changed_test_file_that_is_not_utf8_is_a_malformed_input(tmp_path, checker):
    repo, base = _base_repo(tmp_path)
    repo.write("tests/test_bad.py", b"\xff\xfe not utf-8")
    head = repo.commit()
    with pytest.raises(checker.BaseFailInputError):
        checker.select_tests(repo.root, base, head, ["tests/test_bad.py"])


def test_an_error_child_in_the_junit_is_an_import_kind_failure(checker):
    selected = {"tests/test_x.py": [(None, "test_new")]}
    rows = checker.classify_junit(_junit([("tests.test_x", "test_new", "error")]), selected)
    assert rows[0]["category"] == "RED_ON_BASE" and rows[0]["kind"] == "import"


def test_unreadable_junit_xml_is_a_broken_check(checker):
    with pytest.raises(checker.CheckBroken):
        checker.classify_junit(b"<testsuites><oops", {"tests/test_x.py": [(None, "test_new")]})


@pytest.mark.parametrize("behaviour, message", [
    ("timeout", "pytest timed out"),
    ("internal-error", "pytest could not run"),
    ("no-junit", "pytest wrote no JUnit XML"),
])
def test_pytest_failing_to_run_is_a_broken_check_never_a_verdict(tmp_path, monkeypatch, checker, behaviour, message):
    worktree = tmp_path / "base"
    worktree.mkdir()
    monkeypatch.setattr(checker, "read_revision", lambda repo, sha, path: b"def test_new():\n    pass\n")
    monkeypatch.setattr(checker, "_overlay_worktree", lambda *args: (tmp_path, worktree))
    monkeypatch.setattr(checker, "_git", lambda *args: b"")

    def fake_run(argv, **kwargs):
        if behaviour == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 3 if behaviour == "internal-error" else 1, b"", b"")

    monkeypatch.setattr(checker.subprocess, "run", fake_run)
    with pytest.raises(checker.CheckBroken, match=message):
        checker.run_selected_tests(tmp_path, "a" * 40, "b" * 40, {"tests/test_x.py": [(None, "test_new")]},
                                   ["tests/test_x.py"])


def test_a_summary_file_that_cannot_be_written_does_not_hide_the_malfunction(tmp_path, checker):
    checker._malfunction_summary(str(tmp_path / "missing-directory" / "summary.md"))   # must not raise
    checker._malfunction_summary(None)

