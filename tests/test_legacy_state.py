"""Older-shaped state.json files through the code that reads them (issue #152).

`state.json` changed shape several times as features landed, and a deal persists across sessions, so a deal started
before a change must still work after it. The version mechanism is tested in test_state_manager.py (a file without
`schema_version` reads back as "0.0.0" and is upgraded on write). What was not tested is older state flowing through
the downstream consumers. Five synthetic fixtures in tests/fixtures/state/ (an invented company, invented figures)
reproduce the historical shapes:

  01  before schema versioning (#25): historical periods only, the then-smaller ratio set, no schema_version
  02  before spreading_check.py existed (#98/#99): forward year + downside case, no multi_period_financials,
      no financials_source, no working-capital-days ratios
  03  before the merge fix (#120/#122): multi_period_financials holds only PART of a period's raw figures
  04  before the separate analyst-supplied store (#121): analyst subtotals in `financials`, no raw breakdown,
      no analyst_supplied_financials
  05  a NEWER schema than this code knows, with fields it has never heard of

Each goes through the three consumers the issue names: spreading_check.compute (merge into an old-shaped state),
policy_check.compute, and deal_export (the raw-figures read for the workbook).

A state written by a NEWER framework (fixture 05) is read but never written: see test_a_state_written_by_a_newer
framework_is_refused_and_left_untouched (issue #170).

Wrongly-typed state is a separate matter: the issue asked that it "fail with a clear error instead of being silently
reinterpreted". That is issue #171 (see the end of this file): each consumer validates the keys it reads and raises a
StateShapeError naming the key, with the deliberate tolerances listed and tested.
"""
import copy
import json
import shutil
from pathlib import Path

import openpyxl
import pytest

import deal_export
import pii_scan
import policy_check
import spreading_check
import state_manager

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "state"
COMPANY, PROPOSAL, DATE = "Synthetic Legacy Co", "Synthetic Fleet Loan", "2026-01-01"
FIXTURES = ["01_pre_schema_version", "02_pre_spreading_check", "03_pre_merge_fix", "04_pre_analyst_store",
            "05_future_unknown_fields"]
EXPECTED_SCHEMA = {"01_pre_schema_version": "0.0.0", "02_pre_spreading_check": "1.1.0", "03_pre_merge_fix": "1.1.0",
                   "04_pre_analyst_store": "1.1.0", "05_future_unknown_fields": "9.9.9"}
HAS_COLLATERAL = {"01_pre_schema_version", "02_pre_spreading_check", "03_pre_merge_fix", "05_future_unknown_fields"}
DRAFT = "# Credit Assessment Memorandum\n\n- **Borrower:** [Company Name]\n\nSynthetic body text.\n"


def load_fixture(name):
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def install(tmp_path, monkeypatch, state):
    """Put `state` where the code looks for it, in a throwaway working directory."""
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "deals" / COMPANY / f"{PROPOSAL}_{DATE}"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return folder


def revenue_cells(workbook_path):
    """{column header: value} for the Revenue row of the 'Financial Spreading' sheet."""
    sheet = openpyxl.load_workbook(workbook_path)["Financial Spreading"]
    header = [cell.value for cell in sheet[1]]
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if row[0] == "Revenue":
            return dict(zip(header[1:], row[1:], strict=False))
    raise AssertionError("no Revenue row in the exported workbook")


# ---------------------------------------------------------------------------
# The fixtures themselves
# ---------------------------------------------------------------------------

