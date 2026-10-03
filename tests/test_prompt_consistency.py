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
from pathlib import Path

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


def test_underwriter_prompt_no_longer_tells_the_underwriter_to_self_calculate_working_capital_days(
    underwriter_prompt,
):
    """Regression guard for issue #99: once evaluate_financial_model() computes
    Working Capital Days itself, Guideline 2 must stop instructing the
    Underwriter to calculate it by hand -- that carve-out predates the fix
    and would otherwise keep telling the Underwriter to bypass the now-
    available pre-calculated, code-enforced figure."""
    assert "always calculate it yourself" not in underwriter_prompt
    assert "Working Capital Days" in underwriter_prompt


def test_underwriter_prompt_carves_out_unenforced_collateral_metrics_from_reported_figures(
    underwriter_prompt,
):
    """Regression guard for issue #99's collateral-side fallback: Gross/Net
    Exposure, RV Exposure, and LGD % have no ground-truthed formula
    (unlike collateral_cover_pct), so the Underwriter must be told not to
    declare them in reported_figures -- otherwise check_reported_figures()
    flags every single one as UNRESOLVABLE_REPORTED_FIGURE on every deal."""
    assert "UNRESOLVABLE_REPORTED_FIGURE" in underwriter_prompt
    assert "collateral_cover_pct" in underwriter_prompt


def test_underwriter_prompt_has_a_parent_ubo_support_guideline(underwriter_prompt):
    """Regression guard for issue #96: templates/cam/asset_finance_cam.md's
    Ultimate Parent section promises "Guideline 12: Parent/UBO Support
    Analysis" exists in agents/underwriter_agent.md -- this was previously a
    dangling reference to a Guideline that didn't exist at all (zero
    mentions of "Parent"/"UBO" anywhere in the file). Confirms both the
    heading itself and the three concrete asks the issue called out: the
    full ownership chain (not just the immediate parent), ownership
    percentages, and actively checking for recent ownership changes."""
    assert "Parent/UBO Support Analysis" in underwriter_prompt
    assert "ownership chain" in underwriter_prompt
    assert "ownership percentages" in underwriter_prompt
    assert "recent ownership change" in underwriter_prompt


def test_underwriter_prompt_instructs_the_ownership_tree_in_an_untagged_fence(underwriter_prompt):
    """Regression guard for issue #113: the ownership-tree instruction must
    explicitly tell the Underwriter to use a plain (untagged) fence, never
    ```json -- that tag is reserved for the trailing structured-output
    block and gets stripped from the exported document by docx_builder.py,
    so using it for the tree would silently delete the tree too."""
    assert "box-drawing" in underwriter_prompt
    assert "never" in underwriter_prompt and "```json" in underwriter_prompt


def test_underwriter_prompt_documents_the_swot_table_and_its_grounding_rule(underwriter_prompt):
    """Regression guard for issue #114 piece 1: the SWOT guidance must name
    the four-column table layout and keep the grounding rule -- a SWOT is
    judgment over already-cited facts, never a place for a new unsourced
    claim (the framework's "never invent" principle)."""
    assert "Competitive position (SWOT)" in underwriter_prompt
    assert "Strengths | Weaknesses | Opportunities | Threats" in underwriter_prompt
    assert "never a place to introduce a new unsourced claim" in underwriter_prompt


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


# ---------------------------------------------------------------------------
# Numbered cross-references between prompts and docs (issue #107).
#
# "Guideline 9" and "Audit Checklist item 4" are plain numbers in prose. Insert an item mid-list and every number
# after it silently points at a different item; that exact bug has been found and fixed twice by hand. This is a
# deliberately small guard, not a documentation parser: it finds only the two exact phrases `Guideline N` and
# `Audit Checklist item N`, resolves N against the real numbered list (column-0 `N. ` lines of the Underwriter
# prompt; the Reviewer prompt's "### Audit Checklist" section), and requires the item's heading to still contain the
# subject this repository pins for that number in EXPECTED_SUBJECTS. Adding a reference to a number that is not pinned
# fails too, so every reference is a conscious one. After a legitimate renumbering, fix the references the failure
# lists and update EXPECTED_SUBJECTS in the same change.
# ---------------------------------------------------------------------------

