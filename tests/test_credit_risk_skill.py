"""The `credit-risk` skill against synthetic deals (issue #196, Phase 4).

Same method as `test_credit_challenge_skill.py`, which says what it can and cannot show: one synthetic deal per scenario in
`docs/skill-design.md` ("Implementation acceptance gates for the follow-up PR"), a hand-written report for each, and
`skill_report_contract.report_problems` applied to it, with a deliberately broken variant that must be caught. Nothing
calls a model, so a pass shows the contract is checkable on realistic records and that the skill asks for it.
"""
import re

import pytest
from skill_report_contract import ReportSpec, report_problems
from test_credit_challenge_skill import (BASE_STATE, DATE, DRAFT, FOLDER, NO_DRAFT, PROPOSAL, WITH_DRAFT, make_deal)
from test_skill_inventory import body_of, frontmatter, read

SPEC = ReportSpec(
    item_word="Risk",
    labels=("Risk", "Recorded evidence", "Potential impact", "Current mitigant", "Residual risk / uncertainty",
            "Information gap / next question"),
    nothing_phrase="No material risk identified in the material reviewed",
    caveat="Analyst aid only. Read-only: no deal record or draft was changed.",
    required_lines=("**Categories reviewed:**",),
)
CAVEAT = f"> {SPEC.caveat} This is an organised view of the recorded material, not a formal review."
NONE = "None recorded in the material reviewed."


def risk(n, title, what, evidence, impact, mitigant, residual, gap):
    return (f"### Risk {n} — {title}\n\n1. **Risk:** {what}\n2. **Recorded evidence:** {evidence}\n"
            f"3. **Potential impact:** {impact}\n4. **Current mitigant:** {mitigant}\n"
            f"5. **Residual risk / uncertainty:** {residual}\n6. **Information gap / next question:** {gap}\n")


def header(manifest, draft, categories):
    return f"{'**Material reviewed:** `' + FOLDER + '/state.json`; manifest: ' + manifest + '; draft: ' + draft + '.'}\n" \
           f"**Categories reviewed:** {categories}\n\n"


SCENARIOS = {}


def scenario(name, deal, report, items, must_say=(), must_not_say=(), extra=None):
    SCENARIOS[name] = (deal, report, items, must_say, must_not_say, extra)


scenario(
    "several_categories_one_with_no_material",
    dict(state={**BASE_STATE, "steps_completed": ["triage", "spread", "commercial", "collateral"],
                "financials_source": "framework-computed", "ratios": {"FY-Current": {"dscr": 1.4}},
                "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.25}],
                "commercial": {"largest_customer_note": "Largest customer is 20% of revenue"},
                "security_package": {"summary": "First charge over the fleet"}}),
    header("not present", NO_DRAFT, "financial; commercial/business model; collateral/security. No material identified for: "
           "management and execution; sector/market.") +
    risk(1, "Covenant headroom", "The recorded debt service cover sits close to its minimum covenant.",
         "`dscr` for `FY-Current` is `1.4` against a `minimum` covenant with threshold `1.25`, as recorded.",
         "If the ratio fell, the covenant would be nearer to breach.", NONE,
         "Whether the ratio is stable is not recorded.", "What drives the FY-Current ratio?") + "\n" +
    risk(2, "Customer concentration", "One customer is a large part of revenue.",
         "`Largest customer is 20% of revenue` (`commercial`).",
         "Losing the customer would reduce revenue.", NONE,
         "Contract length is not recorded.", "How long is the largest customer's contract?") + "\n" +
    risk(3, "Security", "The security is recorded only as a first charge.",
         "`First charge over the fleet` (`security_package`).",
         "The value that could be recovered from the fleet is not recorded.",
         "`First charge over the fleet` ranks ahead of other creditors.",
         "No valuation is recorded.", "Is there a valuation of the fleet?") + "\n" + CAVEAT,
    3, must_say=("No material identified for: management and execution; sector/market",),
    must_not_say=("### Risk 4",))

