"""Every command-line entry point, run for real as a subprocess (issue #144).

The slash commands call these scripts from a Bash step (`python scripts/<name>.py ...`), so the argparse
setup and the `if __name__ == "__main__":` block are the actual interface. Unit tests that call `main()` or an
internal function never run that code. Each script here is executed in a throwaway working directory (every
script resolves `deals/`, `config/`, `templates/` and `inputs/` against the working directory) with the
Anthropic credentials removed from its environment, and must

- print usage and exit 0 for `--help`,
- exit 2 with a usage message for an unknown flag,
- do its job on a representative minimal input (where that is possible without a model call),
- fail with a non-zero exit when given an input that cannot work.

`coverage` follows the child processes (`patch = ["subprocess"]` in pyproject.toml), so these runs count
toward the coverage of the `__main__` blocks.
"""
import ast
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Every script that defines a command-line entry point. A new one must be added here AND given tests below;
# test_the_list_matches_the_scripts_that_have_a_main_block fails until it is.
CLI_SCRIPTS = (
    "calibrate", "check_coverage", "check_test_count", "conventions", "deal_export", "orchestrator", "pii_scan", "policy_check",
    "research_export", "run_evals", "source_manifest", "spreading_check", "state_manager",
)


def has_main_block(path):
    """True if the module has a top-level `if __name__ == "__main__":` (parsed, so a mention in a docstring
    or a comment does not count)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left, comparators = node.test.left, node.test.comparators
            if (isinstance(left, ast.Name) and left.id == "__name__" and len(comparators) == 1
                    and isinstance(comparators[0], ast.Constant) and comparators[0].value == "__main__"):
                return True
    return False


# An empty directory standing in for the user's home and config directories in every child process. Removing the
# ANTHROPIC_* variables is not enough: the SDK also reads a credentials profile from the user's config directory
# (~/.config/anthropic, %APPDATA%\\Anthropic), so on a machine with one configured a child could authenticate and
# make a real, paid model call. Pointing the home variables at an empty directory removes that path as well.
_ISOLATED_HOME = tempfile.mkdtemp(prefix="cli-home-")
atexit.register(shutil.rmtree, _ISOLATED_HOME, ignore_errors=True)
HOME_VARIABLES = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME")


def child_env():
    """The parent's environment minus anything that could reach a model or an account: no ANTHROPIC_* variable and
    no real home or config directory (so no on-disk SDK credentials profile either)."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("ANTHROPIC")}
    env.update(dict.fromkeys(HOME_VARIABLES, _ISOLATED_HOME))
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@pytest.fixture
def cli(tmp_path):
    """run(script, *args) -> CompletedProcess, in an empty working directory."""
    workdir = tmp_path / "work"
    workdir.mkdir()

    def run(script, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / f"{script}.py"), *map(str, args)],
            cwd=cwd or workdir, env=child_env(), capture_output=True, text=True, encoding="utf-8", timeout=120,
            stdin=subprocess.DEVNULL)

    run.workdir = workdir
    return run


# ---------------------------------------------------------------------------
# The list of CLIs is exactly the scripts that have one
# ---------------------------------------------------------------------------

def test_the_list_matches_the_scripts_that_have_a_main_block():
    found = sorted(p.stem for p in SCRIPTS_DIR.glob("*.py") if has_main_block(p))
    assert found == sorted(CLI_SCRIPTS), (
        "scripts with an `if __name__ == \"__main__\":` block and CLI_SCRIPTS disagree; add the new script "
        "to CLI_SCRIPTS and give it --help / bad-flag / representative tests in this file")


@pytest.mark.parametrize("source, expected", [
    ('if __name__ == "__main__":\n    pass\n', True),
    ("if __name__ == '__main__':\n    pass\n", True),
    ('"""if __name__ == \\"__main__\\": in a docstring"""\nx = 1\n', False),
    ('# if __name__ == "__main__":\nx = 1\n', False),
    ('def main():\n    if __name__ == "__main__":\n        pass\n', False),   # not top level
])
def test_the_main_block_detector_reads_code_not_text(tmp_path, source, expected):
    path = tmp_path / "m.py"
    path.write_text(source, encoding="utf-8")
    assert has_main_block(path) is expected


