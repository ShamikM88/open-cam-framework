"""Deterministic advisory check: do the tests a pull request adds or changes fail on the base commit?

A reviewer aid, never a gate (issue #475, option C, PR 1 of 3). The script selects the test functions
and Test* class methods the pull request adds or changes (an AST comparison of each changed tests/*.py
between the base and the head, decorators included, so a changed parametrization counts), overlays
the pull request's changed tests/ paths onto a git worktree of the base commit, runs exactly those
tests there and reports one of four verdicts with the failure kind named. It runs from the pull
request's own merge commit, so the report is evidence for a reviewer and not a proof.

Every verdict exits 0; the script exits 2 only when the check itself is broken, because a broken
check must not look like a clean result. Nothing outside tests/ is ever overlaid or selected, and the
script itself imports nothing from the repository: it drives git and pytest as subprocesses and reads
the JUnit XML pytest writes. Only a broken check exits 2, and it must never look like a clean result.
"""
import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TEST_NAME_RE = re.compile(r"^test")
TEST_CLASS_RE = re.compile(r"^Test")
DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
MAX_TABLE_ROWS = 100
PYTEST_TIMEOUT_SECONDS = 600
MALFUNCTION_EXIT = 2


class BaseFailInputError(ValueError):
    """An input is malformed or contains an unsafe repository path."""


class CheckBroken(Exception):
    """The check itself is broken (git, the worktree, pytest, the JUnit XML); never a verdict."""


def _contains_control(value):
    return any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value)


def normalize_path(value):
    """Normalize a repo-relative path identically on Windows and POSIX; reject anything unsafe."""
    if not isinstance(value, str) or not value or _contains_control(value):
        raise BaseFailInputError("unsafe path input")
    raw = value.replace("\\", "/")
    if raw.startswith("/") or DRIVE_PREFIX.match(raw):
        raise BaseFailInputError("unsafe path input")
    if raw.endswith("/"):
        raise BaseFailInputError("changed-file list contains a directory path")
    components = []
    for component in raw.split("/"):
        if component in {"", "."}:
            continue
        if component == "..":
            raise BaseFailInputError("unsafe path input")
        components.append(component)
    if not components:
        raise BaseFailInputError("empty path input")
    return "/".join(components)


def read_changed_files(path):
    """Read Git's UTF-8 NUL-delimited --name-only output; an empty file is valid."""
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BaseFailInputError("changed-file list is not UTF-8") from exc
    if not text:
        return []
    if not text.endswith("\0"):
        raise BaseFailInputError("malformed changed-file list")
    raw_paths = text[:-1].split("\0")
    paths = [normalize_path(path) for path in raw_paths]
    if len(set(paths)) != len(paths):
        raise BaseFailInputError("duplicate changed-file path")
    return paths


def parse_shas(base_sha, head_sha):
    """Both inputs are full 40-hex commit SHAs, or the input is malformed."""
    for sha in (base_sha, head_sha):
        if not SHA_RE.match(sha):
            raise BaseFailInputError("malformed commit SHA")
    return base_sha, head_sha


def is_tests_path(path):
    return path == "tests" or path.startswith("tests/")


# ---------------------------------------------------------------------------
# Selection: AST comparison of top-level test functions and Test* class methods
# ---------------------------------------------------------------------------

def parse_test_units(source):
    """{(class or None, function name): ast.dump of the def, decorators included} for a test file.

    Top-level `def test*` functions and `def test*` methods of top-level `Test*` classes are the
    selectable units (the repository's one class-based test file, tests/test_wiki_publish.py,
    is covered by the class rule). None means the source is unparseable at this revision: the
    caller reports that file as unparseable rather than selecting or crashing.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    units = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and TEST_NAME_RE.match(node.name):
            units[(None, node.name)] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.ClassDef) and TEST_CLASS_RE.match(node.name):
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and TEST_NAME_RE.match(child.name):
                    units[(node.name, child.name)] = ast.dump(child, include_attributes=False)
    return units


def select_from_sources(base_source, head_source):
    """(selected units, head unparseable). A unit is selected when it is new, or when its AST
    (decorators included, so a changed parametrization counts) differs between base and head.

    An edit confined to non-test code selects nothing: the check's question is whether the tests
    this pull request adds or changes fail on the base, not whether anything the pull request
    touched is exercised. A base that does not exist or cannot be parsed counts as all-new.
    """
    head_units = parse_test_units(head_source)
    if head_units is None:
        return [], True
    base_units = {} if base_source is None else (parse_test_units(base_source) or {})
    selected = [unit for unit, dump in head_units.items()
                if unit not in base_units or base_units[unit] != dump]
    selected.sort()
    return selected, False


def _node_id(path, unit):
    class_name, function_name = unit
    if class_name is None:
        return f"{path}::{function_name}"
    return f"{path}::{class_name}::{function_name}"


# ---------------------------------------------------------------------------
# The git worktree of the base, with the pull request's changed tests/ paths overlaid
# ---------------------------------------------------------------------------

def _git(repo, *args):
    """Run git in the checkout; a failure is a broken check, never a verdict."""
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, timeout=60)  # noqa: S603, S607
    if result.returncode != 0:
        raise CheckBroken("git refused a command")
    return result.stdout


def read_revision(repo, sha, path):
    """A file's bytes at a revision, or None when the path does not exist there (a deleted file)."""
    result = subprocess.run(["git", "show", f"{sha}:{path}"],  # noqa: S603, S607
                            cwd=str(repo), capture_output=True, timeout=60)
    if result.returncode != 0:
        return None
    return result.stdout


