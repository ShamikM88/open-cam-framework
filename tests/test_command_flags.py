"""Every `--flag` a document tells Claude (or a person) to pass must exist in that script (issue #144).

The slash commands in `.claude/commands/*.md` are prompts that run `python scripts/<name>.py --flag ...` in a
Bash step, and nothing in CI runs those commands. If a flag is renamed or removed, the command breaks silently the
next time someone uses it. This test scans the command files and the documentation for such invocations and checks
each flag against the script's real `--help` output, naming the file and line when one is missing.

It reads invocations the way a person writes them in Markdown:
- inline code (`python scripts/x.py --a --b`) -- flags up to the closing backtick;
- fenced blocks, including a command continued over several lines with a trailing backslash;
- stopping at a pipe, `;`, `&&` or a redirect, and ignoring anything inside quotes.
"""
import re
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

INVOCATION_RE = re.compile(r"python3?\s+(?:\./)?scripts/([A-Za-z0-9_]+)\.py")
FLAG_RE = re.compile(r"(?<![\w-])(--[A-Za-z][\w-]*)")
QUOTED_RE = re.compile(r'"[^"]*"|\'[^\']*\'')
STOP_RE = re.compile(r"\||;|&&|\s>\s|\d?>&?\d*\s")
OPTION_LINE_RE = re.compile(r"^ {2}(?:-\w(?: \S+)?, )?(--[\w-]+)", re.M)


def documents():
    """The Markdown files that may tell someone to run a script."""
    paths = sorted((REPO_ROOT / ".claude" / "commands").glob("*.md"))
    for name in ("README.md", "CLAUDE.md"):
        paths.append(REPO_ROOT / name)
    for pattern in ("evals/*.md", "templates/*.md", "config/*.md", "agents/*.md"):
        paths.extend(sorted(REPO_ROOT.glob(pattern)))
    return [p for p in paths if p.is_file()]


def extract_invocations(text):
    """[(line_number, script_name, [flags])] for every `python scripts/<name>.py ...` in `text`."""
    lines = text.splitlines()
    found = []
    for index, line in enumerate(lines):
        for match in INVOCATION_RE.finditer(line):
            inline = line[:match.start()].count("`") % 2 == 1
            rest = line[match.end():]
            if inline:
                rest = rest.split("`", 1)[0]
            else:
                cursor = index
                while rest.rstrip().endswith("\\") and cursor + 1 < len(lines):
                    cursor += 1
                    rest = rest.rstrip()[:-1] + " " + lines[cursor].strip()
            rest = QUOTED_RE.sub(" ", rest)
            stop = STOP_RE.search(rest)
            if stop:
                rest = rest[:stop.start()]
            found.append((index + 1, match.group(1), FLAG_RE.findall(rest)))
    return found


def parse_options(help_text):
    """The long options an argparse `--help` lists (the option lines, not flags mentioned in descriptions)."""
    return set(OPTION_LINE_RE.findall(help_text))


@cache
def real_options(script):
    path = SCRIPTS_DIR / f"{script}.py"
    if not path.is_file():
        return None
    result = subprocess.run([sys.executable, str(path), "--help"], capture_output=True, text=True, encoding="utf-8",
                            timeout=60)
    assert result.returncode == 0, f"{script}.py --help failed: {result.stderr}"
    return parse_options(result.stdout)


def violations(document_name, text, options_for):
    """Human-readable problems: a script that does not exist, or a flag its --help does not list."""
    problems = []
    for line_number, script, flags in extract_invocations(text):
        known = options_for(script)
        if known is None:
            problems.append(f"{document_name}:{line_number}: scripts/{script}.py does not exist")
            continue
        for flag in flags:
            if flag not in known:
                problems.append(f"{document_name}:{line_number}: python scripts/{script}.py uses {flag}, which "
                                f"`{script}.py --help` does not list (it lists: {', '.join(sorted(known))})")
    return problems


# ---------------------------------------------------------------------------
# The real documents
# ---------------------------------------------------------------------------

