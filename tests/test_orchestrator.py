"""Tests for scripts/orchestrator.py: verdict parsing, the Maker-Checker
governance loop, per-iteration checkpointing, deterministic policy
enforcement, and export gating.

run_pipeline() reads agents/*.md, config/style_guide.md, and
templates/cam/*.md relative to the current working directory (same as
deal_export.py / state_manager.py's own base_dir=None convention) -- the
`project_root` fixture below builds a minimal fake project in tmp_path and
chdirs into it so these tests never touch the real repository's deals/ or
templates/ directories.

Since _apply_deterministic_policy_checks() now forces REJECTED whenever a
draft's trailing structured JSON block doesn't cover every required CP and
risk category, every MockClient "draft" response that a test expects to be
approved and exported must include a *compliant* block -- see
_compliant_draft() below. Drafts used only to exercise an LLM-originated (or
deliberately triggered code-enforced) REJECTED path don't need one.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import docx
import pytest

from orchestrator import (
    MAX_REVIEW_ITERATIONS,
    REQUIRED_RISK_TAXONOMY,
    _apply_deterministic_policy_checks,
    _build_grounding_context,
    _check_reported_figures,
    _xml_block,
    _completion_kwargs,
    _compute_collateral_cover_pct,
    _content_hash,
    _ground_truth_figures,
    _load_multi_period_financials,
    _normalize_category,
    _resolve_maker_checker_config,
    _values_match,
    evaluate_financial_model,
    parse_underwriter_output,
    parse_verdict,
    run_pipeline,
)
from state_manager import StateError, read_state, state_path, write_state


class MockClient:
    """Stand-in for anthropic.Anthropic: returns each response in `responses`,
    in order, from successive .messages.create() calls. Also records every
    call's kwargs (self.calls) so a test can assert on what model/
    temperature/etc. a given call actually received -- e.g. confirming the
    Maker and Checker calls used different configured models."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.call_count += 1
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("MockClient received more calls than responses were queued")
        text = self.responses.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


# config/settings.json ships checked into the real repo with maker_model
# already set (a fork gets a working config automatically) -- this fixture
# mirrors that so every test gets a resolvable config by default, same as
# production. Tests specifically about a missing/malformed config (below)
# override or remove this file themselves.
DEFAULT_TEST_MAKER_MODEL = "claude-test-maker-model"


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "underwriter_agent.md").write_text("MAKER PROMPT", encoding="utf-8")
    (agents_dir / "risk_reviewer_agent.md").write_text("CHECKER PROMPT", encoding="utf-8")

    cam_dir = tmp_path / "templates" / "cam"
    cam_dir.mkdir(parents=True)
    (cam_dir / "corporate_credit_cam.md").write_text("TEMPLATE", encoding="utf-8")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"maker_model": DEFAULT_TEST_MAKER_MODEL}), encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# _resolve_maker_checker_config()/_completion_kwargs(): the Maker and
# Checker are independently configurable model/temperature knobs, read from
# config/settings.json fresh on every call rather than a hardcoded constant.
# ---------------------------------------------------------------------------

def test_resolve_maker_checker_config_raises_when_no_config_file_at_all(tmp_path, monkeypatch):
    """config/settings.json ships checked into the repo with maker_model
    already set -- a fork gets a working config automatically. No config
    file at all (as opposed to project_root's normal fixture setup) means
    it was deleted or this is some other genuinely broken setup, not "a
    fresh install that hasn't configured it yet" -- silently substituting
    a hardcoded fallback model would let a real deal draft on an
    unconfigured, untracked model with no indication anything was wrong
    (the config-drift failure mode issue #34 was about). Uses a bare
    tmp_path/monkeypatch rather than project_root, since the whole point
    here is the *absence* of config/settings.json -- project_root's own
    fixture setup always creates one."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="maker_model"):
        _resolve_maker_checker_config()


def test_resolve_maker_checker_config_reads_model_from_settings(project_root):
    (project_root / "config" / "settings.json").write_text(
        json.dumps({"maker_model": "claude-custom-v9"}), encoding="utf-8",
    )

    config = _resolve_maker_checker_config()
    assert config["maker_model"] == "claude-custom-v9"
    assert config["checker_model"] == "claude-custom-v9"  # still no separate override given


def test_resolve_maker_checker_config_checker_model_independent_of_maker(project_root):
    """The core of the model-independence fix: an explicit checker_model
    must NOT be overridden by the maker's model."""
    (project_root / "config" / "settings.json").write_text(json.dumps({
        "maker_model": "claude-maker-model",
        "checker_model": "claude-checker-model",
        "maker_temperature": 0.2,
        "checker_temperature": 0.0,
    }), encoding="utf-8")

    config = _resolve_maker_checker_config()
    assert config["maker_model"] == "claude-maker-model"
    assert config["checker_model"] == "claude-checker-model"
    assert config["maker_temperature"] == 0.2
    assert config["checker_temperature"] == 0.0


def test_resolve_maker_checker_config_raises_on_malformed_settings(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="maker_model"):
        _resolve_maker_checker_config()


def test_completion_kwargs_omits_temperature_when_none():
    assert _completion_kwargs("some-model", None) == {"model": "some-model"}


def test_completion_kwargs_includes_temperature_via_extra_body_when_given():
    """Routed through extra_body, not a direct temperature= kwarg -- the
    anthropic SDK (>=1.x) dropped temperature/top_p/top_k from
    Messages.create()'s own typed signature; a direct kwarg raises
    TypeError before any request is sent. extra_body is the SDK's own
    documented escape hatch for this. This project's own test suite can't
    catch a regression back to the direct-kwarg form on its own (MockClient
    accepts arbitrary **kwargs), so this test pins the exact shape."""
    assert _completion_kwargs("some-model", 0.3) == {
        "model": "some-model", "extra_body": {"temperature": 0.3},
    }


def test_run_pipeline_uses_a_different_model_for_maker_and_checker_calls(project_root):
    """End-to-end proof, not just the config-parsing function in isolation:
    with checker_model configured differently, the actual client.messages
    .create() calls for the draft/revision (Maker) vs audit (Checker) steps
    receive different `model` kwargs."""
    (project_root / "config" / "settings.json").write_text(json.dumps({
        "maker_model": "claude-maker-model", "checker_model": "claude-checker-model",
    }), encoding="utf-8")

    client = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit", client=client)

    assert client.calls[0]["model"] == "claude-maker-model"   # [1/3] draft
    assert client.calls[1]["model"] == "claude-checker-model"  # [2/3] audit


# ---------------------------------------------------------------------------
# _content_hash()/model_provenance: which model and which exact version of
# each prompt file produced a deal's draft/audit must be recoverable later,
# even if the model or the prompt changes afterward.
# ---------------------------------------------------------------------------

def test_content_hash_is_stable_and_distinguishes_different_content():
    assert _content_hash("MAKER PROMPT") == _content_hash("MAKER PROMPT")
    assert _content_hash("MAKER PROMPT") != _content_hash("MAKER PROMPT v2")


def test_run_pipeline_records_model_provenance_on_state(project_root):
    client = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit", client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    provenance = state["model_provenance"]
    assert provenance["maker_model"] == DEFAULT_TEST_MAKER_MODEL
    assert provenance["checker_model"] == DEFAULT_TEST_MAKER_MODEL  # no checker_model configured -- falls back to maker
    assert provenance["underwriter_prompt_hash"] == _content_hash("MAKER PROMPT")
    assert provenance["risk_reviewer_prompt_hash"] == _content_hash("CHECKER PROMPT")


def test_run_pipeline_exports_into_the_deals_existing_dated_folder(project_root):
    """Issue #97: a deal started on an earlier day must export its .docx/.xlsx
    next to its own state.json, not into a fresh folder dated today."""
    from datetime import datetime
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-10", deal_type="corporate_credit")

    client = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit", client=client)

    original = project_root / "deals" / "Acme Corp" / "Fleet Loan_2026-01-10"
    assert (original / "Acme Corp_Fleet Loan_CAM.docx").exists()
    assert (original / "Acme Corp_Fleet Loan_Spreading.xlsx").exists()
    assert (original / "state.json").exists()
    today = datetime.now().strftime("%Y-%m-%d")
    assert not (project_root / "deals" / "Acme Corp" / f"Fleet Loan_{today}").exists()


def test_run_pipeline_new_review_exports_into_todays_folder_alongside_its_state(project_root):
    from datetime import datetime
    write_state("Acme Corp", "Fleet Loan", date_str="2026-01-10", deal_type="corporate_credit")

    client = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client, new_review=True)

    today = datetime.now().strftime("%Y-%m-%d")
    fresh = project_root / "deals" / "Acme Corp" / f"Fleet Loan_{today}"
    assert (fresh / "Acme Corp_Fleet Loan_CAM.docx").exists()
    assert (fresh / "state.json").exists()
    # Last year's folder is left exactly as it was -- no export leaked into it.
    assert not (project_root / "deals" / "Acme Corp" / "Fleet Loan_2026-01-10" / "Acme Corp_Fleet Loan_CAM.docx").exists()


