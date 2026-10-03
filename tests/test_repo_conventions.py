"""Repository-wide source conventions that a linter cannot express (issue #142).

`# pragma: no cover` hides code from the coverage gate, so each use must carry a reason on the same line
(`# pragma: no cover - <why this is unreachable or untestable>`). A bare pragma would let the percentage be
gamed without anyone being able to tell why.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PRAGMA_RE = re.compile(r"#\s*pragma:\s*no\s+cover\b(?P<rest>.*)$")
REASON_RE = re.compile(r"^\s*[-:—]\s*\S{3,}")


def pragma_violations(text):
    """(line_number, line) for each `pragma: no cover` without a `- reason` after it."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = PRAGMA_RE.search(line)
        if match and not REASON_RE.match(match.group("rest")):
            found.append((number, line.strip()))
    return found


def test_every_pragma_no_cover_in_scripts_carries_a_reason():
    problems = []
    for path in sorted((REPO_ROOT / "scripts").glob("*.py")):
        for number, line in pragma_violations(path.read_text(encoding="utf-8")):
            problems.append(f"{path.name}:{number}: {line}")
    assert not problems, ("`# pragma: no cover` needs a reason on the same line "
                          "(`# pragma: no cover - why`):\n" + "\n".join(problems))


@pytest.mark.parametrize("line, ok", [
    ("x = 1  # pragma: no cover - only reachable on Windows", True),
    ("x = 1  # pragma: no cover: platform specific", True),
    ("x = 1  # pragma: no cover — defensive, unreachable", True),
    ("x = 1  # pragma: no cover", False),
    ("x = 1  # pragma: no cover  ", False),
    ("x = 1  # pragma: no cover -", False),
    ("x = 1  # pragma: no cover - a", False),
    ("x = 1  # an ordinary comment", True),
])
def test_the_pragma_checker_accepts_a_reason_and_rejects_a_bare_pragma(line, ok):
    assert (pragma_violations(line) == []) is ok