def test_the_fixtures_are_synthetic_and_contain_nothing_that_looks_like_real_data():
    for name in FIXTURES:
        text = (FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8")
        state = json.loads(text)
        assert state["company"] == COMPANY and state["proposal"] == PROPOSAL
        findings = pii_scan.scan_for_likely_real_data(text)
        # the schema requires a `date` field, so an ISO date is expected; nothing else may be flagged
        assert all("ISO date" in f["reason"] for f in findings), (name, findings)


@pytest.mark.parametrize("name", FIXTURES)
def test_each_fixture_reads_back_with_the_schema_version_it_was_written_with(tmp_path, monkeypatch, name):
    install(tmp_path, monkeypatch, load_fixture(name))
    assert state_manager.read_state(COMPANY, PROPOSAL)["schema_version"] == EXPECTED_SCHEMA[name]


# ---------------------------------------------------------------------------
# spreading_check.compute: merge new figures into an old-shaped state
# ---------------------------------------------------------------------------

NEW_PERIOD = {"FY+2": {"revenue": 5100, "cost_of_sales": 3150}}
UNTOUCHED_KEYS = ("inputs", "collateral", "steps_completed", "review_trail", "review_verdict", "draft_path",
                  "stress_assumptions", "financials_source_note", "future_key", "x_analyst_note")


WRITABLE = [name for name in FIXTURES if name != "05_future_unknown_fields"]   # 05 is newer: it is refused (#170)


@pytest.mark.parametrize("name", WRITABLE)
def test_merging_a_new_period_into_an_old_state_keeps_everything_else(tmp_path, monkeypatch, name):
    original = load_fixture(name)
    install(tmp_path, monkeypatch, original)
    result = spreading_check.compute(COMPANY, PROPOSAL, copy.deepcopy(NEW_PERIOD), update_financials_source=False)
    written = json.loads(next(tmp_path.glob("deals/*/*/state.json")).read_text(encoding="utf-8"))

    assert "FY+2" in result["financials"] and result["financials"]["FY+2"]["gross_profit"] == 1950
    for period, data in original["financials"].items():                    # earlier periods are still there...
        assert written["financials"][period]["raw"] == data["raw"], period  # ...with their raw figures intact
    for key in UNTOUCHED_KEYS:                                              # nothing the merge did not touch is lost
        if key in original:
            assert written[key] == original[key], key
    assert written["schema_version"] == state_manager.SCHEMA_VERSION        # fixtures 01-04 are older: upgraded
    assert written["multi_period_financials"]["FY+2"] == NEW_PERIOD["FY+2"]


def test_a_partially_recorded_period_is_merged_field_by_field_not_replaced(tmp_path, monkeypatch):
    original = load_fixture("03_pre_merge_fix")
    install(tmp_path, monkeypatch, original)
    spreading_check.compute(COMPANY, PROPOSAL, {"FY-Current": {"admin_expenses": 777}}, update_financials_source=False)
    written = json.loads(next(tmp_path.glob("deals/*/*/state.json")).read_text(encoding="utf-8"))
    # the two fields recorded before are kept, the new one is added: nothing reset to zero
    assert written["multi_period_financials"]["FY-Current"] == {"revenue": 4650, "cost_of_sales": 2900,
                                                                "admin_expenses": 777}
    assert written["multi_period_financials"]["FY+1"] == original["multi_period_financials"]["FY+1"]


def test_a_state_written_by_a_newer_framework_is_refused_and_left_untouched(tmp_path, monkeypatch):
    """Issue #170: it used to be re-stamped with this code's older version (a silent downgrade)."""
    original = load_fixture("05_future_unknown_fields")
    folder = install(tmp_path, monkeypatch, original)
    before = (folder / "state.json").read_bytes()
    with pytest.raises(state_manager.SchemaVersionError, match="9.9.9"):
        spreading_check.compute(COMPANY, PROPOSAL, copy.deepcopy(NEW_PERIOD))
    assert (folder / "state.json").read_bytes() == before


def test_unknown_fields_survive_a_write_to_a_state_of_a_supported_version(tmp_path, monkeypatch):
    original = load_fixture("05_future_unknown_fields")
    original["schema_version"] = "1.1.0"          # the fixture's unknown fields, but a version this code may write
    install(tmp_path, monkeypatch, original)
    spreading_check.compute(COMPANY, PROPOSAL, copy.deepcopy(NEW_PERIOD))
    written = json.loads(next(tmp_path.glob("deals/*/*/state.json")).read_text(encoding="utf-8"))
    assert written["future_key"] == original["future_key"] and written["x_analyst_note"] == original["x_analyst_note"]


def test_the_analyst_supplied_flag_is_only_flipped_when_asked(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, load_fixture("04_pre_analyst_store"))
    kept = spreading_check.compute(COMPANY, PROPOSAL, copy.deepcopy(NEW_PERIOD), update_financials_source=False)
    assert kept["financials_source"] == "analyst-supplied"        # /project's call: never silently flipped
    flipped = spreading_check.compute(COMPANY, PROPOSAL, copy.deepcopy(NEW_PERIOD))
    assert flipped["financials_source"] == "framework-computed"   # /spread's default: it owns that decision


# ---------------------------------------------------------------------------
# policy_check.compute
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", FIXTURES)
def test_policy_check_computes_a_policy_state_from_every_historical_shape(tmp_path, monkeypatch, name):
    install(tmp_path, monkeypatch, load_fixture(name))
    result = policy_check.compute(COMPANY, PROPOSAL)
    state = result["policy_state"]
    assert {"KYC-AML"} <= {cp["cp_id"] for cp in state["required_conditions_precedent"]}
    assert result["cam_data_present"] is True                     # every fixture has financials
    assert bool(state["security_gaps"]) is (name in HAS_COLLATERAL)   # a collateral row with no recorded security


def test_a_draft_without_the_structured_block_is_reported_not_a_crash(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, load_fixture("02_pre_spreading_check"))
    draft = tmp_path / "draft.md"
    draft.write_text(DRAFT, encoding="utf-8")
    result = policy_check.compute(COMPANY, PROPOSAL, draft_path=str(draft))
    assert result["compliant"] is False and result["reasons"]


# ---------------------------------------------------------------------------
# deal_export: the raw-figures read for the workbook
# ---------------------------------------------------------------------------

def export(tmp_path):
    out = deal_export.export_deal(COMPANY, PROPOSAL, "corporate_credit", DRAFT, date_str=DATE)
    return Path(out)


@pytest.mark.parametrize("name", [n for n in FIXTURES if n != "04_pre_analyst_store"])
def test_the_workbook_is_filled_from_the_nested_raw_figures_of_every_framework_computed_shape(
        tmp_path, monkeypatch, name, capsys):
    install(tmp_path, monkeypatch, load_fixture(name))
    folder = export(tmp_path)
    capsys.readouterr()
    revenue = revenue_cells(next(folder.glob("*_Spreading.xlsx")))
    assert revenue["FY-Current"] == 4650 and revenue["FY-2"] == 4000     # from financials[...]["raw"]
    assert next(folder.glob("*_CAM.docx")).stat().st_size > 0


def test_an_analyst_supplied_state_without_the_raw_store_exports_blank_inputs_without_crashing(
        tmp_path, monkeypatch, capsys):
    install(tmp_path, monkeypatch, load_fixture("04_pre_analyst_store"))
    folder = export(tmp_path)
    capsys.readouterr()
    revenue = revenue_cells(next(folder.glob("*_Spreading.xlsx")))
    assert all(value is None for value in revenue.values())              # nothing invented for the missing store
    assert next(folder.glob("*_CAM.docx")).stat().st_size > 0


def test_an_analyst_supplied_state_with_the_raw_store_fills_the_workbook_from_that_store(
        tmp_path, monkeypatch, capsys):
    state = load_fixture("04_pre_analyst_store")
    state["analyst_supplied_financials"] = {"FY-Current": {"revenue": 4321}}
    install(tmp_path, monkeypatch, state)
    revenue = revenue_cells(next(export(tmp_path).glob("*_Spreading.xlsx")))
    capsys.readouterr()
    assert revenue["FY-Current"] == 4321      # read from analyst_supplied_financials, never from the other store


def test_the_downside_columns_are_filled_only_when_the_state_has_a_downside_case(tmp_path, monkeypatch, capsys):
    install(tmp_path, monkeypatch, load_fixture("02_pre_spreading_check"))
    with_downside = revenue_cells(next(export(tmp_path).glob("*_Spreading.xlsx")))
    assert with_downside["FY+1 (Downside)"] == pytest.approx(4900 * 0.9)   # 10% haircut on the supplied forecast
    shutil.rmtree(tmp_path / "deals")
    install(tmp_path, monkeypatch, load_fixture("01_pre_schema_version"))
    without = revenue_cells(next(export(tmp_path).glob("*_Spreading.xlsx")))
    capsys.readouterr()
    assert without["FY+1 (Downside)"] is None


# ---------------------------------------------------------------------------
# Wrongly-typed state (issue #171): a clear StateShapeError naming the key, from the consumers that read it
# ---------------------------------------------------------------------------
#
# Each consumer validates the keys IT reads (state_manager.validate_state; spreading_check.STATE_KEYS_READ,
# policy_check.STATE_KEYS_READ, deal_export.STATE_KEYS_READ), so a deal is never rejected for a key nothing in that
# step uses. The 17 shapes x 3 consumers below were first characterised as opaque AttributeError/TypeError (issue
# #152, strict xfails); this is the same matrix, now asserting the clear error where one is due and, explicitly, the
# deliberate tolerances where it is not.

def modern_state():
    state = load_fixture("03_pre_merge_fix")
    state["covenants"] = [{"metric": "dscr", "type": "minimum", "threshold": 1.25}]
    state["security_package"] = []
    return state


def mutated(**changes):
    state = modern_state()
    state.update(changes)
    return state


MALFORMED = {
    "financials_is_list": mutated(financials=[1, 2]),
    "financials_is_string": mutated(financials="none"),
    "financials_is_null": mutated(financials=None),
    "ratios_is_string": mutated(ratios="n/a"),
    "ratios_is_list": mutated(ratios=[1]),
    "period_is_number": mutated(financials={"FY-Current": 5}, ratios={"FY-Current": 5}),
    "collateral_is_dict": mutated(collateral={"a": 1}),
    "collateral_is_string": mutated(collateral="none"),
    "covenants_is_dict": mutated(covenants={"metric": "dscr"}),
    "covenants_is_string": mutated(covenants="none"),
    "security_package_is_string": mutated(security_package="none"),
    "multi_period_is_list": mutated(multi_period_financials=[1]),
    "multi_period_period_is_number": mutated(multi_period_financials={"FY-Current": 7}),
    "steps_is_string": mutated(steps_completed="triage"),
    "downside_is_list": mutated(downside_case=[1]),
    "financials_source_is_number": mutated(financials_source=5),
    "toplevel_list": [1, 2, 3],
}
CONSUMERS = ("spreading_check", "policy_check", "deal_export")
# For each shape and consumer: the text the StateShapeError must contain (the offending key), or None when that
# consumer accepts the shape -- either because it never reads that key, or by a DELIBERATE tolerance:
#   - a null value is "not recorded" (financials_is_null) everywhere;
#   - deal_export treats a non-object period as empty (period_is_number), a non-list collateral as no collateral
#     (collateral_is_*) and a non-object downside_case as no downside case (downside_is_list), as it always has.
# steps_completed is read only by the orchestrator and `state_manager --check-steps` (tested separately below).
EXPECTED_ERROR = {
    "financials_is_list":            ('"financials"', '"financials"', '"financials"'),
    "financials_is_string":          ('"financials"', '"financials"', '"financials"'),
    "financials_is_null":            (None, None, None),
    "ratios_is_string":              ('"ratios"', '"ratios"', None),
    "ratios_is_list":                ('"ratios"', '"ratios"', None),
    "period_is_number":              ('"financials"', '"financials"', None),
    "collateral_is_dict":            (None, '"collateral"', None),
    "collateral_is_string":          (None, '"collateral"', None),
    "covenants_is_dict":             (None, '"covenants"', None),
    "covenants_is_string":           (None, '"covenants"', None),
    "security_package_is_string":    (None, '"security_package"', None),
    "multi_period_is_list":          ('"multi_period_financials"', None, None),
    "multi_period_period_is_number": ('"multi_period_financials"', None, None),
    "steps_is_string":               (None, None, None),
    "downside_is_list":              (None, '"downside_case"', None),
    "financials_source_is_number":   (None, '"financials_source"', '"financials_source"'),
    "toplevel_list":                 ("top level", "top level", "top level"),
}


def run_consumer(consumer, tmp_path, monkeypatch, state, capsys):
    folder = tmp_path / "deals" / COMPANY / f"{PROPOSAL}_{DATE}"
    if folder.exists():
        shutil.rmtree(tmp_path / "deals")
    folder = install(tmp_path, monkeypatch, state)
    before = (folder / "state.json").read_bytes()
    try:
        if consumer == "spreading_check":
            spreading_check.compute(COMPANY, PROPOSAL, {"FY+2": {"revenue": 1}})
        elif consumer == "policy_check":
            policy_check.compute(COMPANY, PROPOSAL)
        else:
            deal_export.export_deal(COMPANY, PROPOSAL, "corporate_credit", DRAFT, date_str=DATE)
        outcome = None
    except Exception as exc:   # noqa: BLE001 - the whole point is to classify whatever comes out
        outcome = exc
    finally:
        capsys.readouterr()
    return outcome, (folder / "state.json").read_bytes() == before


MATRIX = [(shape, consumer, EXPECTED_ERROR[shape][i]) for shape in MALFORMED for i, consumer in enumerate(CONSUMERS)]


@pytest.mark.parametrize("shape, consumer, expected_key", [m for m in MATRIX if m[2]],
                         ids=[f"{s}-{c}" for s, c, k in MATRIX if k])
def test_wrongly_typed_state_fails_with_a_clear_error_naming_the_key(tmp_path, monkeypatch, capsys, shape, consumer,
                                                                     expected_key):
    outcome, unchanged = run_consumer(consumer, tmp_path, monkeypatch, copy.deepcopy(MALFORMED[shape]), capsys)
    assert isinstance(outcome, state_manager.StateShapeError), repr(outcome)
    assert isinstance(outcome, ValueError) and isinstance(outcome, state_manager.StateError)
    message = str(outcome)
    assert expected_key in message, message
    assert "deals" in message and "state.json" in message             # names the file
    assert message.endswith("the file was not modified.") and unchanged  # and it was not


@pytest.mark.parametrize("shape, consumer", [(s, c) for s, c, k in MATRIX if not k],
                         ids=[f"{s}-{c}" for s, c, k in MATRIX if not k])
def test_a_shape_a_consumer_never_reads_or_deliberately_tolerates_is_still_accepted(tmp_path, monkeypatch, capsys,
                                                                                    shape, consumer):
    outcome, _ = run_consumer(consumer, tmp_path, monkeypatch, copy.deepcopy(MALFORMED[shape]), capsys)
    assert outcome is None, repr(outcome)


# ---------------------------------------------------------------------------
# The deliberate tolerances, one by one, with what they actually produce
# ---------------------------------------------------------------------------

def _export(tmp_path, monkeypatch, state):
    install(tmp_path, monkeypatch, state)
    out = Path(deal_export.export_deal(COMPANY, PROPOSAL, "corporate_credit", DRAFT, date_str=DATE))
    return next(out.glob("*_Spreading.xlsx"))


def test_deal_export_treats_a_non_object_period_as_empty(tmp_path, monkeypatch, capsys):
    state = modern_state()
    state["financials"]["FY-Current"] = 5
    revenue = revenue_cells(_export(tmp_path, monkeypatch, state))
    capsys.readouterr()
    assert revenue["FY-Current"] is None and revenue["FY-2"] == 4000      # that period blank, nothing invented, the rest intact


def test_deal_export_treats_a_non_list_collateral_as_no_collateral(tmp_path, monkeypatch, capsys):
    """The #106 gap: the fallback's OUTPUT, not just 'does not crash' -- the Collateral sheet gets its single blank
    placeholder row, exactly as for a deal with no collateral at all."""
    for index, collateral in enumerate(({"a": 1}, "none", 7)):
        state = modern_state()
        state["collateral"] = collateral
        workdir = tmp_path / f"case{index}"
        workdir.mkdir()
        workbook = openpyxl.load_workbook(_export(workdir, monkeypatch, state))
        capsys.readouterr()
        sheet = workbook["Collateral & Exposure"]
        assert [sheet.cell(row=2, column=c).value for c in (1, 2, 7, 9)] == [None, None, None, None]
        assert sheet.cell(row=3, column=1).value == "Total"                # one blank asset row, then the total


def test_deal_export_treats_a_non_object_downside_case_as_no_downside(tmp_path, monkeypatch, capsys):
    state = modern_state()
    state["downside_case"] = [1]
    revenue = revenue_cells(_export(tmp_path, monkeypatch, state))
    capsys.readouterr()
    assert revenue["FY+1 (Downside)"] is None


def test_a_failed_export_leaves_no_output_folder_and_no_auto_saved_template(tmp_path, monkeypatch, capsys):
    """The state is read and validated before anything is created: a state.json that cannot be used must not leave
    an empty dated folder or a freshly auto-saved template (which holds this deal's draft) behind."""
    install(tmp_path, monkeypatch, mutated(financials_source=5))
    folder_listing = sorted(p.name for p in (tmp_path / "deals" / COMPANY).iterdir())
    with pytest.raises(state_manager.StateShapeError):
        deal_export.export_deal(COMPANY, PROPOSAL, "brand_new_type", DRAFT, date_str="2026-02-02")
    capsys.readouterr()
    assert sorted(p.name for p in (tmp_path / "deals" / COMPANY).iterdir()) == folder_listing
    assert not (tmp_path / "templates").exists()


@pytest.mark.parametrize("key", sorted(state_manager.STATE_SHAPES))
def test_a_null_value_means_not_recorded_for_every_key_at_every_consumer(tmp_path, monkeypatch, capsys, key):
    for consumer in CONSUMERS:
        state = modern_state()
        state[key] = None
        outcome, _ = run_consumer(consumer, tmp_path, monkeypatch, state, capsys)
        assert outcome is None, (consumer, key, repr(outcome))


def test_a_null_period_is_accepted_as_empty():
    state_manager.validate_state({"financials": {"FY-Current": None}, "ratios": {"FY-Current": None}})


def test_unknown_keys_are_never_inspected_or_rejected(tmp_path, monkeypatch, capsys):
    state = modern_state()
    state.update(future_key=[1, {"x": 2}], x_note="anything", triage="not even an object", inputs={"pd": "1%"})
    for consumer in CONSUMERS:
        outcome, _ = run_consumer(consumer, tmp_path, monkeypatch, copy.deepcopy(state), capsys)
        assert outcome is None, (consumer, repr(outcome))


@pytest.mark.parametrize("name", FIXTURES)
def test_every_historical_fixture_passes_the_full_validation(name):
    """The five historical shapes (and the newer one with unknown fields) must stay loadable."""
    state_manager.validate_state(load_fixture(name))


# ---------------------------------------------------------------------------
# Keys read by the orchestrator, `state_manager --check-steps`, and the review trail
# ---------------------------------------------------------------------------

def test_append_review_trail_rejects_a_review_trail_that_is_not_a_list(tmp_path):
    base = str(tmp_path)
    path = state_manager.state_path("Acme", "Loan", date_str="2026-01-01", base_dir=base)
    Path(path).parent.mkdir(parents=True)
    Path(path).write_text(json.dumps({"review_trail": "oops"}), encoding="utf-8")
    before = Path(path).read_bytes()
    with pytest.raises(state_manager.StateShapeError, match='"review_trail" must be a list'):
        state_manager.append_review_trail("Acme", "Loan", "APPROVED", date_str="2026-01-01", base_dir=base)
    assert Path(path).read_bytes() == before


# ---------------------------------------------------------------------------
# validate_state itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", [[], [1], "text", 3, None, True])
def test_the_top_level_must_be_an_object(state):
    with pytest.raises(state_manager.StateShapeError, match="the top level must be a JSON object"):
        state_manager.validate_state(state, path="x/state.json")


@pytest.mark.parametrize("key, bad, expected", [
    ("financials", [1], "found list"), ("financials", "x", "found string"), ("financials", 5, "found number"),
    ("financials", True, "found boolean"), ("ratios", {"FY": 5}, "[\'FY\'] must be an object, found number"),
    ("multi_period_financials", {"FY": [1]}, "[\'FY\'] must be an object, found list"),
    ("analyst_supplied_financials", [], "found list"),
    ("inputs", [], 'must be an object ({...}), found list'), ("stress_assumptions", "5", "found string"),
    ("downside_case", [], "found list"), ("downside_case", {"financials": []}, '"downside_case.financials"'),
    ("downside_case", {"ratios": {"FY+1": 3}}, '"downside_case.ratios"[\'FY+1\'] must be an object'),
    ("collateral", {}, "must be a list"), ("covenants", "x", "found string"), ("security_package", 1, "found number"),
    ("guarantees", {}, "must be a list"), ("steps_completed", "triage", "found string"), ("review_trail", {}, "found object"),
    ("financials_source", 5, "found 5"), ("financials_source", "analyst_supplied", "found 'analyst_supplied'"),
    ("financials_source", ["analyst-supplied"], "must be \"framework-computed\" or \"analyst-supplied\""),
])
def test_each_key_kind_rejects_the_wrong_type_with_a_message_naming_the_key(key, bad, expected):
    with pytest.raises(state_manager.StateShapeError) as caught:
        state_manager.validate_state({key: bad}, path="deals/A/B/state.json")
    message = str(caught.value)
    assert expected in message and message.startswith("Cannot use deals/A/B/state.json: ")
    assert key in message


@pytest.mark.parametrize("key, good", [
    ("financials", {}), ("financials", {"FY": {}}), ("ratios", {"FY": {"dscr": None}}),
    ("multi_period_financials", {"FY": {"revenue": 1}}), ("analyst_supplied_financials", {}), ("inputs", {}),
    ("stress_assumptions", {"revenue_haircut_pct": 10}), ("downside_case", {}),
    ("downside_case", {"financials": {"FY+1": {}}, "ratios": {"FY+1": {}}}), ("downside_case", {"financials": None}),
    ("collateral", []), ("collateral", [1, "x", None]), ("covenants", []), ("security_package", []),
    ("guarantees", [{}]), ("steps_completed", ["a"]), ("review_trail", []),
    ("financials_source", "framework-computed"), ("financials_source", "analyst-supplied"),
])
def test_each_key_kind_accepts_the_right_type_including_any_list_elements(key, good):
    state = {key: good}
    assert state_manager.validate_state(state) is state


def test_every_problem_is_reported_together_not_one_at_a_time():
    with pytest.raises(state_manager.StateShapeError) as caught:
        state_manager.validate_state({"financials": [], "covenants": "x", "steps_completed": {}, "ratios": {"FY": 1}})
    message = str(caught.value)
    for fragment in ('"financials"', '"covenants"', '"steps_completed"', '"ratios"[\'FY\']'):
        assert fragment in message
    assert message.split(". Fix the file")[0].count("; ") == 3          # four problems, three separators


def test_keys_selects_what_is_checked_and_a_mapping_overrides_the_kind():
    bad = {"financials": [], "covenants": "x"}
    state_manager.validate_state(bad, keys=())                                   # nothing but the top level
    state_manager.validate_state(bad, keys=("ratios", "steps_completed"))        # keys it does not look at
    with pytest.raises(state_manager.StateShapeError, match='"covenants"'):
        state_manager.validate_state(bad, keys=("covenants",))
    state_manager.validate_state({"financials": {"FY": 5}}, keys={"financials": "object"})   # looser kind, as deal_export
    with pytest.raises(state_manager.StateShapeError, match="must be an object"):
        state_manager.validate_state({"financials": []}, keys={"financials": "object"})


def test_an_unknown_key_name_in_keys_is_a_programming_error():
    with pytest.raises(KeyError):
        state_manager.validate_state({}, keys=("not_a_state_key",))


def test_read_state_checks_only_the_top_level_unless_told_which_keys_to_check(tmp_path):
    base = str(tmp_path)
    path = state_manager.state_path("Acme", "Loan", date_str="2026-01-01", base_dir=base)
    Path(path).parent.mkdir(parents=True)
    Path(path).write_text(json.dumps({"financials": [1]}), encoding="utf-8")
    assert state_manager.read_state("Acme", "Loan", date_str="2026-01-01", base_dir=base)["financials"] == [1]
    with pytest.raises(state_manager.StateShapeError, match='"financials"'):
        state_manager.read_state("Acme", "Loan", date_str="2026-01-01", base_dir=base, keys=("financials",))


def test_a_corrupt_file_is_a_state_shape_error_and_still_a_value_error(tmp_path):
    base = str(tmp_path)
    path = state_manager.state_path("Acme", "Loan", date_str="2026-01-01", base_dir=base)
    Path(path).parent.mkdir(parents=True)
    Path(path).write_text('{"financials": ', encoding="utf-8")
    with pytest.raises(state_manager.StateShapeError, match="is corrupted and could not be parsed"):
        state_manager.read_state("Acme", "Loan", date_str="2026-01-01", base_dir=base)


def test_the_error_hierarchy():
    assert issubclass(state_manager.SchemaVersionError, state_manager.StateError)
    assert issubclass(state_manager.StateShapeError, state_manager.StateError)
    assert issubclass(state_manager.StateError, ValueError)
