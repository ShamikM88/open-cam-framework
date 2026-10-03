"""Guards for the central tool configuration in pyproject.toml (issues #139 and #142).

A setting that nothing tests can be deleted by accident and nobody notices until the thing it prevented
happens again. Each test below proves that a setting is ACTIVE by running the real tool, with this
repository's pyproject.toml, against a deliberately bad snippet and expecting it to be rejected:

- `filterwarnings = ["error"]`: a leaked file object (ResourceWarning) fails the test that leaked it;
- `--disable-socket`: a network connection attempt fails;
- pytest-timeout: a test that overruns its timeout fails;
- the ruff rule set: B905, B904, UP031, S, and the PLW1514 preview rule (open() without encoding) all fire
  on scripts/, and `assert` is allowed in tests/ only;
- CI uses the config-driven ruff command and bandit at `-ll`.
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: no stdlib TOML reader
    tomllib = None

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _config():
    if tomllib is None:
        pytest.skip("tomllib needs Python 3.11+")
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _run_pytest_on(tmp_path, source, *extra):
    """Run a real, separate pytest on one throwaway test file using THIS repository's configuration."""
    test_file = tmp_path / "test_probe.py"
    test_file.write_text(textwrap.dedent(source), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(PYPROJECT), "--rootdir", str(tmp_path), "-p", "no:cacheprovider",
         str(test_file), *extra],
        capture_output=True, text=True, cwd=tmp_path, timeout=120)


def _ruff(snippet, filename):
    """ruff with this repository's configuration, on a snippet pretending to live at `filename`."""
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", str(PYPROJECT), "--stdin-filename", filename,
         "--output-format", "json", "-"],
        input=textwrap.dedent(snippet), capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)


def _codes(result):
    """The rule codes ruff reported (it exits 1 with a JSON list when it found something)."""
    assert result.returncode in (0, 1), result.stderr
    return {finding["code"] for finding in json.loads(result.stdout)}


# ---------------------------------------------------------------------------
# pytest settings
# ---------------------------------------------------------------------------

def test_the_probe_runner_passes_a_clean_test(tmp_path):
    """Control: the harness below does not fail everything, so a failure further down means something."""
    result = _run_pytest_on(tmp_path, """
        def test_fine():
            assert 1 + 1 == 2
        """)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_leaked_file_object_fails_the_test_that_leaked_it(tmp_path):
    result = _run_pytest_on(tmp_path, """
        def test_leaks(tmp_path):
            f = open(tmp_path / "x.txt", "w", encoding="utf-8")
            f = None  # dropped without close(): ResourceWarning
        """)
    assert result.returncode != 0
    assert "ResourceWarning" in result.stdout


def test_a_network_connection_attempt_fails(tmp_path):
    result = _run_pytest_on(tmp_path, """
        import socket

        def test_connects():
            socket.create_connection(("127.0.0.1", 9), timeout=1)
        """)
    assert result.returncode != 0
    assert "SocketBlockedError" in result.stdout


def test_a_test_that_overruns_the_timeout_fails(tmp_path):
    # -o overrides the configured value so the check takes a second, but it only fires if pytest-timeout is
    # installed and active under this configuration.
    result = _run_pytest_on(tmp_path, """
        import time

        def test_hangs():
            time.sleep(30)
        """, "-o", "timeout=1")
    assert result.returncode != 0
    assert "Timeout" in result.stdout


def test_the_configured_settings_are_the_agreed_ones():
    options = _config()["tool"]["pytest"]["ini_options"]
    assert options["filterwarnings"] == ["error"]
    assert "--disable-socket" in options["addopts"]
    assert 1 <= options["timeout"] <= 600
    coverage = _config()["tool"]["coverage"]
    assert coverage["run"]["branch"] is True and coverage["run"]["source"] == ["scripts"]


# ---------------------------------------------------------------------------
# ruff rule set
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snippet, code", [
    ("pairs = dict(zip([1], [2]))\n", "B905"),                                   # zip() without strict=
    ("def f():\n    try:\n        pass\n    except OSError:\n        raise ValueError('x')\n", "B904"),
    ("message = 'a %s' % 'b'\n", "UP031"),                                       # printf-style formatting
    ("import subprocess\nsubprocess.run(['git'])\n", "S607"),                    # partial executable path
    ("def f(x):\n    assert x\n", "S101"),                                       # assert in production code
    ("def f(p):\n    return open(p)\n", "PLW1514"),                              # open() without encoding (preview rule)
])
def test_the_rule_set_rejects_each_class_of_problem_in_scripts(snippet, code):
    result = _ruff(snippet, "scripts/_probe.py")
    assert code in _codes(result)
    assert result.returncode != 0


def test_asserts_and_fixed_subprocess_calls_are_allowed_in_tests_but_not_other_rules():
    allowed = _ruff("import subprocess\n\ndef test_x():\n    assert subprocess.run(['git', '--version'])\n",
                    "tests/_probe.py")
    assert _codes(allowed) == set()
    still_enforced = _ruff("def test_x():\n    return open('f')\n", "tests/_probe.py")
    assert "PLW1514" in _codes(still_enforced)  # the encoding rule applies to tests as well


def test_a_clean_snippet_passes():
    assert _codes(_ruff("def f(p):\n    with open(p, encoding='utf-8') as fh:\n        return fh.read()\n",
                        "scripts/_probe.py")) == set()


def test_the_preview_rule_does_not_switch_on_other_preview_rules():
    config = _config()["tool"]["ruff"]["lint"]
    assert config["explicit-preview-rules"] is True and config["extend-select"] == ["PLW1514"]


# ---------------------------------------------------------------------------
# CI uses this configuration
# ---------------------------------------------------------------------------

def test_ci_runs_the_config_driven_ruff_and_bandit_at_medium():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    ruff_lines = [line for line in workflow.splitlines() if "ruff check" in line]
    assert ruff_lines and not any("--select" in line for line in ruff_lines), \
        "the rule set lives in pyproject.toml, not in a workflow command line"
    bandit_lines = [line for line in workflow.splitlines() if "bandit" in line and "run:" in line]
    assert bandit_lines and all(" -ll" in line and " -lll" not in line for line in bandit_lines)
