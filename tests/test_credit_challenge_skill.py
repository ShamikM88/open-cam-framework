"""The `credit-challenge` skill against synthetic deals (issue #196, Phase 4).

`test_skills.py` pins the skill's frontmatter, tool list and key sentences. This file adds what the design asks for
beyond that: one synthetic deal per scenario in `docs/skill-design.md` ("Implementation acceptance gates"), a hand-written
report for each, and `skill_report_contract.report_problems` applied to it. Every report is clean, and a deliberately
broken variant of it is caught, so the checker is shown to catch what it claims to.

No test here can show that a model writes such a report: nothing calls a model. A passing test shows that the contract is
checkable on realistic records and that the skill's text asks for it.
"""
import json
import re
from pathlib import Path

import pytest
from skill_report_contract import ReportSpec, report_problems
from source_manifest import save_source
from state_manager import read_state, state_path, write_state
from test_skill_inventory import body_of, frontmatter, read

COMPANY, PROPOSAL, DATE = "Synthetic Co", "Synthetic Fleet Loan", "2026-02-01"
FOLDER = f"deals/{COMPANY}/{PROPOSAL}_{DATE}"
DRAFT = f"deals/{COMPANY}/{PROPOSAL}_draft.md"
SPEC = ReportSpec(
    item_word="Challenge",
    labels=("Challenge / question", "Recorded evidence", "Potential impact", "Current mitigant", "Residual uncertainty",
            "Information gap / next question"),
    nothing_phrase="No challenge identified in the material reviewed",
    caveat="Analyst aid only. Read-only: no deal record or draft was changed.",
)
CAVEAT = f"> {SPEC.caveat} This is an adversarial question set over the material identified above, not a formal review."


def item(n, title, question, evidence, impact, mitigant, uncertainty, gap):
    return (f"### Challenge {n} — {title}\n\n1. **Challenge / question:** {question}\n2. **Recorded evidence:** {evidence}\n"
            f"3. **Potential impact:** {impact}\n4. **Current mitigant:** {mitigant}\n5. **Residual uncertainty:** {uncertainty}\n"
            f"6. **Information gap / next question:** {gap}\n")


def material(manifest, draft):
    return f"**Material reviewed:** `{FOLDER}/state.json`; manifest: {manifest}; draft: {draft}.\n"


NO_DRAFT = "no draft available"
WITH_DRAFT = f"`{DRAFT}`"


def make_deal(tmp_path, *, state, draft=None, manifest=None, manifest_file=None):
    """A deal built the way the pipeline builds one, so the dated folder and file names are the real ones."""
    write_state(COMPANY, PROPOSAL, date_str=DATE, base_dir=tmp_path, **state)
    folder = Path(state_path(COMPANY, PROPOSAL, date_str=DATE, base_dir=tmp_path)).parent
    if draft is not None:
        (folder.parent / f"{PROPOSAL}_draft.md").write_text(draft, encoding="utf-8")
    if manifest_file is not None:        # a manifest entry written without its file
        (folder / "sources").mkdir()
        (folder / "sources" / "manifest.json").write_text(json.dumps(manifest_file, indent=2), encoding="utf-8")
    for claim, url in manifest or []:
        source = tmp_path / "registry_copy.txt"
        source.write_text("Synthetic registry extract.", encoding="utf-8")
        save_source(COMPANY, PROPOSAL, step="triage", claim=claim, source_path=str(source), url=url,
                    fetched_date=DATE, date_str=DATE, base_dir=tmp_path)
    return folder


BASE_STATE = {"steps_completed": ["triage"], "triage": {"verdict": "Go", "sources": ["https://example.invalid/registry"]}}

# scenario -> (deal arguments, report, items expected, phrases the report must hold)
SCENARIOS = {}


def scenario(name, deal, report, items, must_say=(), field_says=None):
    SCENARIOS[name] = (deal, report, items, must_say, field_says or {})