# Between the words: spaces, or ONE line break (the files are hard-wrapped, so "Guideline" can end a line and "9)"
# start the next; a wrapped code comment or block quote continues after its own `#` / `>` marker) -- but never a blank
# line, and never a continuation that begins a numbered-list item ("1. ").
_GAP = r"(?:[ \t]+|[ \t]*\n[ \t]*(?:[#>][ \t]*)?(?!\d+\.[ \t]))"
REFERENCE_RE = re.compile(rf"\b(Guideline|Audit{_GAP}Checklist{_GAP}item){_GAP}(\d+)\b")
NUMBERED_ITEM_RE = re.compile(r"^(\d+)\. (.+)$")
HEADING_RE = re.compile(r"^(#{1,6}) ")

# (kind, file the numbered list lives in, heading its list sits under or None for the whole file)
REFERENCE_TARGETS = {
    "Guideline": ("agents/underwriter_agent.md", None),
    "Audit Checklist item": ("agents/risk_reviewer_agent.md", "### Audit Checklist"),
}
# What each referenced number currently is. Lower-case substring of the item's first line.
EXPECTED_SUBJECTS = {
    "Guideline": {1: "grounding", 5: "structured output", 7: "depth & materiality",
                  9: "analyst-supplied spreading disclosure", 10: "institutional credit policy",
                  11: "persisted deal learnings", 12: "parent/ubo support analysis"},
    "Audit Checklist item": {4: "calibrated credit policy"},
}
SCANNED_FILES = ("CLAUDE.md", "README.md", ".claude/commands/*.md", "agents/*.md", "templates/cam/*.md",
                 "templates/README.md", "evals/README.md", "config/skills_registry.md", "config/system_instructions.md",
                 "scripts/*.py")     # code comments and the prompt/error text the scripts send to the model


def numbered_items(text, under_heading=None):
    """{number: (line_number, first line of the item)} for column-0 `N. ` lines, optionally only inside the
    section that starts at `under_heading` and ends at the next heading of the same or a higher level. The numbers
    must run 1, 2, 3, ... with no gap or repeat, or ValueError says where."""
    lines = text.split("\n")
    start, end = 0, len(lines)
    if under_heading is not None:
        starts = [i for i, line in enumerate(lines) if line.startswith(under_heading)]
        if not starts:
            raise ValueError(f"no heading starting with {under_heading!r}")
        start = starts[0] + 1
        level = len(under_heading) - len(under_heading.lstrip("#"))
        for i in range(start, len(lines)):
            match = HEADING_RE.match(lines[i])
            if match and len(match.group(1)) <= level:
                end = i
                break
    items = {}
    expected = 1
    for i in range(start, end):
        match = NUMBERED_ITEM_RE.match(lines[i])
        if match:
            number = int(match.group(1))
            if number != expected:
                raise ValueError(f"line {i + 1}: numbered item {number} where {expected} was expected "
                                 "(a gap or a repeated number)")
            items[number] = (i + 1, match.group(2))
            expected += 1
    return items


def find_references(text):
    """[(line_number, kind, number, context)] for every `Guideline N` / `Audit Checklist item N` in `text`, also when
    a single line break splits the phrase. `line_number` is where the phrase starts; `context` is the line (or the two
    lines) it sits on. Only singular, capitalised, digit-numbered forms are recognised."""
    lines = text.split("\n")
    found = []
    for match in REFERENCE_RE.finditer(text):
        first = text.count("\n", 0, match.start())
        last = text.count("\n", 0, match.end())
        kind = "Audit Checklist item" if match.group(1).startswith("Audit") else "Guideline"
        found.append((first + 1, kind, int(match.group(2)), " ".join(line.strip() for line in lines[first:last + 1])))
    return found


def stale_references(references, items_by_kind, expected_subjects):
    """Problems (file/line/context strings) for references that do not resolve to the pinned subject.
    `references` is [(file, line_number, kind, number, line)]."""
    problems = []
    for file, line_number, kind, number, line in references:
        where, context = f"{file}:{line_number}", f"{line[:140]}"
        items = items_by_kind[kind]
        pinned = expected_subjects.get(kind, {}).get(number)
        if number not in items:
            problems.append(f"{where}: '{kind} {number}' does not exist (the list has {len(items)} items): {context}")
        elif pinned is None:
            problems.append(f"{where}: '{kind} {number}' is not pinned in EXPECTED_SUBJECTS (it is currently "
                            f"{items[number][1][:60]!r}); pin it, or fix the reference: {context}")
        elif pinned not in items[number][1].lower():
            problems.append(f"{where}: '{kind} {number}' is now {items[number][1][:70]!r} but this repository pins "
                            f"it as {pinned!r} -- stale after a renumbering?: {context}")
    return problems


