"""Runtime-only smoke check (issue #144): does a user who installs ONLY requirements.txt get a working framework?

The test job installs requirements-dev.txt, so a script that quietly imports a dev-only package (pytest, hypothesis,
ruff, ...), or a runtime dependency missing from requirements.txt that a dev dependency happens to pull in, would
pass every test and still fail for a user. CI's `runtime-smoke` job runs THIS file in a clean environment built from
requirements.txt alone:

    python -m venv .smoke && .smoke/bin/pip install -r requirements.txt
    .smoke/bin/python tests/runtime_smoke.py --expect-clean

Standard library only (it must run where pytest is not installed). It makes no network call and needs no API key:

1. with --expect-clean: none of the development-only packages is importable (proves the environment is clean);
2. every module under scripts/ imports;
3. every script with a command-line entry point prints usage for --help;
4. a deal is exported end to end in a throwaway directory (state -> spreading figures -> policy check -> .docx and
   .xlsx) and the files open;
5. the evaluation dataset validates (zero model calls).

Exit status 0 when everything passed; 1 with one line per failure otherwise.
"""
import argparse
import ast
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
DEV_ONLY = ("pytest", "hypothesis", "ruff", "bandit", "coverage", "diff_cover", "pytest_cov", "pytest_timeout",
            "pytest_socket")


def has_main_block(path):
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
            return True
    return False


def child_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("ANTHROPIC")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(script, *args, cwd):
    return subprocess.run([sys.executable, str(SCRIPTS / f"{script}.py"), *map(str, args)], cwd=cwd,
                          env=child_env(), capture_output=True, text=True, encoding="utf-8", timeout=120)


def check_clean_environment():
    return [f"development-only package {name!r} is importable; the smoke environment is not runtime-only"
            for name in DEV_ONLY if importlib.util.find_spec(name) is not None]


def check_imports():
    sys.path.insert(0, str(SCRIPTS))
    failures = []
    for path in sorted(SCRIPTS.glob("*.py")):
        try:
            importlib.import_module(path.stem)
        except Exception as exc:  # noqa: BLE001 - report whatever an import raises, then carry on with the rest
            failures.append(f"import {path.stem}: {type(exc).__name__}: {exc}")
    return failures


def check_help():
    failures = []
    with tempfile.TemporaryDirectory() as workdir:
        for path in sorted(SCRIPTS.glob("*.py")):
            if has_main_block(path):
                result = run(path.stem, "--help", cwd=workdir)
                if result.returncode != 0 or not result.stdout.startswith("usage:"):
                    failures.append(f"{path.name} --help: exit {result.returncode}: {result.stderr.strip()[:200]}")
    return failures


def check_export_end_to_end():
    failures = []
    financials = {"FY-2": {"revenue": 4000, "cost_of_sales": 2600}, "FY-1": {"revenue": 4300, "cost_of_sales": 2750},
                  "FY-Current": {"revenue": 4650, "cost_of_sales": 2900, "admin_expenses": 540,
                                 "interest_paid": 98, "scheduled_principal": 150, "long_term_debt": 1100,
                                 "share_capital": 100, "retained_profit": 790, "cash": 310}}
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        (work / "financials.json").write_text(json.dumps(financials), encoding="utf-8")
        (work / "draft.md").write_text("# CAM\n\n- **Borrower:** Smoke Co\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n",
                                       encoding="utf-8")
        steps = [
            ("spreading_check", "--company", "Smoke Co", "--proposal", "Loan", "--financials", "financials.json"),
            ("policy_check", "--company", "Smoke Co", "--proposal", "Loan"),
            ("deal_export", "--company", "Smoke Co", "--proposal", "Loan", "--draft", "draft.md"),
        ]
        for step in steps:
            result = run(*step, cwd=work)
            if result.returncode != 0:
                failures.append(f"{step[0]}: exit {result.returncode}: {result.stderr.strip()[-300:]}")
                return failures
        outputs = list((work / "deals").rglob("*.docx")) + list((work / "deals").rglob("*.xlsx"))
        if len(outputs) != 2:
            return [f"deal_export produced {len(outputs)} document(s), expected a .docx and an .xlsx"]
        import docx
        import openpyxl
        try:
            docx.Document(str(next(p for p in outputs if p.suffix == ".docx")))
            openpyxl.load_workbook(str(next(p for p in outputs if p.suffix == ".xlsx")))
        except Exception as exc:  # noqa: BLE001 - any failure to open the exported files is the finding
            failures.append(f"exported files do not open: {type(exc).__name__}: {exc}")
    return failures


def check_eval_dataset():
    result = run("run_evals", "--validate", cwd=REPO_ROOT)
    if result.returncode != 0:
        return [f"run_evals --validate: exit {result.returncode}: {(result.stdout + result.stderr).strip()[-300:]}"]
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description="Runtime-only smoke check; see this file's docstring.")
    parser.add_argument("--expect-clean", action="store_true",
                        help="also fail if any development-only package is importable")
    args = parser.parse_args(argv)

    checks = [("imports", check_imports), ("--help", check_help), ("export end to end", check_export_end_to_end),
              ("evaluation dataset", check_eval_dataset)]
    if args.expect_clean:
        checks.insert(0, ("clean environment", check_clean_environment))
    failures = []
    for name, check in checks:
        problems = check()
        print(f"{'ok  ' if not problems else 'FAIL'} {name}")
        failures.extend(f"{name}: {p}" for p in problems)
    for failure in failures:
        print(f"  {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
