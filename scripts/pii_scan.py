"""Heuristic scanner for likely-real (non-placeholder) data in a CAM
template -- a second line of defense before promoting a `templates/local/
cam/` override to the shared `templates/cam/` defaults (see
`templates/README.md`'s "Contributing one back" section and CLAUDE.md's
"Promoting a local override upstream" rule).

This is deliberately a heuristic, not a guarantee: it catches the obvious,
mechanically-detectable cases (an unbracketed currency figure, an email
address, a Companies House-style registration number, a "Something
Limited"-shaped company name, a real-looking date) that a human skimming a
long template can plausibly miss. It cannot verify a template is *fully*
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

# A bracketed [placeholder] is the templates' own convention for "not real
# data" (see templates/cam/*.md) -- every pattern below is checked against
# the line with any [bracketed] spans first removed, so a legitimate
# placeholder like "[Amount & Currency]" or "[Company Name] Limited" never
# trips a finding on the words around it.
BRACKETED_SPAN_RE = re.compile(r"\[[^\[\]]*\]")

CURRENCY_AMOUNT_RE = re.compile(r"[£$€]\s?\d[\d,]*(?:\.\d+)?")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
UK_COMPANIES_HOUSE_NUMBER_RE = re.compile(r"\b\d{8}\b")
UK_PHONE_RE = re.compile(r"\b(?:0|\+44\s?)\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}\b")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
LONG_DATE_RE = re.compile(
    r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4}\b"
)
COMPANY_NAME_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&,.'()-]*(?:\s+[A-Z(][A-Za-z0-9&,.'()-]*)*\s+"
    r"(?:Limited|Ltd\.?|PLC|Plc|LLP|Inc\.?)\b"
)

# (pattern, human-readable reason) -- order doesn't matter, every pattern is
# checked against every line.
CHECKS = [
    (CURRENCY_AMOUNT_RE, "looks like a real currency amount, not a [placeholder]"),
    (EMAIL_RE, "looks like a real email address"),
    (UK_COMPANIES_HOUSE_NUMBER_RE, "looks like a real 8-digit Companies House registration number"),
    (UK_PHONE_RE, "looks like a real phone number"),
    (ISO_DATE_RE, "looks like a real ISO date, not a [placeholder]"),
    (LONG_DATE_RE, "looks like a real date, not a [placeholder]"),
    (COMPANY_NAME_RE, "looks like a real company name (Limited/Ltd/PLC/LLP/Inc), not a [placeholder]"),
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
    parser = argparse.ArgumentParser(
        description="Scan a CAM template file for likely-real (non-placeholder) data before "
                     "promoting it from templates/local/cam/ to the shared templates/cam/ "
                     "defaults. Prints JSON findings to stdout -- an empty list is a heuristic "
                     "'nothing obvious found', not a guarantee; a human review is still required "
                     "per templates/README.md's promotion rule."
    )
    parser.add_argument("path", help="Path to the template file to scan")
    args = parser.parse_args()

    with open(args.path, encoding="utf-8") as f:
        content = f.read()

    print(json.dumps({"findings": scan_for_likely_real_data(content)}, indent=2))
