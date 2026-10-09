"""The worked examples in the documentation are real runs of the real scripts (issue #117, D2).

docs/examples/synthetic_co/ holds the inputs of the synthetic deal used throughout docs/workflows.md,
docs/financial-model.md, docs/commands.md and docs/troubleshooting.md. This module runs that deal through the scripts
in a scratch directory, exactly as the pages describe, and checks two things for every number and message quoted
there: the script really produces it, and the page really says it. If a formula, a message or a figure changes, either
the script or the page is wrong, and this fails naming the figure.

All data is synthetic; nothing here calls a model (the orchestrator is run without credentials and fails at its first
call; the child processes have no ANTHROPIC_* variables and an empty home directory).
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_calibrate import _write_pdf
from test_cli_subprocess import SCRIPTS_DIR, child_env

import pii_scan
from state_manager import SCHEMA_VERSION

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
EXAMPLES = DOCS / "examples" / "synthetic_co"
COMPANY, PROPOSAL = "Synthetic Co", "Synthetic Fleet Loan"
DEAL_DIR = f"deals/{COMPANY}"


def page(name):
    return re.sub(r"\s+", " ", (DOCS / name).read_text(encoding="utf-8"))


def normalised(text):
    return text.replace("\\", "/")


class Deal:
    """The synthetic deal in a scratch directory, with a `run` that executes a script as a child process."""

    def __init__(self, root):
        self.root = root
        for name in ("agents", "templates"):
            shutil.copytree(REPO / name if name == "agents" else REPO / "templates" / "cam",
                            root / name if name == "agents" else root / "templates" / "cam")
        (root / "config").mkdir()
        shutil.copy(REPO / "config" / "settings.json", root / "config" / "settings.json")
        (root / DEAL_DIR).mkdir(parents=True)
        for example, target in (("financials_input.json", "financials_input.json"),
                                ("forward_input.json", "forward_input.json"), ("stress_input.json", "stress_input.json"),
                                ("brief.md", "brief.md"), ("draft.md", "draft.md")):
            shutil.copy(EXAMPLES / example, root / DEAL_DIR / f"{PROPOSAL}_{target}")

    def run(self, script, *args):
        return subprocess.run([sys.executable, str(SCRIPTS_DIR / f"{script}.py"), *map(str, args)], cwd=self.root,
                              env=child_env(), capture_output=True, text=True, encoding="utf-8", timeout=180,
                              stdin=subprocess.DEVNULL)

    def deal_args(self):
        return ["--company", COMPANY, "--proposal", PROPOSAL]

    def path(self, suffix):
        return f"{DEAL_DIR}/{PROPOSAL}_{suffix}"

    def state_file(self):
        return next((self.root / DEAL_DIR).glob(f"{PROPOSAL}_*/state.json"))

    def write_state(self, **fields):
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]); from state_manager import write_state; "
                f"write_state({COMPANY!r}, {PROPOSAL!r}, **json.loads(sys.argv[2]))")
        done = subprocess.run([sys.executable, "-c", code, str(SCRIPTS_DIR), json.dumps(fields)], cwd=self.root,
                              env=child_env(), capture_output=True, text=True, encoding="utf-8")
        assert done.returncode == 0, done.stderr

    def policy(self, draft=False):
        extra = ["--draft", self.path("draft.md")] if draft else []
        done = self.run("policy_check", *self.deal_args(), *extra)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)


@pytest.fixture(scope="module")
def deal(tmp_path_factory):
    d = Deal(tmp_path_factory.mktemp("docs_example"))
    spread = d.run("spreading_check", *d.deal_args(), "--financials", d.path("financials_input.json"))
    assert spread.returncode == 0, spread.stderr
    d.spread = json.loads(spread.stdout)
    project = d.run("spreading_check", *d.deal_args(), "--financials", d.path("forward_input.json"),
                    "--stress-assumptions", d.path("stress_input.json"), "--no-update-financials-source")
    assert project.returncode == 0, project.stderr
    d.project = json.loads(project.stdout)
    structure = json.loads((EXAMPLES / "deal_structure.json").read_text(encoding="utf-8"))
    d.write_state(deal_type="asset_finance", inputs={"pd": "0.20%", "lgd": "LGD 3 (15%)"},
                  steps_completed=["spread", "project"], **structure)
    d.structure = structure
    d.policy_state = d.policy()
    return d


# ---------------------------------------------------------------------------
# Spreading: docs/workflows.md and docs/financial-model.md
# ---------------------------------------------------------------------------

CURRENT = {"ebitda": 1000, "tangible_net_worth": 2200, "total_debt": 2000, "gross_profit": 2000, "operating_profit": 700,
           "fcf": 350, "total_equity": 2300, "current_assets": 1250, "current_liabilities": 900}
CURRENT_RATIOS = {"dscr": 2.0, "gross_leverage": 2.0, "net_debt_to_ebitda": 1.75, "current_ratio": 1250 / 900,
                  "gearing": 2000 / 2300, "ebit_interest_cover": 700 / 150, "fcf_conversion_pct": 0.35,
                  "trade_debtor_days": 43.8, "trade_creditor_days": 54.75, "stock_days": 400 / 3000 * 365,
                  "working_capital_cycle_days": 43.8 + 400 / 3000 * 365 - 54.75}


def test_the_current_year_figures_are_what_the_documents_say(deal):
    financials = deal.project["financials"]["FY-Current"]
    ratios = deal.project["ratios"]["FY-Current"]
    for key, expected in CURRENT.items():
        assert financials[key] == expected, key
    for key, expected in CURRENT_RATIOS.items():
        assert ratios[key] == pytest.approx(expected), key
    assert deal.spread["financials_source"] == "framework-computed"
    assert deal.project["financials_source"] == "framework-computed"      # /project passed --no-update-financials-source
    workflows = page("workflows.md")
    for snippet in ('"ebitda": 1000', '"tangible_net_worth": 2200', '"total_debt": 2000', '"dscr": 2.0',
                    '"gross_leverage": 2.0', '"net_debt_to_ebitda": 1.75', '"current_ratio": 1.3888888888888888',
                    '"gearing": 0.8695652173913043', '"fcf_conversion_pct": 0.35',
                    '"working_capital_cycle_days": 37.71666666666667'):
        assert snippet in workflows, snippet
    model = page("financial-model.md")
    for snippet in ("**2000**", "**700**", "**1000**", "**350**", "**2200**", "**2.0**", "**1.75**", "**1.3889**",
                    "**0.8696**", "**0.35**", "**43.8**", "**54.75**", "**37.7167**", "48.6667"):
        assert snippet in model, snippet


def test_the_forward_years_and_the_downside_case_are_what_the_documents_say(deal):
    ratios, downside = deal.project["ratios"], deal.project["downside_case"]
    assert deal.project["financials"]["FY+1"]["ebitda"] == 550 and deal.project["financials"]["FY+1"]["total_debt"] == 1650
    assert ratios["FY+1"]["dscr"] == pytest.approx(1.1) and ratios["FY+1"]["gross_leverage"] == pytest.approx(3.0)
    assert deal.project["financials"]["FY+2"]["ebitda"] == -100
    assert ratios["FY+2"]["gross_leverage"] is None and ratios["FY+2"]["dscr"] == pytest.approx(-100 / 490)
    shocked = downside["financials"]["FY+1"]
    assert shocked["raw"]["revenue"] == pytest.approx(4370) and shocked["raw"]["interest_paid"] == pytest.approx(183)
    assert shocked["ebitda"] == pytest.approx(320)
    assert downside["ratios"]["FY+1"]["dscr"] == pytest.approx(320 / 533)
    assert downside["ratios"]["FY+1"]["gross_leverage"] == pytest.approx(5.15625)
    model, workflows = page("financial-model.md"), page("workflows.md")
    for snippet in ("**320**", "**0.600**", "**5.15625**", "revenue 4600 becomes 4370", "150 + 1650 x 200 / 10000 = 183",
                    "-0.204", "DSCR **1.1**", "gross leverage **3.0**"):
        assert snippet in model, snippet
    for snippet in ("DSCR 1.1 and gross leverage 3.0", "DSCR is -0.204", "5.15625 and DSCR 0.600"):
        assert snippet in workflows, snippet


# ---------------------------------------------------------------------------
# Policy checking: docs/workflows.md
# ---------------------------------------------------------------------------

def test_the_policy_state_is_what_the_workflow_page_says(deal):
    out = deal.policy_state
    state = out["policy_state"]
    assert [c["cp_id"] for c in state["required_conditions_precedent"]] == [
        "KYC-AML", "FACILITY-EXECUTION", "GUARANTEE-SYNTHETIC-PARENT-HOLDINGS"]
    assert [c["cs_id"] for c in state["required_conditions_subsequent"]] == [
        "MI-REPORTING", "CS-COVENANT-COMPLIANCE", "CS-SEC-REPERFECT-AST-001", "CS-SEC-REPERFECT-AST-002",
        "CS-GUARANTEE-SYNTHETIC-PARENT-HOLDINGS"]
    results = {r["metric"]: r for r in state["covenant_results"]}
    assert results["dscr"]["status"] == "PASS" and results["dscr"]["headroom_pct"] == pytest.approx(0.6)
    assert results["gross_leverage"]["status"] == "PASS" and results["gross_leverage"]["headroom_pct"] == pytest.approx(0.4286, abs=1e-4)
    assert [b["breach_id"] for b in state["downside_covenant_breaches"]] == ["DOWNSIDE-FY-1-GROSS-LEVERAGE"]
    breach = state["downside_covenant_breaches"][0]
    assert (breach["base_actual"], breach["downside_actual"], breach["downside_status"]) == (3.0, 5.15625, "FAIL")
    forward = {r["forward_id"]: r for r in state["forward_covenant_results"]}
    assert {k: v["status"] for k, v in forward.items()} == {
        "FORWARD-FY-1-DSCR": "FAIL", "FORWARD-FY-1-GROSS-LEVERAGE": "PASS",
        "FORWARD-FY-2-DSCR": "FAIL", "FORWARD-FY-2-GROSS-LEVERAGE": "UNRESOLVABLE"}
    assert forward["FORWARD-FY-2-GROSS-LEVERAGE"]["reason"] == "gross leverage not meaningful: EBITDA is negative"
    assert forward["FORWARD-FY-1-DSCR"]["actual"] == pytest.approx(1.1)
    assert state["security_gaps"] == [] and out["reasons"] == [] and out["compliant"] is True
    assert out["cam_data_present"] is True
    workflows = page("workflows.md")
    for snippet in ("`KYC-AML`, `FACILITY-EXECUTION` and `GUARANTEE-SYNTHETIC-PARENT-HOLDINGS`",
                    "`CS-SEC-REPERFECT-AST-002`", "DSCR 2.0 against a minimum of 1.25 is `PASS` with 60% headroom",
                    "2.0 against a maximum of 3.5 is `PASS` with 42.9% headroom",
                    "`DOWNSIDE-FY-1-GROSS-LEVERAGE`", "`FORWARD-FY-1-DSCR` is `FAIL` (1.1)",
                    "`FORWARD-FY-2-DSCR` is `FAIL` (-0.204)", "gross leverage not meaningful: EBITDA is negative",
                    "`reasons` is empty and `compliant` is `true`"):
        assert snippet in workflows, snippet


def test_the_compliant_draft_is_compliant_and_each_defect_gives_the_documented_reason(deal):
    assert deal.policy(draft=True)["reasons"] == []
    draft = (EXAMPLES / "draft.md").read_text(encoding="utf-8")
    target = deal.root / deal.path("draft.md")
    expected = {
        "FACILITY-EXECUTION missing": (draft.replace('"FACILITY-EXECUTION", ', ""),
                                       ["Missing Required CP FACILITY-EXECUTION: Execution of Facility Agreement."]),
        "wrong figure": (draft.replace('"dscr": 2.0', '"dscr": 2.5'),
                         ["Narrative/Ground-Truth Mismatch: reported dscr 2.5 vs computed 2.0"]),
        "downside not acknowledged": (draft.replace('["DOWNSIDE-FY-1-GROSS-LEVERAGE"]', "[]"),
                                      ["Undisclosed Downside Breach DOWNSIDE-FY-1-GROSS-LEVERAGE: gross_leverage "
                                       "breaches threshold in FY+1 under stress but is not addressed in the draft."]),
    }
    workflows = page("workflows.md")
    for name, (text, reasons) in expected.items():
        target.write_text(text, encoding="utf-8")
        assert deal.policy(draft=True)["reasons"] == reasons, name
        for reason in reasons:
            assert re.sub(r"\s+", " ", reason) in workflows or reason.split(":")[0] in workflows, name
    target.write_text(draft.split("```json")[0], encoding="utf-8")
    reasons = deal.policy(draft=True)["reasons"]
    assert any(r.startswith("Missing or malformed Risk Category: Market") for r in reasons)
    assert any(r.startswith("Missing Narrative Sources") for r in reasons)
    assert sum(r.startswith("Missing Required CP") for r in reasons) == 3
    assert sum(r.startswith("Missing Required Condition Subsequent") for r in reasons) == 5
    assert sum(r.startswith("Missing or malformed Risk Category") for r in reasons) == 7
    target.write_text(draft, encoding="utf-8")
    assert "Missing Required CP FACILITY-EXECUTION: Execution of Facility Agreement." in workflows
    assert "Undisclosed Downside Breach DOWNSIDE-FY-1-GROSS-LEVERAGE: gross_leverage breaches threshold in FY+1 under stress but is not addressed in the draft." in workflows
    assert "Narrative/Ground-Truth Mismatch: reported dscr 2.5 vs computed 2.0" in workflows


def test_a_charge_that_is_registered_but_not_perfected_gives_the_documented_conditions_and_reasons(deal):
    good = deal.structure["security_package"]
    gap = json.loads(json.dumps(good))
    gap[1] = {"secures_asset_id": "AST-002", "perfection_status": "Registered", "ranking": "Second"}
    try:
        deal.write_state(security_package=gap)
        out = deal.policy(draft=True)
    finally:
        deal.write_state(security_package=good)
    assert out["compliant"] is False
    assert out["reasons"][-2:] == ["Unperfected Security: Asset AST-002 charge status is 'Registered', not Perfected.",
                                   "Subordinate Ranking: Asset AST-002 charge ranking is 'Second', not First."]
    assert {"SEC-PERFECT-AST-002", "SEC-PRIORITY-AST-002"} <= {c["cp_id"] for c in out["policy_state"]["required_conditions_precedent"]}
    workflows = page("workflows.md")
    for snippet in ("`SEC-PERFECT-AST-002` and `SEC-PRIORITY-AST-002`",
                    "Unperfected Security: Asset AST-002 charge status is 'Registered', not Perfected.",
                    "Subordinate Ranking: Asset AST-002 charge ranking is 'Second', not First."):
        assert snippet in workflows, snippet


# ---------------------------------------------------------------------------
# Resuming, exporting, sources, conventions, research, calibration
# ---------------------------------------------------------------------------

def test_check_steps_prints_what_the_workflow_page_shows(deal):
    ok = deal.run("state_manager", "--check-steps", *deal.deal_args(), "--required", "spread")
    assert json.loads(ok.stdout) == {"missing_steps": [], "ok": True} and ok.returncode == 0
    missing = deal.run("state_manager", "--check-steps", *deal.deal_args(), "--required", "spread,triage,collateral")
    assert json.loads(missing.stdout) == {"missing_steps": ["triage", "collateral"], "ok": False} and missing.returncode == 0
    workflows = page("workflows.md")
    assert '{"missing_steps": [], "ok": true}' in workflows
    assert '"missing_steps": ["triage", "collateral"]' in workflows


def test_the_export_writes_the_files_and_message_the_documents_describe(deal):
    done = deal.run("deal_export", *deal.deal_args(), "--type", "asset_finance", "--draft", deal.path("draft.md"))
    assert done.returncode == 0, done.stderr
    folder = next((deal.root / DEAL_DIR).glob(f"{PROPOSAL}_20*-*-*"))
    assert normalised(done.stdout).strip() == f"Done! Files generated in {DEAL_DIR}/{folder.name}"
    assert sorted(p.name for p in folder.iterdir()) == [
        f"{COMPANY}_{PROPOSAL}_CAM.docx", f"{COMPANY}_{PROPOSAL}_Spreading.xlsx", "state.json"]
    assert f"Done! Files generated in deals/Synthetic Co/Synthetic Fleet Loan_" in page("workflows.md")
    outputs = page("outputs.md")
    assert f"{COMPANY}_{PROPOSAL}_CAM.docx" in outputs and f"{COMPANY}_{PROPOSAL}_Spreading.xlsx" in outputs


def test_saved_sources_and_conventions_print_what_the_workflow_page_shows(deal):
    (deal.root / "registry_extract.txt").write_text("Synthetic registry extract (example)\n", encoding="utf-8")
    saved = deal.run("source_manifest", *deal.deal_args(), "--step", "research", "--claim",
                     "Synthetic legal identity extract", "--file", "registry_extract.txt", "--url",
                     "https://example.invalid/registry")
    entry = json.loads(saved.stdout)
    assert {k: v for k, v in entry.items() if k != "fetched_date"} == {
        "filename": "example.invalid_registry.txt", "url": "https://example.invalid/registry", "step": "research",
        "claim": "Synthetic legal identity extract"}
    assert deal.run("source_manifest", "--check-sources", *deal.deal_args()).stdout.strip() == '{\n  "missing_saved_sources": false\n}'
    wrote = deal.run("conventions", "--company", COMPANY, "--write", "--financials-source", "analyst-supplied", "--note",
                     "Depreciation embedded in Cost of Goods Sold", "--proposal", PROPOSAL, "--confirmed-date", "2026-01-15")
    convention = json.loads(wrote.stdout)
    assert convention["financials_source_default"] == "analyst-supplied" and convention["confirmed_date"] == "2026-01-15"
    assert convention["history"] == [{"financials_source": "analyst-supplied", "note": "Depreciation embedded in Cost of Goods Sold",
                                      "confirmed_date": "2026-01-15", "proposal": PROPOSAL}]
    assert json.loads(deal.run("conventions", "--enterprise", "--read").stdout) == {"found": False, "convention": None}
    workflows = page("workflows.md")
    for snippet in ('"filename": "example.invalid_registry.txt"', '"missing_saved_sources": false',
                    '"financials_source_default": "analyst-supplied"', '{"found": false, "convention": null}'):
        assert snippet in workflows, snippet


def test_the_research_export_prints_what_the_workflow_page_shows(deal):
    done = deal.run("research_export", *deal.deal_args(), "--brief", deal.path("brief.md"))
    assert done.returncode == 0, done.stderr
    folder = next((deal.root / DEAL_DIR).glob(f"{PROPOSAL}_20*-*-*"))
    assert normalised(done.stdout).strip() == (
        f"Done! Research brief exported to {DEAL_DIR}/{folder.name}/{COMPANY}_{PROPOSAL}_Research_Brief.docx")
    assert (folder / f"{COMPANY}_{PROPOSAL}_Research_Brief.docx").is_file()
    assert "Done! Research brief exported to deals/Synthetic Co/Synthetic Fleet Loan_" in page("workflows.md")


def test_calibrate_without_a_key_prints_what_the_workflow_page_shows(tmp_path):
    work = tmp_path / "calibration"
    samples = work / "inputs" / "calibration_samples"
    samples.mkdir(parents=True)
    (work / "config").mkdir()
    _write_pdf(samples / "sample_cam.pdf", "Synthetic Co", pages=1, lines_per_page=5)
    done = subprocess.run([sys.executable, str(SCRIPTS_DIR / "calibrate.py"), "--type", "asset_finance"], cwd=work,
                          env=child_env(), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    lines = normalised(done.stdout).strip().splitlines()
    assert done.returncode == 0 and lines == [
        "[INFO] ANTHROPIC_API_KEY not found. Running calibrate.py in --mock mode.",
        "[MOCK] Read 295 characters from 1 sample PDF(s).",
        "[MOCK] Wrote placeholder `config/style_guide.md`.",
        "[MOCK] Wrote placeholder template to templates/local/cam/asset_finance_cam.md."]
    workflows = page("workflows.md")
    for line in lines:
        assert line in workflows, line
    assert "(MOCK)" in (work / "config" / "style_guide.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Errors: docs/troubleshooting.md
# ---------------------------------------------------------------------------

def test_the_state_error_messages_are_the_ones_the_troubleshooting_page_quotes(deal):
    path = deal.state_file()
    good = path.read_text(encoding="utf-8")
    trouble = page("troubleshooting.md")
    try:
        bad = json.loads(good)
        bad["ratios"] = []
        path.write_text(json.dumps(bad), encoding="utf-8")
        shape = deal.run("policy_check", *deal.deal_args())
        bad = json.loads(good)
        bad["schema_version"] = "9.0.0"
        path.write_text(json.dumps(bad), encoding="utf-8")
        newer = deal.run("spreading_check", *deal.deal_args(), "--financials", deal.path("financials_input.json"))
        path.write_text("{ not json", encoding="utf-8")
        corrupt = deal.run("state_manager", "--check-steps", *deal.deal_args(), "--required", "spread")
    finally:
        path.write_text(good, encoding="utf-8")
    assert shape.returncode == 1 and shape.stdout == ""
    assert re.match(r'error: Cannot use .*state\.json: "ratios" must be an object keyed by period whose values are '
                    r'objects \(e\.g\. \{"FY-Current": \{\.\.\.\}\}\), found list\.', shape.stderr)
    assert 'error: Cannot use deals/.../state.json: "ratios" must be an object keyed by period whose values are objects' in trouble
    assert newer.returncode == 1
    assert (f"it was written by a newer framework (schema_version 9.0.0) than this one supports ({SCHEMA_VERSION}); "
            "writing would downgrade it. Update this checkout of the framework instead; nothing was changed.") in newer.stderr
    assert (f"it was written by a newer framework (schema_version 9.0.0) than this one supports ({SCHEMA_VERSION}); "
            "writing would downgrade it.") in trouble
    assert corrupt.returncode == 1 and "is corrupted and could not be parsed" in corrupt.stderr
    assert "error: state.json at ... is corrupted and could not be parsed" in trouble


def test_the_orchestrator_without_credentials_fails_the_way_the_troubleshooting_page_says(deal):
    done = deal.run("orchestrator", "--company", COMPANY, "--proposal", PROPOSAL, "--type", "asset_finance")
    assert done.returncode == 1
    assert "[1/3] Underwriter Agent drafting CAM for Synthetic Co" in done.stdout
    assert "Could not resolve authentication method" in done.stderr
    trouble = page("troubleshooting.md")
    assert "Could not resolve authentication method" in trouble and "[1/3] Underwriter Agent drafting CAM for ..." in trouble
    # the grounded figures were checkpointed before the model call, as docs/data-model.md says
    state = json.loads(deal.state_file().read_text(encoding="utf-8"))
    assert "policy_state" in state and "model_provenance" in state


# ---------------------------------------------------------------------------
# The examples themselves
# ---------------------------------------------------------------------------

def test_the_example_inputs_are_valid_json_with_the_documented_shape():
    structure = json.loads((EXAMPLES / "deal_structure.json").read_text(encoding="utf-8"))
    assert set(structure) == {"covenants", "collateral", "security_package", "guarantees"}
    assert structure["covenants"] == [{"metric": "dscr", "type": "minimum", "threshold": 1.25},
                                      {"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}]
    assert json.loads((EXAMPLES / "stress_input.json").read_text(encoding="utf-8")) == {
        "revenue_haircut_pct": 5, "interest_rate_bump_bps": 200}
    for name, periods in (("financials_input.json", {"FY-1", "FY-Current"}), ("forward_input.json", {"FY+1", "FY+2"})):
        assert set(json.loads((EXAMPLES / name).read_text(encoding="utf-8"))) == periods


def test_the_examples_and_the_new_pages_contain_no_real_looking_data():
    """Synthetic names and figures only: the PII scan finds nothing in the examples or the pages that quote them,
    apart from ISO dates (an example date is not sensitive)."""
    files = [*EXAMPLES.glob("*.md"), *EXAMPLES.glob("*.json"), DOCS / "workflows.md", DOCS / "commands.md",
             DOCS / "troubleshooting.md", DOCS / "ai-assurance.md", DOCS / "contributing.md", DOCS / "operations.md"]
    for path in files:
        findings = pii_scan.scan_for_likely_real_data(path.read_text(encoding="utf-8"))
        real = [f for f in findings if "ISO date" not in f["reason"]]
        assert real == [], (path.name, real)


def test_the_environment_this_test_runs_in_cannot_reach_a_model():
    env = child_env()
    assert not any(k.upper().startswith("ANTHROPIC") for k in env)
    assert os.path.basename(env["HOME"]).startswith("cli-home-")