def test_every_flag_a_document_passes_to_a_script_exists_in_that_script():
    problems, invocations = [], 0
    for path in documents():
        text = path.read_text(encoding="utf-8")
        invocations += len(extract_invocations(text))
        problems += violations(str(path.relative_to(REPO_ROOT)), text, real_options)
    assert invocations >= 40, f"only {invocations} script invocations found; the extractor has stopped working"
    assert not problems, "\n".join(problems)


def test_the_scan_covers_the_command_files_and_the_docs_that_mention_scripts():
    names = {str(p.relative_to(REPO_ROOT)).replace("\\", "/") for p in documents()}
    assert ".claude/commands/spread.md" in names and ".claude/commands/assemble.md" in names
    assert {"README.md", "CLAUDE.md", "evals/README.md"} <= names


def test_the_real_help_parser_finds_the_known_options():
    assert {"--dry-run", "--live", "--allow-partial", "--export-baseline", "--help"} <= real_options("run_evals")
    assert {"--no-update-financials-source", "--stress-assumptions"} <= real_options("spreading_check")
    assert {"--check-sources", "--step", "--file"} <= real_options("source_manifest")
    assert real_options("no_such_script") is None


# ---------------------------------------------------------------------------
# The extractor and the check, on synthetic Markdown
# ---------------------------------------------------------------------------

def _options(known):
    return lambda script: known.get(script)


def test_inline_code_flags_stop_at_the_closing_backtick():
    text = "Run `python scripts/state_manager.py --check-steps --company X` then pass --not-a-flag-here.\n"
    assert extract_invocations(text) == [(1, "state_manager", ["--check-steps", "--company"])]


def test_a_fenced_command_continued_with_backslashes_is_read_as_one_command():
    text = ("```bash\n"
            "python scripts/source_manifest.py --company \"Acme\" --proposal \"Loan\" \\\n"
            "    --step triage --claim \"two --words\" \\\n"
            "    --file x.pdf --url https://example.invalid/a--b\n"
            "```\n")
    assert extract_invocations(text) == [
        (2, "source_manifest", ["--company", "--proposal", "--step", "--claim", "--file", "--url"])]


def test_pipes_redirects_and_quoted_text_are_not_flags_of_the_script():
    text = ("python scripts/policy_check.py --company A --proposal B | python -m json.tool --indent 2\n"
            "python scripts/run_evals.py --dry-run > out.txt --not-ours\n"
            "python scripts/x.py --a && echo --b ; echo --c\n")
    assert [flags for _, _, flags in extract_invocations(text)] == [["--company", "--proposal"], ["--dry-run"], ["--a"]]


def test_two_invocations_on_one_line_are_separate():
    text = "Use `python scripts/a.py --one` or `python scripts/b.py --two`.\n"
    assert extract_invocations(text) == [(1, "a", ["--one"]), (1, "b", ["--two"])]


def test_text_without_an_invocation_yields_nothing():
    assert extract_invocations("Use the --write flag of check_test_count.py. Run pytest --cov.\n") == []


def test_a_renamed_or_removed_flag_is_reported_with_its_file_and_line():
    text = "intro\n`python scripts/spreading_check.py --company A --old-flag-name`\n"
    problems = violations("cmd.md", text, _options({"spreading_check": {"--company", "--proposal", "--help"}}))
    assert len(problems) == 1
    assert problems[0].startswith("cmd.md:2:") and "--old-flag-name" in problems[0]


def test_a_missing_script_is_reported():
    problems = violations("cmd.md", "`python scripts/gone.py --x`\n", _options({}))
    assert problems == ["cmd.md:1: scripts/gone.py does not exist"]


def test_flags_that_do_exist_are_clean():
    text = "`python scripts/state_manager.py --check-steps --company A`\n"
    assert violations("d.md", text, _options({"state_manager": {"--check-steps", "--company", "--help"}})) == []


def test_option_lines_are_read_but_flags_mentioned_in_descriptions_are_not():
    help_text = ("usage: x.py [-h] [--real]\n\noptions:\n  -h, --help  show this help\n"
                 "  --real REAL  like --mentioned in prose\n  -x, --short-too  a flag with a short form\n")
    assert parse_options(help_text) == {"--help", "--real", "--short-too"}
