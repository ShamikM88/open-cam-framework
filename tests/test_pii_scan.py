"""Tests for scripts/pii_scan.py -- the heuristic "likely real data" scanner
used as a second line of defense before promoting a templates/local/cam/
override to the shared templates/cam/ defaults (see templates/README.md's
"Contributing one back" section)."""
import glob
import os

from pii_scan import scan_for_likely_real_data


def _reasons(findings):
    return {f["reason"] for f in findings}


# ---------------------------------------------------------------------------
# Each pattern individually
# ---------------------------------------------------------------------------

def test_detects_an_unbracketed_currency_amount():
    findings = scan_for_likely_real_data("Existing exposure of £800,000 on this facility.")
    assert any("currency amount" in f["reason"] for f in findings)
    assert any(f["snippet"] == "£800,000" for f in findings)


def test_detects_an_email_address():
    findings = scan_for_likely_real_data("Contact john.smith@example.com for details.")
    assert any("email address" in f["reason"] for f in findings)


def test_detects_an_8_digit_companies_house_number():
    findings = scan_for_likely_real_data("Company Registration No. 02480571")
    assert any("Companies House" in f["reason"] for f in findings)


def test_detects_a_uk_phone_number():
    findings = scan_for_likely_real_data("Call 07911 123456 for more details.")
    assert any("phone number" in f["reason"] for f in findings)


def test_detects_an_iso_date():
    findings = scan_for_likely_real_data("Confirmed on 2026-09-13.")
    assert any("ISO date" in f["reason"] for f in findings)


def test_detects_a_long_form_date():
    findings = scan_for_likely_real_data("Meeting scheduled for 14 September 2026.")
    assert any(f["reason"] == "looks like a real date, not a [placeholder]" for f in findings)


def test_detects_a_company_name_with_parentheses():
    """A real UK-style company name like "TLC (Southern) Limited" must be
    caught even though the parentheses break a naive word-boundary regex."""
    findings = scan_for_likely_real_data("Borrower: TLC (Southern) Limited")
    assert any(f["snippet"] == "TLC (Southern) Limited" for f in findings)


def test_detects_a_plc_and_an_llp():
    findings = scan_for_likely_real_data("Guaranteed by Acme Holdings PLC and Beta Partners LLP.")
    snippets = {f["snippet"] for f in findings}
    assert "Acme Holdings PLC" in snippets
    assert "Beta Partners LLP" in snippets


# ---------------------------------------------------------------------------
# Placeholder text must never be flagged
# ---------------------------------------------------------------------------

def test_bracketed_placeholders_are_never_flagged():
    text = (
        "| [Borrower Legal Name] | [Internal Reference] | [Amount & Currency] |\n"
        "Company Registration No. [Registration Number]\n"
        "Annual Review Date (Current) [Date]\n"
    )
    assert scan_for_likely_real_data(text) == []


def test_a_placeholder_next_to_real_looking_text_only_flags_the_real_part():
    """[Company Name] Limited -- the bracketed part is a placeholder, but
    "Limited" alone isn't a company-name match without a preceding
    capitalized word, so this specific combination should find nothing."""
    findings = scan_for_likely_real_data("Borrower: [Company Name] Limited")
    assert findings == []


def test_empty_and_none_input_returns_no_findings():
    assert scan_for_likely_real_data("") == []
    assert scan_for_likely_real_data(None) == []


# ---------------------------------------------------------------------------
# Line numbers and multiple findings per line
# ---------------------------------------------------------------------------

def test_line_numbers_are_1_indexed_and_correct():
    text = "Clean line one.\nClean line two.\nContact test@example.com here.\n"
    findings = scan_for_likely_real_data(text)
    assert len(findings) == 1
    assert findings[0]["line"] == 3


def test_multiple_findings_on_the_same_line_are_all_reported():
    findings = scan_for_likely_real_data("£500,000 owed, contact test@example.com, ref 12345678.")
    assert len(findings) == 3
    assert _reasons(findings) == {
        "looks like a real currency amount, not a [placeholder]",
        "looks like a real email address",
        "looks like a real 8-digit Companies House registration number",
    }


# ---------------------------------------------------------------------------
# The shipped default templates themselves must always scan clean -- if a
# future edit accidentally leaves real example data in one of them, this
# test should catch it.
# ---------------------------------------------------------------------------

def test_shipped_default_templates_scan_clean():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_paths = glob.glob(os.path.join(repo_root, "templates", "cam", "*.md"))
    assert template_paths, "expected at least one shipped template to check"
    for path in template_paths:
        with open(path, encoding="utf-8") as f:
            findings = scan_for_likely_real_data(f.read())
        assert findings == [], f"{path} flagged likely-real data: {findings}"
