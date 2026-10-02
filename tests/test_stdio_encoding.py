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


def _main_block(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and getattr(node.test.left, "id", None) == "__name__"):
            return node
    return None


def test_every_script_with_a_cli_entry_point_configures_stdio_first():
    scripts = sorted(p for p in SCRIPTS_DIR.glob("*.py") if _main_block(p) is not None)
    assert len(scripts) >= 11  # guards against this test silently scanning nothing
    for path in scripts:
        body = _main_block(path).body
        imports = [n for n in body if isinstance(n, ast.ImportFrom) and n.module == "textio"
                   and any(a.name == "configure_stdio" for a in n.names)]
        calls = [n for n in body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and getattr(n.value.func, "id", None) == "configure_stdio"]
        assert imports and calls, f"{path.name}: its __main__ block must call configure_stdio()"
        first_call = next(n for n in body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call))
        assert first_call is calls[0], f"{path.name}: configure_stdio() must run before any other call"