# ---------------------------------------------------------------------------
# --help and a bad flag, for every script
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", CLI_SCRIPTS)
def test_help_prints_usage_and_exits_zero(cli, script):
    result = cli(script, "--help")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage:") and result.stderr == ""


@pytest.mark.parametrize("script", CLI_SCRIPTS)
def test_an_unknown_flag_exits_two_with_usage_on_stderr(cli, script):
    result = cli(script, "--no-such-flag-xyz")
    assert result.returncode == 2
    assert "usage:" in result.stderr and result.stdout == ""


# ---------------------------------------------------------------------------
# Representative real runs
# ---------------------------------------------------------------------------

def test_pii_scan_reports_findings_as_json(cli):
    clean = cli.workdir / "clean.md"
    clean.write_text("# Template\n\nBorrower: [Company Name]\n", encoding="utf-8")
    dirty = cli.workdir / "dirty.md"
    dirty.write_text("Contact jane.doe@example.com about GBP 1,250,000.\n", encoding="utf-8")
    assert json.loads(cli("pii_scan", clean).stdout) == {"findings": []}
    findings = json.loads(cli("pii_scan", dirty).stdout)["findings"]
    assert findings, "an email address and a currency figure must be reported"


def test_pii_scan_on_a_missing_file_fails(cli):
    result = cli("pii_scan", "no-such-file.md")
    assert result.returncode != 0 and "no-such-file.md" in result.stderr


def test_state_manager_check_steps_reports_missing_steps_and_always_exits_zero(cli):
    result = cli("state_manager", "--check-steps", "--company", "Acme", "--proposal", "Loan",
                 "--required", "spread, collateral")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"missing_steps": ["spread", "collateral"], "ok": False}


def test_source_manifest_saves_a_source_then_reports_it(cli):
    source = cli.workdir / "filing.txt"
    source.write_text("the filing", encoding="utf-8")
    before = json.loads(cli("source_manifest", "--company", "Acme", "--proposal", "Loan", "--check-sources").stdout)
    assert before == {"missing_saved_sources": False}   # nothing declared, nothing missing
    saved = cli("source_manifest", "--company", "Acme", "--proposal", "Loan", "--step", "triage",
                "--claim", "legal identity", "--file", source, "--url", "https://example.invalid/filing")
    assert saved.returncode == 0, saved.stderr
    entry = json.loads(saved.stdout)
    assert entry["step"] == "triage" and entry["claim"] == "legal identity"
    manifests = list(cli.workdir.glob("deals/Acme/Loan_*/sources/manifest.json"))
    assert len(manifests) == 1 and json.loads(manifests[0].read_text(encoding="utf-8"))[0]["step"] == "triage"


def test_source_manifest_requires_its_save_arguments_unless_checking(cli):
    result = cli("source_manifest", "--company", "Acme", "--proposal", "Loan")
    assert result.returncode == 2 and "--step" in result.stderr and "--claim" in result.stderr


def test_policy_check_computes_the_policy_state_for_a_new_deal(cli):
    result = cli("policy_check", "--company", "Acme", "--proposal", "Loan")
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)["policy_state"]
    assert {c["cp_id"] for c in state["required_conditions_precedent"]} >= {"KYC-AML"}


def test_conventions_write_then_read_round_trip_for_a_company_and_the_enterprise(cli):
    written = cli("conventions", "--company", "Acme", "--proposal", "Loan", "--write",
                  "--financials-source", "analyst-supplied", "--note", "Depreciation sits inside COGS")
    assert written.returncode == 0, written.stderr
    record = json.loads(written.stdout)
    assert record["financials_source_default"] == "analyst-supplied"
    read = json.loads(cli("conventions", "--company", "Acme", "--read").stdout)
    assert read["found"] is True and read["convention"]["financials_source_note"] == "Depreciation sits inside COGS"
    assert json.loads(cli("conventions", "--enterprise", "--read").stdout) == {"found": False, "convention": None}
    assert cli("conventions", "--enterprise", "--write", "--financials-source", "framework-computed").returncode == 0
    assert json.loads(cli("conventions", "--enterprise", "--read").stdout)["found"] is True


