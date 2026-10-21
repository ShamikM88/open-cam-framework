"""The export read-back verifier (issue #196): what it accepts, what it reports, and that it never writes to a deal."""
import base64
import json
import tempfile
from pathlib import Path

import docx
import openpyxl
import pytest

import export_readback
from deal_export import export_deal
from state_manager import StateError, state_path, write_state

COMPANY = "Synthetic Co"
PROPOSAL = "Synthetic Fleet Loan"
DATE = "2026-01-05"
FOLDER = Path("deals") / COMPANY / f"{PROPOSAL}_{DATE}"
WORKBOOK = FOLDER / f"{COMPANY}_{PROPOSAL}_Spreading.xlsx"
DOCUMENT = FOLDER / f"{COMPANY}_{PROPOSAL}_CAM.docx"
DRAFT = FOLDER / "draft_approved.md"

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
OTHER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

DRAFT_MARKDOWN = """# Synthetic Co credit memo

The facility is a [synthetic link](https://example.invalid/page) with **bold** wording.

- First bullet
- Second bullet

| Metric | Value |
| :--- | ---: |
| Revenue | 5,000 |
| Debt | 2,400 |

![Chart alt text](chart.png)
"""

RAW = {
    "revenue": 5000, "cost_of_sales": 3000, "admin_expenses": 1200, "interest_paid": 50,
    "current_debt": 0, "overdraft": 0, "long_term_debt": 2400, "loan_notes": 0, "scheduled_principal": 200,
}


def build_deal(**state_fields):
    """Write a state and export it, in the current working directory, as `/assemble` would leave it."""
    fields = {"financials_source": "framework-computed", "financials": {"FY-Current": {"raw": RAW}}}
    fields.update(state_fields)
    Path("chart.png").write_bytes(PNG_1X1)
    write_state(COMPANY, PROPOSAL, date_str=DATE, **fields)
    export_deal(COMPANY, PROPOSAL, "corporate_credit", DRAFT_MARKDOWN, date_str=DATE)
    DRAFT.write_text(DRAFT_MARKDOWN, encoding="utf-8")
    write_state(COMPANY, PROPOSAL, date_str=DATE, approved_draft_path=DRAFT.as_posix())


@pytest.fixture
def deal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_deal()
    return tmp_path


def reexport():
    """Write the unedited artifacts again."""
    export_deal(COMPANY, PROPOSAL, "corporate_credit", DRAFT_MARKDOWN, date_str=DATE)


def verify(**kwargs):
    return export_readback.verify_deal(COMPANY, PROPOSAL, **kwargs)


def edit_workbook(change):
    workbook = openpyxl.load_workbook(WORKBOOK)
    change(workbook)
    workbook.save(WORKBOOK)


def edit_document(change):
    document = docx.Document(DOCUMENT)
    change(document)
    document.save(DOCUMENT)


def cell_holding(workbook, value, sheet="Financial Spreading"):
    for row in workbook[sheet].iter_rows():
        for cell in row:
            if cell.value == value:
                return cell
    raise AssertionError(f"no cell holds {value!r}")


def folder_bytes():
    return {p.as_posix(): p.read_bytes() for p in sorted(Path("deals").rglob("*")) if p.is_file()}


def leftover_scratch_dirs():
    return sorted(p.name for p in Path(tempfile.gettempdir()).glob("export_readback_*"))


# ---------------------------------------------------------------------------
# A faithful export matches
# ---------------------------------------------------------------------------

def test_an_untouched_export_matches_in_both_artifacts(deal):
    report = verify()

    assert report["status"] == "match"
    assert report["mismatch_count"] == 0 and report["findings"] == [] and report["truncated"] is False
    assert report["artifacts"]["workbook"]["status"] == "match"
    assert report["artifacts"]["document"]["status"] == "match"
    assert Path(report["selected_folder"]) == FOLDER
    assert Path(report["artifacts"]["approved_draft"]) == DRAFT
    assert report["limits"] and all(isinstance(line, str) for line in report["limits"])


