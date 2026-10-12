"""A checker for the reports that the evidence-grounded analyst skills write (issue #196).

A skill is a prompt, so no test can show that a model obeys it, and no test in this repository makes a model call. What can
be tested is the contract a *report* must meet. This module reads a report together with the deal folder it is about and
returns the problems it finds, so a test can show two things: a hand-written report for a synthetic deal is clean, and
each deliberate breakage of it is caught. That shows the contract is checkable and that the checker catches what it
claims to; it does not show a model writes a clean report.

What it checks, for a report that is not a refusal:

- it opens with a "Material reviewed" line and ends with the skill's closing caveat;
- each item has all of the skill's fields, once each, in order, or the report says nothing was identified and has no item;
- every backtick-quoted value or key occurs in the deal's recorded text (a path is exempt), and every number outside a
  quote occurs there too, so no figure is derived;
- no verdict word, grade, score or lend/decline wording appears as the report's own conclusion (a recorded value quoted in
  backticks is not the report's conclusion).
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReportSpec:
    item_word: str          # "Challenge" or "Risk": each item is "### <item_word> N -- title"
    labels: tuple           # the fields of an item, in order
    nothing_phrase: str     # what the report says when it identifies no item
    caveat: str             # the closing caveat's opening words
    required_lines: tuple = ()   # lines the report must carry besides Material reviewed, e.g. "**Categories reviewed:**"


VERDICT_RE = re.compile(r"(?<![A-Za-z])(?:APPROVED|REJECTED|PASS|PASSED|FAIL|FAILED)(?![A-Za-z])", re.IGNORECASE)
CONCLUSION_RE = re.compile(
    r"(?i)\b(?:we|i)\s+recommend|\brecommend(?:ed|ation)?\b[^.\n]{0,20}\b(?:lend|lending|declin\w*|approv\w*)"
    r"|\bshould\s+(?:be\s+)?(?:lent|lend|declin\w*|approv\w*)|\b(?:borrower|credit|risk)\s+(?:rating|grade|score)\b"
    r"|\b(?:overall\s+)?(?:rating|grade|score)\s*[:=]|\bcleared\b|\blend/decline\b")
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def record_text(deal_folder):
    """Everything the skill may read for this deal: state.json, the manifest, the conventional draft."""
    folder = Path(deal_folder)
    parts = []
    for path in (folder / "state.json", folder / "sources" / "manifest.json",
                 folder.parent / f"{folder.name.rsplit('_', 1)[0]}_draft.md"):
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _is_path(span):
    return "/" in span or span.endswith((".json", ".md", ".html", ".txt"))


def _without_quotes(text):
    return CODE_SPAN_RE.sub(" ", text)


def report_problems(report, deal_folder, spec, *, items=None, stopped=False):
    """The problems found in `report`; an empty list means it meets the contract.

    `items` is the number of items the report should hold (0 for "nothing identified"); `stopped` is for a report that
    refused or stopped, which has no items and no caveat requirement."""
    problems = []
    text = report.strip()
    has_items = re.findall(rf"(?m)^### {spec.item_word} \d+", text)
    if stopped:
        if has_items:
            problems.append("a refusal or stop must not contain items")
        if not re.match(r"\*\*(?:Refused|Stopped):\*\*", text):
            problems.append("a refusal or stop must open with Refused or Stopped and say why")
        return problems
    recorded = record_text(deal_folder)
    if not text.startswith("**Material reviewed:**"):
        problems.append("the report must open with a Material reviewed line")
    for line in spec.required_lines:
        if line not in text:
            problems.append(f"the report must carry a {line} line")
    if not text.splitlines()[-1].lstrip("> ").startswith(spec.caveat):
        problems.append("the closing caveat is missing")
    if items is not None and len(has_items) != items:
        problems.append(f"expected {items} items, found {len(has_items)}")
    if has_items and spec.nothing_phrase in text:
        problems.append("the report both identifies items and says none were identified")
    if not has_items and spec.nothing_phrase not in text:
        problems.append("the report has no item and does not say none was identified")
    for block in re.split(r"(?m)^### ", text)[1:]:
        if not re.match(rf"{spec.item_word} \d+", block):
            continue
        positions = [block.find(f"**{label}:**") for label in spec.labels]
        if -1 in positions:
            problems.append(f"{block.splitlines()[0]}: missing field {spec.labels[positions.index(-1)]}")
        elif positions != sorted(positions):
            problems.append(f"{block.splitlines()[0]}: fields out of order")
        elif any(block.count(f"**{label}:**") != 1 for label in spec.labels):
            problems.append(f"{block.splitlines()[0]}: a field appears more than once")
    for span in CODE_SPAN_RE.findall(text):
        if not _is_path(span) and span not in recorded:
            problems.append(f"quoted value not in the record: `{span}`")
    prose = _without_quotes(text)
    scan = re.sub(r"(?m)^### \w+ \d+", "", prose)
    scan = re.sub(r"(?m)^\s*\d+\.\s", "", scan)
    scan = scan.replace(spec.caveat, "")
    for number in NUMBER_RE.findall(scan):
        if number.rstrip(".,") not in recorded and number.replace(",", "") not in recorded.replace(",", ""):
            problems.append(f"number not in the record: {number}")
    if VERDICT_RE.search(prose):
        problems.append("a verdict word appears outside a quoted recorded value")
    if CONCLUSION_RE.search(prose):
        problems.append("a rating, score, recommendation or clearance appears")
    return problems


def state_values(deal_folder):
    """The parsed state.json, for a test that wants to compare a quoted value with the record."""
    return json.loads((Path(deal_folder) / "state.json").read_text(encoding="utf-8"))