@pytest.mark.parametrize("args, fragment", [
    (("--enterprise", "--write"), "--financials-source"),
    (("--proposal", "Loan", "--enterprise", "--read"), "--company"),
    (("--read",), "required"),
    (("--company", "A", "--enterprise", "--read"), "not allowed"),
])
def test_conventions_rejects_inconsistent_arguments(cli, args, fragment):
    result = cli("conventions", *args)
    assert result.returncode == 2 and fragment in result.stderr


def test_deal_export_writes_the_docx_and_xlsx_into_the_dated_folder(cli):
    draft = cli.workdir / "draft.md"
    draft.write_text("# Credit Assessment Memorandum\n\n- **Borrower:** [Company Name]\n\nBody text.\n",
                     encoding="utf-8")
    result = cli("deal_export", "--company", "Acme", "--proposal", "Loan", "--type", "corporate_credit",
                 "--draft", draft)
    assert result.returncode == 0, result.stderr
    assert "Done! Files generated in" in result.stdout
    folder = next(cli.workdir.glob("deals/Acme/Loan_*"))
    assert (folder / "Acme_Loan_CAM.docx").stat().st_size > 0 and (folder / "Acme_Loan_Spreading.xlsx").stat().st_size > 0


def test_deal_export_rejects_a_malformed_date_and_a_missing_draft(cli):
    draft = cli.workdir / "draft.md"
    draft.write_text("# T\n", encoding="utf-8")
    bad_date = cli("deal_export", "--company", "A", "--proposal", "P", "--draft", draft, "--date-str", "foo")
    assert bad_date.returncode == 2 and "YYYY-MM-DD" in bad_date.stderr
    missing = cli("deal_export", "--company", "A", "--proposal", "P", "--draft", "nope.md")
    assert missing.returncode != 0 and "nope.md" in missing.stderr
    assert not (cli.workdir / "deals").exists()  # nothing was created for either failure


def test_research_export_writes_the_brief_docx(cli):
    brief = cli.workdir / "brief.md"
    brief.write_text("# Research brief\n\n- **Verdict:** Go\n\nSector notes [placeholder].\n", encoding="utf-8")
    result = cli("research_export", "--company", "Acme", "--proposal", "Brief", "--brief", brief)
    assert result.returncode == 0, result.stderr
    assert "Research brief exported to" in result.stdout
    assert list(cli.workdir.glob("deals/Acme/Brief_*/*Research_Brief.docx"))


def test_research_export_on_a_missing_brief_fails(cli):
    result = cli("research_export", "--company", "A", "--proposal", "P", "--brief", "nope.md")
    assert result.returncode != 0 and "nope.md" in result.stderr


def test_spreading_check_recomputes_and_checkpoints_the_figures(cli):
    financials = cli.workdir / "fin.json"
    financials.write_text(json.dumps({"FY-Current": {"revenue": 1000, "cost_of_sales": 600, "admin_expenses": 100}}),
                          encoding="utf-8")
    result = cli("spreading_check", "--company", "Acme", "--proposal", "Loan", "--financials", financials)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["financials"]["FY-Current"]["gross_profit"] == 400 and out["financials_source"] == "framework-computed"
    state_files = list(cli.workdir.glob("deals/Acme/Loan_*/state.json"))
    assert len(state_files) == 1
    assert json.loads(state_files[0].read_text(encoding="utf-8"))["financials"]["FY-Current"]["ebitda"] == 300


def test_spreading_check_no_update_financials_source_leaves_the_flag_alone(cli):
    financials = cli.workdir / "fin.json"
    financials.write_text(json.dumps({"FY-Current": {"revenue": 10}}), encoding="utf-8")
    result = cli("spreading_check", "--company", "Acme", "--proposal", "Loan", "--financials", financials,
                 "--no-update-financials-source")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["financials_source"] is None


