"""Regression tests guarding agents/underwriter_agent.md and
agents/risk_reviewer_agent.md against silently drifting out of sync with
the code that actually parses/enforces what they document -- e.g. a field
renamed in policy_checks.py but not in the prompt's own JSON example, or a
prompt referencing a grounding-context field name orchestrator.py never
actually emits (exactly the kind of bug this file's tests were added to
catch after one was found and fixed by hand: the Projections & Sensitivities
guideline used to trigger on "base_case_metrics"/"downside_case_metrics",
field names that never appeared anywhere in _build_grounding_context()'s
actual output).

Scope, deliberately: this checks *structural* consistency between the
prompts and the code -- schema field names, canonical category names, JSON
validity -- entirely offline, deterministic, no ANTHROPIC_API_KEY needed,
matching the rest of this test suite's fast/dependency-free convention.
It does NOT attempt generative *quality* evaluation (actually calling the
model and grading a drafted CAM's narrative) -- that's a fundamentally
different kind of test (non-deterministic, costs money, needs a live key)
and is out of scope here; a future dedicated eval harness would be the
right way to do that properly.
"""
import json
import re

import pytest

from orchestrator import _build_grounding_context, parse_verdict
from policy_checks import REQUIRED_RISK_TAXONOMY, parse_underwriter_output

FENCED_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def underwriter_prompt():
    return _read("agents/underwriter_agent.md")


@pytest.fixture(scope="module")
def risk_reviewer_prompt():
    return _read("agents/risk_reviewer_agent.md")


# ---------------------------------------------------------------------------
# agents/underwriter_agent.md's Structured Output guideline
# ---------------------------------------------------------------------------

def test_underwriter_structured_output_example_is_valid_json(underwriter_prompt):
    match = FENCED_JSON_RE.search(underwriter_prompt)
    assert match, "expected at least one fenced ```json block in underwriter_agent.md"
    json.loads(match.group(1))  # must not raise


def test_underwriter_structured_output_schema_matches_what_the_parser_recognizes(underwriter_prompt):
    """Every top-level key in the prompt's own JSON example must be one
    parse_underwriter_output() actually extracts -- otherwise the prompt is
    documenting a field the code silently ignores, or the parser expects a
    field the prompt never told the Underwriter to produce."""
    match = FENCED_JSON_RE.search(underwriter_prompt)
    example = json.loads(match.group(1))

    recognized_fields = set(parse_underwriter_output("").keys())
    assert set(example.keys()) == recognized_fields


def test_underwriter_prompt_names_every_required_risk_category(underwriter_prompt):
    for category in REQUIRED_RISK_TAXONOMY:
        assert category in underwriter_prompt, (
            f"REQUIRED_RISK_TAXONOMY category {category!r} (policy_checks.py) is not "
            "mentioned anywhere in agents/underwriter_agent.md -- the Underwriter has no "
            "way to know it must cover this category"
        )


def test_underwriter_prompt_does_not_reference_grounding_context_field_names_that_do_not_exist(
    underwriter_prompt,
):
    """Regression test: the Projections & Sensitivities guideline used to
    tell the Underwriter to trigger on grounding-context keys named
    "base_case_metrics"/"downside_case_metrics" -- but
    _build_grounding_context() never emits JSON under those key names (it
    emits plain-English section headers followed by financials/ratios
    blocks). A model reading the prompt literally could conclude the
    trigger condition never fires and skip the section entirely even when
    forward-year/downside data is present. Fixed by referencing the prompt
    to the real section headers instead -- this guards the fix."""
    assert "base_case_metrics" not in underwriter_prompt
    assert "downside_case_metrics" not in underwriter_prompt


def test_underwriter_prompt_references_the_real_downside_grounding_context_header(underwriter_prompt):
    """Positive counterpart to the test above: derive the actual downside
    section header from the real _build_grounding_context() (not a
    hand-copied string that could itself drift) and confirm the prompt's
    Projections & Sensitivities guideline references it."""
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [], policy_state=None,
        downside_case={"financials": {"FY+1": {"revenue": 100}}, "ratios": {}},
    )
    # A short, stable leading phrase from the real header -- not the whole
    # verbose sentence (which includes deal-specific instructional prose a
    # static prompt file would only paraphrase, not quote verbatim).
    header_match = re.search(r"Downside \(stressed\) forward-year financials and ratios", context)
    assert header_match, "expected _build_grounding_context() to emit a Downside (stressed)... header"

    assert header_match.group(0) in underwriter_prompt


