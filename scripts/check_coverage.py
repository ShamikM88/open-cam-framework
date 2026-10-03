"""Coverage enforcement for CI (issue #142): the overall floor and the per-module floor for the governance modules.

`pytest --cov` measures coverage; this turns the measurement into a pass/fail against thresholds that live in
pyproject.toml (`[tool.opencam.coverage]`) so a floor changes in one reviewed diff and never in a workflow command
line. coverage.py itself can only enforce one overall number, which would let a well-covered helper hide an untested
`policy_engine.py`; the modules that decide whether a draft is code-enforced-REJECTED, or that compute the figures
the CAM reports, need their own, higher, floor.

    pytest --cov --cov-report=json
    python scripts/check_coverage.py coverage.json

`--report-only` prints the same table and always exits 0 (used to read the numbers of a new environment before a
floor is decided). Coverage here is line + branch ("percent_covered" in coverage.py's JSON report), measured over
`scripts/` with subprocess coverage on (see pyproject.toml).

The changed-lines floor (`diff-cover`) is separate: it is configured under `[tool.diff_cover]` in the same file and
run by CI on pull requests only.

No `anthropic` dependency, matching template_resolver.py/state_manager.py's pattern.
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:   # pragma: no cover - Python 3.10 only; CI and the dev environment run 3.11+
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"
REQUIRED_KEYS = ("overall", "critical", "critical_modules")


def load_thresholds(config_path=None):
    """The `[tool.opencam.coverage]` table, validated: percentages in (0, 100] and a non-empty module list."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with open(path, "rb") as f:
        table = tomllib.load(f).get("tool", {}).get("opencam", {}).get("coverage")
    if not isinstance(table, dict):
        raise ValueError(f"{path} has no [tool.opencam.coverage] table")
    missing = [key for key in REQUIRED_KEYS if key not in table]
    if missing:
        raise ValueError(f"[tool.opencam.coverage] in {path} is missing: {', '.join(missing)}")
    for key in ("overall", "critical"):
        value = table[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 100:
            raise ValueError(f"[tool.opencam.coverage] {key} must be a percentage in (0, 100], got {value!r}")
    modules = table["critical_modules"]
    if (not isinstance(modules, list) or not modules or not all(isinstance(m, str) and m for m in modules)
            or len(set(modules)) != len(modules)):
        raise ValueError("[tool.opencam.coverage] critical_modules must be a non-empty list of distinct module names")
    return {"overall": float(table["overall"]), "critical": float(table["critical"]),
            "critical_modules": list(modules)}


def module_percentages(report):
    """{module name: percent covered} from coverage.py's JSON report (`files` keyed by path)."""
    percentages = {}
    for filename, data in (report.get("files") or {}).items():
        path = Path(filename.replace("\\", "/"))
        if path.suffix == ".py":
            if path.stem in percentages:
                raise ValueError(f"two measured files share the module name {path.stem!r} ({filename}); "
                                 "modules are matched by name, so this would be ambiguous")
            try:
                percentages[path.stem] = float(data["summary"]["percent_covered"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"coverage report has no summary.percent_covered for {filename}") from None
    return percentages


def evaluate(report, thresholds):
    """Returns (ok, table_lines). A critical module absent from the report counts as a failure, never as a pass."""
    if not isinstance(report, dict):
        raise ValueError("coverage report is not a JSON object (is it coverage.py's JSON report?)")
    problems = []
    lines = []
    totals = report.get("totals") or {}
    if "percent_covered" not in totals:
        raise ValueError("coverage report has no totals.percent_covered (is it coverage.py's JSON report?)")
    overall = float(totals["percent_covered"])
    lines.append(f"overall: {overall:.2f}% (floor {thresholds['overall']:g}%)")
    if overall < thresholds["overall"]:
        problems.append(f"overall coverage {overall:.2f}% is below the {thresholds['overall']:g}% floor")

    measured = module_percentages(report)
    lines.append(f"critical modules (floor {thresholds['critical']:g}% each):")
    for module in thresholds["critical_modules"]:
        if module not in measured:
            lines.append(f"  {module}: NOT MEASURED")
            problems.append(f"{module} was not measured (is it under the coverage source, and was it run?)")
            continue
        percent = measured[module]
        below = percent < thresholds["critical"]
        lines.append(f"  {module}: {percent:.2f}%{'  <-- below floor' if below else ''}")
        if below:
            problems.append(f"{module} coverage {percent:.2f}% is below the {thresholds['critical']:g}% floor")
    return not problems, lines + [f"FAIL: {p}" for p in problems]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check coverage.py's JSON report against the floors in pyproject.toml "
                    "([tool.opencam.coverage]): an overall floor and a higher per-module floor for the "
                    "governance modules.")
    parser.add_argument("report", help="coverage.py JSON report (pytest --cov --cov-report=json)")
    parser.add_argument("--config", default=None, help="pyproject.toml to read the floors from (default: the repo's)")
    parser.add_argument("--report-only", action="store_true",
                        help="print the table and exit 0 even when below a floor")
    args = parser.parse_args(argv)

    try:
        thresholds = load_thresholds(args.config)
        with open(args.report, encoding="utf-8") as f:
            report = json.load(f)
        ok, lines = evaluate(report, thresholds)
    except (OSError, ValueError) as exc:   # json.JSONDecodeError is a ValueError
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    if ok:
        print("Coverage floors met.")
        return 0
    return 0 if args.report_only else 1


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    sys.exit(main())