scenario(
    "sourced_and_analyst_supplied",
    dict(state={**BASE_STATE, "steps_completed": ["triage", "spread"], "financials_source": "analyst-supplied",
                "financials": {"FY-Current": {"ebitda": 250}}},
         draft="EBITDA of 250 supports comfortable debt service.",
         manifest=[("Registered office confirmed", "https://example.invalid/registry")]),
    material("1 entry", WITH_DRAFT) + "\n" + item(
        1, "Basis of the EBITDA figure",
        "Is the draft's statement about debt service supported by a source, or only by the analyst's own figure?",
        "`ebitda` for `FY-Current` is `250` and `financials_source` is `analyst-supplied`. The draft says "
        "`EBITDA of 250 supports comfortable debt service.` The manifest's one entry is for `Registered office confirmed`, "
        "a different claim.",
        "If the figure rests only on the analyst's own template, the draft's wording may be stronger than the evidence.",
        "None recorded in the material reviewed.",
        "Whether a saved document backs the figure is not recorded.",
        "Which saved document, if any, does the analyst-supplied EBITDA come from?") + "\n" + CAVEAT,
    1, must_say=("analyst-supplied", "Registered office confirmed"))

scenario(
    "unsupported_narrative_claim",
    dict(state=BASE_STATE, draft="The borrower is the market leader in its region."),
    material("not present", WITH_DRAFT) + "\n" + item(
        1, "Market position",
        "What supports the statement that the borrower leads its market?",
        "The draft says `The borrower is the market leader in its region.` No state field and no saved source mentions "
        "market position.",
        "If the statement is the draft's own assertion, a reader may take it as established.",
        "None recorded in the material reviewed.",
        "The claim is not present in the material reviewed; that does not mean it is untrue.",
        "Which source, if any, shows the borrower's position in its market?") + "\n" + CAVEAT,
    1, must_say=("not present in the material reviewed",))

scenario(
    "contradictory_values",
    dict(state={**BASE_STATE, "steps_completed": ["triage", "commercial"],
                "commercial": {"largest_customer_note": "Largest customer is 20% of revenue"}},
         draft="The largest customer accounts for 35% of revenue."),
    material("not present", WITH_DRAFT) + "\n" + item(
        1, "Customer concentration",
        "Which of the two statements about the largest customer is right?",
        "`commercial` records `Largest customer is 20% of revenue`; the draft says `The largest customer accounts for 35% "
        "of revenue.` Both are quoted as they stand.",
        "If the larger figure is correct, the concentration is greater than the commercial step recorded.",
        "None recorded in the material reviewed.",
        "The record does not say which figure is current.",
        "Which document gives the largest customer's share of revenue?") + "\n" + CAVEAT,
    1, must_say=("Largest customer is 20% of revenue", "35%"))

scenario(
    "genuine_mitigant",
    dict(state={**BASE_STATE, "steps_completed": ["triage", "commercial"],
                "commercial": {"supplier_note": "A second approved supplier is recorded for the main input"}},
         draft="Supply of the main input is concentrated in one supplier."),
    material("not present", WITH_DRAFT) + "\n" + item(
        1, "Supplier concentration",
        "How exposed is the borrower to the loss of its main supplier?",
        "The draft says `Supply of the main input is concentrated in one supplier.`",
        "If that supplier failed, supply of the main input could be interrupted.",
        "`A second approved supplier is recorded for the main input` (`commercial`).",
        "The record does not say whether the second supplier could cover the volume.",
        "What share of the main input could the second approved supplier provide?") + "\n" + CAVEAT,
    1, field_says={"Current mitigant": "A second approved supplier is recorded for the main input"})

scenario(
    "missing_provenance",
    dict(state=BASE_STATE,
         manifest_file=[{"filename": "registry_extract.html", "url": "https://example.invalid/registry", "step": "triage",
                         "claim": "Registered office confirmed", "fetched_date": DATE}]),
    material("1 entry", NO_DRAFT) + "\n" + item(
        1, "Registered office evidence",
        "Is there a saved document behind the registered-office claim?",
        "The manifest lists `registry_extract.html` for `Registered office confirmed`, but no such file is in `sources/`. "
        "A manifest entry records a link between a claim and a source; it is not proof that the source supports it.",
        "If the document was never saved, the claim cannot be audited later.",
        "None recorded in the material reviewed.",
        "Whether the claim is true is not established by the record.",
        "Can the registry extract be saved again and added to the manifest?") + "\n" + CAVEAT,
    1, must_say=("no such file", "not proof"))