scenario(
    "sourced_and_analyst_supplied",
    dict(state={**BASE_STATE, "financials_source": "analyst-supplied", "financials": {"FY-Current": {"ebitda": 250}}},
         manifest=[("Registered office confirmed", "https://example.invalid/registry")]),
    header("1 entry", NO_DRAFT, "financial; ownership, guarantees and support. No material identified for: collateral/security.") +
    risk(1, "Basis of the earnings figure", "The earnings figure rests on the analyst's own template.",
         "`ebitda` for `FY-Current` is `250` and `financials_source` is `analyst-supplied`. The one manifest entry, "
         "`Registered office confirmed`, is a sourced claim about something else.",
         "A reader may take the figure as independently sourced.", NONE,
         "No source for the figure is recorded.", "Which document supports the figure?") + "\n" + CAVEAT,
    1, must_say=("analyst-supplied", "sourced claim"))

scenario(
    "recorded_mitigant",
    dict(state={**BASE_STATE, "commercial": {"supplier_note": "A second approved supplier is recorded for the main input"}},
         draft="Supply of the main input is concentrated in one supplier."),
    header("not present", WITH_DRAFT, "commercial/business model. No material identified for: collateral/security.") +
    risk(1, "Supplier concentration", "Supply of the main input is concentrated.",
         "The draft says `Supply of the main input is concentrated in one supplier.`",
         "A failure of that supplier could interrupt supply.",
         "`A second approved supplier is recorded for the main input` (`commercial`).",
         "Whether the second supplier could cover the volume is not recorded.",
         "What share of the input could the second supplier provide?") + "\n" + CAVEAT,
    1, extra={"Current mitigant": "A second approved supplier is recorded for the main input"})

scenario(
    "contradictory_evidence",
    dict(state={**BASE_STATE, "commercial": {"largest_customer_note": "Largest customer is 20% of revenue"}},
         draft="The largest customer accounts for 35% of revenue."),
    header("not present", WITH_DRAFT, "commercial/business model.") +
    risk(1, "Customer concentration", "The record gives two different pictures of the largest customer.",
         "`commercial` records `Largest customer is 20% of revenue`; the draft says `The largest customer accounts for 35% "
         "of revenue.` Both are quoted as they stand.",
         "If the larger share is right, concentration is higher than the commercial step recorded.", NONE,
         "The record does not say which is current.", "Which document gives the share?") + "\n" + CAVEAT,
    1, must_say=("Both are quoted",))

scenario(
    "absent_or_unverifiable_evidence",
    dict(state=BASE_STATE,
         manifest_file=[{"filename": "registry_extract.html", "url": "https://example.invalid/registry", "step": "triage",
                         "claim": "Registered office confirmed", "fetched_date": DATE}]),
    header("1 entry", NO_DRAFT, "ownership, guarantees and support.") +
    risk(1, "Unverifiable registry claim", "The registered-office claim cannot be traced to a saved document.",
         "The manifest lists `registry_extract.html` for `Registered office confirmed`, but no such file is in `sources/`. "
         "A manifest entry is provenance metadata, not proof that a source supports the claim.",
         "If the document was never saved, the claim cannot be audited later.", NONE,
         "Whether the claim is true is not established, and its absence from `sources/` is not evidence that it is false.",
         "Can the extract be saved again?") + "\n" + CAVEAT,
    1, must_say=("no such file", "not proof"))

scenario(
    "no_draft",
    dict(state={**BASE_STATE, "triage": {"verdict": "Go", "sources": []}}),
    header("not present", NO_DRAFT, "commercial/business model.") +
    risk(1, "Basis of the Go screen", "The screen lists no source.",
         "`verdict` is `Go` and `sources` is `[]`. No draft was available, so only the record was reviewed.",
         "A screen with no source cannot be re-checked.", NONE,
         "The record may be incomplete rather than the screen unsupported.", "Which sources informed it?") + "\n" + CAVEAT,
    1, must_say=("no draft available",))