# ---------------------------------------------------------------------------
# Text encoding (issue #137): the files orchestrator.py loads into a prompt
# are read under one explicit policy, so a cp1252-default machine (Windows)
# behaves the same as a UTF-8 one. `cp1252_default_open` (conftest.py) makes
# any open() that forgets `encoding=` behave like Windows on every platform.
# ---------------------------------------------------------------------------

def _run_once(client=None):
    client = client or MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit", client=client)
    return client


def test_maker_prompt_keeps_the_shipped_agent_files_non_ascii_characters(project_root, cp1252_default_open):
    # The real agents/underwriter_agent.md has an em dash and a box-drawing
    # ownership-tree example (Guideline 12); read under cp1252 they reached
    # the model as mojibake ("â”œâ”€...").
    tree = "├── [Parent] 60% — └── [Sibling] 40%"
    (project_root / "agents" / "underwriter_agent.md").write_text("MAKER PROMPT\n" + tree, encoding="utf-8")

    client = _run_once()

    assert tree in client.calls[0]["messages"][0]["content"]


def test_a_shipped_agent_file_that_is_not_utf8_fails_loudly_naming_it(project_root):
    from textio import TextEncodingError
    (project_root / "agents" / "underwriter_agent.md").write_bytes("Price £1".encode("cp1252"))
    with pytest.raises(TextEncodingError, match="underwriter_agent.md"):
        _run_once(MockClient([]))


def test_legacy_cp1252_style_guide_is_still_read_but_with_a_warning(project_root, capsys):
    (project_root / "config" / "style_guide.md").write_bytes("Quote £ and – dashes".encode("cp1252"))

    client = _run_once()

    assert "Quote £ and – dashes" in client.calls[0]["messages"][0]["content"]
    err = capsys.readouterr().err
    assert "style_guide.md" in err and "cp1252" in err


@pytest.mark.parametrize("relpath", [
    "config/credit_policy_notes.md",
    "config/deal_learnings.md",
    "deals/Acme Corp/_learnings.md",
])
def test_every_other_legacy_capable_user_file_also_falls_back_with_a_warning(project_root, capsys, relpath):
    legacy_text = "Note £ and – dashes"
    needs_policy = relpath.endswith("credit_policy_notes.md")  # notes only reach the prompt with a policy
    if needs_policy:
        (project_root / "config" / "credit_policy.md").write_text("POLICY", encoding="utf-8")
    path = project_root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(legacy_text.encode("cp1252"))

    draft = _compliant_draft(credit_policy_considered=True) if needs_policy else _compliant_draft()
    client = _run_once(MockClient([draft, _approved_json()]))

    assert legacy_text in client.calls[0]["messages"][0]["content"]
    err = capsys.readouterr().err
    assert path.name in err and "cp1252" in err


def test_a_user_owned_file_that_decodes_under_neither_encoding_fails_naming_it(project_root):
    from textio import TextEncodingError
    (project_root / "config" / "credit_policy.md").write_bytes(b"limit \x81\x8d")
    with pytest.raises(TextEncodingError, match="credit_policy.md"):
        _run_once(MockClient([]))


def test_utf8_user_files_reach_the_prompt_intact_under_a_cp1252_default(project_root, cp1252_default_open):
    text = "Policy: exposure ≤ £5m — see Álvarez ├──"
    (project_root / "config" / "credit_policy.md").write_text(text, encoding="utf-8")
    (project_root / "config" / "style_guide.md").write_text(text, encoding="utf-8")

    # A calibrated credit policy makes the draft declare it considered it.
    client = _run_once(MockClient([_compliant_draft(credit_policy_considered=True), _approved_json()]))

    prompt = client.calls[0]["messages"][0]["content"]
    assert prompt.count(text) == 2  # once as the style guide, once inside the policy block


def test_a_rejection_with_non_cp1252_notes_still_revises_and_exports_on_a_cp1252_pipe(project_root, monkeypatch):
    """Issue #154: the Checker's notes are printed on a REJECTED verdict; on a
    Windows pipe (cp1252) a >= sign raised UnicodeEncodeError there, aborting
    the run before the revision call and the export."""
    import io
    import sys
    import textio

    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", write_through=True)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252",
                                                        errors="backslashreplace", write_through=True))
    textio.configure_stdio()  # what every script's __main__ block now does first

    notes = "DSCR ≥ 1.25x → breach for Łukasz"
    client = MockClient([
        _compliant_draft(body="# FIRST DRAFT MARKER"), _rejected_json(notes),
        _compliant_draft(body="# REVISED DRAFT MARKER"), _approved_json(),
    ])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit", client=client)

    assert client.call_count == 4  # draft, rejected audit, REVISION, approved audit
    assert notes in stdout.buffer.getvalue().decode("utf-8")
    exported = list((project_root / "deals" / "Acme Corp").glob("*/*_CAM.docx"))
    assert len(exported) == 1, "the revised draft was never exported"
    exported_text = "\n".join(p.text for p in docx.Document(str(exported[0])).paragraphs)
    # The REVISED draft is what was exported, not the rejected first one.
    assert "REVISED DRAFT MARKER" in exported_text and "FIRST DRAFT MARKER" not in exported_text


def _approved_json(notes=None):
    return '```json\n' + json.dumps({"verdict": "APPROVED", "notes": notes}) + '\n```'


def _rejected_json(notes):
    return '```json\n' + json.dumps({"verdict": "REJECTED", "notes": notes}) + '\n```'


# A deal with no covenants/security_package/guarantees in state.json only
# ever requires the two standard CPs and the one standard CS, and the
# Underwriter is always free to self-report every canonical risk category
# as covered.
STANDARD_CP_IDS = ["KYC-AML", "FACILITY-EXECUTION"]
STANDARD_CS_IDS = ["MI-REPORTING"]
ALL_CATEGORIES_COVERED = {category: {"status": "covered"} for category in REQUIRED_RISK_TAXONOMY}


def _compliant_draft(body="# Draft CAM", cp_ids=STANDARD_CP_IDS, cs_ids=STANDARD_CS_IDS,
                      risk_categories=None, reported_figures=None, sources=None,
                      financials_source_disclosed=None, credit_policy_considered=None):
    """A draft whose trailing structured JSON block satisfies every
    deterministic policy check by default (see agents/underwriter_agent.md's
    Structured Output guideline) -- for tests where the draft is expected to
    actually be approved and exported.

    reported_figures defaults to empty: per the Structured Output guideline,
    omitting a metric entirely (rather than reporting an unverifiable guess)
    is always compliant, and most tests below have no financial data behind
    them for a reported figure to be checked against anyway.

    sources defaults to non-empty (unlike reported_figures) since the
    Missing Narrative Sources check requires at least one citation to be
    declared whenever a draft exists at all -- an empty list is never
    compliant, so a test exercising that specific check must override it
    explicitly rather than relying on this default.

    financials_source_disclosed defaults to omitted from the payload
    entirely (unlike the other fields), matching a real Maker draft for a
    framework-computed deal, where the key is simply irrelevant -- only a
    test exercising the analyst-supplied caveat check needs to pass it."""
    payload = {
        "cp_ids_included": list(cp_ids),
        "cs_ids_included": list(cs_ids),
        "risk_categories_covered": risk_categories if risk_categories is not None else ALL_CATEGORIES_COVERED,
        "reported_figures": reported_figures if reported_figures is not None else {},
        "sources": sources if sources is not None else ["Test Source"],
    }
    if financials_source_disclosed is not None:
        payload["financials_source_disclosed"] = financials_source_disclosed
    if credit_policy_considered is not None:
        payload["credit_policy_considered"] = credit_policy_considered
    return body + "\n\n```json\n" + json.dumps(payload) + "\n```"


# ---------------------------------------------------------------------------
# A state.json the pipeline cannot use is refused up front (issue #171)
# ---------------------------------------------------------------------------

def _bad_value_for(key):
    from state_manager import STATE_SHAPES
    return "made-up" if STATE_SHAPES[key] == "source" else "x"      # a string is wrong for every other kind


# What run_pipeline reads from an existing state, pinned as a LITERAL (parametrizing over the module's own constant
# would let a dropped key take its test with it).
ORCHESTRATOR_STATE_KEYS = ("steps_completed", "financials", "ratios", "financials_source", "collateral",
                           "multi_period_financials", "stress_assumptions", "downside_case", "covenants",
                           "security_package", "guarantees", "review_trail")


def _orchestrator_state_keys():
    return list(ORCHESTRATOR_STATE_KEYS)


def test_the_orchestrators_declared_keys_are_exactly_the_pinned_ones():
    import orchestrator
    assert tuple(orchestrator.STATE_KEYS_READ) == ORCHESTRATOR_STATE_KEYS