def test_spreading_check_on_a_missing_input_fails(cli):
    result = cli("spreading_check", "--company", "A", "--proposal", "P", "--financials", "nope.json")
    assert result.returncode != 0 and "nope.json" in result.stderr


def test_check_coverage_passes_a_compliant_report_and_fails_a_weak_one(cli):
    config = cli.workdir / "pyproject.toml"
    config.write_text(
        '[tool.opencam.coverage]\noverall = 85\ncritical = 90\ncritical_modules = ["alpha"]\n', encoding="utf-8")
    report = cli.workdir / "coverage.json"

    def write(overall, alpha):
        report.write_text(json.dumps({"totals": {"percent_covered": overall},
                                      "files": {"scripts/alpha.py": {"summary": {"percent_covered": alpha}}}}),
                          encoding="utf-8")

    write(96, 96)
    ok = cli("check_coverage", report, "--config", config)
    assert ok.returncode == 0 and "Coverage floors met." in ok.stdout
    write(96, 50)
    weak = cli("check_coverage", report, "--config", config)
    assert weak.returncode == 1 and "FAIL: alpha coverage 50.00%" in weak.stdout
    assert cli("check_coverage", report, "--config", config, "--report-only").returncode == 0


def test_check_coverage_on_a_missing_report_exits_two_naming_the_file(cli):
    result = cli("check_coverage", "no-such-report.json")
    assert result.returncode == 2 and "no-such-report.json" in result.stderr


def test_check_test_count_passes_on_a_match_and_fails_on_a_mismatch(cli):
    output = cli.workdir / "pytest_output.txt"
    badge = cli.workdir / "badge.json"
    badge.write_text('{"passed": 5}', encoding="utf-8")
    output.write_text("5 passed in 1s\n", encoding="utf-8")
    assert cli("check_test_count", output, "--badge-path", badge).returncode == 0
    output.write_text("6 passed in 1s\n", encoding="utf-8")
    mismatch = cli("check_test_count", output, "--badge-path", badge)
    assert mismatch.returncode == 1 and "mismatch" in mismatch.stdout
    assert json.loads(badge.read_text(encoding="utf-8")) == {"passed": 5}  # the plain check never edits the file


def test_calibrate_mock_writes_a_style_guide_and_a_template_from_a_sample_pdf(cli):
    from test_calibrate import _write_pdf
    samples = cli.workdir / "inputs" / "calibration_samples"
    samples.mkdir(parents=True)
    (cli.workdir / "config").mkdir()  # calibrate writes into an existing config/, as in a checkout
    _write_pdf(samples / "sample.pdf", "Synthetic Co", pages=1, lines_per_page=5)
    result = cli("calibrate", "--mock", "--type", "asset_finance", "--on-overflow", "split")
    assert result.returncode == 0, result.stderr
    assert (cli.workdir / "config" / "style_guide.md").is_file()
    assert (cli.workdir / "templates" / "local" / "cam" / "asset_finance_cam.md").is_file()


def test_calibrate_without_sample_pdfs_says_so_and_writes_nothing(cli):
    result = cli("calibrate", "--mock")
    assert "No sample PDFs found" in result.stdout
    assert not (cli.workdir / "config").exists() and not (cli.workdir / "templates").exists()


def test_calibrate_rejects_an_unknown_overflow_mode(cli):
    result = cli("calibrate", "--on-overflow", "shrug")
    assert result.returncode == 2 and "invalid choice" in result.stderr


def test_run_evals_validates_lists_and_dry_runs_the_shipped_dataset(cli, tmp_path):
    validate = cli("run_evals", "--validate")
    assert validate.returncode == 0 and "15 cases, all valid" in validate.stdout
    listing = cli("run_evals", "--list")
    assert listing.returncode == 0 and "chk-clean-control" in listing.stdout
    out = tmp_path / "evals-out"
    dry = cli("run_evals", "--dry-run", "--out", out)
    assert dry.returncode == 0, dry.stderr + dry.stdout
    assert "0 calls" in dry.stdout and len(list(out.glob("*/results.json"))) == 1