def test_an_analyst_supplied_export_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_deal(financials_source="analyst-supplied", financials={"FY-Current": {"revenue": 500, "ebitda": 90}},
               analyst_supplied_financials={"FY-Current": {"revenue": 500}}, ratios={"FY-Current": {"dscr": 1.4}})

    assert verify()["status"] == "match"


def test_a_workbook_with_a_group_basis_sheet_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_deal(
        financials_source="analyst-supplied", analyst_supplied_financials={"FY-Current": {"revenue": 500}},
        financials={"FY-Current": {"revenue": 500}},
        group_basis={
            "basis": "supplied_consolidated", "reporting_entity": "Synthetic Group Ltd",
            "entities_covered": ["Synthetic Group Ltd", "Synthetic Group OpCo"],
            "source": {"document": "Borrower-supplied consolidated accounts", "accounting_basis": "UK GAAP",
                       "date": "2026-08-31"},
            "period_end": "2026-03-31", "periods": ["FY-Current"], "figures_basis": "supplied",
            "perimeter_confirmed": True, "perimeter_confirmed_on": "2026-10-21"})

    assert verify()["status"] == "match"
    assert openpyxl.load_workbook(WORKBOOK).sheetnames[-1] == "Group basis"


def test_opening_and_saving_the_files_unchanged_is_not_reported_as_an_edit(deal):
    edit_workbook(lambda workbook: None)
    edit_document(lambda document: None)

    assert verify()["status"] == "match"


def test_a_state_changed_after_export_makes_the_workbook_stale(deal):
    write_state(COMPANY, PROPOSAL, date_str=DATE, financials={"FY-Current": {"raw": {**RAW, "revenue": 6000}}})

    report = verify()

    assert report["status"] == "mismatch"
    assert report["artifacts"]["workbook"]["status"] == "mismatch"
    assert report["artifacts"]["document"]["status"] == "match"
    assert {f["kind"] for f in report["findings"]} == {"value"}


# ---------------------------------------------------------------------------
# Workbook differences
# ---------------------------------------------------------------------------

def test_an_edited_input_cell_is_reported_with_its_location_and_both_values(deal):
    edit_workbook(lambda workbook: setattr(cell_holding(workbook, 5000), "value", 5001))

    report = verify()

    assert report["status"] == "mismatch" and report["artifacts"]["document"]["status"] == "match"
    assert [(f["artifact"], f["kind"], f["expected"], f["actual"]) for f in report["findings"]] == [
        ("workbook", "value", 5000, 5001)]
    assert report["findings"][0]["location"].startswith("Financial Spreading!")


def test_an_overwritten_formula_is_reported_as_the_formula_text(deal):
    holder = {}

    def overwrite(workbook):
        formula_cell = next(c for row in workbook["Financial Spreading"].iter_rows() for c in row
                            if isinstance(c.value, str) and c.value.startswith("="))
        holder["formula"], holder["coordinate"] = formula_cell.value, formula_cell.coordinate
        formula_cell.value = 123

    edit_workbook(overwrite)

    finding, = verify()["findings"]

    assert finding["location"] == f"Financial Spreading!{holder['coordinate']}"
    assert (finding["kind"], finding["expected"], finding["actual"]) == ("value", holder["formula"], 123)


def test_a_cleared_cell_is_a_difference(deal):
    edit_workbook(lambda workbook: setattr(cell_holding(workbook, 5000), "value", None))

    finding, = verify()["findings"]

    assert (finding["expected"], finding["actual"]) == (5000, None)


def test_a_changed_number_format_bold_or_italic_is_reported_by_kind(deal):
    def restyle(workbook):
        cell = cell_holding(workbook, 5000)
        cell.number_format = "0.000"
        cell.font = openpyxl.styles.Font(bold=not cell.font.bold, italic=not cell.font.italic)

    edit_workbook(restyle)

    assert sorted(f["kind"] for f in verify()["findings"]) == ["bold", "italic", "number_format"]