@pytest.mark.parametrize("key", _orchestrator_state_keys())
def test_a_malformed_state_is_refused_before_any_model_call_or_write(project_root, key):
    """For every key the orchestrator reads (the pinned literal above)."""
    from state_manager import StateShapeError
    bad_state = {key: _bad_value_for(key)}
    write_state("Acme Corp", "Fleet Loan", **bad_state)
    path = Path(state_path("Acme Corp", "Fleet Loan"))
    before = path.read_bytes()
    client = MockClient([])                               # any model call would raise
    with pytest.raises(StateShapeError, match=key):
        run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit", client=client)
    assert client.call_count == 0 and path.read_bytes() == before


@pytest.mark.parametrize("key", _orchestrator_state_keys())
def test_a_null_value_is_not_recorded_for_every_key_the_orchestrator_reads(project_root, key):
    """`.get(key, default)` returns None for a present-but-null key; the pipeline must treat it as absent. Reaching the
    first model call (here: the mock refusing one) proves the saved state was read without a crash."""
    write_state("Acme Corp", "Fleet Loan", **{key: None})
    with pytest.raises(AssertionError, match="more calls than responses"):
        run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit", client=MockClient([]))


# ---------------------------------------------------------------------------
# Non-positive denominators (issue #169): the headless pipeline hands the Maker the covenant's explanation
# ---------------------------------------------------------------------------