def test_run_evals_refuses_conflicting_modes_and_an_unaffordable_plan(cli, tmp_path):
    clash = cli("run_evals", "--live", "--dry-run")
    assert clash.returncode == 2 and "mutually exclusive" in clash.stderr
    over_cap = cli("run_evals", "--dry-run", "--repeats", "50", "--out", tmp_path / "unused")
    assert over_cap.returncode == 2 and "Refused" in over_cap.stderr
    assert not (tmp_path / "unused").exists()


def test_orchestrator_without_credentials_stops_after_parsing_and_never_succeeds(tmp_path):
    """The one script that cannot be run end to end without a model. In a copy of just the files it reads, with
    every Anthropic variable removed, it must parse its arguments, start the pipeline, and then fail non-zero
    (it never gets as far as a request) -- not silently succeed or hang."""
    env = child_env()
    assert not any(k.upper().startswith("ANTHROPIC") for k in env)
    for variable in HOME_VARIABLES:      # no route to a credentials profile in the real home/config directory
        assert env[variable] == _ISOLATED_HOME and os.listdir(_ISOLATED_HOME) == [], variable
    work = tmp_path / "orchestrator-cwd"
    shutil.copytree(REPO_ROOT / "agents", work / "agents")
    shutil.copytree(REPO_ROOT / "templates" / "cam", work / "templates" / "cam")
    (work / "config").mkdir()
    shutil.copy(REPO_ROOT / "config" / "settings.json", work / "config" / "settings.json")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "orchestrator.py"), "--company", "Acme", "--proposal", "Loan"],
        cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120, stdin=subprocess.DEVNULL)
    assert result.returncode != 0
    assert "Underwriter Agent drafting CAM for Acme" in result.stdout


def test_orchestrator_on_a_missing_input_file_fails_before_starting(tmp_path):
    work = tmp_path / "orchestrator-cwd"
    work.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "orchestrator.py"), "--company", "A", "--proposal", "P",
         "--financials", "nope.json"],
        cwd=work, env=child_env(), capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode != 0 and "nope.json" in result.stderr
    assert not (work / "deals").exists()


# ---------------------------------------------------------------------------
# A state.json the code cannot use is an error message and exit status 1, never a traceback (issues #170, #171)
# ---------------------------------------------------------------------------

def _write_state(workdir, content):
    folder = workdir / "deals" / "Acme" / "Loan_2026-01-01"
    folder.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (folder / "state.json").write_text(text, encoding="utf-8")
    return folder / "state.json"


def _assert_a_clean_state_error(result, *fragments):
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "Traceback" not in result.stderr and result.stdout == ""
    assert result.stderr.startswith("error: ") and len(result.stderr.strip().splitlines()) == 1, result.stderr
    for fragment in fragments:
        assert fragment in result.stderr, (fragment, result.stderr)


def test_spreading_check_reports_a_malformed_state_cleanly(cli):
    state_file = _write_state(cli.workdir, {"multi_period_financials": [1]})
    before = state_file.read_bytes()
    (cli.workdir / "fin.json").write_text(json.dumps({"FY-Current": {"revenue": 100}}), encoding="utf-8")
    result = cli("spreading_check", "--company", "Acme", "--proposal", "Loan", "--financials", "fin.json")
    _assert_a_clean_state_error(result, '"multi_period_financials" must be an object keyed by period', "state.json")
    assert state_file.read_bytes() == before


def test_spreading_check_reports_a_newer_schema_cleanly(cli):
    state_file = _write_state(cli.workdir, {"schema_version": "9.9.9"})
    before = state_file.read_bytes()
    (cli.workdir / "fin.json").write_text(json.dumps({"FY-Current": {"revenue": 100}}), encoding="utf-8")
    result = cli("spreading_check", "--company", "Acme", "--proposal", "Loan", "--financials", "fin.json")
    _assert_a_clean_state_error(result, "9.9.9", "downgrade")
    assert state_file.read_bytes() == before


