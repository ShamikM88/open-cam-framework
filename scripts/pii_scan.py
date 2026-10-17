"""Heuristic scanner for likely-real (non-placeholder) data in a CAM
template -- a second line of defense before promoting a `templates/local/
cam/` override to the shared `templates/cam/` defaults (see
`templates/README.md`'s "Contributing one back" section and CLAUDE.md's
"Promoting a local override upstream" rule).

This is deliberately a heuristic, not a guarantee: it catches the obvious,
mechanically-detectable cases (an unbracketed currency figure, an email
address, a Companies House-style registration number, a "Something
Limited"-shaped company name, a real-looking date) that a human skimming a
long template can plausibly miss. The patterns are UK-shaped first, with a
short list of other common company suffixes, international and North American
phone formats, the US EIN, IBANs, German HRA/HRB register numbers and the VAT numbers
of a few countries added (issue #111); it is not an international PII detector, and a
clean result says nothing about, for example, another country's register number, a
national ID or a street address. It cannot verify a template is *fully*
scrubbed -- that still requires a human review, per the existing
promotion rule. False positives are expected and fine (a flagged line that
turns out to be a genuinely generic example is a two-second check); false
negatives are also possible (nothing here understands context), so this
finding a clean result is not itself a clearance to promote.

Dependency-free (no anthropic/docx/openpyxl imports), matching
state_manager.py/template_resolver.py/policy_checks.py's pattern, so it's
cheap to unit test and safe to run standalone.
"""
import argparse
import json
import re
import sys

# A bracketed [placeholder] is the templates' own convention for "not real
# data" (see templates/cam/*.md) -- every pattern below is checked against
# the line with any [bracketed] spans first removed, so a legitimate
# placeholder like "[Amount & Currency]" or "[Company Name] Limited" never
# trips a finding on the words around it.
BRACKETED_SPAN_RE = re.compile(r"\[[^\[\]]*\]")

CURRENCY_AMOUNT_RE = re.compile(r"[£$€¥]\s?\d[\d,]*(?:\.\d+)?")
_AMOUNT_WITH_OPTIONAL_SCALE = (
    r"(?:\d{1,3}(?:[,. ]\d{3})*(?:[.,]\d+)?|\d{4,})(?:\s*[mMkKbB])?"
)
CURRENCY_CODES = r"(?:GBP|USD|EUR|CHF|JPY|CAD|AUD)"
CURRENCY_CODE_AMOUNT_RE = re.compile(
    rf"(?<![A-Z]){CURRENCY_CODES}\s*{_AMOUNT_WITH_OPTIONAL_SCALE}(?![A-Z0-9])"
    rf"|(?<![A-Z0-9]){_AMOUNT_WITH_OPTIONAL_SCALE}\s*{CURRENCY_CODES}\b",
    re.IGNORECASE,
)
CURRENCY_SUFFIX_AMOUNT_RE = re.compile(
    r"(?<!\w)\d{1,3}(?:[. ]\d{3})+(?:,\d+)?\s*[€$£¥](?!\w)"
)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
UK_COMPANIES_HOUSE_NUMBER_RE = re.compile(r"\b\d{8}\b")
UK_PHONE_RE = re.compile(r"\b(?:0|\+44\s?)\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}\b")
# Two more phone shapes (issue #111), narrow on purpose so a figure or a date is not read as a number: an international
# number needs its leading "+" and no word character before it. That makes it exclusive with UK_PHONE_RE's "+44" branch,
# whose leading \b needs a word character before the "+": that branch never matches "call +44 20 ..." (after a space), so
# a +44 number is reported here, and exactly once. A North American one needs the area code in brackets or every group
# separated, and no digit or "+" touching it (so the tail of a longer UK number is not reported a second time).
INTERNATIONAL_PHONE_RE = re.compile(
    r"(?<!\w)\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}(?!\d)"
)
NORTH_AMERICAN_PHONE_RE = re.compile(r"(?<![\d+])(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\d)")
# A US employer identification number (NN-NNNNNNN); the UK has no counterpart of this shape.
US_EIN_RE = re.compile(r"(?<![\d-])\d{2}-\d{7}(?![\d-])")
# An IBAN (two capitals, two check digits, then 8-30 letters and digits, optionally in groups of four), a German
# commercial register number (HRA/HRB) and a VAT number with the country prefix for a few common countries. Shape only: no
# checksum is verified, and a number written without its prefix or in an unlisted country is not recognised.
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
GERMAN_REGISTER_NUMBER_RE = re.compile(r"\bHR[AB]\s?\d{1,6}\b")
VAT_NUMBER_RE = re.compile(
    r"\b(?:ATU\d{8}|BE0?\d{9}|DE\d{9}|ES[A-Z0-9]\d{7}[A-Z0-9]|FR[A-Z0-9]{2}\d{9}|GB\d{9}(?:\d{3})?|"
    r"IE\d[A-Z0-9+*]\d{5}[A-Z]{1,2}|IT\d{11}|NL\d{9}B\d{2})\b"
)
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUMERIC_SLASH_DATE_RE = re.compile(
    r"\b(?:(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])|"
    r"(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01]))/\d{4}\b"
)
NUMERIC_DOT_DATE_RE = re.compile(
    r"\b(?:0?[1-9]|[12]\d|3[01])\.(?:0?[1-9]|1[0-2])\.\d{4}\b"
)
_MONTH_NAME_PATTERN = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
)
LONG_DATE_RE = re.compile(
    r"\b(?:\d{1,2}\s+" + _MONTH_NAME_PATTERN + r"\.?\s+\d{4}|"
    + _MONTH_NAME_PATTERN + r"\.?\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
COMPANY_NAME_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&,.'()-]*(?:\s+[A-Z(][A-Za-z0-9&,.'()-]*)*\s+"
    r"(?:Limited|Ltd\.?|PLC|Plc|LLP|Inc\.?"
    # Common suffixes elsewhere (issue #111). Only forms that are rarely an ordinary word are listed: "Co", "SA" and "SAS"
    # are left out because they would flag far too much unrelated text.
    r"|LLC|L\.L\.C\.?|Corp\.?|Corporation|GmbH|AG|S\.A\.?|S\.p\.A\.?|B\.V\.?|BV|N\.V\.?|NV|SARL|Pty|Pte)\b"
)