def test_underwriter_prompt_requires_the_analyst_supplied_spreading_caveat(underwriter_prompt):
    """Regression guard for issue #55's caveat requirement (Guideline 9):
    when a deal's spreading was analyst-supplied rather than
    framework-computed, the Underwriter must disclose that in the CAM --
    this only checks the instruction still exists in the prompt (an LLM
    behavior, not something pytest can otherwise verify), so a future edit
    can't silently drop the requirement."""
    assert "financials_source" in underwriter_prompt
    assert "analyst-supplied" in underwriter_prompt


def test_underwriter_prompt_references_financials_source_note(underwriter_prompt):
    """Regression guard for issue #58: Guideline 9 must instruct the
    Underwriter to cite a persisted convention description (state.json's
    financials_source_note, or the equivalent headless grounding-context
    text) verbatim in the caveat, not just declare that a caveat exists."""
    assert "financials_source_note" in underwriter_prompt


def test_underwriter_and_risk_reviewer_prompts_reference_credit_policy_notes(
    underwriter_prompt, risk_reviewer_prompt,
):
    """Regression guard for issue #58: both agent prompts must know to
    check config/credit_policy_notes.md alongside config/credit_policy.md
    itself (Guideline 10 / Audit Checklist item 4)."""
    assert "credit_policy_notes.md" in underwriter_prompt
    assert "credit_policy_notes.md" in risk_reviewer_prompt


def test_risk_reviewer_prompt_references_the_real_credit_policy_notes_grounding_context_header(
    risk_reviewer_prompt,
):
    """Positive counterpart: derive the actual "Credit Policy Interpretation
    Notes" section header from the real _build_grounding_context() and
    confirm Audit Checklist item 4 references it."""
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
        credit_policy_notes="A confirmed interpretation note.",
    )
    header_match = re.search(r"Credit Policy Interpretation Notes", context)
    assert header_match, "expected _build_grounding_context() to emit a Credit Policy Interpretation Notes header"
    assert header_match.group(0) in risk_reviewer_prompt


def test_underwriter_prompt_references_deal_learnings_files(underwriter_prompt):
    """Regression guard for issue #86: Guideline 11 must know to check both
    deals/<Company>/_learnings.md and config/deal_learnings.md."""
    assert "_learnings.md" in underwriter_prompt
    assert "deal_learnings.md" in underwriter_prompt


def test_underwriter_prompt_references_the_real_deal_learnings_grounding_context_header(underwriter_prompt):
    """Positive counterpart: derive the actual "Deal Learnings" section
    header from the real _build_grounding_context() and confirm
    Guideline 11 references it."""
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": {}, "ratios": {}}, [],
        enterprise_learnings="A confirmed learning.",
    )
    header_match = re.search(r"Deal Learnings", context)
    assert header_match, "expected _build_grounding_context() to emit a Deal Learnings header"
    assert header_match.group(0) in underwriter_prompt


# ---------------------------------------------------------------------------
# agents/risk_reviewer_agent.md's verdict JSON schema
# ---------------------------------------------------------------------------

def test_risk_reviewer_verdict_examples_are_valid_json_and_recognized_by_parse_verdict(risk_reviewer_prompt):
    matches = FENCED_JSON_RE.findall(risk_reviewer_prompt)
    assert len(matches) >= 2, "expected both an APPROVED and a REJECTED example in risk_reviewer_agent.md"

    verdicts_found = set()
    for raw in matches:
        example = json.loads(raw)  # must not raise
        assert "verdict" in example and "notes" in example

        # Round-trip through the real parser exactly as orchestrator.py
        # would encounter it in a live response, confirming the prompt's
        # own example is actually parseable by the code that reads it.
        wrapped = "```json\n" + json.dumps(example) + "\n```"
        verdict, _ = parse_verdict(wrapped)
        assert verdict in ("APPROVED", "REJECTED")
        verdicts_found.add(verdict)

    assert verdicts_found == {"APPROVED", "REJECTED"}


def test_risk_reviewer_prompt_documents_research_brief_handling(risk_reviewer_prompt):
    """Regression guard for issue #87: the Reviewer role must know a
    /research brief is a distinct case from both a CAM with policy_state
    and a CAM without one."""
    assert "/research" in risk_reviewer_prompt
    assert "research brief" in risk_reviewer_prompt