def _scanned_paths():
    root = Path(__file__).resolve().parents[1]
    return sorted({path for pattern in SCANNED_FILES for path in root.glob(pattern)})


def _real_items():
    root = Path(__file__).resolve().parents[1]
    return {kind: numbered_items((root / file).read_text(encoding="utf-8"), heading)
            for kind, (file, heading) in REFERENCE_TARGETS.items()}


def test_every_numbered_prompt_reference_in_the_repository_resolves_to_its_pinned_subject():
    root = Path(__file__).resolve().parents[1]
    references = [(path.relative_to(root).as_posix(), line_number, kind, number, line)
                  for path in _scanned_paths()
                  for line_number, kind, number, line in find_references(path.read_text(encoding="utf-8"))]
    problems = stale_references(references, _real_items(), EXPECTED_SUBJECTS)
    assert problems == [], "\n" + "\n".join(problems)


def test_the_guard_finds_the_references_it_is_meant_to_guard():
    """Not vacuous: it sees both kinds, across several files, and the pinned numbers are really used."""
    root = Path(__file__).resolve().parents[1]
    found = [(path.name, kind, number) for path in _scanned_paths()
             for _, kind, number, _ in find_references(path.read_text(encoding="utf-8"))]
    assert len(found) >= 30
    assert {kind for _, kind, _ in found} == {"Guideline", "Audit Checklist item"}
    assert len({name for name, _, _ in found}) >= 8
    assert {name for name, _, _ in found} >= {"orchestrator.py", "policy_checks.py", "spreading_check.py"}, \
        "the scripts are scanned too"


def test_the_real_numbered_lists_are_contiguous_and_cover_every_pinned_number():
    """numbered_items() already fails on a gap or repeat; a list that shrank below a pinned number is also wrong.
    (No hard-coded length: appending a legitimate new item must not need a test edit.)"""
    items = _real_items()
    for kind, pinned in EXPECTED_SUBJECTS.items():
        assert list(items[kind]) == list(range(1, len(items[kind]) + 1))
        assert max(pinned) <= max(items[kind]), kind


def test_a_phrase_split_across_a_line_break_is_found_where_it_starts():
    real = (Path(__file__).resolve().parents[1] / "scripts" / "spreading_check.py").read_text(encoding="utf-8")
    assert any(kind == "Guideline" and number == 9 and "Guideline 9" in context.replace("\n", " ")
               for _, kind, number, context in find_references(real)), "the wrapped reference in spreading_check.py"


# --- the machinery, on synthetic text ---------------------------------------------------------------------------

SYNTHETIC_GUIDELINES = """# Role
Intro.

1. Grounding: cite sources.
2. Ratios: compute them.
3. Disclosure: say so (see Guideline 1).
"""


def _synthetic_problems(doc, guidelines=SYNTHETIC_GUIDELINES, pinned=None):
    pinned = pinned if pinned is not None else {"Guideline": {1: "grounding", 3: "disclosure"}}
    refs = [("doc.md", n, k, num, line) for n, k, num, line in find_references(doc)]
    return stale_references(refs, {"Guideline": numbered_items(guidelines), "Audit Checklist item": {}}, pinned)


def test_correct_references_pass():
    assert _synthetic_problems("Per Guideline 1, and again Guideline 3.") == []


def test_a_reference_to_a_missing_item_fails_with_file_line_and_context():
    problems = _synthetic_problems("fine\nSee Guideline 7 for the rest.")
    assert len(problems) == 1
    assert problems[0].startswith("doc.md:2:") and "'Guideline 7' does not exist" in problems[0]
    assert "See Guideline 7 for the rest." in problems[0]


def test_a_reference_to_a_number_nobody_pinned_fails():
    problems = _synthetic_problems("See Guideline 2.")
    assert len(problems) == 1 and "not pinned" in problems[0] and "'Ratios: compute them.'" in problems[0]


def test_inserting_an_item_mid_list_makes_exactly_the_shifted_references_stale():
    inserted = SYNTHETIC_GUIDELINES.replace("2. Ratios: compute them.\n3. Disclosure",
                                            "2. Ratios: compute them.\n3. A NEW ITEM.\n4. Disclosure")
    doc = "Per Guideline 1 and per Guideline 3."          # Guideline 3 used to be the disclosure item
    assert _synthetic_problems(doc, guidelines=SYNTHETIC_GUIDELINES) == []
    problems = _synthetic_problems(doc, guidelines=inserted)
    assert len(problems) == 1                              # Guideline 1 did not move; Guideline 3 did
    assert "'Guideline 3' is now 'A NEW ITEM.'" in problems[0] and "'disclosure'" in problems[0]
    assert "stale after a renumbering" in problems[0]