# (pattern, human-readable reason) -- order doesn't matter, every pattern is
# checked against every line.
CHECKS = [
    (CURRENCY_AMOUNT_RE, "looks like a real currency amount, not a [placeholder]"),
    (CURRENCY_CODE_AMOUNT_RE, "looks like a currency amount next to an ISO currency code"),
    (CURRENCY_SUFFIX_AMOUNT_RE, "looks like a currency amount with a trailing symbol"),
    (EMAIL_RE, "looks like a real email address"),
    (UK_COMPANIES_HOUSE_NUMBER_RE, "looks like a real 8-digit Companies House registration number"),
    (UK_PHONE_RE, "looks like a real phone number"),
    (INTERNATIONAL_PHONE_RE, "looks like a real international phone number"),
    (NORTH_AMERICAN_PHONE_RE, "looks like a real North American phone number"),
    (US_EIN_RE, "looks like a real US employer identification number (EIN)"),
    (IBAN_RE, "looks like a real IBAN (bank account number)"),
    (GERMAN_REGISTER_NUMBER_RE, "looks like a real German commercial register number (HRA/HRB)"),
    (VAT_NUMBER_RE, "looks like a real VAT number"),
    (ISO_DATE_RE, "looks like a real ISO date, not a [placeholder]"),
    (NUMERIC_SLASH_DATE_RE, "looks like a real numeric date, not a [placeholder]"),
    (NUMERIC_DOT_DATE_RE, "looks like a real numeric date, not a [placeholder]"),
    (LONG_DATE_RE, "looks like a real date, not a [placeholder]"),
    (COMPANY_NAME_RE, "looks like a real company name (Limited/Ltd/PLC/LLP/Inc/LLC/Corp/GmbH/AG/S.A. ...), not a [placeholder]"),
]


def scan_for_likely_real_data(text):
    """Scan `text` line by line and return a list of
    `{"line": <1-indexed line number>, "snippet": <the matched text>,
    "reason": <why it was flagged>}` findings, in the order encountered.
    Every [bracketed] span is stripped from each line before matching, so
    the templates' own placeholder convention never triggers a false
    finding on the surrounding words. An empty list means nothing
    mechanically suspicious was found -- see the module docstring for why
    that's not the same as "confirmed clean"."""
    findings = []
    for line_number, line in enumerate((text or "").splitlines(), start=1):
        stripped_line = BRACKETED_SPAN_RE.sub(" ", line)
        for pattern, reason in CHECKS:
            for match in pattern.finditer(stripped_line):
                findings.append({
                    "line": line_number,
                    "snippet": match.group(0),
                    "reason": reason,
                })
    return findings


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="Scan a CAM template file for likely-real (non-placeholder) data before "
                     "promoting it from templates/local/cam/ to the shared templates/cam/ "
                     "defaults. Prints JSON findings to stdout -- an empty list is a heuristic "
                     "'nothing obvious found', not a guarantee; a human review is still required "
                     "per templates/README.md's promotion rule."
    )
    parser.add_argument("path", help="Path to the template file to scan")
    args = parser.parse_args()

    try:
        with open(args.path, encoding="utf-8") as f:
            content = f.read()
        findings = scan_for_likely_real_data(content)
    except OSError as exc:
        sys.exit(f"error: {exc}")

    print(json.dumps({"findings": findings}, indent=2))
