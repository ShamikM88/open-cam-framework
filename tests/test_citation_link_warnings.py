"""Unit tests for draft_lint.citation_link_warnings (issue #580).

A citation a reader can click in the exported Word document is written as a
[readable citation](url) hyperlink. The check is advisory: it returns warnings
and never raises, never changes the draft, and never feeds a policy reasons
list. All inputs are synthetic.
"""
import draft_lint
import pytest


def citation_link_warnings(text):
    # Looked up at call time, so each test runs and reports its own result.
    return draft_lint.citation_link_warnings(text)


def _messages(warnings):
    return [warning["message"] for warning in warnings]


UNLINKED_BRIEF = (
    "# Research brief\n"
    "\n"
    "Findings per [FY25 accounts].\n"
    "\n"
    "## Sources\n"
    "\n"
    "- FY25 accounts: https://example.invalid/filing\n"
)

LINKED_BRIEF = (
    "# Research brief\n"
    "\n"
    "Findings per [FY25 accounts](https://example.invalid/filing).\n"
    "\n"
    "## Sources\n"
    "\n"
    "- [FY25 accounts](https://example.invalid/filing)\n"
)

NO_URL_BRIEF = (
    "# Research brief\n"
    "\n"
    "Findings per [analyst meeting].\n"
    "\n"
    "## Sources\n"
    "\n"
    "- Analyst meeting: document supplied by the analyst, no URL\n"
)


def test_a_bare_url_in_a_sources_entry_warns_naming_its_line():
    warnings = citation_link_warnings(UNLINKED_BRIEF)
    bare = next(warning for warning in warnings if "bare URL" in warning["message"])
    assert bare["line"] == 7
    assert "https://example.invalid/filing" in bare["message"]
    assert "line 7" in bare["message"].casefold()


def test_an_unlinked_in_text_key_whose_source_has_a_url_warns():
    warnings = citation_link_warnings(UNLINKED_BRIEF)
    key = next(warning for warning in warnings if "[FY25 accounts]" in warning["message"])
    assert key["line"] == 3
    assert "[FY25 accounts](url)" in key["message"]


def test_a_source_with_no_url_produces_no_warning():
    # Guideline 1 forbids inventing a URL: a source without one must pass
    # cleanly, in-text key and all.
    assert citation_link_warnings(NO_URL_BRIEF) == []


def test_a_correctly_linked_citation_produces_no_warning():
    assert citation_link_warnings(LINKED_BRIEF) == []


def test_a_page_qualified_in_text_key_matches_its_source_key():
    brief = (
        "# Research brief\n"
        "\n"
        "Turnover per [FY25 accounts, p. 12].\n"
        "\n"
        "## Sources\n"
        "\n"
        "- FY25 accounts: https://example.invalid/filing\n"
    )
    assert any("[FY25 accounts, p. 12]" in message for message in _messages(citation_link_warnings(brief)))


def test_a_bare_url_outside_the_sources_section_does_not_warn():
    # Only Sources entries carry the bare-URL warning; a URL in body prose is
    # ordinary Markdown the exporter renders as text by design.
    brief = "See https://example.invalid/body for the market overview.\n"
    assert citation_link_warnings(brief) == []


def test_no_sources_section_means_no_key_warnings():
    # With no Sources section there is no key defined anywhere, so an in-text
    # bracketed key stays silent.
    assert citation_link_warnings("Findings per [FY25 accounts].\n") == []


def test_sources_entries_inside_a_fenced_block_are_ignored():
    brief = (
        "# Research brief\n"
        "\n"
        "```\n"
        "## Sources\n"
        "- FY25 accounts: https://example.invalid/filing\n"
        "```\n"
    )
    assert citation_link_warnings(brief) == []


def test_warnings_are_sorted_by_line_and_carry_a_line_number():
    warnings = citation_link_warnings(UNLINKED_BRIEF)
    assert [warning["line"] for warning in warnings] == sorted(warning["line"] for warning in warnings)
    assert all(isinstance(warning["line"], int) for warning in warnings)


def test_non_text_input_returns_no_warnings_instead_of_raising():
    # The function never raises: anything it cannot analyse returns [].
    assert citation_link_warnings(b"bytes draft") == []
    assert citation_link_warnings(None) == []
    assert citation_link_warnings(123) == []


def test_the_check_never_changes_the_draft():
    original = UNLINKED_BRIEF
    citation_link_warnings(original)
    assert original == UNLINKED_BRIEF