scenario(
    "no_draft",
    dict(state={**BASE_STATE, "triage": {"verdict": "Go", "sources": []}}),
    material("not present", NO_DRAFT) + "\n" + item(
        1, "Basis of the Go screen",
        "What supports the Go verdict when no source is listed?",
        "`verdict` is `Go` and `sources` is `[]`. No draft was available, so only the record was reviewed.",
        "A screen with no listed source cannot be re-checked.",
        "None recorded in the material reviewed.",
        "The record may be incomplete rather than the screen unsupported.",
        "Which sources informed the Go verdict?") + "\n" + CAVEAT,
    1, must_say=("no draft available", "only the record was reviewed"))

scenario(
    "nothing_to_challenge",
    dict(state=BASE_STATE, manifest=[("Registered office confirmed", "https://example.invalid/registry")]),
    material("1 entry", NO_DRAFT) + "\n" + f"{SPEC.nothing_phrase}. Inspected: `state.json`, `sources/manifest.json`; no draft "
    "was available. This is not a claim that the deal is sound or that the review is complete.\n\n" + CAVEAT,
    0, must_say=("not a claim that the deal is sound",))

NAMES = list(SCENARIOS)


def folder_for(tmp_path, name):
    return make_deal(tmp_path, **SCENARIOS[name][0])


def test_each_synthetic_deal_has_the_real_dated_folder_and_state(tmp_path):
    for name in NAMES:
        root = tmp_path / name
        root.mkdir()
        folder = folder_for(root, name)
        assert folder.name == f"{PROPOSAL}_{DATE}" and folder.parent.name == COMPANY
        state = read_state(COMPANY, PROPOSAL, date_str=DATE, base_dir=root)
        assert state["date"] == DATE and state["company"] == COMPANY


@pytest.mark.parametrize("name", NAMES)
def test_a_report_for_each_scenario_meets_the_contract_and_holds_its_property(name, tmp_path):
    deal, report, items, must_say, field_says = SCENARIOS[name]
    folder = folder_for(tmp_path, name)
    assert report_problems(report, folder, SPEC, items=items) == []
    for phrase in must_say:
        assert phrase.lower() in report.lower(), phrase
    for label, phrase in field_says.items():
        field = re.search(rf"\*\*{re.escape(label)}:\*\*(.*?)(?=\n\d\. \*\*|\n\n|\Z)", report, re.S).group(1)
        assert phrase in field, (label, phrase)


# Each breakage is a change to a clean report; the checker must name it.
BREAKAGES = [
    ("a field is missing", lambda r: re.sub(r"4\. \*\*Current mitigant:\*\*[^\n]*\n", "", r), "missing field Current mitigant"),
    ("fields are out of order", lambda r: r.replace("**Current mitigant:**", "**TMP:**").replace(
        "**Residual uncertainty:**", "**Current mitigant:**").replace("**TMP:**", "**Residual uncertainty:**"),
     "fields out of order"),
    ("an overall verdict is added", lambda r: r.replace("\n> Analyst aid", "\nOverall: APPROVED.\n\n> Analyst aid"),
     "a verdict word appears"),
    ("a rating is added", lambda r: r.replace("\n> Analyst aid", "\nOverall risk rating: low.\n\n> Analyst aid"),
     "a rating, score, recommendation or clearance appears"),
    ("a lend recommendation is added", lambda r: r.replace("\n> Analyst aid", "\nWe recommend lending.\n\n> Analyst aid"),
     "a rating, score, recommendation or clearance appears"),
    ("a derived figure is added", lambda r: r.replace("\n> Analyst aid", "\nThat is 4.7x cover.\n\n> Analyst aid"),
     "number not in the record: 4.7"),
    ("a value that is not in the record is quoted", lambda r: r.replace("\n> Analyst aid", "\nThe draft says `EBITDA of 999`.\n\n> Analyst aid"),
     "quoted value not in the record"),
    ("the caveat is removed", lambda r: r.split("\n> Analyst aid")[0].rstrip() + "\n", "the closing caveat is missing"),
    ("the material reviewed line is removed", lambda r: r.split("\n", 1)[1].lstrip(), "must open with a Material reviewed line"),
]


