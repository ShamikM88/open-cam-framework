"""tests/runtime_smoke.py is what CI's `runtime-smoke` job runs in a runtime-only environment (issue #144). Here it is
run in the development environment, so a breakage in the smoke script itself (or a real failure it would report)
shows up in the ordinary test job and not only in the separate job."""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE = REPO_ROOT / "tests" / "runtime_smoke.py"


def run_smoke(*args):
    return subprocess.run([sys.executable, str(SMOKE), *args], cwd=REPO_ROOT, capture_output=True, text=True,
                          encoding="utf-8", timeout=300)


def test_the_smoke_checks_pass_in_the_development_environment():
    result = run_smoke()
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("imports", "--help", "export end to end", "evaluation dataset"):
        assert f"ok   {name}" in result.stdout


def test_expect_clean_fails_where_development_packages_are_installed():
    """The clean-environment check must be able to fail, or the CI job would prove nothing: pytest itself is
    importable here, so --expect-clean must report it."""
    result = run_smoke("--expect-clean")
    assert result.returncode == 1
    assert "FAIL clean environment" in result.stdout
    assert "development-only package 'pytest' is importable" in result.stderr


def test_the_smoke_script_uses_only_the_standard_library_and_runtime_requirements():
    """It must run where pytest is not installed, so its own imports are stdlib, or a requirements.txt package
    imported lazily inside a check (docx, openpyxl)."""
    import ast
    tree = ast.parse(SMOKE.read_text(encoding="utf-8"))
    top_level = {alias.name.split(".")[0] for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
    top_level |= {node.module.split(".")[0] for node in tree.body if isinstance(node, ast.ImportFrom)}
    assert top_level <= set(sys.stdlib_module_names)
