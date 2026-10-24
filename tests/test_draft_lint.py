import pytest

from draft_lint import lint_draft


FENCE = chr(96) * 3


@pytest.mark.parametrize(
    "phrase",
    [
        "state.json", "steps_completed", "financials_source", "policy_state", "cp_id",
        "Guideline 12", "Audit Checklist item 4", "/research", "/committee-summary",
        "this framework", "this session", "deterministic check", "code-enforced",
        "Underwriter Agent", "OpenCAM",
    ],
)
def test_rule_a_reports_each_forbidden_term_and_line(phrase):
    findings = lint_draft(f"First line.\nA finding: {phrase}.\n", document_kind="research-brief")
    finding = next(item for item in findings if item["rule"] == "A")
    assert finding["line"] == 2
    assert phrase.casefold() in finding["match"].casefold()
    assert "line 2" in finding["message"]


def test_rule_a_normalises_whitespace_and_avoids_broad_substrings():
    found = lint_draft("A note says this\n  framework is internal.", document_kind="research-brief")
    assert any(item["rule"] == "A" and item["match"].casefold() == "this framework" for item in found)
    clean = lint_draft(
        "Guideline Ltd attended the session; the sessional staff wrote OpenCAMish notes.",
        document_kind="research-brief",
    )
    assert clean == []


def test_comments_and_trailing_cam_json_are_not_linted():
    draft = (
        "Clean visible prose.\n<!-- This framework mentions OpenCAM and state.json. -->\n\n"
        + FENCE + "json\n"
        + '{"note": "OpenCAM, state.json, Guideline 9, [Amount]"}\n'
        + FENCE + "\n"
    )
    assert lint_draft(draft, document_kind="cam") == []


def test_html_comment_literals_inside_a_fenced_code_block_remain_visible():
    draft = FENCE + "text\n<!-- OpenCAM -->\n" + FENCE + "\n"
    findings = lint_draft(draft, document_kind="research-brief")
    assert any(item["rule"] == "A" and item["match"] == "OpenCAM" and item["line"] == 2 for item in findings)


def test_unterminated_html_comment_fails_closed():
    with pytest.raises(ValueError, match="Unterminated HTML comment"):
        lint_draft("A draft with <!-- no ending", document_kind="research-brief")


def test_unknown_document_kind_is_refused():
    with pytest.raises(ValueError, match="document_kind"):
        lint_draft("Clean draft", document_kind="email")


def test_template_guidance_and_placeholders_match_with_line_numbers():
    template = (
        "# Synthetic CAM\n\n"
        "<!-- Guidance: Keep *each material claim* tied to reliable evidence. -->\n\n"
        "Borrower: [Borrower Legal Name]\n"
    )
    draft = (
        "# Synthetic CAM\n\n"
        "Keep each material\nclaim tied to reliable evidence.\n\n"
        "Borrower: [Borrower Legal Name]\n"
    )
    findings = lint_draft(draft, document_kind="cam", template_text=template)
    guidance = next(item for item in findings if item["rule"] == "B_TEMPLATE_GUIDANCE")
    placeholder = next(item for item in findings if item["rule"] == "B_UNFILLED_PLACEHOLDER")
    assert guidance["line"] == 3
    assert "Keep each material claim" in guidance["match"]
    assert placeholder["line"] == 6
    assert "[Borrower Legal Name]" in placeholder["match"]


@pytest.mark.parametrize("placeholder", ["[Name]", "[Date]", "[Amount]", "[Company Name]", "[placeholder]", "[Insert value]"])
def test_obvious_unfilled_placeholders_are_rejected_without_a_cam_template(placeholder):
    findings = lint_draft(f"Draft text includes {placeholder}.", document_kind="research-brief")
    assert any(item["rule"] == "B_UNFILLED_PLACEHOLDER" for item in findings)


def test_legitimate_bracketed_citation_and_markdown_link_are_not_placeholders():
    findings = lint_draft(
        "Synthetic report relies on [Smith 2026] and [registry](https://example.invalid/registry).",
        document_kind="research-brief",
    )
    assert findings == []


def test_only_selected_template_tokens_trigger_rule_b():
    template = "# Template\n\nSee [Smith 2026](https://example.invalid/source).\nName: [Name]\n"
    draft = "Reference [Smith 2026] and leave Name: [Name] as written."
    findings = lint_draft(draft, document_kind="cam", template_text=template)
    placeholders = [item for item in findings if item["rule"] == "B_UNFILLED_PLACEHOLDER"]
    assert len(placeholders) == 1 and placeholders[0]["match"] == "[Name]"
