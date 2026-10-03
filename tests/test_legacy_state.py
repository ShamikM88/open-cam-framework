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

Wrongly-typed state is a separate matter: the issue asks that it "fail with a clear error instead of being silently
reinterpreted". Today none of it does -- see the end of this file, which pins what happens now and records the gap as
strict xfails rather than changing production code.
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


@pytest.mark.parametrize("name", FIXTURES)
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
    # A write stamps this code's own version, unconditionally. For fixtures 01-04 that is an upgrade; for fixture
    # 05 (written by a NEWER framework, 9.9.9) it silently DOWNGRADES the recorded version -- existing behaviour,
    # pinned here and reported in the PR rather than changed (state_manager.write_state).
    assert written["schema_version"] == state_manager.SCHEMA_VERSION
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


def test_unknown_fields_of_a_newer_schema_survive_a_write_byte_for_byte(tmp_path, monkeypatch):
    original = load_fixture("05_future_unknown_fields")
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
# Wrongly-typed state: what happens today, and the gap
# ---------------------------------------------------------------------------

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
# What each consumer does TODAY with each malformed state: "ok" (it ignores the key or has an explicit tolerance for
# the shape, e.g. deal_export treats a non-dict period as empty and a non-list collateral as no collateral) or
# "raises" (an opaque AttributeError / TypeError / ValueError from deep inside, naming no key).
TODAY = {
    "financials_is_list":            ("raises", "raises", "raises"),
    "financials_is_string":          ("raises", "raises", "raises"),
    "financials_is_null":            ("ok", "ok", "ok"),
    "ratios_is_string":              ("raises", "raises", "ok"),
    "ratios_is_list":                ("raises", "raises", "ok"),
    "period_is_number":              ("ok", "raises", "ok"),
    "collateral_is_dict":            ("ok", "raises", "ok"),
    "collateral_is_string":          ("ok", "raises", "ok"),
    "covenants_is_dict":             ("ok", "raises", "ok"),
    "covenants_is_string":           ("ok", "raises", "ok"),
    "security_package_is_string":    ("ok", "raises", "ok"),
    "multi_period_is_list":          ("raises", "ok", "ok"),
    "multi_period_period_is_number": ("ok", "ok", "ok"),
    "steps_is_string":               ("ok", "ok", "ok"),
    "downside_is_list":              ("ok", "raises", "ok"),
    "financials_source_is_number":   ("ok", "ok", "ok"),
    "toplevel_list":                 ("raises", "raises", "raises"),
}


def run_consumer(consumer, tmp_path, monkeypatch, state, capsys):
    folder = tmp_path / "deals" / COMPANY / f"{PROPOSAL}_{DATE}"
    if folder.exists():
        shutil.rmtree(tmp_path / "deals")
    install(tmp_path, monkeypatch, state)
    try:
        if consumer == "spreading_check":
            spreading_check.compute(COMPANY, PROPOSAL, {"FY+2": {"revenue": 1}})
        elif consumer == "policy_check":
            policy_check.compute(COMPANY, PROPOSAL)
        else:
            deal_export.export_deal(COMPANY, PROPOSAL, "corporate_credit", DRAFT, date_str=DATE)
        return None
    except Exception as exc:   # noqa: BLE001 - the whole point is to classify whatever comes out
        return exc
    finally:
        capsys.readouterr()


MATRIX = [(shape, consumer, TODAY[shape][i]) for shape in MALFORMED for i, consumer in enumerate(CONSUMERS)]


@pytest.mark.parametrize("shape, consumer, expected", MATRIX, ids=[f"{s}-{c}" for s, c, _ in MATRIX])
def test_what_each_consumer_does_today_with_wrongly_typed_state(tmp_path, monkeypatch, capsys, shape, consumer,
                                                                 expected):
    """Characterisation: pins today's behaviour so a change to it -- including adding validation -- is a reviewed
    diff to TODAY, not an accident."""
    outcome = run_consumer(consumer, tmp_path, monkeypatch, copy.deepcopy(MALFORMED[shape]), capsys)
    assert ("raises" if outcome else "ok") == expected, repr(outcome)


GAP = ("gap found by #152: wrongly-typed state.json fails with an opaque AttributeError/TypeError that names no key "
       "(or is silently tolerated), not a clear error; reported separately, not fixed here -- remove this xfail when "
       "a validator raises a ValueError naming the key (strict: it fails once the behaviour changes)")
RAISING = [(s, c) for s, c, expected in MATRIX if expected == "raises"]


@pytest.mark.xfail(strict=True, reason=GAP)
@pytest.mark.parametrize("shape, consumer", RAISING, ids=[f"{s}-{c}" for s, c in RAISING])
def test_wrongly_typed_state_fails_with_a_clear_error_naming_the_key(tmp_path, monkeypatch, capsys, shape, consumer):
    outcome = run_consumer(consumer, tmp_path, monkeypatch, copy.deepcopy(MALFORMED[shape]), capsys)
    key = shape.split("_is_")[0].replace("multi_period", "multi_period_financials")
    assert isinstance(outcome, ValueError) and key in str(outcome), repr(outcome)
