"""Tests for textio.configure_stdio() (issue #154): a Windows script whose
stdout is redirected or piped encodes it with the locale encoding (cp1252), so
printing a character cp1252 lacks (a >= sign in the Risk Reviewer's notes, a
letter in a company name) raised UnicodeEncodeError and aborted the run after
its verdict was already decided.

These tests build a cp1252 stdout on any platform (an in-memory text wrapper,
or a child process started with PYTHONIOENCODING=cp1252), so the failure is
reproduced on Linux CI too -- not only on a Windows machine.
"""
import ast
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import textio

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
NON_CP1252 = "DSCR ≥ 1.25x → breach, Łukasz"  # >=, ->, and a letter cp1252 lacks


def _cp1252_stream(errors="strict"):
    # newline="\n": no "\r\n" translation, so the bytes assertions hold on Windows too.
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors=errors, newline="\n", write_through=True)


def test_the_failure_mechanism_a_cp1252_stream_cannot_print_these_characters():
    stream = _cp1252_stream()
    with pytest.raises(UnicodeEncodeError):
        print(NON_CP1252, file=stream)


def test_configure_stdio_makes_a_cp1252_stdout_and_stderr_utf8(monkeypatch):
    out, err = _cp1252_stream(), _cp1252_stream(errors="backslashreplace")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    textio.configure_stdio()
    print(NON_CP1252)
    print(NON_CP1252, file=sys.stderr)

    assert out.buffer.getvalue().decode("utf-8") == NON_CP1252 + "\n"
    assert err.buffer.getvalue().decode("utf-8") == NON_CP1252 + "\n"


def test_each_stream_keeps_its_existing_error_policy(monkeypatch):
    out, err = _cp1252_stream(errors="strict"), _cp1252_stream(errors="backslashreplace")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    textio.configure_stdio()

    assert (out.errors, err.errors) == ("strict", "backslashreplace")  # never a blanket "replace"


class _RecordingStream:
    def __init__(self, encoding):
        self.encoding = encoding
        self.errors = "strict"
        self.reconfigured = False

    def reconfigure(self, **kwargs):
        self.reconfigured = True


@pytest.mark.parametrize("encoding", ["utf-8", "UTF-8", "utf8", "utf_8"])
def test_an_already_utf8_stream_is_left_alone(monkeypatch, encoding):
    out, err = _RecordingStream(encoding), _RecordingStream(encoding)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    textio.configure_stdio()

    assert not out.reconfigured and not err.reconfigured


def test_missing_or_non_reconfigurable_streams_are_skipped(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)  # e.g. a windowed process
    monkeypatch.setattr(sys, "stderr", object())  # e.g. a test capture object
    textio.configure_stdio()  # must not raise


def _child_env():
    env = dict(os.environ)
    env.pop("PYTHONUTF8", None)
    env["PYTHONIOENCODING"] = "cp1252"  # what a Windows pipe gives a child, on any platform
    return env


def test_a_cp1252_pipe_in_a_real_child_process_works_after_configure_stdio():
    code = (
        "import sys; sys.path.insert(0, %r); import textio; textio.configure_stdio(); "
        "print('\\u2265 \\u0141ukasz')" % str(SCRIPTS_DIR)
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, env=_child_env())
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8").strip() == "≥ Łukasz"


def test_deal_export_cli_survives_a_cp1252_pipe_with_a_non_cp1252_company_name(tmp_path):
    """The reproduction from the issue: the CLI created the files and then died
    with exit 1 on its final print() of a path containing the company name."""
    (tmp_path / "draft.md").write_text("# Draft\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "deal_export.py"),
         "--company", "Łukasz Co", "--proposal", "Fleet Loan", "--type", "asset_finance",
         "--draft", "draft.md"],
        capture_output=True, cwd=tmp_path, env=_child_env(),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "Łukasz Co" in result.stdout.decode("utf-8")
    assert (tmp_path / "deals" / "Łukasz Co").is_dir()


