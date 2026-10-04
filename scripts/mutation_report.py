"""Per-module mutation report for the weekly mutation workflow (issue #146).

mutmut's own summary is one number for the whole run, which hides exactly what matters here: a well-tested helper
can carry the score while a governance module (`policy_engine`, `state_manager`, ...) is barely asserted on. This
turns `mutmut results --all true` (every mutant with its status, killed ones included) into:

* a score per module -- `(killed + timeout) / (mutants - skipped)`, so a mutant no test even reaches ("no tests")
  counts against the module rather than vanishing from the denominator -- split into governance-critical modules
  (`[tool.opencam.coverage] critical_modules`) and the other mutated modules, with a total row;
* completeness warnings, so an empty or partial report cannot be read as a good one: a configured module with no
  mutants at all, a module whose mutants mostly have no test, mutants never checked, a listing with no killed
  mutants (a survivors-only file, whose scores would be meaningless);
* the largest groups of survivors (`module.function`), the starting point for triage;
* optionally, a target for the critical modules (`target_critical` under `[tool.opencam.mutation]`): each critical
  module is marked as meeting it or below it. **Nothing here ever fails a build over a score**: the workflow is a
  diagnostic report, not a merge gate (see CLAUDE.md, "Mutation testing").

    python scripts/mutation_report.py mutation-all.txt [--config pyproject.toml] [--top 15] [--json report.json]

Exit status: 0 whenever a report was produced, 2 for an unreadable results file or configuration. No `anthropic`
dependency, matching check_coverage.py's pattern.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:   # pragma: no cover - Python 3.10 only; CI and the dev environment run 3.11+
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"
LINE_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\.(\S+?)__mutmut_(\d+):\s*(\S.*?)\s*$")
STATUS_KEYS = {
    "killed": "killed", "survived": "survived", "timeout": "timeout", "no tests": "no_tests",
    "suspicious": "suspicious", "skipped": "skipped", "segfault": "segfault", "not checked": "not_checked",
    "check was interrupted by user": "interrupted",
}
COUNTED = ("killed", "timeout", "survived", "no_tests", "suspicious", "segfault", "skipped", "not_checked",
           "interrupted", "unknown")
NO_TESTS_SHARE_WARNING = 0.5     # more than this share of a module's mutants having no test is reported


def parse_results(text):
    """[(module, function, mutant number, status key)] for every mutant line of `mutmut results` output.

    A class method's mangled name (`xǁ_FileLockǁ__enter__`) is shown as `_FileLock.__enter__`; a status mutmut
    adds later is kept as "unknown" rather than dropped, so every listed mutant is counted exactly once."""
    entries = []
    for line in text.splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        module, raw_function, number, status = match.groups()
        function = re.sub(r"^x(?:_|ǁ)", "", raw_function).replace("ǁ", ".")
        entries.append((module, function, int(number), STATUS_KEYS.get(status.lower(), "unknown")))
    return entries


def load_config(config_path=None):
    """{"modules": every mutated module, "critical": the governance-critical ones, "target_critical": float|None}.

    Modules come from `[tool.mutmut] only_mutate` (`src/<module>.py`), critical ones from
    `[tool.opencam.coverage] critical_modules`, which must all be among the mutated ones."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with open(path, "rb") as f:
        config = tomllib.load(f)
    only_mutate = config.get("tool", {}).get("mutmut", {}).get("only_mutate")
    if not isinstance(only_mutate, list) or not only_mutate:
        raise ValueError(f"{path} has no [tool.mutmut] only_mutate list")
    modules = []
    for entry in only_mutate:
        match = re.fullmatch(r"src/([A-Za-z0-9_]+)\.py", entry) if isinstance(entry, str) else None
        if not match:
            raise ValueError(f"[tool.mutmut] only_mutate entry {entry!r} is not of the form src/<module>.py")
        modules.append(match.group(1))
    opencam = config.get("tool", {}).get("opencam", {})
    critical = opencam.get("coverage", {}).get("critical_modules")
    if not isinstance(critical, list) or not critical:
        raise ValueError(f"{path} has no [tool.opencam.coverage] critical_modules list")
    stray = [m for m in critical if m not in modules]
    if stray:
        raise ValueError(f"critical modules not mutated by [tool.mutmut] only_mutate: {', '.join(stray)}")
    target = opencam.get("mutation", {}).get("target_critical")
    if target is not None and (isinstance(target, bool) or not isinstance(target, (int, float))
                               or not 0 < target <= 100):
        raise ValueError(f"[tool.opencam.mutation] target_critical must be a percentage in (0, 100], got {target!r}")
    return {"modules": modules, "critical": list(critical), "target_critical": None if target is None else float(target)}


def _row(counts):
    total = sum(counts.values())
    tested = total - counts["skipped"]
    row = {key: counts[key] for key in COUNTED}
    row["total"] = total
    row["score"] = round(100.0 * (counts["killed"] + counts["timeout"]) / tested, 1) if tested else None
    reached = tested - counts["no_tests"]
    row["score_excluding_no_tests"] = (
        round(100.0 * (counts["killed"] + counts["timeout"]) / reached, 1) if reached > 0 else None)
    return row