def test_a_removed_or_added_sheet_is_reported(deal):
    edit_workbook(lambda workbook: workbook.remove(workbook["Collateral & Exposure"]))
    finding, = verify()["findings"]
    assert (finding["location"], finding["kind"]) == ("sheet names", "sheets")
    assert finding["expected"] == ["Financial Spreading", "Collateral & Exposure"]
    assert finding["actual"] == ["Financial Spreading"]

    edit_workbook(lambda workbook: workbook.create_sheet("Notes"))
    assert verify()["findings"][0]["actual"] == ["Financial Spreading", "Notes"]


def test_column_width_merged_cells_and_freeze_panes_are_part_of_the_comparison(deal):
    def relayout(workbook):
        sheet = workbook["Financial Spreading"]
        sheet.column_dimensions["B"].width = 77
        sheet.merge_cells("AA60:AB60")
        sheet.freeze_panes = "C3"

    edit_workbook(relayout)

    assert sorted(f["kind"] for f in verify()["findings"]) == ["column_width", "freeze_panes", "merged_cells"]


def test_numbers_are_compared_with_a_tight_tolerance(deal):
    coordinate = cell_holding(openpyxl.load_workbook(WORKBOOK), 5000).coordinate

    def set_value(value):
        edit_workbook(lambda workbook: setattr(workbook["Financial Spreading"][coordinate], "value", value))

    set_value(5000 + 1e-12)
    assert verify()["status"] == "match"

    set_value(5000.001)
    assert verify()["status"] == "mismatch"


@pytest.mark.parametrize("expected, actual, equal", [
    (1, 1.0, True), (0.1 + 0.2, 0.3, True), (5, 5.001, False), (True, 1, False), (1, True, False),
    ("a", "a", True), ("a", "A", False), ("1", 1, False), (None, None, True), (None, "", False), (None, 0, False),
])
def test_value_equality_rules(expected, actual, equal):
    assert export_readback._values_equal(expected, actual) is equal


# ---------------------------------------------------------------------------
# Document differences
# ---------------------------------------------------------------------------

def paragraph_with(document, text):
    return next(p for p in document.paragraphs if text in p.text)


def test_an_edited_paragraph_is_reported_by_block_with_both_versions(deal):
    def reword(document):
        run = paragraph_with(document, "First bullet").runs[0]
        run.text = "Changed bullet"

    edit_document(reword)

    report = verify()
    finding, = report["findings"]

    assert report["artifacts"]["workbook"]["status"] == "match"
    assert (finding["artifact"], finding["kind"]) == ("document", "paragraph")
    assert finding["location"].startswith("block ")
    assert "First bullet" in json.dumps(finding["expected"]) and "Changed bullet" in json.dumps(finding["actual"])


def test_an_edited_table_cell_is_reported(deal):
    edit_document(lambda document: setattr(document.tables[0].cell(1, 1), "text", "9,999"))

    finding, = verify()["findings"]

    assert finding["kind"] == "table"
    assert finding["expected"]["rows"][1] == ["Revenue", "5,000"]
    assert finding["actual"]["rows"][1] == ["Revenue", "9,999"]


def test_an_added_or_removed_block_is_reported_as_a_count_change(deal):
    expected_blocks = len(export_readback._document_blocks(docx.Document(DOCUMENT)))
    edit_document(lambda document: document.add_paragraph("An unauthorised extra paragraph"))

    assert verify()["findings"] == [{
        "artifact": "document", "location": "block count", "kind": "blocks",
        "expected": expected_blocks, "actual": expected_blocks + 1}]

    reexport()
    edit_document(lambda document: paragraph_with(document, "First bullet")._p.getparent().remove(
        paragraph_with(document, "First bullet")._p))

    assert verify()["findings"][0]["actual"] == expected_blocks - 1


def test_a_changed_style_or_run_formatting_is_reported(deal):
    def restyle(document):
        paragraph = paragraph_with(document, "Second bullet")
        paragraph.style = document.styles["Normal"]
        paragraph.runs[0].bold = True

    edit_document(restyle)

    finding, = verify()["findings"]

    assert finding["expected"]["style"] != finding["actual"]["style"]