def test_a_loss_making_borrower_reaches_the_maker_as_unresolvable_with_the_reason(project_root):
    """The orchestrator must pass its freshly computed `financials` to the policy engine, or the reason for an N/A
    ratio degrades to 'no financials are recorded'. The unresolvable covenant also forces a code-enforced REJECTED."""
    write_state("Acme Corp", "Fleet Loan",
                covenants=[{"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}])
    loss_making = {"revenue": 500, "cost_of_sales": 400, "admin_expenses": 200, "long_term_debt": 500,
                   "share_capital": 100, "retained_profit": -300}
    client = MockClient([_compliant_draft(), _approved_json()])
    with pytest.raises(SystemExit):                      # the Reviewer approves, the code check overrides it
        run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                     multi_period_financials={"FY-Current": loss_making}, client=client, max_iterations=1)

    maker_message = client.calls[0]["messages"][0]["content"]
    assert '"status": "UNRESOLVABLE"' in maker_message
    assert "gross leverage not meaningful: EBITDA is negative" in maker_message
    assert '"status": "PASS"' not in maker_message
    trail = read_state("Acme Corp", "Fleet Loan")["review_trail"]
    assert trail[-1]["verdict"] == "REJECTED"
    assert "gross leverage not meaningful: EBITDA is negative" in trail[-1]["notes"]


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


def test_parse_verdict_ignores_an_earlier_echoed_json_block_and_finds_the_trailing_verdict():
    """The grounding context hands the reviewer its own ```json blocks
    (financials/ratios/collateral) to check the draft against -- if it
    quotes one back while explaining its reasoning, that earlier block must
    not be mistaken for the real, trailing verdict block."""
    text = (
        "Here are the ratios I'm checking the draft against:\n"
        '```json\n{"dscr": 5.1, "gross_leverage": 0.68}\n```\n'
        "Everything in the draft reconciles with the above.\n"
        + _approved_json()
    )
    verdict, notes = parse_verdict(text)
    assert verdict == "APPROVED"
    assert notes is None


def test_parse_verdict_skips_multiple_non_verdict_blocks_to_find_the_real_one():
    text = (
        '```json\n{"financials": {"FY-Current": {"revenue": 1000}}}\n```\n'
        '```json\n{"ratios": {"FY-Current": {"dscr": 5.1}}}\n```\n'
        + _rejected_json("Collateral value doesn't match the supplied data.")
    )
    verdict, notes = parse_verdict(text)
    assert verdict == "REJECTED"
    assert notes == "Collateral value doesn't match the supplied data."


# ---------------------------------------------------------------------------
# parse_underwriter_output()
# ---------------------------------------------------------------------------

def test_parse_underwriter_output_extracts_cp_ids_and_categories():
    draft = _compliant_draft(cp_ids=["KYC-AML", "FACILITY-EXECUTION", "SEC-PERFECT-AST-001"])
    result = parse_underwriter_output(draft)
    assert result["cp_ids_included"] == ["KYC-AML", "FACILITY-EXECUTION", "SEC-PERFECT-AST-001"]
    assert result["risk_categories_covered"] == ALL_CATEGORIES_COVERED


def test_parse_underwriter_output_defaults_to_empty_when_block_missing():
    result = parse_underwriter_output("# Draft CAM with no trailing JSON block")
    assert result == {
        "cp_ids_included": [], "cs_ids_included": [], "risk_categories_covered": {},
        "reported_figures": {}, "downside_breaches_acknowledged": [], "sources": [],
        "financials_source_disclosed": False, "credit_policy_considered": False,
    }


def test_parse_underwriter_output_defaults_to_empty_on_malformed_json():
    result = parse_underwriter_output('# Draft\n```json\n{"cp_ids_included": [\n```')
    assert result == {
        "cp_ids_included": [], "cs_ids_included": [], "risk_categories_covered": {},
        "reported_figures": {}, "downside_breaches_acknowledged": [], "sources": [],
        "financials_source_disclosed": False, "credit_policy_considered": False,
    }


def test_parse_underwriter_output_extracts_reported_figures():
    draft = _compliant_draft(cp_ids=["KYC-AML"], reported_figures={"dscr": 1.45, "ebitda": 950000})
    result = parse_underwriter_output(draft)
    assert result["reported_figures"] == {"dscr": 1.45, "ebitda": 950000}


def test_parse_underwriter_output_degrades_safely_when_fields_have_the_wrong_type():
    """A field present but the wrong JSON type (e.g. a list instead of an
    object) must degrade to the safe empty default, not propagate a
    malformed value that later crashes _apply_deterministic_policy_checks()."""
    draft = (
        "# Draft\n\n```json\n"
        + json.dumps({
            "cp_ids_included": "KYC-AML",  # should be a list, not a bare string
            "risk_categories_covered": ["Market"],  # should be a dict, not a list
            "reported_figures": ["dscr", 1.5],  # should be a dict, not a list
        })
        + "\n```"
    )
    result = parse_underwriter_output(draft)
    assert result == {
        "cp_ids_included": [], "cs_ids_included": [], "risk_categories_covered": {},
        "reported_figures": {}, "downside_breaches_acknowledged": [], "sources": [],
        "financials_source_disclosed": False, "credit_policy_considered": False,
    }


def test_apply_deterministic_policy_checks_does_not_crash_on_malformed_risk_categories_type():
    draft = (
        "# Draft\n\n```json\n"
        + json.dumps({"cp_ids_included": [], "risk_categories_covered": ["Market"], "reported_figures": {}})
        + "\n```"
    )
    verdict, notes = _apply_deterministic_policy_checks(
        "APPROVED", None, draft, _policy_state(required_cps=[]), {},
    )
    assert verdict == "REJECTED"
    assert "Missing or malformed Risk Category" in notes


def test_parse_underwriter_output_ignores_unrelated_earlier_json_blocks():
    draft = (
        "# Draft CAM\n"
        '```json\n{"some_other_data": 123}\n```\n'
        + _compliant_draft(body="", cp_ids=["KYC-AML"]).strip()
    )
    result = parse_underwriter_output(draft)
    assert result["cp_ids_included"] == ["KYC-AML"]


# ---------------------------------------------------------------------------
# _normalize_category(): fixed string transform, not semantic judgment.
# ---------------------------------------------------------------------------

def test_normalize_category_strips_trailing_risk_suffix():
    assert _normalize_category("Market Risk") == _normalize_category("Market") == "market"


def test_normalize_category_is_case_and_whitespace_insensitive():
    assert _normalize_category("  KEY MAN  ") == _normalize_category("Key Man Risk") == "key man"


# ---------------------------------------------------------------------------
# _apply_deterministic_policy_checks(): the code-enforced overlay that can
# only ever move a verdict from APPROVED to REJECTED, never the reverse.
# ---------------------------------------------------------------------------

def _policy_state(required_cps=None, required_css=None, covenant_results=None, security_gaps=None):
    return {
        "required_conditions_precedent": required_cps or [{"cp_id": "KYC-AML", "text": "KYC/AML clearance."}],
        "required_conditions_subsequent": (
            required_css if required_css is not None
            else [{"cs_id": "MI-REPORTING", "text": "Periodic MI submission."}]
        ),
        "covenant_results": covenant_results or [],
        "security_gaps": security_gaps or [],
    }


def test_apply_deterministic_policy_checks_passes_through_a_fully_compliant_approval():
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    verdict, notes = _apply_deterministic_policy_checks("APPROVED", None, draft, _policy_state(), {})
    assert verdict == "APPROVED"
    assert notes is None


def test_apply_deterministic_policy_checks_overrides_approval_on_missing_cp():
    """A missing required cp_id must force REJECTED even when the LLM itself said APPROVED."""
    draft = _compliant_draft(cp_ids=[])  # KYC-AML required but not reported as included
    verdict, notes = _apply_deterministic_policy_checks("APPROVED", None, draft, _policy_state(), {})
    assert verdict == "REJECTED"
    assert "Missing Required CP KYC-AML" in notes


def test_apply_deterministic_policy_checks_overrides_on_malformed_not_applicable_category():
    """not_applicable with an empty justification is malformed and must trip the override."""
    categories = dict(ALL_CATEGORIES_COVERED)
    categories["Operational"] = {"status": "not_applicable", "justification": "   "}
    draft = _compliant_draft(cp_ids=["KYC-AML"], risk_categories=categories)
    verdict, notes = _apply_deterministic_policy_checks("APPROVED", None, draft, _policy_state(), {})
    assert verdict == "REJECTED"
    assert "Missing or malformed Risk Category: Operational" in notes


def test_apply_deterministic_policy_checks_accepts_normalized_category_key():
    """"Market Risk" as a key must satisfy the canonical "Market" requirement."""
    categories = dict(ALL_CATEGORIES_COVERED)
    del categories["Market"]
    categories["Market Risk"] = {"status": "covered"}
    draft = _compliant_draft(cp_ids=["KYC-AML"], risk_categories=categories)
    verdict, notes = _apply_deterministic_policy_checks("APPROVED", None, draft, _policy_state(), {})
    assert verdict == "APPROVED"
    assert notes is None


def test_apply_deterministic_policy_checks_accepts_case_insensitive_status_value():
    """A status of "Covered" (capitalized, plausible LLM drift) must be
    recognized the same as "covered" -- only the category *key* was
    normalized before, not the *status value* itself."""
    categories = dict(ALL_CATEGORIES_COVERED)
    categories["Market"] = {"status": "Covered"}
    draft = _compliant_draft(cp_ids=["KYC-AML"], risk_categories=categories)
    verdict, notes = _apply_deterministic_policy_checks("APPROVED", None, draft, _policy_state(), {})
    assert verdict == "APPROVED"
    assert notes is None


def test_apply_deterministic_policy_checks_overrides_on_covenant_failure():
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    policy_state = _policy_state(covenant_results=[
        {"metric": "dscr", "type": "minimum", "threshold": 1.25, "actual": 1.0, "status": "FAIL", "headroom_pct": -0.2},
    ])
    verdict, notes = _apply_deterministic_policy_checks("APPROVED", None, draft, policy_state, {})
    assert verdict == "REJECTED"
    assert "Covenant FAIL: dscr" in notes


def test_apply_deterministic_policy_checks_overrides_on_unresolvable_covenant():
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    policy_state = _policy_state(covenant_results=[
        {"metric": "made_up_metric", "type": "minimum", "threshold": 1.0, "actual": None,
         "status": "UNRESOLVABLE", "headroom_pct": None},
    ])
    verdict, notes = _apply_deterministic_policy_checks("APPROVED", None, draft, policy_state, {})
    assert verdict == "REJECTED"
    assert "Covenant UNRESOLVABLE: made_up_metric" in notes


def test_apply_deterministic_policy_checks_overrides_on_security_gap():
    draft = _compliant_draft(cp_ids=["KYC-AML"])
    policy_state = _policy_state(security_gaps=[
        "Uncharged Asset: AST-002 has no corresponding security charge registered.",
    ])
    verdict, notes = _apply_deterministic_policy_checks("APPROVED", None, draft, policy_state, {})
    assert verdict == "REJECTED"
    assert "Uncharged Asset: AST-002" in notes


def test_apply_deterministic_policy_checks_overrides_on_undisclosed_analyst_supplied_financials():
    """An analyst-supplied deal whose draft never set financials_source_disclosed
    must be force-rejected -- see agents/underwriter_agent.md's Guideline 9."""
    draft = _compliant_draft(cp_ids=["KYC-AML"])  # financials_source_disclosed omitted
    verdict, notes = _apply_deterministic_policy_checks(
        "APPROVED", None, draft, _policy_state(), {}, financials_source="analyst-supplied",
    )
    assert verdict == "REJECTED"
    assert "Missing Analyst-Supplied Spreading Disclosure" in notes


def test_apply_deterministic_policy_checks_accepts_disclosed_analyst_supplied_financials():
    draft = _compliant_draft(cp_ids=["KYC-AML"], financials_source_disclosed=True)
    verdict, notes = _apply_deterministic_policy_checks(
        "APPROVED", None, draft, _policy_state(), {}, financials_source="analyst-supplied",
    )
    assert verdict == "APPROVED"
    assert notes is None


def test_apply_deterministic_policy_checks_ignores_disclosure_flag_when_framework_computed():
    """The caveat check only applies to analyst-supplied deals -- an omitted
    financials_source_disclosed must never trip it for the framework-computed
    (or unspecified) default."""
    draft = _compliant_draft(cp_ids=["KYC-AML"])  # financials_source_disclosed omitted
    verdict, notes = _apply_deterministic_policy_checks(
        "APPROVED", None, draft, _policy_state(), {}, financials_source="framework-computed",
    )
    assert verdict == "APPROVED"
    assert notes is None


def test_apply_deterministic_policy_checks_combines_llm_notes_with_code_enforced_reasons():
    draft = _compliant_draft(cp_ids=[])  # missing KYC-AML
    verdict, notes = _apply_deterministic_policy_checks(
        "REJECTED", "The narrative is too generic.", draft, _policy_state(), {},
    )
    assert verdict == "REJECTED"
    assert "The narrative is too generic." in notes
    assert "Missing Required CP KYC-AML" in notes


def test_apply_deterministic_policy_checks_never_overrides_rejected_to_approved():
    draft = _compliant_draft(cp_ids=["KYC-AML"])  # fully compliant
    verdict, notes = _apply_deterministic_policy_checks("REJECTED", "Weak mitigants.", draft, _policy_state(), {})
    assert verdict == "REJECTED"
    assert notes == "Weak mitigants."


# ---------------------------------------------------------------------------
# Narrative-accuracy check: reported_figures vs. ground_truth_figures. This
# closes the gap the CP/taxonomy/covenant checks don't -- those validate the
# deal's actual computed figures and structure, never what the drafted
# prose *states* those figures to be.
# ---------------------------------------------------------------------------

GROUND_TRUTH = {"dscr": 1.05, "gross_leverage": 3.4, "ebitda": 950000}


def test_apply_deterministic_policy_checks_passes_a_matching_reported_figure():
    draft = _compliant_draft(cp_ids=["KYC-AML"], reported_figures={"dscr": 1.0501})  # within 0.5% tolerance
    verdict, notes = _apply_deterministic_policy_checks(
        "APPROVED", None, draft, _policy_state(), GROUND_TRUTH,
    )
    assert verdict == "APPROVED"
    assert notes is None


def test_apply_deterministic_policy_checks_rejects_a_mismatched_reported_figure():
    """A stated DSCR of 1.45 against a computed 1.05 is well outside tolerance."""
    draft = _compliant_draft(cp_ids=["KYC-AML"], reported_figures={"dscr": 1.45})
    verdict, notes = _apply_deterministic_policy_checks(
        "APPROVED", None, draft, _policy_state(), GROUND_TRUTH,
    )
    assert verdict == "REJECTED"
    assert "Narrative/Ground-Truth Mismatch: reported dscr 1.45 vs computed 1.05" in notes


def test_apply_deterministic_policy_checks_flags_unresolvable_reported_figure():
    """A reported metric with no corresponding computed value must be
    flagged, not silently ignored -- same fallback discipline as the
    covenant-metric check in policy_engine.py."""
    draft = _compliant_draft(cp_ids=["KYC-AML"], reported_figures={"made_up_metric": 42})
    verdict, notes = _apply_deterministic_policy_checks(
        "APPROVED", None, draft, _policy_state(), GROUND_TRUTH,
    )
    assert verdict == "REJECTED"
    assert "UNRESOLVABLE_REPORTED_FIGURE" in notes
    assert "made_up_metric" in notes


def test_apply_deterministic_policy_checks_reported_figures_uses_same_unified_rejection_path():
    """A narrative mismatch must combine with LLM notes exactly like every
    other code-enforced reason -- not a separate/parallel field."""
    draft = _compliant_draft(cp_ids=["KYC-AML"], reported_figures={"dscr": 1.45})
    verdict, notes = _apply_deterministic_policy_checks(
        "REJECTED", "The tone is too informal.", draft, _policy_state(), GROUND_TRUTH,
    )
    assert verdict == "REJECTED"
    assert "The tone is too informal." in notes
    assert "Narrative/Ground-Truth Mismatch: reported dscr 1.45 vs computed 1.05" in notes


def test_apply_deterministic_policy_checks_ignores_omitted_figures():
    """Omitting a metric entirely (rather than guessing) must never itself be a violation."""
    draft = _compliant_draft(cp_ids=["KYC-AML"], reported_figures={})
    verdict, notes = _apply_deterministic_policy_checks(
        "APPROVED", None, draft, _policy_state(), GROUND_TRUTH,
    )
    assert verdict == "APPROVED"
    assert notes is None


# ---------------------------------------------------------------------------
# _values_match(): a fixed 0.5% relative tolerance, applied consistently.
# ---------------------------------------------------------------------------

def test_values_match_within_tolerance():
    assert _values_match(1.0501, 1.05) is True  # ~0.01% off


def test_values_match_rejects_beyond_tolerance():
    assert _values_match(1.45, 1.05) is False


def test_values_match_boundary_exactly_at_tolerance():
    actual = 100.0
    reported = actual * 1.005  # exactly 0.5%
    assert _values_match(reported, actual) is True


def test_values_match_handles_zero_actual_without_dividing_by_zero():
    assert _values_match(0, 0) is True
    assert _values_match(0.001, 0) is False


def test_values_match_returns_false_for_non_numeric_input():
    assert _values_match("not a number", 1.05) is False


# ---------------------------------------------------------------------------
# _compute_collateral_cover_pct() / _ground_truth_figures()
# ---------------------------------------------------------------------------

def test_compute_collateral_cover_pct_aggregates_across_assets():
    collateral = [
        {"exposure": 100, "collateral_value": 80},
        {"exposure": 50, "collateral_value": 40},
    ]
    assert _compute_collateral_cover_pct(collateral) == pytest.approx(120 / 150 * 100)


def test_compute_collateral_cover_pct_returns_none_when_no_exposure():
    assert _compute_collateral_cover_pct([]) is None
    assert _compute_collateral_cover_pct([{"exposure": 0, "collateral_value": 0}]) is None


def test_ground_truth_figures_combines_ratios_financials_and_collateral():
    financials = {"FY-Current": {"raw": {"revenue": 1000}, "ebitda": 510, "tangible_net_worth": 250}}
    ratios = {"FY-Current": {"dscr": 1.5, "gross_leverage": 0.68}}
    collateral = [{"exposure": 100, "collateral_value": 90}]

    truth = _ground_truth_figures(financials, ratios, collateral)

    assert truth["dscr"] == 1.5
    assert truth["gross_leverage"] == 0.68
    assert truth["ebitda"] == 510
    assert truth["tangible_net_worth"] == 250
    assert truth["collateral_cover_pct"] == pytest.approx(90.0)
    assert "raw" not in truth  # the nested raw-input dict is not itself a figure


def test_ground_truth_figures_handles_completely_empty_input():
    assert _ground_truth_figures({}, {}, []) == {}


# ---------------------------------------------------------------------------
# _check_reported_figures()
# ---------------------------------------------------------------------------

def test_check_reported_figures_returns_no_reasons_when_all_match():
    assert _check_reported_figures({"dscr": 1.05}, {"dscr": 1.05}) == []


def test_check_reported_figures_flags_unresolvable_and_mismatch_independently():
    reasons = _check_reported_figures(
        {"dscr": 1.45, "made_up_metric": 42}, {"dscr": 1.05},
    )
    assert len(reasons) == 2
    assert any("Narrative/Ground-Truth Mismatch" in r for r in reasons)
    assert any("UNRESOLVABLE_REPORTED_FIGURE" in r for r in reasons)


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
        _compliant_draft(),      # [1/3] Underwriter draft
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
    assert "policy_state" in state

    docx_path = os.path.join(deal_dir, "Acme Corp_Fleet Loan_CAM.docx")
    xlsx_path = os.path.join(deal_dir, "Acme Corp_Fleet Loan_Spreading.xlsx")
    assert os.path.isfile(docx_path)
    assert os.path.isfile(xlsx_path)


def test_run_pipeline_revises_and_exports_after_one_rejection(project_root):
    client = MockClient([
        _compliant_draft("# Draft v1"),                # initial draft
        _rejected_json("Fix the EBITDA figure."),      # iteration 1 audit
        _compliant_draft("# Draft v2 (revised)"),      # Maker revision
        _approved_json(),                                # iteration 2 audit
    ])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    deal_dir = os.path.dirname(state_path("Acme Corp", "Fleet Loan"))
    with open(os.path.join(deal_dir, "draft_v1.md"), encoding="utf-8") as f:
        assert f.read() == _compliant_draft("# Draft v1")
    with open(os.path.join(deal_dir, "draft_v2.md"), encoding="utf-8") as f:
        assert f.read() == _compliant_draft("# Draft v2 (revised)")

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
    client = MockClient([_compliant_draft(), _approved_json()])
    multi_period_financials = {"FY-Current": {"revenue": 1000, "cost_of_sales": 400}}

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials=multi_period_financials, client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["financials"]["FY-Current"]["gross_profit"] == 600
    assert "ratios" in state and "FY-Current" in state["ratios"]
    assert "spread" in state["steps_completed"]


def test_run_pipeline_stores_collateral_data_on_state(project_root):
    client = MockClient([_compliant_draft(), _approved_json()])
    collateral_data = [{"asset_class": "HGV", "exposure": 100, "collateral_value": 80,
                         "perfection_status": "Registered"}]

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 collateral_data=collateral_data, client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["collateral"] == collateral_data


# ---------------------------------------------------------------------------
# State preservation across re-runs: write_state()'s shallow merge means a
# fresh `financials={}` replaces the whole dict, so run_pipeline() must read
# existing state first and only replace financials/ratios/collateral when
# actually given new data for them, and only ever *add* to steps_completed.
# ---------------------------------------------------------------------------

def test_run_pipeline_preserves_existing_financials_when_rerun_without_new_data(project_root):
    multi_period_financials = {"FY-Current": {"revenue": 1000, "cost_of_sales": 400}}
    client1 = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials=multi_period_financials, client=client1)

    state_after_first = read_state("Acme Corp", "Fleet Loan")
    assert state_after_first["financials"]["FY-Current"]["gross_profit"] == 600

    # Second run for the same deal, no --financials/--spread this time --
    # must not wipe what the first run already checkpointed.
    client2 = MockClient([_compliant_draft("# Draft CAM v2"), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client2)

    state_after_second = read_state("Acme Corp", "Fleet Loan")
    assert state_after_second["financials"]["FY-Current"]["gross_profit"] == 600


def test_run_pipeline_preserves_existing_collateral_when_rerun_without_new_data(project_root):
    collateral_data = [{"asset_class": "HGV", "exposure": 100, "collateral_value": 80,
                         "perfection_status": "Registered"}]
    client1 = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 collateral_data=collateral_data, client=client1)

    client2 = MockClient([_compliant_draft("# Draft CAM v2"), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client2)

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["collateral"] == collateral_data


def test_run_pipeline_accumulates_steps_completed_without_duplicating_across_reruns(project_root):
    client1 = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client1)
    state1 = read_state("Acme Corp", "Fleet Loan")
    assert state1["steps_completed"].count("draft") == 1
    assert state1["steps_completed"].count("audit") == 1
    assert state1["steps_completed"].count("export") == 1

    client2 = MockClient([_compliant_draft("# Draft CAM v2"), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client2)
    state2 = read_state("Acme Corp", "Fleet Loan")
    assert state2["steps_completed"].count("draft") == 1
    assert state2["steps_completed"].count("audit") == 1
    assert state2["steps_completed"].count("export") == 1


# ---------------------------------------------------------------------------
# new_review: a genuinely new annual review under the same company/proposal
# must get its own fresh dated folder, never silently merge into a prior
# year's stale financials/collateral/policy_state.
# ---------------------------------------------------------------------------

def test_run_pipeline_with_new_review_does_not_inherit_a_prior_dated_folders_data(project_root):
    old_financials = {"FY-Current": {"revenue": 1000, "cost_of_sales": 400}}
    write_state("Acme Corp", "Fleet Loan", date_str="2025-01-10",
                financials={"FY-Current": {"gross_profit": 600}}, ratios={},
                collateral=[{"asset_class": "HGV", "exposure": 100}],
                inputs={"pd": "0.10%", "lgd": "LGD 1 (5%)"})

    client = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client, new_review=True)

    new_state = read_state("Acme Corp", "Fleet Loan", new_review=True)
    assert new_state["date"] != "2025-01-10"
    assert new_state["financials"] == {}  # not last year's {"FY-Current": {"gross_profit": 600}}
    assert new_state["collateral"] == []  # not last year's HGV entry
    assert new_state["inputs"] == {"pd": "0.20%", "lgd": "LGD 3 (15%)"}

    # The old folder is untouched.
    old_state = read_state("Acme Corp", "Fleet Loan", date_str="2025-01-10")
    assert old_state["inputs"] == {"pd": "0.10%", "lgd": "LGD 1 (5%)"}
    assert old_state["collateral"] == [{"asset_class": "HGV", "exposure": 100}]


# ---------------------------------------------------------------------------
# downside_case recomputation: financials and stress_assumptions don't have
# to be re-supplied together on every run -- each independently falls back
# to whatever this deal already had checkpointed, and downside_case is
# recomputed whenever either one changes so it never silently drifts out of
# sync with the base-case financials/ratios actually in effect.
# ---------------------------------------------------------------------------

FORWARD_PERIOD_BASE = {
    "revenue": 1000, "cost_of_sales": 400, "admin_expenses": 100,
    "depreciation": 50, "amortisation": 20, "other_income": 10,
    "interest_paid": 30, "interest_received": 5, "scheduled_principal": 70,
    "exceptional_costs": 0, "tax_paid": 40,
    "cash": 60, "trade_debtors": 80, "stock": 90, "other_current_assets": 10,
    "tangible_assets": 500, "intangible_assets": 50, "other_fixed_assets": 20,
    "trade_creditors": 70, "other_current_liabilities": 10,
    "overdraft": 5, "current_debt": 15, "long_term_debt": 300, "loan_notes": 25,
    "share_capital": 100, "retained_profit": 200,
}
STRESS_ASSUMPTIONS = {
    "revenue_haircut_pct": 10, "interest_rate_bump_bps": 200, "opex_increase_pct": 5,
}


def test_run_pipeline_recomputes_downside_case_against_revised_financials_without_restating_stress_assumptions(project_root):
    """A revised --financials file recomputes downside_case against the
    *new* base data even when --stress-assumptions isn't repeated -- it must
    never keep comparing fresh base ratios against a stale, previous-run
    downside case (see orchestrator.py's run_pipeline() downside_case
    recomputation comment)."""
    client1 = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials={"FY+1": dict(FORWARD_PERIOD_BASE)},
                 stress_assumptions=STRESS_ASSUMPTIONS, client=client1)

    state_after_first = read_state("Acme Corp", "Fleet Loan")
    # shocked_revenue=900, gross_profit=500, operating_profit=335, ebitda=405
    # shocked_interest=30+(345*200/10000)=36.9, dscr=405/(36.9+70)
    assert state_after_first["downside_case"]["ratios"]["FY+1"]["dscr"] == pytest.approx(405 / 106.9)

    revised_financials = dict(FORWARD_PERIOD_BASE, revenue=2000)
    client2 = MockClient([_compliant_draft("# Draft v2"), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials={"FY+1": revised_financials}, client=client2)

    state_after_second = read_state("Acme Corp", "Fleet Loan")
    # shocked_revenue=1800, gross_profit=1400, operating_profit=1235, ebitda=1305
    assert state_after_second["downside_case"]["ratios"]["FY+1"]["dscr"] == pytest.approx(1305 / 106.9)
    # stress_assumptions themselves are preserved, not wiped, across the rerun
    assert state_after_second["stress_assumptions"] == STRESS_ASSUMPTIONS


def test_run_pipeline_recomputes_downside_case_from_cached_financials_when_only_stress_assumptions_given(project_root):
    """New --stress-assumptions alone (financials omitted, assumed already
    checkpointed) must actually take effect against the cached base-case
    financials -- not be silently discarded as a no-op."""
    client1 = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials={"FY+1": dict(FORWARD_PERIOD_BASE)},
                 stress_assumptions=STRESS_ASSUMPTIONS, client=client1)

    harsher_assumptions = dict(STRESS_ASSUMPTIONS, revenue_haircut_pct=20)
    client2 = MockClient([_compliant_draft("# Draft v2"), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 stress_assumptions=harsher_assumptions, client=client2)

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["stress_assumptions"] == harsher_assumptions
    # shocked_revenue=800 (20% haircut), gross_profit=400, operating_profit=235, ebitda=305
    assert state["downside_case"]["ratios"]["FY+1"]["dscr"] == pytest.approx(305 / 106.9)


# ---------------------------------------------------------------------------
# End-to-end: a deterministic policy failure (not an LLM-originated one)
# must still drive the governance loop through the exact same revision path,
# and produce a review_trail entry structurally identical to an
# LLM-originated one.
# ---------------------------------------------------------------------------

def test_run_pipeline_code_enforced_rejection_triggers_revision_and_matches_review_trail_shape(project_root):
    # The Reviewer says APPROVED both times; the *first* draft omits a
    # required CP, so the code-enforced layer must override it to REJECTED
    # and drive a revision cycle identical to an LLM-originated rejection.
    client = MockClient([
        _compliant_draft("# Draft v1", cp_ids=[]),   # missing KYC-AML/FACILITY-EXECUTION
        _approved_json(),                              # Reviewer wrongly says APPROVED
        _compliant_draft("# Draft v2 (fixed)"),        # Maker revision, now compliant
        _approved_json(),
    ])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    trail = state["review_trail"]
    assert [entry["verdict"] for entry in trail] == ["REJECTED", "APPROVED"]
    assert "Missing Required CP KYC-AML" in trail[0]["notes"]

    # Structurally identical to an LLM-originated entry: same keys, same types.
    llm_style_entry_keys = {"iteration", "verdict", "notes", "timestamp"}
    assert set(trail[0].keys()) == llm_style_entry_keys
    assert set(trail[1].keys()) == llm_style_entry_keys

    # The revision path actually ran: a second draft + audit call were made.
    assert client.call_count == 4

    docx_path = os.path.join(os.path.dirname(state_path("Acme Corp", "Fleet Loan")),
                              "Acme Corp_Fleet Loan_CAM.docx")
    doc = docx.Document(docx_path)
    assert doc.paragraphs[0].text == "Draft v2 (fixed)"


def test_run_pipeline_narrative_mismatch_triggers_revision_and_matches_review_trail_shape(project_root):
    """A drafted CAM can state figures that don't match what state.json
    actually computed even while every CP/taxonomy box is ticked -- the
    narrative-accuracy check must catch that independently, drive the same
    revision cycle, and produce a review_trail entry with the same shape as
    every other rejection reason."""
    client = MockClient([
        _compliant_draft("# Draft v1", reported_figures={"dscr": 5.0}),  # wildly wrong DSCR
        _approved_json(),                                                 # Reviewer wrongly says APPROVED
        _compliant_draft("# Draft v2 (fixed)", reported_figures={"dscr": 5.1}),  # now matches
        _approved_json(),
    ])
    multi_period_financials = {
        "FY-Current": {
            "revenue": 1000, "cost_of_sales": 400, "admin_expenses": 100,
            "depreciation": 50, "amortisation": 20, "other_income": 10,
            "interest_paid": 30, "scheduled_principal": 70,
        }
    }  # EBITDA=510, DSCR=510/(30+70)=5.1

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials=multi_period_financials, client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    trail = state["review_trail"]
    assert [entry["verdict"] for entry in trail] == ["REJECTED", "APPROVED"]
    assert "Narrative/Ground-Truth Mismatch: reported dscr 5.0 vs computed 5.1" in trail[0]["notes"]

    llm_style_entry_keys = {"iteration", "verdict", "notes", "timestamp"}
    assert set(trail[0].keys()) == llm_style_entry_keys
    assert set(trail[1].keys()) == llm_style_entry_keys

    assert client.call_count == 4  # revision path actually ran

    docx_path = os.path.join(os.path.dirname(state_path("Acme Corp", "Fleet Loan")),
                              "Acme Corp_Fleet Loan_CAM.docx")
    doc = docx.Document(docx_path)
    assert doc.paragraphs[0].text == "Draft v2 (fixed)"


# ---------------------------------------------------------------------------
# Issue #57: institutional credit policy document -- see /calibrate-policy,
# config/credit_policy.md, agents/underwriter_agent.md's Guideline 10, and
# agents/risk_reviewer_agent.md's Audit Checklist item 4. _build_grounding_context()
# appends the policy text into the SHARED grounding_context object both the
# Maker and Checker calls reuse, so "both agents read it" requires no
# separate Checker-specific injection point -- the end-to-end test below is
# what actually proves that design decision was implemented correctly.
# ---------------------------------------------------------------------------

def test_build_grounding_context_includes_credit_policy_section_when_given():
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
        credit_policy="No facility above 2.5x Gross Leverage without additional security.",
    )
    assert "Institutional Credit Policy" in context
    assert "No facility above 2.5x Gross Leverage without additional security." in context