def summarize(entries, config):
    """The whole report as plain data: per-module rows (in config order, then any unconfigured module found), the
    total row, completeness `warnings`, and the largest survivor groups."""
    per_module = {module: Counter({key: 0 for key in COUNTED}) for module in config["modules"]}
    survivors = Counter()
    unconfigured = []
    for module, function, _, status in entries:
        if module not in per_module:
            per_module[module] = Counter({key: 0 for key in COUNTED})
            unconfigured.append(module)
        per_module[module][status] += 1
        if status in ("survived", "suspicious", "no_tests", "segfault"):
            survivors[(module, function, status)] += 1
    rows = {module: _row(counts) for module, counts in per_module.items()}
    for module, row in rows.items():
        row["critical"] = module in config["critical"]
        target = config["target_critical"]
        row["meets_target"] = (None if target is None or not row["critical"] or row["score"] is None
                               else row["score"] >= target)
    total = _row(sum((Counter(c) for c in per_module.values()), Counter({key: 0 for key in COUNTED})))
    warnings = []
    if not entries:
        warnings.append("the results file lists no mutants at all")
    elif total["killed"] == 0:
        warnings.append("no killed mutant is listed: this looks like a survivors-only listing, so the scores below "
                        "are meaningless (use `mutmut results --all true`)")
    for module in config["modules"]:
        row = rows[module]
        if row["total"] == 0:
            warnings.append(f"{module}: configured for mutation but no mutant was listed for it")
        elif row["no_tests"] / row["total"] > NO_TESTS_SHARE_WARNING:
            warnings.append(f"{module}: {row['no_tests']} of {row['total']} mutants have no test that reaches them")
    if total["not_checked"] or total["interrupted"]:
        warnings.append(f"{total['not_checked']} mutants were never checked and {total['interrupted']} were "
                        "interrupted: the run is incomplete")
    if total["unknown"]:
        warnings.append(f"{total['unknown']} mutants have a status this report does not know")
    for module in unconfigured:
        warnings.append(f"{module}: mutants listed for a module that is not configured in [tool.mutmut] only_mutate")
    groups = [{"module": m, "function": f, "status": s, "count": n} for (m, f, s), n in
              sorted(survivors.items(), key=lambda item: (-item[1], item[0]))]
    return {"modules": rows, "total": total, "warnings": warnings, "survivor_groups": groups,
            "target_critical": config["target_critical"]}


def _score(value):
    return "n/a" if value is None else f"{value:.1f}%"


def render_markdown(summary, top=15):
    target = summary["target_critical"]
    lines = ["## Mutation score per module (diagnostic, not a merge gate)", "",
             f"Score = (killed + timed out) / (mutants - skipped); a mutant no test reaches counts against it. "
             f"Target for critical modules: {'none set' if target is None else f'{target:.1f}%'}.", "",
             "| Module | Kind | Mutants | Killed | Timeout | Survived | No tests | Score | Score excl. no-tests |"
             + (" Target |" if target is not None else ""),
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |" + (" --- |" if target is not None else "")]
    for module, row in summary["modules"].items():
        verdict = ""
        if target is not None:
            verdict = " " + {True: "met", False: "below", None: "-"}[row["meets_target"]] + " |"
        lines.append(f"| {module} | {'critical' if row['critical'] else 'supporting'} | {row['total']} | "
                     f"{row['killed']} | {row['timeout']} | {row['survived']} | {row['no_tests']} | "
                     f"{_score(row['score'])} | {_score(row['score_excluding_no_tests'])} |{verdict}")
    total = summary["total"]
    lines.append(f"| **All** | | {total['total']} | {total['killed']} | {total['timeout']} | {total['survived']} | "
                 f"{total['no_tests']} | **{_score(total['score'])}** | {_score(total['score_excluding_no_tests'])} |"
                 + (" |" if target is not None else ""))
    lines.append("")
    if summary["warnings"]:
        lines += ["### Report completeness warnings", ""] + [f"- {w}" for w in summary["warnings"]] + [""]
    else:
        lines += ["Report completeness: every configured module has mutants and none is unchecked.", ""]
    groups = summary["survivor_groups"][:top]
    if groups:
        lines += [f"### Largest groups of survivors (top {len(groups)} of {len(summary['survivor_groups'])})", "",
                  "| Function | Status | Mutants |", "| --- | --- | ---: |"]
        lines += [f"| {g['module']}.{g['function']} | {g['status'].replace('_', ' ')} | {g['count']} |"
                  for g in groups]
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Per-module mutation score and completeness report from `mutmut results --all true` output "
                    "(diagnostic: never fails over a score).")
    parser.add_argument("results", help="file holding the output of `mutmut results --all true`")
    parser.add_argument("--config", default=None, help="pyproject.toml to read the module lists from "
                                                       "(default: this repository's)")
    parser.add_argument("--top", type=int, default=15, help="how many survivor groups to list (default 15)")
    parser.add_argument("--json", dest="json_path", default=None, help="also write the full report as JSON here")
    args = parser.parse_args(argv)
    try:
        with open(args.results, encoding="utf-8") as f:
            text = f.read()
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    summary = summarize(parse_results(text), config)
    print(render_markdown(summary, top=args.top))
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
    return 0


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    sys.exit(main())
