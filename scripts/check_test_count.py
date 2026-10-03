"""Compares pytest's reported passed-test count against the checked-in
badges/test-count.json -- CI's code-enforced guard against that count going
stale in README.md/CLAUDE.md/a downstream consumer (e.g. a portfolio site's
live-stat badge) every time the suite grows, the same "code computes, don't
trust a hand-maintained number" discipline as policy_engine.py, just pointed
at this project's own stats.

`--write` (issue #147) is the one-command way to bring the file up to date by hand: it rewrites
the badge from the same captured pytest output instead of failing. It is for a developer's own
checkout (after rebasing a PR whose test count changed); CI only ever runs the plain check, so
nothing automated ever edits the file.

No `anthropic` dependency, matching template_resolver.py/state_manager.py's
pattern. Deliberately does not run pytest itself -- it parses an already-
captured pytest output file, so CI's "Run test suite" step (which must pass
regardless) and this check stay independent and the suite never runs twice.
"""
import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_BADGE_PATH = Path(__file__).resolve().parent.parent / "badges" / "test-count.json"

PASSED_RE = re.compile(r"(\d+) passed")
# A summary clause that means the run was not green. (`xfailed` / `xpassed` are not matched: the digit is
# directly followed by "failed"/"error" only for real failures.)
FAILURE_RE = re.compile(r"\b\d+ (?:failed|errors?)\b")


def parse_passed_count(pytest_output):
    """Extract the passed-test count from pytest's own summary line.

    Matches e.g. "430 passed in 16.27s" or "428 passed, 2 skipped in 12.34s"
    -- pytest always reports the passed count first among the summary's
    comma-separated clauses when there's more than one.
    """
    matches = PASSED_RE.findall(pytest_output)
    if not matches:
        raise ValueError('pytest output has no "N passed" summary line -- did the suite fail entirely?')
    return int(matches[-1])  # the summary is the last such line; earlier text may mention "N passed"


def read_pytest_output(path):
    """The captured pytest output as text. UTF-8 (with or without a BOM) is the norm; a UTF-16 file
    (what Windows PowerShell 5.1 can produce for `tee`/`>`) is recognised by its byte-order mark."""
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def read_badge_count(badge_path=None):
    path = Path(badge_path) if badge_path else DEFAULT_BADGE_PATH
    value = json.loads(path.read_text(encoding="utf-8"))["passed"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'"passed" in {path} must be an integer, not {value!r}')
    return value


def write_badge_count(count, badge_path=None):
    """Rewrite the badge file to `count`, in the file's canonical shape. Returns the previous count
    (None if the file did not exist or could not be read)."""
    path = Path(badge_path) if badge_path else DEFAULT_BADGE_PATH
    try:
        previous = read_badge_count(path)
    except (OSError, ValueError, KeyError, TypeError):
        previous = None
    path.write_text(json.dumps({"passed": int(count)}, indent=2) + "\n", encoding="utf-8", newline="\n")
    return previous


def check(pytest_output, badge_path=None):
    """Returns (matches, actual, expected)."""
    actual = parse_passed_count(pytest_output)
    expected = read_badge_count(badge_path)
    return actual == expected, actual, expected


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare pytest's reported passed-test count against "
                     "badges/test-count.json. Fails (exit 1) on a mismatch -- "
                     "never auto-corrects the file itself; whoever's PR changed "
                     "the test count must update badges/test-count.json in that "
                     "same PR (see --write).",
        allow_abbrev=False,  # `--wri` must not quietly mean `--write`
    )
    parser.add_argument("pytest_output_file", help="Path to a file containing pytest's captured stdout")
    parser.add_argument("--badge-path", default=None, help="Override badges/test-count.json path (for tests)")
    parser.add_argument("--write", action="store_true",
                        help="rewrite the badge file to pytest's reported count instead of failing on a "
                             "mismatch (for a developer's checkout; CI never passes this)")
    args = parser.parse_args(argv)

    badge_path = Path(args.badge_path) if args.badge_path else DEFAULT_BADGE_PATH
    try:
        output_text = read_pytest_output(args.pytest_output_file)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Cannot read pytest output {args.pytest_output_file}: {type(exc).__name__}: {exc}")
        return 1

    if args.write:
        if FAILURE_RE.search(output_text):
            print("Refusing to --write: the pytest output reports failed or errored tests, so its passed "
                  f"count is not a green baseline. Fix the suite, rerun pytest, then run --write. {badge_path} "
                  "is unchanged.")
            return 1
        try:
            actual = parse_passed_count(output_text)
        except ValueError as exc:
            print(f"Cannot read a passed count from the pytest output: {exc}")
            return 1
        try:
            previous = write_badge_count(actual, args.badge_path)
        except OSError as exc:
            print(f"Cannot write {badge_path}: {type(exc).__name__}: {exc}")
            return 1
        if previous == actual:
            print(f"{badge_path} already says {actual} passed; nothing to change.")
        else:
            print(f"Updated {badge_path}: {previous} -> {actual} passed. Commit it with your change.")
        return 0

    try:
        matches, actual, expected = check(output_text, args.badge_path)
    except (ValueError, KeyError, TypeError, OSError) as exc:  # no "N passed" line, or a missing/malformed badge
        print(f"Cannot compare test counts: {type(exc).__name__}: {exc}")
        return 1

    if not matches:
        print(
            f"Test count mismatch: pytest reported {actual} passed, but "
            f"{badge_path} declares {expected}. Update {badge_path} in this "
            f"PR to match (run: python scripts/check_test_count.py <pytest output file> --write)."
        )
        return 1

    print(f"Test count matches {badge_path} ({actual} passed).")
    return 0


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    sys.exit(main())
