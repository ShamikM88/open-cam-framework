"""Contract tests keeping the documented Python floor aligned with tooling requirements."""

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_readme_and_ruff_config_declare_python_311_as_the_minimum():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "Python 3.11+ required" in readme
    assert config["tool"]["ruff"]["target-version"] == "py311"
    project = config.get("project", {})
    if "requires-python" in project:
        assert project["requires-python"] == ">=3.11"

    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: "3.11"' in ci


@pytest.mark.parametrize("script_name", ["check_coverage.py", "mutation_report.py"])
def test_toml_readers_use_stdlib_without_python_310_fallbacks(script_name):
    source = (REPO_ROOT / "scripts" / script_name).read_text(encoding="utf-8")

    assert "import tomllib" in source
    assert "tomli" not in source
    assert "Python 3.10 only" not in source