def test_a_reference_wrapped_over_a_line_break_is_found_and_reported_where_it_starts():
    text = "intro\nSee the Audit\nChecklist item 2 and, per\nGuideline\n3 here.\nAudit Checklist\nitem 4 too."
    assert [(n, k, num) for n, k, num, _ in find_references(text)] == [
        (2, "Audit Checklist item", 2), (4, "Guideline", 3), (6, "Audit Checklist item", 4)]
    problems = _synthetic_problems("padding\nper\nGuideline\n9 wrapped")
    assert len(problems) == 1 and problems[0].startswith("doc.md:3:") and "Guideline 9 wrapped" in problems[0]


def test_a_reference_wrapped_inside_a_code_comment_or_block_quote_is_found():
    comment = "    # qualitative audit (Audit\n    # Checklist item 5), not something\n    # per Guideline\n    # 9 only"
    assert [(n, k, num) for n, k, num, _ in find_references(comment)] == [(1, "Audit Checklist item", 5), (3, "Guideline", 9)]
    assert [(k, num) for _, k, num, _ in find_references("> see Guideline\n> 12 for details")] == [("Guideline", 12)]


@pytest.mark.parametrize("prose", [
    "the Guideline\n\n3 steps",                    # a blank line is a paragraph break, not a wrap
    "see the Guideline\n1. First item of a list",   # a numbered-list line is not a continuation
    "Guideline\n  \n4",
    "see the Guideline\n# 1. First item",           # a comment-marked list item is not a continuation either
])
def test_a_wrap_is_never_bridged_across_a_blank_line_or_into_a_list_item(prose):
    assert find_references(prose) == []


def test_two_references_on_one_line_are_both_checked():
    problems = _synthetic_problems("Guideline 1 and Guideline 9 together")
    assert len(problems) == 1 and "'Guideline 9'" in problems[0]


@pytest.mark.parametrize("prose", [
    "Follow the guideline 3 steps below.",           # lowercase: ordinary prose
    "A guideline is not a rule.",
    "Guideline N is a placeholder.",                 # no digits
    "Guidelines 1 through 3 apply.",                 # plural form is not one of the two guarded phrases
    "See item 4 of the checklist.",                  # not the guarded phrase
    "The Audit Checklist items below all apply.",    # no number
    "Audit Checklist 4",                             # missing the word 'item'
    "Guideline9 and Guideline-9",                    # not the phrase
    "version 1.9 of Guideline",                      # number before, not after
])
def test_irrelevant_prose_is_not_read_as_a_reference(prose):
    assert find_references(prose) == []


def test_the_reference_phrases_are_found_inside_markdown_decoration():
    found = find_references("(`agents/underwriter_agent.md`'s Guideline 9) and **Audit Checklist item 4**, plus Guideline 12:")
    assert [(kind, number) for _, kind, number, _ in found] == [("Guideline", 9), ("Audit Checklist item", 4), ("Guideline", 12)]


def test_numbered_items_stays_inside_its_section():
    doc = "### Before\n1. not this\n2. nor this\n\n### Audit Checklist (always applies):\n1. first\n2. second\n\n### After\n1. not this either\n"
    assert numbered_items(doc, "### Audit Checklist") == {1: (6, "first"), 2: (7, "second")}
    with pytest.raises(ValueError, match="no heading"):
        numbered_items(doc, "### Missing")


def test_a_deeper_heading_does_not_end_the_section_but_an_equal_or_higher_one_does():
    doc = "## Section\n1. one\n#### deeper\n2. two\n## Next\n3. three\n"
    assert list(numbered_items(doc, "## Section")) == [1, 2]


@pytest.mark.parametrize("doc, message", [("1. a\n3. c\n", "3 where 2 was expected"),
                                          ("1. a\n2. b\n2. again\n", "2 where 3 was expected"),
                                          ("2. starts at two\n", "2 where 1 was expected")])
def test_a_gap_or_repeated_number_in_a_list_is_reported(doc, message):
    with pytest.raises(ValueError, match=message):
        numbered_items(doc)


def test_indented_numbered_lines_are_sub_points_not_items():
    assert list(numbered_items("1. a\n   1. a sub-point\n   2. another\n2. b\n")) == [1, 2]