def test_policy_check_reports_a_malformed_state_cleanly(cli):
    _write_state(cli.workdir, {"financials": [1, 2], "covenants": "none"})
    result = cli("policy_check", "--company", "Acme", "--proposal", "Loan")
    _assert_a_clean_state_error(result, '"financials"', '"covenants"')


def test_policy_check_reports_a_corrupt_state_file_cleanly(cli):
    _write_state(cli.workdir, '{"financials": ')
    _assert_a_clean_state_error(cli("policy_check", "--company", "Acme", "--proposal", "Loan"), "is corrupted")


def test_policy_check_reports_a_utf16_state_file_cleanly(cli):
    """What PowerShell's `>` redirection writes: not UTF-8, not a traceback."""
    folder = cli.workdir / "deals" / "Acme" / "Loan_2026-01-01"
    folder.mkdir(parents=True)
    (folder / "state.json").write_bytes("{}".encode("utf-16"))
    _assert_a_clean_state_error(cli("policy_check", "--company", "Acme", "--proposal", "Loan"), "is corrupted")


def test_policy_check_reports_a_top_level_list_cleanly(cli):
    _write_state(cli.workdir, [1, 2, 3])
    _assert_a_clean_state_error(cli("policy_check", "--company", "Acme", "--proposal", "Loan"), "the top level must be")


def test_deal_export_reports_a_malformed_state_cleanly(cli):
    _write_state(cli.workdir, {"financials_source": 5})
    (cli.workdir / "draft.md").write_text("# Draft\n", encoding="utf-8")
    result = cli("deal_export", "--company", "Acme", "--proposal", "Loan", "--draft", "draft.md")
    _assert_a_clean_state_error(result, '"financials_source" must be "framework-computed" or "analyst-supplied"')


def test_state_manager_check_steps_reports_a_malformed_state_cleanly(cli):
    _write_state(cli.workdir, {"steps_completed": "triage"})
    result = cli("state_manager", "--check-steps", "--company", "Acme", "--proposal", "Loan", "--required", "spread")
    _assert_a_clean_state_error(result, '"steps_completed" must be a list')


def test_state_manager_check_steps_still_exits_zero_for_a_missing_step(cli):
    """The documented 'always exits 0' is about RESULTS: a state that cannot be read is an error, a missing step is not."""
    _write_state(cli.workdir, {"steps_completed": ["triage"]})
    result = cli("state_manager", "--check-steps", "--company", "Acme", "--proposal", "Loan", "--required", "spread")
    assert result.returncode == 0 and json.loads(result.stdout) == {"missing_steps": ["spread"], "ok": False}


def test_source_manifest_check_sources_reports_a_malformed_state_cleanly(cli):
    _write_state(cli.workdir, [1])
    result = cli("source_manifest", "--check-sources", "--company", "Acme", "--proposal", "Loan")
    _assert_a_clean_state_error(result, "the top level must be")


def test_the_orchestrator_reports_a_malformed_state_cleanly_before_any_model_call(tmp_path):
    work = tmp_path / "orchestrator-cwd"
    shutil.copytree(REPO_ROOT / "agents", work / "agents")
    shutil.copytree(REPO_ROOT / "templates" / "cam", work / "templates" / "cam")
    (work / "config").mkdir()
    shutil.copy(REPO_ROOT / "config" / "settings.json", work / "config" / "settings.json")
    _write_state(work, {"steps_completed": "triage"})
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "orchestrator.py"), "--company", "Acme", "--proposal", "Loan"],
        cwd=work, env=child_env(), capture_output=True, text=True, encoding="utf-8", timeout=120,
        stdin=subprocess.DEVNULL)
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert result.stderr.startswith("error: ") and '"steps_completed" must be a list' in result.stderr
    assert "Underwriter Agent drafting" not in result.stdout        # stopped before the pipeline did anything