def test_a_reference_style_definition_also_defines_the_key():
    brief = (
        "# Research brief\n"
        "\n"
        "Findings per [FY25 accounts].\n"
        "\n"
        "## Sources\n"
        "\n"
        "- [FY25 accounts]: https://example.invalid/filing\n"
    )
    warnings = citation_link_warnings(brief)
    assert any("[FY25 accounts]" in message for message in _messages(warnings))


def test_an_unlinked_key_warns_when_its_source_is_already_a_link():
    brief = (
        "# Research brief\n\nFindings per [FY25 accounts].\n\n"
        "## Sources\n\n- [FY25 accounts](https://example.invalid/filing)\n"
    )
    warnings = citation_link_warnings(brief)
    assert [warning["line"] for warning in warnings] == [3]


def test_bracket_keys_inside_the_sources_section_are_not_in_text_citations():
    # A Sources entry naming its key in brackets is a definition, not a
    # citation; only text outside the section is scanned for unlinked keys.
    brief = (
        "# Research brief\n"
        "\n"
        "## Sources\n"
        "\n"
        "- [FY25 accounts]: https://example.invalid/filing\n"
    )
    key_warnings = [
        warning for warning in citation_link_warnings(brief)
        if "is not a link" in warning["message"]
    ]
    assert key_warnings == []


@pytest.mark.parametrize("heading", ["## Sources", "## sources", "### References", "# SOURCES"])
def test_sources_and_references_headings_at_any_level_are_recognised(heading):
    brief = f"# Research brief\n\n{heading}\n\n- FY25 accounts: https://example.invalid/filing\n"
    assert citation_link_warnings(brief) != []


@pytest.mark.parametrize("garbage", [
    "", "\n\n", "<!-- never closed", "## Sources\n- https://", "[", "[]()", "## Sources\n- [a](", "```\n## Sources",
    "## Sources\n\n- \x00 https://example.invalid/\x00", "[a] [b] [c](d) [e]: f", "\ud800".encode("utf-8", "surrogatepass").decode("utf-8", "replace"),
])
def test_malformed_text_never_raises(garbage):
    assert isinstance(citation_link_warnings(garbage), list)


def test_an_unterminated_comment_returns_no_warnings_instead_of_raising():
    assert citation_link_warnings("<!-- never closed\n## Sources\n- https://example.invalid/x\n") == []


def test_html_comments_are_ignored_as_the_exporter_strips_them():
    brief = "# Brief\n\n<!-- Findings per [FY25 accounts]. -->\n\n## Sources\n\n- FY25 accounts: n/a\n"
    assert citation_link_warnings(brief) == []


def test_policy_checks_does_not_use_the_citation_link_check():
    # Advisory only (issue #580): a code-enforced policy reason rejects a deal, so this check must never reach it.
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "scripts" / "policy_checks.py").read_text(encoding="utf-8")
    assert "citation_link" not in source


def test_the_sources_section_ends_at_the_next_heading():
    # Text after a later heading is body text again: an unlinked key there is
    # reported, while a bare URL there is not a Sources entry.
    brief = (
        "# Research brief\n\n## Sources\n\n- FY25 accounts: https://example.invalid/filing\n\n"
        "## Appendix\n\nSee [FY25 accounts] and https://example.invalid/body.\n"
    )
    warnings = citation_link_warnings(brief)
    lines = [warning["line"] for warning in warnings]
    assert lines == [5, 9]
    assert "bare URL" in warnings[0]["message"] and "[FY25 accounts]" in warnings[1]["message"]


def test_an_in_text_bracket_that_matches_no_source_key_is_ignored():
    brief = (
        "# Research brief\n\nA note [see below] and [FY25 accounts](https://example.invalid/filing).\n\n"
        "## Sources\n\n- [FY25 accounts](https://example.invalid/filing)\n"
    )
    assert citation_link_warnings(brief) == []


def test_a_deeper_heading_inside_the_sources_section_does_not_end_it():
    # Same rule as the exporter's sources check: only a heading of the same
    # or a higher level ends the section, so entries under a deeper
    # subheading are still Sources entries, not in-text citations.
    brief = (
        "# Research brief\n\n## Sources\n\n### Filed\n\n- FY25 accounts: https://example.invalid/filing\n\n"
        "## Appendix\n\nSee [FY25 accounts].\n"
    )
    warnings = citation_link_warnings(brief)
    assert [warning["line"] for warning in warnings] == [7, 11]
