"""Tests for scripts/orchestrator.py: verdict parsing, the Maker-Checker
governance loop, per-iteration checkpointing, and export gating.

run_pipeline() reads agents/*.md, config/style_guide.md, and
templates/cam/*.md relative to the current working directory (same as
deal_export.py / state_manager.py's own base_dir=None convention) -- the
`project_root` fixture below builds a minimal fake project in tmp_path and
chdirs into it so these tests never touch the real repository's deals/ or
templates/ directories.
"""
import json
import os
from types import SimpleNamespace

import docx
import pytest

from orchestrator import (
    MAX_REVIEW_ITERATIONS,
    _load_multi_period_financials,
    evaluate_financial_model,
    parse_verdict,
    run_pipeline,
)
from state_manager import read_state, state_path


class MockClient:
    """Stand-in for anthropic.Anthropic: returns each response in `responses`,
    in order, from successive .messages.create() calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.call_count += 1
        if not self.responses:
            raise AssertionError("MockClient received more calls than responses were queued")
        text = self.responses.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "underwriter_agent.md").write_text("MAKER PROMPT", encoding="utf-8")
    (agents_dir / "risk_reviewer_agent.md").write_text("CHECKER PROMPT", encoding="utf-8")

    cam_dir = tmp_path / "templates" / "cam"
    cam_dir.mkdir(parents=True)
    (cam_dir / "corporate_credit_cam.md").write_text("TEMPLATE", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    return tmp_path


def _approved_json(notes=None):
    return '```json\n' + json.dumps({"verdict": "APPROVED", "notes": notes}) + '\n```'


def _rejected_json(notes):
    return '```json\n' + json.dumps({"verdict": "REJECTED", "notes": notes}) + '\n```'


# ---------------------------------------------------------------------------
# parse_verdict()
# ---------------------------------------------------------------------------

def test_parse_verdict_extracts_approved():
    text = "Looks solid overall.\n" + _approved_json()
    verdict, notes = parse_verdict(text)
    assert verdict == "APPROVED"
    assert notes is None


def test_parse_verdict_extracts_rejected_with_notes():
    text = _rejected_json("DSCR in section 11 doesn't match the spreading workbook.")
    verdict, notes = parse_verdict(text)
    assert verdict == "REJECTED"
    assert notes == "DSCR in section 11 doesn't match the spreading workbook."


def test_parse_verdict_is_case_insensitive_on_verdict_value():
    text = '```json\n{"verdict": "approved", "notes": null}\n```'
    verdict, _ = parse_verdict(text)
    assert verdict == "APPROVED"


def test_parse_verdict_falls_back_to_rejected_when_json_block_missing():
    text = "The draft looks fine overall but I have some concerns."
    verdict, notes = parse_verdict(text)
    assert verdict == "REJECTED"
    assert notes == text


def test_parse_verdict_falls_back_to_rejected_on_malformed_json():
    text = '```json\n{"verdict": "APPROVED", "notes": \n```'  # truncated/invalid JSON
    verdict, notes = parse_verdict(text)
    assert verdict == "REJECTED"
    assert notes == text


def test_parse_verdict_falls_back_to_rejected_on_unrecognized_verdict_value():
    text = '```json\n{"verdict": "MAYBE", "notes": "unsure"}\n```'
    verdict, notes = parse_verdict(text)
    assert verdict == "REJECTED"
    assert notes == text


def test_parse_verdict_falls_back_to_rejected_on_empty_response():
    verdict, notes = parse_verdict("")
    assert verdict == "REJECTED"
    assert notes == ""


# ---------------------------------------------------------------------------
# _load_multi_period_financials(): --spread takes precedence over --financials
# ---------------------------------------------------------------------------

def test_load_multi_period_financials_prefers_spread_over_financials(tmp_path):
    financials_path = tmp_path / "financials.json"
    spread_path = tmp_path / "spread.json"
    financials_path.write_text(json.dumps({"FY-Current": {"revenue": 1}}))
    spread_path.write_text(json.dumps({"FY-Current": {"revenue": 2}}))

    result = _load_multi_period_financials(str(financials_path), str(spread_path))
    assert result == {"FY-Current": {"revenue": 2}}


def test_load_multi_period_financials_falls_back_to_financials_when_no_spread(tmp_path):
    financials_path = tmp_path / "financials.json"
    financials_path.write_text(json.dumps({"FY-Current": {"revenue": 1}}))

    result = _load_multi_period_financials(str(financials_path), None)
    assert result == {"FY-Current": {"revenue": 1}}


def test_load_multi_period_financials_returns_none_when_neither_given():
    assert _load_multi_period_financials(None, None) is None


# ---------------------------------------------------------------------------
# evaluate_financial_model() -- re-exported from spreading_builder and used
# directly by run_pipeline(); see test_spreading_builder.py for full coverage.
# ---------------------------------------------------------------------------

def test_evaluate_financial_model_usable_from_orchestrator():
    result = evaluate_financial_model({"FY-Current": {"revenue": 100, "cost_of_sales": 40}})
    assert result["financials"]["FY-Current"]["gross_profit"] == 60


# ---------------------------------------------------------------------------
# Governance loop: export gating, per-iteration checkpointing.
# ---------------------------------------------------------------------------

def test_run_pipeline_exports_when_approved_on_first_iteration(project_root):
    client = MockClient([
        "# Draft CAM",           # [1/3] Underwriter draft
        _approved_json(),        # [2/3] Risk Reviewer audit, iteration 1
    ])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    deal_dir = os.path.dirname(state_path("Acme Corp", "Fleet Loan"))
    assert os.path.isfile(os.path.join(deal_dir, "draft_v1.md"))
    assert not os.path.exists(os.path.join(deal_dir, "draft_v2.md"))

    state = read_state("Acme Corp", "Fleet Loan")
    assert [entry["verdict"] for entry in state["review_trail"]] == ["APPROVED"]
    assert state["review_verdict"] == "APPROVED"
    assert "export" in state["steps_completed"]

    docx_path = os.path.join(deal_dir, "Acme Corp_Fleet Loan_CAM.docx")
    xlsx_path = os.path.join(deal_dir, "Acme Corp_Fleet Loan_Spreading.xlsx")
    assert os.path.isfile(docx_path)
    assert os.path.isfile(xlsx_path)


def test_run_pipeline_revises_and_exports_after_one_rejection(project_root):
    client = MockClient([
        "# Draft v1",                                  # initial draft
        _rejected_json("Fix the EBITDA figure."),      # iteration 1 audit
        "# Draft v2 (revised)",                         # Maker revision
        _approved_json(),                                # iteration 2 audit
    ])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    deal_dir = os.path.dirname(state_path("Acme Corp", "Fleet Loan"))
    with open(os.path.join(deal_dir, "draft_v1.md"), encoding="utf-8") as f:
        assert f.read() == "# Draft v1"
    with open(os.path.join(deal_dir, "draft_v2.md"), encoding="utf-8") as f:
        assert f.read() == "# Draft v2 (revised)"

    state = read_state("Acme Corp", "Fleet Loan")
    assert [entry["verdict"] for entry in state["review_trail"]] == ["REJECTED", "APPROVED"]
    assert state["review_trail"][0]["notes"] == "Fix the EBITDA figure."
    assert state["review_verdict"] == "APPROVED"

    # The exported draft must be the revised, approved one -- not the rejected first draft.
    docx_path = os.path.join(deal_dir, "Acme Corp_Fleet Loan_CAM.docx")
    doc = docx.Document(docx_path)
    assert doc.paragraphs[0].text == "Draft v2 (revised)"


def test_run_pipeline_exits_nonzero_and_skips_export_after_max_rejections(project_root):
    client = MockClient([
        "# Draft",
        _rejected_json("still wrong 1"),
        "# Draft",
        _rejected_json("still wrong 2"),
        "# Draft",
        _rejected_json("still wrong 3"),
    ])

    with pytest.raises(SystemExit) as exc_info:
        run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                     client=client)
    assert exc_info.value.code == 1

    deal_dir = os.path.dirname(state_path("Acme Corp", "Fleet Loan"))
    for i in range(1, MAX_REVIEW_ITERATIONS + 1):
        assert os.path.isfile(os.path.join(deal_dir, f"draft_v{i}.md"))

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["review_verdict"] == "REJECTED"
    assert len(state["review_trail"]) == MAX_REVIEW_ITERATIONS
    assert not os.path.exists(os.path.join(deal_dir, "Acme Corp_Fleet Loan_CAM.docx"))
    assert client.call_count == 6  # 1 initial draft + 3 audits + 2 revisions, no 3rd revision


def test_run_pipeline_stores_financials_and_ratios_on_state(project_root):
    client = MockClient(["# Draft CAM", _approved_json()])
    multi_period_financials = {"FY-Current": {"revenue": 1000, "cost_of_sales": 400}}

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials=multi_period_financials, client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["financials"]["FY-Current"]["gross_profit"] == 600
    assert "ratios" in state and "FY-Current" in state["ratios"]
    assert "spread" in state["steps_completed"]


def test_run_pipeline_stores_collateral_data_on_state(project_root):
    client = MockClient(["# Draft CAM", _approved_json()])
    collateral_data = [{"asset_class": "HGV", "exposure": 100, "collateral_value": 80,
                         "perfection_status": "Registered"}]

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 collateral_data=collateral_data, client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["collateral"] == collateral_data