scenario(
    "unique_conventional_draft",
    dict(state=BASE_STATE, draft="The borrower is the market leader in its region."),
    header("not present", WITH_DRAFT, "sector/market.") +
    risk(1, "Market position", "The draft asserts market leadership.",
         "The draft says `The borrower is the market leader in its region.` No state field or saved source mentions market "
         "position; that is not present in the material reviewed, which does not mean it is untrue.",
         "A reader may take the assertion as established.", NONE,
         "The claim's basis is not recorded.", "What shows the borrower's position?") + "\n" + CAVEAT,
    1, must_say=(DRAFT, "not present in the material reviewed"))

scenario(
    "several_draft_like_files_and_no_conventional_one",
    dict(state=BASE_STATE),
    header("not present", NO_DRAFT + " (two other draft-like files exist and were not read)", "financial.") +
    f"{SPEC.nothing_phrase}. Inspected: `state.json`. No draft was chosen from among the others. This is not a claim that "
    "the deal is sound or that the review is complete.\n\n" + CAVEAT,
    0, must_say=("were not read",))

scenario(
    "no_material_risk",
    dict(state=BASE_STATE, manifest=[("Registered office confirmed", "https://example.invalid/registry")]),
    header("1 entry", NO_DRAFT, "none supported by the material.") +
    f"{SPEC.nothing_phrase}. Inspected: `state.json`, `sources/manifest.json`; no draft was available. This is not a claim "
    "that the deal is sound or that the review is complete.\n\n" + CAVEAT,
    0, must_say=("not a claim that the deal is sound",))

NAMES = list(SCENARIOS)


def build(tmp_path, name):
    folder = make_deal(tmp_path, **SCENARIOS[name][0])
    if name == "several_draft_like_files_and_no_conventional_one":
        for suffix in ("v2", "v3"):
            (folder.parent / f"{PROPOSAL}_draft_{suffix}.md").write_text("The borrower is the market leader.", encoding="utf-8")
    return folder


@pytest.mark.parametrize("name", NAMES)
def test_a_report_for_each_scenario_meets_the_contract_and_holds_its_property(name, tmp_path):
    deal, report, items, must_say, must_not_say, extra = SCENARIOS[name]
    folder = build(tmp_path, name)
    assert report_problems(report, folder, SPEC, items=items) == []
    for phrase in must_say:
        assert phrase.lower() in report.lower(), phrase
    for phrase in must_not_say:
        assert phrase not in report, phrase
    for label, phrase in (extra or {}).items():
        field = re.search(rf"\*\*{re.escape(label)}:\*\*(.*?)(?=\n\d\. \*\*|\n\n|\Z)", report, re.S).group(1)
        assert phrase in field, (label, phrase)


BREAKAGES = [
    ("a field is missing", lambda r: re.sub(r"4\. \*\*Current mitigant:\*\*[^\n]*\n", "", r), "missing field Current mitigant"),
    ("a field is renamed", lambda r: r.replace("**Residual risk / uncertainty:**", "**Residual risk:**"),
     "missing field Residual risk / uncertainty"),
    ("an overall verdict is added", lambda r: r.replace("\n> Analyst aid", "\nOverall: REJECTED.\n\n> Analyst aid"),
     "a verdict word appears"),
    ("a lowercase pass verdict is added", lambda r: r.replace("\n> Analyst aid", "\nOverall, the result is pass.\n\n> Analyst aid"),
     "a verdict word appears"),
    ("a mixed-case fail verdict is added", lambda r: r.replace("\n> Analyst aid", "\nOverall, the result is FaIl.\n\n> Analyst aid"),
     "a verdict word appears"),
    ("a risk grade is added", lambda r: r.replace("\n> Analyst aid", "\nBorrower risk grade: B.\n\n> Analyst aid"),
     "a rating, score, recommendation or clearance appears"),
    ("a probability is invented", lambda r: r.replace("\n> Analyst aid", "\nThe chance of default is 3.5%.\n\n> Analyst aid"),
     "number not in the record: 3.5"),
    ("a quoted value is not in the record", lambda r: r.replace("\n> Analyst aid", "\nThe draft says `Fleet worth 9,000`.\n\n> Analyst aid"),
     "quoted value not in the record"),
    ("the categories line is removed", lambda r: re.sub(r"\*\*Categories reviewed:\*\*[^\n]*\n", "", r),
     "must carry a **Categories reviewed:** line"),
    ("the caveat is removed", lambda r: r.split("\n> Analyst aid")[0].rstrip() + "\n", "the closing caveat is missing"),
]