def test_a_retargeted_hyperlink_is_reported(deal):
    def retarget(document):
        relationships = document.part.rels
        link = next(rel for rel in relationships.values() if rel.is_external and "example.invalid" in rel.target_ref)
        link._target = "https://example.invalid/other"

    edit_document(retarget)

    finding, = verify()["findings"]

    assert "https://example.invalid/page" in json.dumps(finding["expected"])
    assert "https://example.invalid/other" in json.dumps(finding["actual"])


def test_a_swapped_embedded_image_or_changed_alt_text_is_reported(deal):
    def swap(document):
        image_part = next(rel.target_part for rel in document.part.rels.values() if "image" in rel.reltype)
        image_part._blob = OTHER_PNG

    edit_document(swap)
    finding, = verify()["findings"]
    assert finding["expected"]["image_hashes"] != finding["actual"]["image_hashes"]

    reexport()

    def rename_alt(document):
        for element in document.element.body.iter():
            if element.tag.endswith("}docPr"):
                element.set("descr", "A different description")

    edit_document(rename_alt)
    finding, = verify()["findings"]
    assert finding["expected"]["image_alt_text"] == ["Chart alt text"]
    assert finding["actual"]["image_alt_text"] == ["A different description"]


def test_a_draft_edited_after_export_makes_the_document_stale(deal):
    DRAFT.write_text(DRAFT_MARKDOWN.replace("Second bullet", "Third bullet"), encoding="utf-8")

    report = verify()

    assert report["artifacts"]["document"]["status"] == "mismatch"
    assert report["artifacts"]["workbook"]["status"] == "match"


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def test_findings_are_capped_but_the_count_is_exact(deal):
    def scribble(workbook):
        sheet = workbook["Financial Spreading"]
        for row in range(1, 41):
            for column in range(20, 27):
                sheet.cell(row=row, column=column).value = f"edit {row}-{column}"

    edit_workbook(scribble)

    report = verify()

    assert report["mismatch_count"] == 280 > export_readback.MAX_FINDINGS
    assert len(report["findings"]) == export_readback.MAX_FINDINGS and report["truncated"] is True
    assert report["artifacts"]["workbook"]["mismatch_count"] == 280


# ---------------------------------------------------------------------------
# Unusable input: one clear error, nothing compared
# ---------------------------------------------------------------------------

def test_a_deal_with_no_state_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(StateError, match="no existing state.json"):
        verify()


def test_an_explicit_date_with_no_state_is_refused_and_a_bad_date_is_refused(deal):
    with pytest.raises(StateError, match="no state.json in the dated folder"):
        verify(date_str="2025-12-31")
    with pytest.raises(ValueError, match="--date-str"):
        verify(date_str="05/01/2026")


def test_the_named_date_selects_that_folder_rather_than_the_newest(deal):
    write_state(COMPANY, PROPOSAL, date_str="2026-06-01", financials_source="framework-computed")

    assert Path(verify(date_str=DATE)["selected_folder"]) == FOLDER
    with pytest.raises(StateError, match="does not exist"):
        verify()   # the newest dated folder has a state but no exports


@pytest.mark.parametrize("missing, label", [(WORKBOOK, "workbook"), (DOCUMENT, "document")])
def test_a_missing_artifact_is_refused_by_name(deal, missing, label):
    missing.unlink()

    with pytest.raises(StateError, match=f"the {label} .* does not exist"):
        verify()


def test_a_corrupt_workbook_or_document_is_one_error_not_a_traceback(deal):
    WORKBOOK.write_bytes(b"not a workbook")
    with pytest.raises(ValueError, match="cannot read workbook"):
        verify()

    reexport()
    DOCUMENT.write_bytes(b"not a document")
    with pytest.raises(ValueError, match="cannot read document"):
        verify()