def test_build_grounding_context_omits_credit_policy_section_when_not_given():
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
    )
    assert "Institutional Credit Policy" not in context


def test_run_pipeline_threads_credit_policy_into_both_maker_and_checker_prompts(project_root):
    """The whole point of putting credit_policy into the shared
    grounding_context (rather than a Maker-only interpolation like
    style_guide) is that both the draft call and the audit call receive it
    automatically -- verify both actually do, not just that the text exists
    somewhere in orchestrator.py."""
    (project_root / "config" / "credit_policy.md").write_text(
        "# Institutional Credit Policy (Calibrated)\n\n"
        "No facility above 2.5x Gross Leverage without additional security.",
        encoding="utf-8",
    )
    client = MockClient([
        _compliant_draft(credit_policy_considered=True),  # [1/3] Underwriter draft
        _approved_json(),                                   # [2/3] Risk Reviewer audit, iteration 1
    ])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    maker_call, checker_call = client.calls[0], client.calls[1]
    maker_prompt = maker_call["messages"][0]["content"]
    checker_prompt = checker_call["messages"][0]["content"]
    assert "No facility above 2.5x Gross Leverage without additional security." in maker_prompt
    assert "No facility above 2.5x Gross Leverage without additional security." in checker_prompt


def test_run_pipeline_omits_credit_policy_section_when_none_calibrated(project_root):
    """project_root's fixture never creates config/credit_policy.md by
    default -- a fork with no calibrated policy must not have the section
    injected into either prompt at all."""
    client = MockClient([
        _compliant_draft(),
        _approved_json(),
    ])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    maker_prompt = client.calls[0]["messages"][0]["content"]
    checker_prompt = client.calls[1]["messages"][0]["content"]
    assert "Institutional Credit Policy" not in maker_prompt
    assert "Institutional Credit Policy" not in checker_prompt