def _is_main_guard(node):
    """`if __name__ == "__main__":` in either operand order."""
    if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and len(node.test.comparators) == 1):
        return False
    operands = [node.test.left, node.test.comparators[0]]
    has_name = any(isinstance(o, ast.Name) and o.id == "__name__" for o in operands)
    has_main = any(isinstance(o, ast.Constant) and o.value == "__main__" for o in operands)
    return has_name and has_main


def _entry_point_problem(source):
    """None if the source has no `__main__` block or starts it correctly; else
    a description. The block's FIRST statement must be
    `from textio import configure_stdio` and its SECOND the bare call
    `configure_stdio()` -- anything before (an assignment, a parse_args() call),
    a guarded or merely-named call, or `import textio` style does not count."""
    for node in ast.parse(source).body:
        if _is_main_guard(node):
            body = node.body
            first, second = (body + [None, None])[:2]
            if not (isinstance(first, ast.ImportFrom) and first.module == "textio"
                    and [a.name for a in first.names] == ["configure_stdio"]):
                return "first statement of the __main__ block must be `from textio import configure_stdio`"
            if not (isinstance(second, ast.Expr) and isinstance(second.value, ast.Call)
                    and isinstance(second.value.func, ast.Name)
                    and second.value.func.id == "configure_stdio" and not second.value.args):
                return "second statement of the __main__ block must be the bare call `configure_stdio()`"
            return None
    return None


def _has_entry_point(source):
    return any(_is_main_guard(n) for n in ast.parse(source).body)


def test_every_script_with_a_cli_entry_point_configures_stdio_first():
    sources = {p.name: p.read_text(encoding="utf-8") for p in sorted(SCRIPTS_DIR.glob("*.py"))}
    cli_scripts = {name: src for name, src in sources.items() if _has_entry_point(src)}
    assert len(cli_scripts) >= 11  # guards against this test silently scanning nothing
    problems = {name: _entry_point_problem(src) for name, src in cli_scripts.items()}
    assert {name: p for name, p in problems.items() if p} == {}


_GOOD = 'if __name__ == "__main__":\n    from textio import configure_stdio\n    configure_stdio()\n    main()\n'


@pytest.mark.parametrize("source, expect_problem", [
    (_GOOD, False),
    (_GOOD.replace('__name__ == "__main__"', '"__main__" == __name__'), False),
    ("def main():\n    pass\n", False),  # no entry block: not a CLI, nothing to check
    ('if __name__ == "__main__":\n    main()\n', True),  # forgotten
    ('if __name__ == "__main__":\n    args = parse()\n    from textio import configure_stdio\n'
     '    configure_stdio()\n', True),  # something runs before it
    ('if __name__ == "__main__":\n    parser = P()\n    from textio import configure_stdio\n'
     '    configure_stdio()\n', True),
    ('if __name__ == "__main__":\n    from textio import configure_stdio\n    configure_stdio\n    main()\n', True),
    ('if __name__ == "__main__":\n    from textio import configure_stdio\n    if verbose:\n'
     '        configure_stdio()\n', True),  # guarded
    ('if __name__ == "__main__":\n    import textio\n    textio.configure_stdio()\n', True),
    ('if "__main__" == __name__:\n    main()\n', True),  # reversed operands are still detected
])
def test_the_entry_point_check_catches_the_ways_a_cli_could_skip_it(source, expect_problem):
    assert _has_entry_point(source) == ("__main__" in source and "__name__" in source)
    assert (_entry_point_problem(source) is not None) == expect_problem


def test_a_closed_stream_is_skipped_not_reconfigured(monkeypatch):
    closed = _cp1252_stream()
    closed.close()
    monkeypatch.setattr(sys, "stdout", closed)
    monkeypatch.setattr(sys, "stderr", closed)
    textio.configure_stdio()  # must not raise ValueError: I/O operation on closed file