@pytest.mark.parametrize("recorded, message", [
    (None, "records no approved_draft_path"),
    ("   ", "records no approved_draft_path"),
    (5, "records no approved_draft_path"),
    ("/etc/passwd", "relative path inside the deal's dated folder"),
    ("C:\\evil\\draft.md", "relative path inside the deal's dated folder"),
    ("deals/../../outside.md", "relative path inside the deal's dated folder"),
    ("deals\\..\\outside.md", "relative path inside the deal's dated folder"),
    ("elsewhere/draft.md", "is not inside the deal's dated folder"),
    (str(FOLDER / "missing.md"), "does not exist"),
])
def test_an_unusable_approved_draft_path_is_refused(deal, recorded, message):
    Path("elsewhere").mkdir()
    Path("elsewhere/draft.md").write_text("# other", encoding="utf-8")
    path = state_path(COMPANY, PROPOSAL, date_str=DATE)
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    state["approved_draft_path"] = recorded
    Path(path).write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(StateError, match=message):
        verify()


def test_an_approved_draft_symlinked_out_of_the_folder_is_refused(deal):
    outside = Path("outside.md")
    outside.write_text("# outside", encoding="utf-8")
    link = FOLDER / "linked.md"
    try:
        link.symlink_to(outside.resolve())
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    write_state(COMPANY, PROPOSAL, date_str=DATE, approved_draft_path=link.as_posix())

    with pytest.raises(StateError, match="is not inside the deal's dated folder"):
        verify()


def test_a_malformed_group_basis_or_source_in_the_state_is_refused(deal):
    path = state_path(COMPANY, PROPOSAL, date_str=DATE)
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    state["group_basis"] = {"basis": "nonsense"}
    Path(path).write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(StateError, match="group_basis"):
        verify()

    del state["group_basis"]
    state["financials_source"] = 5
    Path(path).write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(StateError, match="financials_source"):
        verify()


# ---------------------------------------------------------------------------
# Read-only for the deal, and no scratch left behind
# ---------------------------------------------------------------------------

def test_verifying_changes_nothing_in_the_deal_and_leaves_no_scratch_files(deal):
    before_scratch = leftover_scratch_dirs()
    before = folder_bytes()
    verify()
    edit_workbook(lambda workbook: setattr(cell_holding(workbook, 5000), "value", 1))
    changed = folder_bytes()
    assert verify()["status"] == "mismatch"

    assert folder_bytes() == changed and changed != before
    assert leftover_scratch_dirs() == before_scratch


def test_scratch_files_are_removed_even_when_a_comparison_fails(deal):
    before_scratch = leftover_scratch_dirs()
    WORKBOOK.write_bytes(b"broken")

    with pytest.raises(ValueError):
        verify()

    assert leftover_scratch_dirs() == before_scratch


# ---------------------------------------------------------------------------
# The command line, in process
# ---------------------------------------------------------------------------

def test_main_prints_the_report_and_exits_zero_on_a_match(deal, capsys):
    assert export_readback.main(["--company", COMPANY, "--proposal", PROPOSAL]) == 0

    out = capsys.readouterr()
    assert json.loads(out.out)["status"] == "match" and out.err == ""


def test_main_exits_one_with_the_findings_on_a_mismatch(deal, capsys):
    edit_workbook(lambda workbook: setattr(cell_holding(workbook, 5000), "value", 4999))

    assert export_readback.main(["--company", COMPANY, "--proposal", PROPOSAL, "--date-str", DATE]) == 1

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "mismatch" and report["mismatch_count"] == 1


def test_main_turns_an_unusable_deal_into_one_error_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as caught:
        export_readback.main(["--company", COMPANY, "--proposal", PROPOSAL])

    assert caught.value.code == (
        f"error: no existing state.json for company '{COMPANY}' and proposal '{PROPOSAL}'")


def test_main_refuses_missing_required_flags_with_a_usage_error(capsys):
    with pytest.raises(SystemExit) as caught:
        export_readback.main(["--company", COMPANY])

    assert caught.value.code == 2 and "--proposal" in capsys.readouterr().err