@pytest.mark.parametrize("label,breakage,expected", BREAKAGES, ids=[b[0] for b in BREAKAGES])
def test_each_breakage_of_a_clean_report_is_caught(label, breakage, expected, tmp_path):
    name = "contradictory_evidence"
    folder = build(tmp_path, name)
    report = SCENARIOS[name][1]
    assert report_problems(report, folder, SPEC, items=1) == []
    broken = breakage(report)
    assert broken != report
    assert any(expected in p for p in report_problems(broken, folder, SPEC, items=1)), (label, report_problems(broken, folder, SPEC))


def test_a_quotation_from_a_draft_like_file_that_was_not_chosen_is_caught(tmp_path):
    name = "several_draft_like_files_and_no_conventional_one"
    folder = build(tmp_path, name)
    report = SCENARIOS[name][1]
    assert report_problems(report, folder, SPEC, items=0) == []
    quoted = report.replace("\n> Analyst aid", "\nOne file says `The borrower is the market leader.`\n\n> Analyst aid")
    assert any("quoted value not in the record" in p for p in report_problems(quoted, folder, SPEC, items=0))


@pytest.mark.parametrize("report", [
    "**Refused:** the proposal name contains a slash, which is not accepted.",
    "**Stopped:** the lookup reached 100 files, so the latest dated folder cannot be established.",
    "**Stopped:** the draft path is outside `deals/Synthetic Co/`, so it was not read.",
])
def test_a_refusal_or_stop_says_why_and_holds_no_item(report, tmp_path):
    folder = build(tmp_path, "no_draft")
    assert report_problems(report, folder, SPEC, stopped=True) == []
    assert report_problems(report + "\n\n### Risk 1 — x", folder, SPEC, stopped=True)


SKILL = ".claude/skills/credit-risk/SKILL.md"


def test_the_skill_removes_the_tools_it_says_it_does_not_use():
    removed = set(frontmatter(read(SKILL))["disallowed-tools"].split())
    assert {"Write", "Edit", "NotebookEdit", "Bash", "WebFetch", "WebSearch"} <= removed


def test_the_skill_asks_for_the_report_shape_the_checker_reads():
    body = " ".join(body_of(read(SKILL)).split())
    assert "**Material reviewed:** line" in body and "**Categories reviewed:** line" in body
    assert 'the draft path or “no draft available”' in body
    for label in SPEC.labels:
        assert f"**{label}:**" in body
    assert SPEC.nothing_phrase in body and SPEC.caveat in body


def test_the_refusal_list_names_each_character_once_so_a_backslash_in_a_name_is_refused():
    """The skill's list is read as space-separated one-character tokens. A doubled backslash is a two-character token that
    no name contains by accident, so a name with one backslash would not be refused; this is the defect the review found."""
    line = next(ln for ln in read(SKILL).splitlines() if "contains any of" in ln)
    listed = re.search(r"contains any of `([^`]+)`", line).group(1).split()
    assert listed == list("\\/:*?\"<>|$[]{}")        # the same characters as the other analyst aids, in the same order
    assert all(len(token) == 1 for token in listed)
    for name in ("Acme\\Holdings", "Acme/Holdings", "Acme:Holdings", "Acme$Holdings"):
        assert any(token in name for token in listed), name