@pytest.mark.parametrize("label,breakage,expected", BREAKAGES, ids=[b[0] for b in BREAKAGES])
def test_each_breakage_of_a_clean_report_is_caught(label, breakage, expected, tmp_path):
    name = "unsupported_narrative_claim"
    folder = folder_for(tmp_path, name)
    report = SCENARIOS[name][1]
    assert report_problems(report, folder, SPEC, items=1) == []
    broken = breakage(report)
    assert broken != report
    assert any(expected in p for p in report_problems(broken, folder, SPEC, items=1)), (label, report_problems(broken, folder, SPEC))


def test_a_report_that_finds_nothing_but_lists_an_item_is_caught_and_so_is_the_reverse(tmp_path):
    folder = folder_for(tmp_path, "nothing_to_challenge")
    clean = SCENARIOS["nothing_to_challenge"][1]
    both = clean.replace("\n> Analyst aid", "\n" + item(1, "x", "q", "e", "i", "m", "u", "g") + "\n> Analyst aid")
    assert any("both identifies items" in p for p in report_problems(both, folder, SPEC))
    neither = clean.replace(SPEC.nothing_phrase, "Nothing")
    assert any("does not say none was identified" in p for p in report_problems(neither, folder, SPEC))


@pytest.mark.parametrize("sentence", [
    "The deal is approved.",
    "The facility is rejected.",
    "Overall decision: pass.",
    "Overall decision: PaSsEd.",
    "Overall decision: fail.",
    "Overall decision: fAiLeD.",
])
def test_lowercase_and_mixed_case_verdicts_are_rejected(sentence, tmp_path):
    name = "unsupported_narrative_claim"
    folder = folder_for(tmp_path, name)
    report = SCENARIOS[name][1]
    broken = report.replace("\\n> Analyst aid", f"\\n{sentence}\\n\\n> Analyst aid")
    assert any("verdict word" in problem for problem in report_problems(broken, folder, SPEC, items=1)), sentence


def test_a_recorded_verdict_word_may_be_quoted_but_not_asserted(tmp_path):
    folder = make_deal(tmp_path, state={**BASE_STATE, "policy_note": "PASS"})
    quoted = material("not present", NO_DRAFT) + "\n" + item(
        1, "t", "q", "`policy_note` is recorded as `PASS`.", "i", "None recorded in the material reviewed.", "u", "g") + "\n" + CAVEAT
    assert report_problems(quoted, folder, SPEC, items=1) == []
    asserted = quoted.replace("recorded as `PASS`", "recorded as PASS")
    assert any("verdict word" in p for p in report_problems(asserted, folder, SPEC, items=1))


@pytest.mark.parametrize("report", [
    "**Stopped:** the lookup reached 100 files, so the latest dated folder cannot be established.",
    "**Refused:** the company name starts with `-`, which is not accepted.",
    "**Stopped:** the draft path is outside `deals/Synthetic Co/`, so it was not read.",
])
def test_a_refusal_or_stop_says_why_and_holds_no_item(report, tmp_path):
    folder = folder_for(tmp_path, "no_draft")
    assert report_problems(report, folder, SPEC, stopped=True) == []
    assert report_problems("Here you go.", folder, SPEC, stopped=True)
    assert report_problems(report + "\n\n" + item(1, "t", "q", "e", "i", "m", "u", "g"), folder, SPEC, stopped=True)


# ---------------------------------------------------------------------------
# What the skill's text asks for (a pin of the instruction, not proof of obedience)
# ---------------------------------------------------------------------------

SKILL = ".claude/skills/credit-challenge/SKILL.md"


def test_the_skill_removes_the_tools_it_says_it_does_not_use():
    removed = set(frontmatter(read(SKILL))["disallowed-tools"].split())
    assert {"Write", "Edit", "NotebookEdit", "Bash", "WebFetch", "WebSearch"} <= removed


def test_the_skill_asks_for_the_report_shape_the_checker_reads():
    body = " ".join(body_of(read(SKILL)).split())
    assert "Begin the report with a **Material reviewed:** line" in body
    assert 'the draft path or “no draft available”' in body
    for label in SPEC.labels:
        assert f"**{label}:**" in body
    assert SPEC.nothing_phrase in body and SPEC.caveat in body