# ---------------------------------------------------------------------------
# Issue #58: persisted analyst-confirmed conventions -- see scripts/
# conventions.py, .claude/commands/spread.md's "Check for a persisted
# convention" step, and .claude/commands/review.md's "Persisting a
# policy-interpretation correction" step. Two new grounding-context pieces:
# financials_source_note (deal-scoped, inherited from state.json -- see
# run_pipeline()'s own financials_source resolution) and credit_policy_notes
# (fork-wide, same dual-path pattern as credit_policy itself).
# ---------------------------------------------------------------------------

def test_build_grounding_context_cites_financials_source_note_in_the_caveat_when_given():
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
        financials_source="analyst-supplied",
        financials_source_note="Depreciation embedded in Cost of Goods Sold, per prior confirmation on 2026-01-15.",
    )
    assert "analyst-supplied" in context
    assert "Depreciation embedded in Cost of Goods Sold, per prior confirmation on 2026-01-15." in context


def test_build_grounding_context_omits_financials_source_note_text_when_not_given():
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
        financials_source="analyst-supplied",
    )
    assert "The confirmed convention" not in context


def test_run_pipeline_resets_financials_source_note_when_fresh_financials_given(project_root):
    """A fresh --financials/--spread run always resets financials_source to
    framework-computed (see run_pipeline()'s own comment) -- a stale note
    from a prior analyst-supplied run must not linger in state.json either,
    even though it's currently inert while financials_source itself is reset."""
    write_state("Acme Corp", "Fleet Loan", financials_source="analyst-supplied",
                financials_source_note="Stale note from a prior run.")
    client = MockClient([_compliant_draft(), _approved_json()])
    multi_period_financials = {
        "FY-Current": {
            "revenue": 1000, "cost_of_sales": 400, "admin_expenses": 100,
            "depreciation": 50, "amortisation": 20, "other_income": 10,
            "interest_paid": 30, "scheduled_principal": 70,
        }
    }

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials=multi_period_financials, client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["financials_source"] == "framework-computed"
    assert state.get("financials_source_note", "") == ""


