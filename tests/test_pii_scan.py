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


# ---------------------------------------------------------------------------
# Beyond the UK (issue #111): more company suffixes, international and North American phones, the US EIN.
# Narrow on purpose: this is not an international PII detector, and every new pattern has a negative case below.
# ---------------------------------------------------------------------------

def _snippets(text):
    return [f["snippet"] for f in scan_for_likely_real_data(text)]


def _by_reason(text, fragment):
    return [f["snippet"] for f in scan_for_likely_real_data(text) if fragment in f["reason"]]


def test_detects_company_suffixes_used_outside_the_uk():
    cases = {
        "The borrower is Acme Lending LLC and its parent.": "Acme Lending LLC",
        "Guarantor: Beispiel Handels GmbH.": "Beispiel Handels GmbH",
        "Guarantor: Muster Holding AG.": "Muster Holding AG",
        "Parent is Example Holdings Corp in the US.": "Example Holdings Corp",
        "Parent is Example Holdings Corporation in the US.": "Example Holdings Corporation",
        "Subsidiary Voorbeeld Finance B.V. operates it.": "Voorbeeld Finance B.V",
        "Subsidiary Exemple Finance S.A. operates it.": "Exemple Finance S.A",
        "Subsidiary Esempio Credito S.p.A. operates it.": "Esempio Credito S.p.A",
        "Borrower Sample Trading Pty Ltd in Sydney.": "Sample Trading Pty Ltd",
        "Borrower Sample Trading Pte in Singapore.": "Sample Trading Pte",
        "Borrower Exemple Capital SARL in Paris.": "Exemple Capital SARL",
    }
    for text, name in cases.items():
        assert any(name in snippet for snippet in _by_reason(text, "company name")), text


def test_the_uk_company_suffixes_are_still_detected():
    cases = ["Acme Holdings Limited", "Foo Trading Ltd", "Bar Group PLC", "Baz Partners LLP", "Qux Corp Inc"]
    for name in cases:
        assert _by_reason(f"The borrower is {name} today.", "company name"), name


def test_ordinary_words_and_unbracketed_abbreviations_are_not_company_names():
    text = ("Corporate Credit Policy applies. The Agreement was signed by the Security Agent. The AGM is in May. "
            "Smith & Co acted. South Africa (SA) is the market. Annual Review of the Facility.")
    assert _by_reason(text, "company name") == []


def test_a_bracketed_placeholder_before_a_foreign_suffix_is_not_flagged():
    assert scan_for_likely_real_data("[Company Name] GmbH and [Borrower] LLC and [Parent] Corp.") == []


def test_detects_international_and_north_american_phone_numbers():
    cases = {
        "Call +49 30 12345678 today.": "+49 30 12345678",
        "Call +1 212 555 0147 today.": "+1 212 555 0147",
        "Call +33 1 23 45 67 89 today.": "+33 1 23 45 67 89",
        "Call (212) 555-0147 today.": "(212) 555-0147",
        "Call 212-555-0147 today.": "212-555-0147",
        "Call 212.555.0147 today.": "212.555.0147",
        "Call 212 555 0147 today.": "212 555 0147",
    }
    for text, number in cases.items():
        assert number in _by_reason(text, "phone number"), text


def test_a_uk_number_is_reported_once_and_not_again_as_an_international_or_american_one():
    """One number, one finding. A 0-prefixed UK number stays a UK finding (its tail is not also a North American one); a
    +44 number is found, which it was not before when it followed a space, and not twice."""
    for number in ("+44 20 7946 0958", "+442079460958", "0207 946 0958", "020 7946 0958", "01632 960001", "Tel+44 20 7946 0958"):
        findings = [f for f in scan_for_likely_real_data(f"Call {number} now.") if "phone number" in f["reason"]]
        assert len(findings) == 1, (number, findings)
    for number in ("0207 946 0958", "020 7946 0958", "01632 960001"):
        (finding,) = [f for f in scan_for_likely_real_data(f"Call {number} now.") if "phone number" in f["reason"]]
        assert finding["reason"] == "looks like a real phone number", number


def test_figures_dates_versions_and_ratios_are_not_phone_numbers():
    text = ("Facility of 1,250,000 over 36-60 months at 4.75%. Version 3.2.1 of the model. DSCR 1.25x and leverage 3.5x. "
            "Page 12 of 40. Reference 2026-01-05. Items 10 20 30.")
    assert [f for f in scan_for_likely_real_data(text) if "phone number" in f["reason"]] == []


def test_a_bracketed_phone_number_is_a_placeholder():
    assert scan_for_likely_real_data("Phone: [+49 30 12345678] or [(212) 555-0147].") == []


def test_detects_a_us_employer_identification_number():
    assert _by_reason("Borrower EIN 12-3456789 on file.", "employer identification") == ["12-3456789"]


def test_an_ein_shaped_string_inside_a_longer_number_or_a_date_is_not_an_ein():
    for text in ("Account 12-3456789012 closed.", "Reference 912-3456789.", "Range 12-3456789-5.", "Dated 2026-01-05."):
        assert _by_reason(text, "employer identification") == [], text


def test_the_scanner_still_says_it_is_not_an_international_detector():
    import pii_scan
    doc = " ".join(pii_scan.__doc__.split())
    assert "not an international PII detector" in doc and "clean result" in doc
