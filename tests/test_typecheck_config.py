"""The mypy type check's configuration and pinning (issue #424, Phase 0).

Phase 0 added the checker and its configuration; since Phase 3 option B, type errors fail the typecheck job. These tests pin what is easy to
get wrong quietly: the target list drifting from the governance-critical modules, the strict override reaching modules
that were never meant to be strict, the checker leaking into the runtime requirements, and a missing-imports
suppression widening past the two third-party libraries the design note names.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
tomllib = pytest.importorskip("tomllib")


def pyproject():
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]


def critical_modules():
    return pyproject()["opencam"]["coverage"]["critical_modules"]


def overrides_by_scope():
    """{frozenset of module patterns: the override table}."""
    return {frozenset(table["module"]): table for table in pyproject()["mypy"]["overrides"]}


def test_the_checker_targets_exactly_the_eleven_governance_modules_and_each_exists():
    files = pyproject()["mypy"]["files"]
    assert files == [f"scripts/{name}.py" for name in critical_modules()]
    assert len(files) == 11
    for relative in files:
        assert (REPO_ROOT / relative).is_file(), relative


def test_the_global_settings_stay_strict_about_missing_imports_and_stale_ignores():
    config = pyproject()["mypy"]
    assert config["python_version"] == "3.11"                  # the version CI runs
    assert config["mypy_path"] == "scripts"                    # the scripts import each other as top-level names
    assert config["ignore_missing_imports"] is False           # a missing first-party import must stay visible
    assert config["warn_unused_ignores"] is True
    assert config["show_error_codes"] is True


def test_the_strict_override_covers_exactly_the_target_modules():
    strict = [table for pattern, table in overrides_by_scope().items() if "disallow_untyped_defs" in table]
    assert len(strict) == 1
    (table,) = strict
    assert sorted(table["module"]) == sorted(critical_modules())
    for flag in ("disallow_untyped_defs", "disallow_incomplete_defs", "check_untyped_defs", "warn_return_any",
                 "no_implicit_optional", "strict_equality"):
        assert table[flag] is True, flag


def test_missing_import_suppression_is_limited_to_the_one_untyped_third_party_library():
    suppressed = set()
    for table in pyproject()["mypy"]["overrides"]:
        if table.get("ignore_missing_imports"):
            suppressed |= set(table["module"])
    # python-docx ships py.typed, so only openpyxl (no py.typed in 3.1.5) needs the suppression.
    assert suppressed == {"openpyxl", "openpyxl.*"}


def requirement_names(filename):
    names = {}
    for raw in (REPO_ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = raw.partition("#")[0].strip()
        if line and not line.startswith("-"):
            names[re.split(r"[\s;\[<>=!~]", line, maxsplit=1)[0].lower()] = line
    return names


def test_mypy_is_a_development_requirement_pinned_exactly_and_never_a_runtime_one():
    assert "mypy" in requirement_names("requirements-dev.txt")
    assert "mypy" not in requirement_names("requirements.txt")
    pins = {}
    for raw in (REPO_ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;#]+)\s*(?:#.*)?", raw.strip())
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    assert re.fullmatch(r"\d+\.\d+\.\d+", pins["mypy"])
    # mypy's own runtime dependencies are pinned too, so the CI environment is reproducible.
    for dependency in ("mypy-extensions", "pathspec", "typing-extensions"):
        assert dependency in pins, dependency


def test_the_shared_json_value_alias_has_one_home_in_state_manager():
    """Every annotated module imports `JsonValue` from state_manager (issue #424); a second definition would let the
    copies drift, so exactly one scripts/*.py module-level assignment may define it."""
    import ast

    definers = []
    for script in sorted((REPO_ROOT / "scripts").glob("*.py")):
        tree = ast.parse(script.read_text(encoding="utf-8"))
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            if any(isinstance(target, ast.Name) and target.id == "JsonValue" for target in targets):
                definers.append(script.name)
    assert definers == ["state_manager.py"], definers

    import typing

    import state_manager

    parts = typing.get_args(state_manager.JsonValue)
    assert {str, int, float, bool, type(None)} <= set(parts)          # every JSON scalar
    assert {typing.get_origin(part) for part in parts} >= {list, dict}  # and both containers