def test_run_pipeline_inherits_financials_source_note_from_existing_state_when_no_fresh_financials_given(project_root):
    """The headless pipeline can never originate an analyst-supplied
    convention note (no analyst present to confirm one -- see
    scripts/conventions.py's own docstring), but it must inherit one an
    earlier interactive /spread step already confirmed and checkpointed --
    this is the whole reason financials_source_note needs the same
    threading financials_source itself already has."""
    write_state("Acme Corp", "Fleet Loan", financials_source="analyst-supplied",
                financials_source_note="Depreciation embedded in Cost of Goods Sold, "
                                        "per prior confirmation for this borrower on 2026-01-15.")
    client = MockClient([
        _compliant_draft(financials_source_disclosed=True),
        _approved_json(),
    ])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    maker_prompt = client.calls[0]["messages"][0]["content"]
    assert "Depreciation embedded in Cost of Goods Sold, per prior confirmation for this " \
           "borrower on 2026-01-15." in maker_prompt

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["financials_source_note"] == (
        "Depreciation embedded in Cost of Goods Sold, per prior confirmation for this "
        "borrower on 2026-01-15."
    )


def test_build_grounding_context_includes_credit_policy_notes_section_when_given():
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
        credit_policy_notes="2026-01-15 -- Acme Corp/Fleet Loan: 'Key Man' does not apply to "
                             "committee-managed borrowers per confirmed interpretation.",
    )
    assert "Credit Policy Interpretation Notes" in context
    assert "'Key Man' does not apply to committee-managed borrowers" in context


def test_build_grounding_context_omits_credit_policy_notes_section_when_not_given():
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
    )
    assert "Credit Policy Interpretation Notes" not in context


def test_run_pipeline_threads_credit_policy_notes_into_both_maker_and_checker_prompts(project_root):
    """Same shared-grounding-context design as credit_policy itself (#57) --
    both the draft call and the audit call must receive the notes file."""
    (project_root / "config" / "credit_policy_notes.md").write_text(
        "# Credit Policy Interpretation Notes\n\n"
        "## 2026-01-15 -- Acme Corp/Fleet Loan\n"
        "**Policy point:** Key Man risk criterion\n"
        "**Correction:** Does not apply to committee-managed borrowers.",
        encoding="utf-8",
    )
    client = MockClient([
        _compliant_draft(credit_policy_considered=True),
        _approved_json(),
    ])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    maker_prompt = client.calls[0]["messages"][0]["content"]
    checker_prompt = client.calls[1]["messages"][0]["content"]
    assert "Does not apply to committee-managed borrowers." in maker_prompt
    assert "Does not apply to committee-managed borrowers." in checker_prompt


def test_run_pipeline_omits_credit_policy_notes_section_when_none_exist(project_root):
    client = MockClient([_compliant_draft(), _approved_json()])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    maker_prompt = client.calls[0]["messages"][0]["content"]
    checker_prompt = client.calls[1]["messages"][0]["content"]
    assert "Credit Policy Interpretation Notes" not in maker_prompt
    assert "Credit Policy Interpretation Notes" not in checker_prompt


# ---------------------------------------------------------------------------
# Issue #86: end-of-deal "learnings" -- see /assemble's "Surface end-of-deal
# learnings" step and agents/underwriter_agent.md's Guideline 11. Two scopes
# (deals/<Company>/_learnings.md, config/deal_learnings.md), same shared-
# grounding-context placement as credit_policy_notes -- purely advisory, no
# structured-output field, so no test here touches parse_underwriter_output()
# or check_draft_compliance().
# ---------------------------------------------------------------------------

def test_build_grounding_context_includes_deal_learnings_section_when_either_scope_given():
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
        enterprise_learnings="Always benchmark against IBISWorld sector code 12345.",
    )
    assert "Deal Learnings" in context
    assert "Always benchmark against IBISWorld sector code 12345." in context

    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
        company_learnings="New CFO has a banking background.",
    )
    assert "Deal Learnings" in context
    assert "New CFO has a banking background." in context


def test_build_grounding_context_omits_deal_learnings_section_when_neither_given():
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
    )
    assert "Deal Learnings" not in context


def test_run_pipeline_threads_deal_learnings_into_both_maker_and_checker_prompts(project_root):
    """Same shared-grounding-context design as credit_policy_notes (#58) --
    both the draft call and the audit call must receive both scopes, even
    though only the Underwriter's prompt (Guideline 11) references it."""
    (project_root / "config" / "deal_learnings.md").write_text(
        "# Deal Learnings\n\n"
        "## 2026-01-10 -- Other Corp/Other Deal\n"
        "**Learning:** Always benchmark against IBISWorld sector code 12345.",
        encoding="utf-8",
    )
    company_dir = project_root / "deals" / "Acme Corp"
    company_dir.mkdir(parents=True)
    (company_dir / "_learnings.md").write_text(
        "# Deal Learnings\n\n"
        "## 2026-01-15 -- Acme Corp/Fleet Loan\n"
        "**Learning:** New CFO has a banking background.",
        encoding="utf-8",
    )
    client = MockClient([_compliant_draft(), _approved_json()])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    maker_prompt = client.calls[0]["messages"][0]["content"]
    checker_prompt = client.calls[1]["messages"][0]["content"]
    assert "Always benchmark against IBISWorld sector code 12345." in maker_prompt
    assert "New CFO has a banking background." in maker_prompt
    assert "Always benchmark against IBISWorld sector code 12345." in checker_prompt
    assert "New CFO has a banking background." in checker_prompt


def test_run_pipeline_omits_deal_learnings_section_when_none_exist(project_root):
    client = MockClient([_compliant_draft(), _approved_json()])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 client=client)

    maker_prompt = client.calls[0]["messages"][0]["content"]
    checker_prompt = client.calls[1]["messages"][0]["content"]
    assert "Deal Learnings" not in maker_prompt
    assert "Deal Learnings" not in checker_prompt


# ---------------------------------------------------------------------------
# Issue #91: grounding-context content is embedded via XML-style tags, not
# fixed Markdown triple-backtick fences -- a fence breaks if the embedded
# content itself contains a ``` sequence, a real risk once analyst-writable
# free text (credit_policy_notes, deal learnings) started flowing through
# _build_grounding_context(), not just vetted calibrated documents.
# ---------------------------------------------------------------------------