def select_tests(repo, base_sha, head_sha, paths):
    """Walk the changed tests/ paths and return (selection, overlay paths, unparseable files).

    The selection maps each changed tests/*.py to its new or changed units. Every changed tests/
    path that still exists at the head is overlaid on the base worktree — test files, fixtures and
    conftest.py alike, because a test and its fixture change are one unit. A path deleted at the
    head selects nothing and is not overlaid: no selected test can reference it.
    """
    selection = {}
    unparseable = []
    overlay = []
    for path in paths:
        if not is_tests_path(path):
            continue
        head_source = read_revision(repo, head_sha, path)
        if head_source is None:
            continue
        overlay.append(path)
        if not path.endswith(".py"):
            continue
        try:
            text = head_source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BaseFailInputError("changed test file is not UTF-8") from exc
        base_source = read_revision(repo, base_sha, path)
        base_text = None if base_source is None else base_source.decode("utf-8", errors="replace")
        selected, head_broken = select_from_sources(base_text, text)
        if head_broken:
            unparseable.append(path)
        elif selected:
            selection[path] = selected
    return selection, overlay, unparseable


def _overlay_worktree(repo, base_sha, overlay, paths_to_sources):
    """Create a detached worktree of the base and copy the head versions of the changed tests/
    paths into it. Nothing outside tests/ is ever written into the worktree."""
    tmp_root = Path(tempfile.mkdtemp(prefix="base-fail-"))
    worktree = tmp_root / "base"
    try:
        _git(repo, "worktree", "add", "--detach", str(worktree), base_sha)
        for path in overlay:
            target = worktree / Path(*path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(paths_to_sources[path])
    except (OSError, CheckBroken):
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise
    return tmp_root, worktree


def pytest_exit_is_malfunction(code):
    """pytest's own contract: 0/1/2/5 are results (all passed, tests failed, collection errors,
    nothing collected), and so is 4 when the JUnit XML is there — pytest exits 4 for a selected node
    id whose file cannot be collected on the base, which is exactly the PR's test importing a module
    the base lacks (#453's case) and is reported from the JUnit's collection error. Only 3, pytest's
    internal error, is a broken check by its exit code alone; a usage error that wrote no JUnit at
    all is caught by the missing-file check in the caller."""
    return code == 3


def run_selected_tests(repo, base_sha, head_sha, selection, overlay):
    """Run the selected node IDs with pytest inside the base worktree; return the JUnit XML bytes.

    The JUnit file is the interface: it names every run of every selected function, including each
    parametrized case, and distinguishes failures (assertions) from errors (imports).
    """
    node_ids = [_node_id(path, unit) for path, units in sorted(selection.items()) for unit in units]
    paths_to_sources = {path: read_revision(repo, head_sha, path) for path in overlay}
    tmp_root, worktree = _overlay_worktree(repo, base_sha, overlay, paths_to_sources)
    xml_path = tmp_root / "junit.xml"
    try:
        try:
            completed = subprocess.run(  # noqa: S603 - a fixed argument list, like every CLI in this repository
                [sys.executable, "-m", "pytest", f"--junitxml={xml_path}", "-p", "no:cacheprovider",
                 "-q", *node_ids], cwd=str(worktree), capture_output=True,
                timeout=PYTEST_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise CheckBroken("pytest timed out") from exc
        if pytest_exit_is_malfunction(completed.returncode):
            raise CheckBroken("pytest could not run")
        if not xml_path.exists():
            raise CheckBroken("pytest wrote no JUnit XML")
        return xml_path.read_bytes()
    finally:
        _git(repo, "worktree", "remove", "--force", str(worktree))
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# JUnit classification and the verdicts
# ---------------------------------------------------------------------------

def _classname(path, unit):
    module = path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")
    class_name, _ = unit
    return module if class_name is None else f"{module}.{class_name}"


def classify_junit(xml_bytes, selection):
    """One row per selected unit: its run count, its failures, its passes and its failure kind.

    A JUnit `failure` child is an assertion failure (the test ran and its assertion failed); an
    `error` child is an error (an import or fixture the run could not provide). A unit with no
    test cases at all could not run: kind `import` when its file failed collection, `no-result`
    otherwise. Parametrized runs are counted per function — one unit, many cases.
    """
    try:
        # The XML is pytest's own JUnit output from this run, written by pytest with escaped names;
        # the script never resolves entities or external documents (S314).
        root = ET.fromstring(xml_bytes)  # noqa: S314  # nosec B314 - pytest's own JUnit output (bandit's code for S314)
    except ET.ParseError as exc:
        raise CheckBroken("unreadable JUnit XML") from exc
    cases = [case for suite in root.iter("testsuite") for case in suite.iter("testcase")]
    collection_error_files = set()
    for suite in root.iter("testsuite"):
        for error in suite.iter("error"):
            for match in re.finditer(r"tests/[A-Za-z0-9_./-]+\.py", "".join(error.itertext())):
                collection_error_files.add(match.group(0))
    rows = []
    for path, units in sorted(selection.items()):
        for unit in units:
            function_name = unit[1]
            matched = [case for case in cases
                       if case.get("classname") == _classname(path, unit)
                       and (case.get("name") == function_name
                            or (case.get("name") or "").startswith(function_name + "["))]
            runs = failed = passed = 0
            kinds = set()
            for case in matched:
                runs += 1
                if case.find("failure") is not None:
                    failed += 1
                    kinds.add("assertion")
                elif case.find("error") is not None:
                    failed += 1
                    kinds.add("import")
                elif case.find("skipped") is not None:
                    continue  # never executed: counted as a run, neither red nor green evidence (CL531-3)
                else:
                    passed += 1
            if runs == 0:
                kind = "import" if path in collection_error_files else "no-result"
                rows.append({"id": _node_id(path, unit), "runs": 0, "failed": 0, "passed": 0,
                             "kind": kind, "category": "CANNOT_RUN"})
            elif not failed and not passed:
                rows.append({"id": _node_id(path, unit), "runs": runs, "failed": 0, "passed": 0,
                             "kind": "skipped", "category": "CANNOT_RUN"})
            else:
                category = "RED_ON_BASE" if failed else "PASSES_ON_BASE"
                rows.append({"id": _node_id(path, unit), "runs": runs, "failed": failed,
                             "passed": passed, "kind": ", ".join(sorted(kinds)) or "assertion",
                             "category": category})
    return rows


def pr_verdict(rows, unparseable=()):
    """The pull-request-level verdict from the per-unit rows: red evidence first, then a check that
    could not fully run (a changed test file that does not parse at the head counts), then all-clear.
    No rows and no unparseable file means nothing was selected."""
    if any(row["category"] == "RED_ON_BASE" for row in rows):
        return "RED_ON_BASE"
    if unparseable or any(row["category"] == "CANNOT_RUN" for row in rows):
        return "CANNOT_RUN"
    if not rows:
        return "NO_TESTS_SELECTED"
    return "PASSES_ON_BASE"


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _escape_markdown(text):
    """Keep a displayed identifier as inert Markdown text, never a link or HTML. A node id's own
    punctuation (`::`, `_`, `-`) is left readable; only characters that could break a table cell,
    form a link or an image, or open HTML are escaped."""
    replacements = {
        "&": "&amp;", "<": "&lt;", ">": "&gt;",
        "`": "\\`", "*": "\\*", "[": "\\[", "]": "\\]", "|": "\\|",
        "!": "\\!", "#": "\\#", "~": "\\~", "+": "\\+",
    }
    return "".join(replacements.get(char, char) for char in text)


LEGEND = (
    "RED_ON_BASE: the tests fail without the PR's change — the red-first evidence.",
    "PASSES_ON_BASE: no red-on-base evidence; expected for refactors, coverage and documentation PRs.",
    "CANNOT_RUN: the tests could not run on the base (an import the base lacks; expected when the PR adds a module).",
    "NO_TESTS_SELECTED: the PR adds or changes no test function.",
)

_HEADING = "### New tests on the base commit (advisory; not a required check)"


def _verdict_line(verdict, rows, unparseable=()):
    selected = len(rows)
    plural = "s" if selected != 1 else ""
    if verdict == "NO_TESTS_SELECTED":
        return "Verdict: NO_TESTS_SELECTED — the PR adds or changes no test function."
    if verdict == "PASSES_ON_BASE":
        return f"Verdict: PASSES_ON_BASE — {selected} selected test function{plural}, all passed on the base commit."
    if verdict == "CANNOT_RUN":
        blocked = sum(1 for row in rows if row["category"] == "CANNOT_RUN")
        kinds = {row["kind"] for row in rows if row["category"] == "CANNOT_RUN"}
        parts = []
        if rows:
            parts.append(f"{selected} selected test function{plural}, {blocked} could not run on the base commit")
        if unparseable:
            parts.append(f"{len(unparseable)} changed test file{'s' if len(unparseable) != 1 else ''} "
                         "could not be parsed at the head commit")
            kinds.add("unparseable")
        return f"Verdict: CANNOT_RUN — {'; '.join(parts)} (kind: {', '.join(sorted(kinds))})."
    red = sum(1 for row in rows if row["failed"])
    kinds = ", ".join(sorted({row["kind"] for row in rows if row["failed"]}))
    return (f"Verdict: RED_ON_BASE — {selected} selected test function{plural}, "
            f"{red} failed on the base commit (kind: {kinds}).")


def build_report(rows, unparseable):
    """The stable advisory Markdown report: heading, verdict, one row per selected unit, the legend."""
    verdict = pr_verdict(rows, unparseable)
    report = [_HEADING, "", _verdict_line(verdict, rows, unparseable)]
    if rows:
        report.extend((
            "",
            "| Test function | Runs | Failed | Passed | Kind |",
            "| :--- | ---: | ---: | ---: | :--- |",
        ))
        for row in rows[:MAX_TABLE_ROWS]:
            report.append("| {} | {} | {} | {} | {} |".format(
                _escape_markdown(row["id"]), row["runs"], row["failed"], row["passed"], row["kind"]))
        omitted = len(rows) - MAX_TABLE_ROWS
        if omitted > 0:
            report.append(f"{omitted} more rows omitted")
    for path in unparseable:
        report.append("")
        report.append(f"{_escape_markdown(path)} could not be parsed at the head commit; its tests are CANNOT_RUN.")
    report.append("")
    report.extend(LEGEND)
    return "\n".join(report) + "\n"


def _append_summary(path, text):
    summary = Path(path)
    with summary.open("a", encoding="utf-8") as destination:
        if summary.stat().st_size > 0:
            destination.write("\n")     # job summaries may already hold earlier steps' text
        destination.write(text)


def _malfunction_summary(path):
    """Best effort: a broken check must never look like a clean result in the job summary either."""
    if not path:
        return
    try:
        _append_summary(path, "CHECK MALFUNCTION: the base-fail check could not run.\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report whether the tests a pull request adds or changes fail on the base "
                    "commit (advisory only; never a gate).")
    parser.add_argument("--base-sha", required=True, metavar="SHA",
                        help="the pull request's base commit, 40 hexadecimal characters")
    parser.add_argument("--head-sha", required=True, metavar="SHA",
                        help="the merge commit the workflow checked out, 40 hexadecimal characters")
    parser.add_argument("--changed-files", required=True, metavar="PATH",
                        help="UTF-8 NUL-delimited file list from git diff --name-only -z")
    parser.add_argument("--summary-file", metavar="PATH",
                        help="append the same report to a Markdown summary file")
    args = parser.parse_args(argv)

    try:
        base_sha, head_sha = parse_shas(args.base_sha, args.head_sha)
        paths = read_changed_files(args.changed_files)
        repo = Path.cwd()
        if _git(repo, "rev-parse", "HEAD").decode("utf-8").strip() != head_sha:
            raise CheckBroken("HEAD is not the requested head commit")
        selection, overlay, unparseable = select_tests(repo, base_sha, head_sha, paths)
        if not selection:   # nothing to run: never start pytest with no node ids (it would run the whole suite)
            report = build_report([], unparseable)
        else:
            xml_bytes = run_selected_tests(repo, base_sha, head_sha, selection, overlay)
            rows = classify_junit(xml_bytes, selection)
            report = build_report(rows, unparseable)
        if args.summary_file:
            _append_summary(args.summary_file, report)
        print(report, end="")
        return 0
    except (OSError, UnicodeError, BaseFailInputError, CheckBroken) as exc:
        # Deliberately omit raw path and SHA text from diagnostics: the changed-file list is
        # pull-request-controlled data and never echoed.
        _malfunction_summary(args.summary_file)
        print(f"error: the check could not run ({type(exc).__name__})", file=sys.stderr)
        return MALFUNCTION_EXIT


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    raise SystemExit(main())