def test_xml_block_wraps_content_containing_a_stray_fence_marker_without_corruption():
    content_with_fence = "Some analyst note.\n```\nSELECT * FROM accounts;\n```\nMore text."
    block = _xml_block("institutional_credit_policy", content_with_fence)

    assert block == f"<institutional_credit_policy>\n{content_with_fence}\n</institutional_credit_policy>"
    # The embedded ``` sequence does not prematurely close anything -- the
    # tag's own open/close delimiters are still present exactly once each,
    # unlike a triple-backtick fence which the same content would corrupt.
    assert block.count("<institutional_credit_policy>") == 1
    assert block.count("</institutional_credit_policy>") == 1


def test_build_grounding_context_keeps_fence_breaking_content_isolated_from_what_follows():
    """End-to-end: credit_policy content containing a stray ``` sequence
    must not corrupt or bleed into the financials/ratios blocks that follow
    it in the same grounding context string -- the exact failure mode a
    fixed triple-backtick fence was vulnerable to."""
    tricky_policy = "No facility above 2.5x leverage.\n```\nsome pasted code\n```\nAlso see appendix."
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {"FY-Current": {"revenue": 1000}}, "ratios": {}}, [],
        credit_policy=tricky_policy,
    )

    assert "<institutional_credit_policy>" in context
    assert "</institutional_credit_policy>" in context
    assert tricky_policy in context
    # The financials block that follows is still intact and independently
    # tagged -- not swallowed or corrupted by the credit policy's own
    # embedded ``` sequence.
    assert "<financials>" in context
    assert '"revenue": 1000' in context


def test_build_grounding_context_no_longer_uses_markdown_fences_for_analyst_writable_content():
    """Regression guard: none of the analyst-writable sections (credit
    policy, credit policy notes, deal learnings) should ever regress back
    to a fixed ``` fence."""
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
        credit_policy="A policy document.",
        credit_policy_notes="A confirmed interpretation note.",
        company_learnings="A borrower-specific learning.",
        enterprise_learnings="An enterprise-wide learning.",
    )
    assert "```" not in context


# ---------------------------------------------------------------------------
# An analyst-supplied forecast (issue #124) is not recomputed or erased by the headless run
# ---------------------------------------------------------------------------

ANALYST_DOWNSIDE = {"financials": {"FY+1": {"ebitda": 240}}, "ratios": {"FY+1": {"dscr": 1.1}},
                    "basis": {"FY+1": "analyst-supplied"}, "unavailable": {"FY+2": "no scenario supplied"},
                    "description": "Revenue down 10%"}


def _analyst_forecast_state(**extra):
    write_state("Acme Corp", "Fleet Loan", financials_source="analyst-supplied",
                financials={"FY-Current": {"ebitda": 250}, "FY+1": {"ebitda": 300}},
                ratios={"FY-Current": {"dscr": 1.5}, "FY+1": {"dscr": 1.4}},
                multi_period_financials={"FY-Current": {"revenue": 900}}, stress_assumptions={"revenue_haircut_pct": 10},
                downside_case=ANALYST_DOWNSIDE, steps_completed=["spread", "project"], **extra)


def test_run_pipeline_keeps_an_analyst_supplied_downside_instead_of_recomputing_it_from_history(project_root):
    _analyst_forecast_state(forecast_source="analyst-supplied")
    client = MockClient([_compliant_draft(financials_source_disclosed=True), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit", client=client)
    state = read_state("Acme Corp", "Fleet Loan")
    assert state["downside_case"] == ANALYST_DOWNSIDE
    assert state["forecast_source"] == "analyst-supplied" and state["financials_source"] == "analyst-supplied"
    assert state["financials"]["FY+1"] == {"ebitda": 300}, "the supplied forward year is not recomputed"


def test_without_the_analyst_flag_the_same_state_gets_the_ordinary_recomputation(project_root):
    """The guard is what protects it: the ordinary path (every existing deal) is unchanged."""
    _analyst_forecast_state()
    client = MockClient([_compliant_draft(financials_source_disclosed=True), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit", client=client)
    assert read_state("Acme Corp", "Fleet Loan")["downside_case"] == {"financials": {}, "ratios": {}}


def test_a_fresh_recomputation_replaces_the_forward_years_and_clears_the_analyst_forecast_flag(project_root):
    _analyst_forecast_state(forecast_source="analyst-supplied")
    client = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials={"FY-Current": {"revenue": 100, "cost_of_sales": 60}}, client=client)
    state = read_state("Acme Corp", "Fleet Loan")
    assert state["forecast_source"] is None and state["financials_source"] == "framework-computed"
    assert set(state["financials"]) == {"FY-Current"}


def test_a_deal_that_never_had_the_flag_does_not_gain_one_from_a_run(project_root):
    client = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials={"FY-Current": {"revenue": 100, "cost_of_sales": 60}}, client=client)
    assert "forecast_source" not in read_state("Acme Corp", "Fleet Loan")


# ---------------------------------------------------------------------------
# A changed stress assumption cannot be applied to an analyst-supplied forecast (issue #124)
# ---------------------------------------------------------------------------

def _deal_files():
    return {p.as_posix(): p.read_bytes() for p in Path("deals").rglob("*") if p.is_file()}


def test_a_changed_stress_assumption_for_an_analyst_supplied_forecast_is_refused_and_nothing_is_written(project_root):
    """The headless run cannot recompute that forecast's downside, so persisting a new assumption would leave state
    whose stress assumptions no longer describe its downside case. Refuse, before any write or model call."""
    _analyst_forecast_state(forecast_source="analyst-supplied")
    before = _deal_files()
    client = MockClient([])
    with pytest.raises(StateError) as refused:
        run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                     stress_assumptions={"revenue_haircut_pct": 25}, client=client)
    message = str(refused.value)
    assert len(message.splitlines()) == 1
    assert "analyst-supplied" in message and "/project" in message and "revenue_haircut_pct" in message
    assert client.call_count == 0, "no model call"
    assert _deal_files() == before, "state.json, and everything else under deals/, is byte-identical"


@pytest.mark.parametrize("changed", [{"opex_increase_pct": 5}, {"revenue_haircut_pct": 10, "opex_increase_pct": 5},
                                     {"revenue_haircut_pct": 0}, {"interest_rate_bump_bps": 100}])
def test_any_difference_from_the_recorded_stress_assumptions_is_refused(project_root, changed):
    _analyst_forecast_state(forecast_source="analyst-supplied")
    before = _deal_files()
    with pytest.raises(StateError, match="stress"):
        run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                     stress_assumptions=changed, client=MockClient([]))
    assert _deal_files() == before


def test_stress_assumptions_cannot_be_added_to_an_analyst_forecast_that_had_none(project_root):
    write_state("Acme Corp", "Fleet Loan", financials_source="analyst-supplied", forecast_source="analyst-supplied",
                financials={"FY+1": {"ebitda": 300}}, ratios={"FY+1": {"dscr": 1.4}}, steps_completed=["spread"])
    before = _deal_files()
    with pytest.raises(StateError, match="stress"):
        run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                     stress_assumptions={"revenue_haircut_pct": 10}, client=MockClient([]))
    assert _deal_files() == before


@pytest.mark.parametrize("repeat", [{"revenue_haircut_pct": 10}, {"revenue_haircut_pct": 10, "opex_increase_pct": 0},
                                    {"revenue_haircut_pct": 10.0}, None, {}])
def test_repeating_the_recorded_stress_assumptions_is_a_no_op(project_root, repeat):
    _analyst_forecast_state(forecast_source="analyst-supplied")
    client = MockClient([_compliant_draft(financials_source_disclosed=True), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 stress_assumptions=repeat, client=client)
    state = read_state("Acme Corp", "Fleet Loan")
    assert state["stress_assumptions"] == {"revenue_haircut_pct": 10}, "the recorded assumptions are not rewritten"
    assert state["downside_case"] == ANALYST_DOWNSIDE and state["forecast_source"] == "analyst-supplied"


def test_a_changed_assumption_is_still_allowed_with_a_fresh_recomputation_or_without_an_analyst_forecast(project_root):
    """The refusal is only for a forecast the headless run cannot recompute: the ordinary path is unchanged."""
    _analyst_forecast_state()          # no forecast_source flag: an ordinary deal
    client = MockClient([_compliant_draft(financials_source_disclosed=True), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 stress_assumptions={"revenue_haircut_pct": 25}, client=client)
    assert read_state("Acme Corp", "Fleet Loan")["stress_assumptions"] == {"revenue_haircut_pct": 25}

    _analyst_forecast_state(forecast_source="analyst-supplied")
    client = MockClient([_compliant_draft(), _approved_json()])
    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials={"FY-Current": {"revenue": 100, "cost_of_sales": 60},
                                          "FY+1": {"revenue": 100, "cost_of_sales": 60}},
                 stress_assumptions={"revenue_haircut_pct": 30}, client=client)
    state = read_state("Acme Corp", "Fleet Loan")
    assert state["forecast_source"] is None and state["stress_assumptions"] == {"revenue_haircut_pct": 30}
    assert state["downside_case"]["financials"]["FY+1"]["raw"]["revenue"] == 70, "recomputed from the fresh figures"
