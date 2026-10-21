"""Contract tests for the read-only review comparator; deal data is synthetic."""
import builtins
import hashlib
import json
from decimal import Decimal

import pytest

import review_compare as rc
from state_manager import state_path


COMPANY = "Synthetic Co"
PROPOSAL = "Asset Facility"
PRIOR_DATE = "2025-10-20"
CURRENT_DATE = "2026-10-20"


def state(**values):
    return {"company": COMPANY, "proposal": PROPOSAL, **values}


def report(prior=None, current=None, *, rolled=False):
    return rc.compare_states(
        COMPANY, PROPOSAL, PRIOR_DATE, CURRENT_DATE,
        state(**(prior or {})), state(**(current or {})),
        periods_rolled_one_year=rolled,
    )


def write_state(tmp_path, date_value, value, *, raw_text=None):
    path = state_path(COMPANY, PROPOSAL, date_str=date_value, base_dir=str(tmp_path))
    path_obj = __import__("pathlib").Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    if raw_text is None:
        path_obj.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    else:
        path_obj.write_text(raw_text, encoding="utf-8")
    return path_obj


def invoke(tmp_path, capsys, *extra):
    args = ["--company", COMPANY, "--proposal", PROPOSAL, "--current-date", CURRENT_DATE, *extra]
    code = rc.main(args)
    captured = capsys.readouterr()
    return code, captured


def test_equal_values_are_unchanged_with_decimal_equality_and_stable_summary():
    result = report(
        {"financials_source": "framework-computed",
         "financials": {"FY-Current": {"revenue": 1, "ebitda": 2.0}}},
        {"financials_source": "framework-computed",
         "financials": {"FY-Current": {"ebitda": 2, "revenue": 1.0}}},
    )
    assert result["changes"] == []
    assert result["summary"]["changed_paths"] == 0
    assert "financials" in result["summary"]["unchanged_categories"]
    assert result["basis_change"]["comparable_like_for_like"] is True


def test_numeric_delta_is_current_minus_prior_and_exact_decimal():
    result = report(
        {"financials": {"FY-Current": {"revenue": Decimal("100.10")}}},
        {"financials": {"FY-Current": {"revenue": Decimal("101.35")}}},
    )
    row = next(item for item in result["changes"] if item["path"] == "financials.FY-Current.revenue")
    assert row == {
        "path": "financials.FY-Current.revenue", "change": "value_changed",
        "prior": Decimal("100.10"), "current": Decimal("101.35"), "delta": Decimal("1.25"),
    }


def test_delta_is_only_present_when_both_values_are_numeric():
    result = report(
        {"financials": {"FY-Current": {"ebitda": None, "revenue": 9, "currency": "GBP"}}},
        {"financials": {"FY-Current": {"ebitda": 10, "revenue": "not a number", "currency": "EUR"}}},
    )
    rows = {item["path"]: item for item in result["changes"]}
    assert rows["financials.FY-Current.ebitda"]["change"] == "not_recorded_to_value"
    assert "delta" not in rows["financials.FY-Current.ebitda"]
    assert rows["financials.FY-Current.revenue"]["change"] == "value_changed"
    assert "delta" not in rows["financials.FY-Current.revenue"]
    assert "delta" not in rows["financials.FY-Current.currency"]


def test_all_leaf_transition_kinds_are_reported_without_missing_value_deltas():
    result = report(
        {"stress_assumptions": {"removed": 1, "changed": "old", "null_to_value": None}},
        {"stress_assumptions": {"added": 2, "changed": "new", "null_to_value": 3}},
    )
    rows = {item["path"]: item for item in result["changes"]}
    assert rows["stress_assumptions.added"]["change"] == "not_recorded_to_value"
    assert rows["stress_assumptions.removed"]["change"] == "value_to_not_recorded"
    assert rows["stress_assumptions.changed"]["change"] == "value_changed"
    assert rows["stress_assumptions.null_to_value"]["change"] == "not_recorded_to_value"
    assert all("delta" not in row for row in rows.values())


def test_period_additions_and_removals_are_recursive_by_period_and_field():
    result = report(
        {"financials": {"FY-1": {"revenue": 90}, "FY-2": {"ebitda": 8}}},
        {"financials": {"FY-Current": {"revenue": 100}, "FY-1": {"ebitda": 9}}},
    )
    paths = {item["path"]: item["change"] for item in result["changes"]}
    assert paths["financials.FY-Current.revenue"] == "not_recorded_to_value"
    assert paths["financials.FY-1.revenue"] == "value_to_not_recorded"
    assert paths["financials.FY-1.ebitda"] == "not_recorded_to_value"
    assert paths["financials.FY-2.ebitda"] == "value_to_not_recorded"


def test_downside_case_diffs_nested_periods_instead_of_collapsing_whole_object():
    result = report(
        {"downside_case": {"financials": {"FY+1": {"ebitda": 4}}, "ratios": {"FY+1": {"dscr": 1.1}}}},
        {"downside_case": {"financials": {"FY+1": {"ebitda": 3}, "FY+2": {"ebitda": 2}},
                           "ratios": {"FY+1": {"dscr": 0.9}}}},
    )
    rows = {item["path"]: item for item in result["changes"]}
    assert rows["downside_case.financials.FY+1.ebitda"]["delta"] == -1
    assert rows["downside_case.financials.FY+2.ebitda"]["change"] == "not_recorded_to_value"
    assert rows["downside_case.ratios.FY+1.dscr"]["delta"] == Decimal("-0.2")


def test_financial_source_and_note_are_first_and_basis_change_is_unmistakable():
    result = report(
        {"financials_source": "framework-computed", "financials_source_note": "audited basis",
         "financials": {"FY-Current": {"raw": {"revenue": 100}}}},
        {"financials_source": "analyst-supplied", "financials_source_note": "management figures",
         "financials": {"FY-Current": {"ebitda": 5}}},
    )
    assert [row["path"] for row in result["changes"][:2]] == ["financials_source", "financials_source_note"]
    assert result["basis_change"]["comparable_like_for_like"] is False
    assert "not necessarily like-for-like" in result["basis_change"]["warning"]


def test_raw_figure_moved_to_analyst_store_is_two_transitions_not_a_delta():
    result = report(
        {"financials_source": "framework-computed",
         "financials": {"FY-Current": {"raw": {"revenue": 100}, "ebitda": 20}}},
        {"financials_source": "analyst-supplied", "financials": {"FY-Current": {"ebitda": 21}},
         "analyst_supplied_financials": {"FY-Current": {"revenue": 110}}},
    )
    rows = {item["path"]: item for item in result["changes"]}
    assert rows["financials.FY-Current.raw.revenue"]["change"] == "value_to_not_recorded"
    assert rows["analyst_supplied_financials.FY-Current.revenue"]["change"] == "not_recorded_to_value"
    assert "delta" not in rows["financials.FY-Current.raw.revenue"]
    assert "delta" not in rows["analyst_supplied_financials.FY-Current.revenue"]


@pytest.mark.parametrize(("before", "after"), [
    ("PASS", "FAIL"), ("FAIL", "UNRESOLVABLE"), ("UNRESOLVABLE", "PASS"),
])
def test_recorded_covenant_verdict_movement_is_compared_by_metric_and_type(before, after):
    result = report(
        {"policy_state": {"covenant_results": [
            {"metric": "gross_leverage", "type": "max", "status": before, "actual": 2.8}]}},
        {"policy_state": {"covenant_results": [
            {"metric": "gross_leverage", "type": "max", "status": after, "actual": 3.9}]}},
    )
    rows = {item["path"]: item for item in result["changes"]}
    assert rows['policy_state.covenant_results[metric="gross_leverage",type="max",occurrence=1].status']["current"] == after
    assert rows['policy_state.covenant_results[metric="gross_leverage",type="max",occurrence=1].actual']["delta"] == Decimal("1.1")


def test_identified_records_match_by_stable_id_not_list_position():
    before = [
        {"metric": "leverage", "type": "max", "threshold": 3.5},
        {"metric": "dscr", "type": "min", "threshold": 1.2},
    ]
    after = [
        {"metric": "dscr", "type": "min", "threshold": 1.2},
        {"metric": "leverage", "type": "max", "threshold": 4.0},
    ]
    result = report({"covenants": before}, {"covenants": after})
    assert [row["path"] for row in result["changes"]] == [
        'covenants[metric="leverage",type="max",occurrence=1].threshold'
    ]
    assert result["changes"][0]["delta"] == Decimal("0.5")


def test_duplicate_covenant_identity_uses_occurrence_counter():
    result = report(
        {"covenants": [{"metric": "lev", "type": "max", "threshold": 3},
                       {"metric": "lev", "type": "max", "threshold": 4}]},
        {"covenants": [{"metric": "lev", "type": "max", "threshold": 3.1},
                       {"metric": "lev", "type": "max", "threshold": 4}]},
    )
    assert len(result["changes"]) == 1
    assert "occurrence=1" in result["changes"][0]["path"]
    assert result["changes"][0]["delta"] == Decimal("0.1")


@pytest.mark.parametrize(("field", "identity", "key"), [
    ("forward_covenant_results", "forward_id", "FW-1"),
    ("downside_covenant_breaches", "breach_id", "BR-1"),
    ("required_conditions_precedent", "cp_id", "CP-1"),
    ("required_conditions_subsequent", "cs_id", "CS-1"),
])
def test_policy_result_lists_use_their_real_stable_ids(field, identity, key):
    before = {"policy_state": {field: [{identity: key, "text": "old"}]}}
    after = {"policy_state": {field: [{identity: "NEW-1", "text": "new"}]}}
    result = report(before, after)
    assert [row["change"] for row in result["changes"] if row["path"].startswith("policy_state." + field)] == [
        "removed", "added"
    ]


def test_collateral_uses_asset_id_while_security_and_guarantees_keep_exact_order():
    result = report(
        {"collateral": [{"asset_id": "A-1", "value": 10}, {"asset_id": "A-2", "value": 20}],
         "security_package": [{"secures_asset_id": "A-1", "ranking": "First"},
                              {"secures_asset_id": "A-2", "ranking": "Second"}],
         "guarantees": [{"provider": "Parent", "type": "Corporate"}]},
        {"collateral": [{"asset_id": "A-2", "value": 21}, {"asset_id": "A-3", "value": 30}],
         "security_package": [{"secures_asset_id": "A-2", "ranking": "Second"},
                              {"secures_asset_id": "A-1", "ranking": "First"}],
         "guarantees": [{"provider": "Parent", "type": "Corporate"}, {"provider": "Sponsor", "type": "Personal"}]},
    )
    rows = {row["path"]: row for row in result["changes"]}
    assert rows['collateral[asset_id="A-2"].value']["delta"] == Decimal("1")
    assert rows['collateral[asset_id="A-1"]']["change"] == "removed"
    assert rows['collateral[asset_id="A-3"]']["change"] == "added"
    assert rows["security_package"]["change"] == "value_changed"
    assert rows["guarantees"]["change"] == "value_changed"


def test_optional_ownership_and_facility_terms_are_not_recorded_in_both_not_inferred():
    result = report({"financials": {"FY-Current": {"ebitda": 5}}},
                    {"financials": {"FY-Current": {"ebitda": 5}}})
    assert "ownership" in result["summary"]["not_recorded_in_both"]
    assert "facility_terms" in result["summary"]["not_recorded_in_both"]


def test_optional_ownership_and_facility_terms_are_compared_when_present():
    result = report(
        {"ownership": {"parent": "P1"}, "facility_terms": {"limit": 100}},
        {"ownership": {"parent": "P2"}, "facility_terms": {"limit": 125}},
    )
    rows = {item["path"]: item for item in result["changes"]}
    assert rows["ownership.parent"]["change"] == "value_changed"
    assert rows["facility_terms.limit"]["delta"] == Decimal("25")


def test_unidentified_lists_compare_as_exact_ordered_values_and_ids_sort_deterministically():
    result = report(
        {"security_package": [{"asset": "B"}, {"asset": "A"}]},
        {"security_package": [{"asset": "A"}, {"asset": "B"}]},
    )
    assert [row["path"] for row in result["changes"]] == ["security_package"]
    assert result["changes"][0]["change"] == "value_changed"
    added_removed = report(
        {"collateral": [{"asset_id": "B", "value": 1}]},
        {"collateral": [{"asset_id": "C", "value": 2}, {"asset_id": "A", "value": 3}]},
    )
    assert [row["change"] for row in added_removed["changes"]] == ["added", "removed", "added"]
    assert added_removed["summary"]["changed_paths"] == len(added_removed["changes"])


def test_change_order_is_source_first_then_sorted_object_keys():
    result = report(
        {"financials_source_note": "old", "financials_source": "framework-computed",
         "stress_assumptions": {"z": 1, "a": 1}},
        {"financials_source_note": "new", "financials_source": "analyst-supplied",
         "stress_assumptions": {"z": 2, "a": 2}},
    )
    assert [row["path"] for row in result["changes"]] == [
        "financials_source", "financials_source_note", "stress_assumptions.a", "stress_assumptions.z"
    ]


def test_changed_paths_equals_exact_change_count():
    result = report(
        {"financials": {"FY-Current": {"raw": {"revenue": 10}, "ebitda": 5}}},
        {"financials": {"FY-Current": {"ebitda": 6}},
         "analyst_supplied_financials": {"FY-Current": {"revenue": 11}}},
    )
    assert result["summary"]["changed_paths"] == len(result["changes"]) == 3


def test_serialization_is_deterministic_and_preserves_decimal_json_numbers():
    result = report({"financials": {"FY-Current": {"revenue": Decimal("1.000000000000000001")}}},
                    {"financials": {"FY-Current": {"revenue": Decimal("1.000000000000000002")}}})
    first = rc._json_dump(result)
    second = rc._json_dump(report(
        {"financials": {"FY-Current": {"revenue": Decimal("1.000000000000000001")}}},
        {"financials": {"FY-Current": {"revenue": Decimal("1.000000000000000002")}}},
    ))
    assert first == second
    decoded = json.loads(first, parse_float=Decimal, parse_int=Decimal)
    assert decoded["changes"][0]["prior"] == Decimal("1.000000000000000001")
    assert decoded["changes"][0]["delta"] == Decimal("1E-18")


def test_markdown_has_stable_title_table_summary_and_missing_downside_warning():
    result = report({}, {})
    text = rc.render_markdown(result)
    assert text.startswith("# Review comparison — Synthetic Co / Asset Facility")
    assert "| Item | Prior | Current | Change |" in text
    assert "Changed paths: 0" in text
    assert "Downside case: not recorded in either state; this is not evidence that downside risk is absent" in text
    assert "financials_source" in text


def test_restatement_flag_absent_means_no_restatement_key():
    result = report(
        {"financials": {"FY-Current": {"raw": {"revenue": 100}, "ebitda": 20}},
         "ratios": {"FY-Current": {"dscr": 1.2}},
         "multi_period_financials": {"FY+1": {"revenue": 200}}},
        {"financials": {"FY-1": {"raw": {"revenue": 100}, "ebitda": 20}},
         "ratios": {"FY-1": {"dscr": 1.2}}},
    )
    assert "restatement_check" not in result


def test_restatement_consistent_compares_raw_values_subtotals_and_ratios_and_ignores_forward():
    result = report(
        {"financials_source": "framework-computed",
         "financials": {"FY-Current": {"raw": {"revenue": 100}, "ebitda": 20},
                        "FY+1": {"raw": {"revenue": 200}, "ebitda": 30}},
         "multi_period_financials": {"FY-Current": {"revenue": 100}, "FY+1": {"revenue": 200}},
         "ratios": {"FY-Current": {"dscr": 1.2}, "FY+1": {"dscr": 1.5}}},
        {"financials_source": "framework-computed",
         "financials": {"FY-1": {"raw": {"revenue": 100}, "ebitda": 20},
                        "FY+1": {"raw": {"revenue": 999}, "ebitda": 999}},
         "multi_period_financials": {"FY-1": {"revenue": 100}},
         "ratios": {"FY-1": {"dscr": 1.2}, "FY+1": {"dscr": 9.9}}},
        rolled=True,
    )
    check = result["restatement_check"]
    assert check["periods_rolled_one_year_confirmed"] is True
    pair = next(p for p in check["periods"] if p["prior_period"] == "FY-Current")
    assert pair["current_period"] == "FY-1"
    assert pair["status"] == "consistent"
    assert pair["differences"] == []
    assert all(p["prior_period"] != "FY+1" for p in check["periods"])


def test_restatement_restated_pairs_include_exact_difference_and_not_cause():
    result = report(
        {"financials": {"FY-Current": {"raw": {"revenue": 100}, "ebitda": 20}},
         "ratios": {"FY-Current": {"dscr": 1.2}}},
        {"financials": {"FY-1": {"raw": {"revenue": 110}, "ebitda": 21}},
         "ratios": {"FY-1": {"dscr": 1.1}}},
        rolled=True,
    )
    pair = result["restatement_check"]["periods"][0]
    assert pair["status"] == "restated"
    values = {row["path"]: row for row in pair["differences"]}
    assert values["raw_line_items.revenue"]["difference"] == Decimal("10")
    assert values["financials.ebitda"]["difference"] == Decimal("1")
    assert values["ratios.dscr"]["difference"] == Decimal("-0.1")
    assert "cause" not in pair


def test_restatement_not_comparable_when_shifted_current_period_is_absent():
    result = report({"financials": {"FY-Current": {"ebitda": 20}}},
                    {"financials": {"FY-Current": {"ebitda": 21}}}, rolled=True)
    pair = result["restatement_check"]["periods"][0]
    assert pair["status"] == "not_comparable"
    assert pair["current_period"] == "FY-1"
    assert pair["current_values"] is None


def test_restatement_shows_basis_change_warning_next_to_pair_and_in_markdown():
    result = report(
        {"financials_source": "framework-computed", "financials": {"FY-Current": {"ebitda": 20}}},
        {"financials_source": "analyst-supplied", "financials": {"FY-1": {"ebitda": 20}}},
        rolled=True,
    )
    pair = result["restatement_check"]["periods"][0]
    assert pair["warning"] == rc.BASIS_WARNING
    assert result["restatement_check"]["basis_change_warning"] == rc.BASIS_WARNING
    assert rc.BASIS_WARNING in rc.render_markdown(result)


def test_restatement_uses_analyst_raw_store_when_basis_is_analyst_supplied():
    result = report(
        {"financials_source": "analyst-supplied",
         "analyst_supplied_financials": {"FY-Current": {"revenue": 100}},
         "financials": {"FY-Current": {"ebitda": 20}}},
        {"financials_source": "analyst-supplied",
         "analyst_supplied_financials": {"FY-1": {"revenue": 105}},
         "financials": {"FY-1": {"ebitda": 20}}},
        rolled=True,
    )
    pair = result["restatement_check"]["periods"][0]
    assert pair["prior_values"]["raw_line_items"] == {"revenue": 100}
    assert pair["prior_values"]["raw_store"] == "analyst_supplied_financials.FY-Current"
    assert pair["status"] == "restated"


def test_read_only_files_hashes_and_no_new_files_or_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prior = write_state(tmp_path, PRIOR_DATE, state(financials={"FY-Current": {"ebitda": 10}}))
    current = write_state(tmp_path, CURRENT_DATE, state(financials={"FY-Current": {"ebitda": 12}}))
    prior_hash, current_hash = hashlib.sha256(prior.read_bytes()).hexdigest(), hashlib.sha256(current.read_bytes()).hexdigest()
    before_paths = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    result = rc.compare_deal(COMPANY, PROPOSAL, CURRENT_DATE, prior_date=PRIOR_DATE)
    after_paths = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert result["summary"]["changed_paths"] == 1
    assert hashlib.sha256(prior.read_bytes()).hexdigest() == prior_hash
    assert hashlib.sha256(current.read_bytes()).hexdigest() == current_hash
    assert after_paths == before_paths


def test_missing_prior_date_uses_the_current_states_recorded_pointer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state(financials={"FY-Current": {"ebitda": 10}}))
    write_state(tmp_path, CURRENT_DATE, state(prior_review_date=PRIOR_DATE,
                                              financials={"FY-Current": {"ebitda": 12}}))
    result = rc.compare_deal(COMPANY, PROPOSAL, CURRENT_DATE)
    assert result["prior_date"] == PRIOR_DATE
    assert result["changes"][0]["delta"] == Decimal("2")


def test_explicit_prior_date_overrides_the_recorded_pointer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    older = "2024-10-20"
    write_state(tmp_path, older, state(financials={"FY-Current": {"ebitda": 8}}))
    write_state(tmp_path, PRIOR_DATE, state(financials={"FY-Current": {"ebitda": 10}}))
    write_state(tmp_path, CURRENT_DATE, state(prior_review_date=older, financials={"FY-Current": {"ebitda": 12}}))
    result = rc.compare_deal(COMPANY, PROPOSAL, CURRENT_DATE, prior_date=PRIOR_DATE)
    assert result["prior_date"] == PRIOR_DATE
    assert result["changes"][0]["delta"] == Decimal("2")


@pytest.mark.parametrize(("text", "expected"), [
    ('{"company":"Synthetic Co","proposal":"Asset Facility","financials":{"FY-Current":{"ebitda":NaN}}}',
     "current state contains a non-finite number at $.financials.FY-Current.ebitda"),
    ('{"company":"Synthetic Co","proposal":"Asset Facility","financials":{"FY-Current":{"ebitda":Infinity}}}',
     "current state contains a non-finite number at $.financials.FY-Current.ebitda"),
])
def test_nonfinite_json_numbers_refuse_with_one_error_line(tmp_path, monkeypatch, capsys, text, expected):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state())
    write_state(tmp_path, CURRENT_DATE, state(), raw_text=text)
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE)
    assert code == 1
    assert captured.stdout == ""
    assert captured.stderr == f"error: {expected}: {state_path(COMPANY, PROPOSAL, date_str=CURRENT_DATE)}\n"
    assert "Traceback" not in captured.stderr


@pytest.mark.parametrize(("text", "expected_fragment"), [
    ("{broken", "current state is corrupt JSON"),
    ("[]", "current state must be a JSON object"),
])
def test_corrupt_or_nonobject_states_refuse_cleanly(tmp_path, monkeypatch, capsys, text, expected_fragment):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state())
    write_state(tmp_path, CURRENT_DATE, state(), raw_text=text)
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE)
    assert code == 1 and captured.stdout == ""
    assert captured.stderr.startswith(f"error: {expected_fragment}: ")
    assert len(captured.stderr.strip().splitlines()) == 1 and "Traceback" not in captured.stderr


def test_missing_prior_state_refuses_with_one_exact_error_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, CURRENT_DATE, state())
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE)
    expected = f"error: prior state not found: {state_path(COMPANY, PROPOSAL, date_str=PRIOR_DATE)}\n"
    assert code == 1
    assert captured.stdout == ""
    assert captured.stderr == expected


def test_missing_current_state_refuses_with_one_exact_error_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state())
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE)
    expected = f"error: current state not found: {state_path(COMPANY, PROPOSAL, date_str=CURRENT_DATE)}\n"
    assert code == 1
    assert captured.stdout == ""
    assert captured.stderr == expected

def test_read_permission_failure_is_reported_as_one_error_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state())
    path = write_state(tmp_path, CURRENT_DATE, state())
    original_open = builtins.open

    def denied(file, *args, **kwargs):
        if __import__("pathlib").Path(file).resolve() == path.resolve():
            raise PermissionError("permission denied")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", denied)
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE)
    assert code == 1 and captured.stdout == ""
    relative_path = state_path(COMPANY, PROPOSAL, date_str=CURRENT_DATE)
    assert captured.stderr == f"error: current state is unreadable: {relative_path}: permission denied\n"


@pytest.mark.parametrize(("flag", "value", "expected"), [
    ("--current-date", "2026-02-30", "error: --current-date '2026-02-30' is not a valid calendar date\n"),
    ("--current-date", "2026-1-01", "error: --current-date '2026-1-01' must be a YYYY-MM-DD date\n"),
    ("--prior-date", "2025-02-29", "error: --prior-date '2025-02-29' is not a valid calendar date\n"),
])
def test_impossible_or_malformed_calendar_dates_are_one_error_line(tmp_path, monkeypatch, capsys, flag, value, expected):
    monkeypatch.chdir(tmp_path)
    code = rc.main(["--company", COMPANY, "--proposal", PROPOSAL, "--current-date", CURRENT_DATE, flag, value])
    captured = capsys.readouterr()
    assert code == 1 and captured.stdout == ""
    assert captured.stderr == expected


def test_prior_date_must_be_strictly_earlier_before_any_file_is_read(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = rc.main(["--company", COMPANY, "--proposal", PROPOSAL, "--current-date", CURRENT_DATE,
                    "--prior-date", CURRENT_DATE])
    captured = capsys.readouterr()
    assert code == 1 and captured.stdout == ""
    assert captured.stderr == "error: --prior-date must be strictly earlier than --current-date\n"


def test_missing_prior_pointer_fails_instead_of_guessing_earlier_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state())
    write_state(tmp_path, CURRENT_DATE, state())
    code, captured = invoke(tmp_path, capsys)
    assert code == 1 and captured.stdout == ""
    assert captured.stderr == "error: current state has no prior_review_date; pass --prior-date explicitly\n"


@pytest.mark.parametrize(("field", "wrong", "expected"), [
    ("company", "Other Co", "current state company does not match --company 'Synthetic Co'"),
    ("proposal", "Different Proposal", "current state proposal does not match --proposal 'Asset Facility'"),
])
def test_identity_mismatch_refuses_with_one_error_line(tmp_path, monkeypatch, capsys, field, wrong, expected):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state())
    write_state(tmp_path, CURRENT_DATE, state(**{field: wrong}))
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE)
    path = state_path(COMPANY, PROPOSAL, date_str=CURRENT_DATE)
    assert code == 1 and captured.stdout == ""
    assert captured.stderr == f"error: {expected}: {path}\n"


def test_newer_schema_is_read_without_modification_and_legacy_state_is_supported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prior_path = write_state(tmp_path, PRIOR_DATE, state(financials={"FY-Current": {"ebitda": 10}}))
    current_path = write_state(tmp_path, CURRENT_DATE, state(schema_version="99.99.99",
                                                             financials={"FY-Current": {"ebitda": 11}}))
    # Remove the version entirely from the prior, simulating a legacy state.
    legacy = state(financials={"FY-Current": {"ebitda": 10}})
    prior_path.write_text(json.dumps(legacy), encoding="utf-8")
    before = prior_path.read_bytes(), current_path.read_bytes()
    result = rc.compare_deal(COMPANY, PROPOSAL, CURRENT_DATE, prior_date=PRIOR_DATE)
    assert result["changes"][0]["delta"] == Decimal("1")
    assert (prior_path.read_bytes(), current_path.read_bytes()) == before


def test_main_json_mode_prints_exactly_one_json_object_and_no_extra_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state(financials={"FY-Current": {"ebitda": 10}}))
    write_state(tmp_path, CURRENT_DATE, state(financials={"FY-Current": {"ebitda": 11}}))
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE)
    assert code == 0 and captured.stderr == ""
    assert captured.stdout.count("{") > 1
    assert len(captured.stdout.strip().splitlines()) > 1
    decoded = json.loads(captured.stdout, parse_int=Decimal, parse_float=Decimal)
    assert decoded["summary"]["changed_paths"] == len(decoded["changes"])


def test_main_markdown_mode_renders_the_comparison(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state(financials={"FY-Current": {"ebitda": 10}}))
    write_state(tmp_path, CURRENT_DATE, state(financials={"FY-Current": {"ebitda": 11}}))
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE, "--format", "markdown")
    assert code == 0 and captured.stderr == ""
    assert captured.stdout.startswith("# Review comparison — Synthetic Co / Asset Facility")
    assert "| Item | Prior | Current | Change |" in captured.stdout
    assert "delta 1" in captured.stdout


def test_basis_change_note_is_reported_even_when_no_financial_paths_changed():
    result = report({"financials_source": "framework-computed", "financials_source_note": "same"},
                    {"financials_source": "analyst-supplied", "financials_source_note": "same"})
    assert result["basis_change"]["warning"] == rc.BASIS_WARNING
    assert result["changes"][0]["path"] == "financials_source"


def test_nonfinite_validator_walks_lists_and_nested_objects():
    assert rc._first_nonfinite({"ok": [1, {"value": Decimal("NaN")}]}) == "$.ok[1].value"
    assert rc._first_nonfinite({"ok": [1, {"value": Decimal("1.2")}]}) is None


def test_state_loader_reports_a_wrong_prior_identity_before_comparing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, {"company": "Wrong Co", "proposal": PROPOSAL})
    write_state(tmp_path, CURRENT_DATE, state())
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE)
    path = state_path(COMPANY, PROPOSAL, date_str=PRIOR_DATE)
    assert code == 1 and captured.stdout == ""
    assert captured.stderr == f"error: prior state company does not match --company 'Synthetic Co': {path}\n"


def test_no_version_key_and_unknown_newer_keys_do_not_get_written_or_dropped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old = write_state(tmp_path, PRIOR_DATE, state(unknown_old_extension={"x": 1}))
    new = write_state(tmp_path, CURRENT_DATE, state(schema_version="9.0.0",
                                                    future_extension={"unicode": "Łódź"}))
    before = old.read_bytes(), new.read_bytes()
    rc.compare_deal(COMPANY, PROPOSAL, CURRENT_DATE, prior_date=PRIOR_DATE)
    assert (old.read_bytes(), new.read_bytes()) == before


def test_corrupt_utf8_state_refuses_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, PRIOR_DATE, state())
    path = write_state(tmp_path, CURRENT_DATE, state())
    path.write_bytes(b"\xff\xfe not UTF-8")
    code, captured = invoke(tmp_path, capsys, "--prior-date", PRIOR_DATE)
    assert code == 1 and captured.stdout == ""
    relative_path = state_path(COMPANY, PROPOSAL, date_str=CURRENT_DATE)
    assert captured.stderr.startswith(f"error: current state is unreadable: {relative_path}: ")
    assert len(captured.stderr.strip().splitlines()) == 1
    assert "Traceback" not in captured.stderr


def test_markdown_restatement_section_follows_unchanged_by_label_categories():
    result = report(
        {"financials": {"FY-Current": {"raw": {"revenue": 100}, "ebitda": 20}}},
        {"financials": {"FY-1": {"raw": {"revenue": 101}, "ebitda": 20}}},
        rolled=True,
    )
    rendered = rc.render_markdown(result)
    assert rendered.index("## Unchanged by-label categories") < rendered.index("## Restatement check")
    assert "periods-rolled-one-year = true" in rendered


def test_recorded_prior_pointer_must_be_a_valid_earlier_calendar_date(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_state(tmp_path, CURRENT_DATE, state(prior_review_date="2026-02-30"))
    code, captured = invoke(tmp_path, capsys)
    assert code == 1 and captured.stdout == ""
    assert captured.stderr == "error: prior_review_date '2026-02-30' is not a valid calendar date\n"


def test_json_boolean_is_not_equal_to_numeric_one():
    result = report({"stress_assumptions": {"confirmed": True}},
                    {"stress_assumptions": {"confirmed": 1}})
    row = next(item for item in result["changes"] if item["path"] == "stress_assumptions.confirmed")
    assert row["change"] == "value_changed"
    assert "delta" not in row


def test_unidentified_or_malformed_covenant_records_fall_back_to_ordered_list_comparison():
    result = report(
        {"covenants": [{"metric": "lev", "threshold": 3}]},
        {"covenants": [{"metric": "lev", "threshold": 4}]},
    )
    assert [row["path"] for row in result["changes"]] == ["covenants"]
    assert result["changes"][0]["change"] == "value_changed"


def test_compare_states_rejects_nonfinite_imported_state_too():
    with pytest.raises(rc.ComparisonError, match=r"prior state contains a non-finite number"):
        rc.compare_states(
            COMPANY, PROPOSAL, PRIOR_DATE, CURRENT_DATE,
            state(financials={"FY-Current": {"ebitda": float("inf")}}), state(),
        )


def test_restatement_rolls_fy_minus_one_to_fy_minus_two_and_custom_labels_are_not_comparable():
    rolled = report(
        {"financials": {"FY-1": {"raw": {"revenue": 100}, "ebitda": 20}}},
        {"financials": {"FY-2": {"raw": {"revenue": 100}, "ebitda": 20}}},
        rolled=True,
    )
    assert rolled["restatement_check"]["periods"][0]["current_period"] == "FY-2"
    assert rolled["restatement_check"]["periods"][0]["status"] == "consistent"
    custom = report({"financials": {"FY-2024": {"ebitda": 20}}},
                    {"financials": {"FY-2024": {"ebitda": 20}}}, rolled=True)
    assert custom["restatement_check"]["periods"][0]["status"] == "not_comparable"


def test_restatement_falls_back_to_multi_period_raw_values_when_period_financial_raw_is_absent():
    result = report(
        {"financials": {"FY-Current": {"ebitda": 20}},
         "multi_period_financials": {"FY-Current": {"revenue": 100}}},
        {"financials": {"FY-1": {"ebitda": 20}},
         "multi_period_financials": {"FY-1": {"revenue": 100}}},
        rolled=True,
    )
    pair = result["restatement_check"]["periods"][0]
    assert pair["status"] == "consistent"
    assert pair["prior_values"]["raw_line_items"] == {"revenue": 100}
    assert pair["prior_values"]["raw_store"] == "multi_period_financials.FY-Current"


def test_restating_check_with_no_historical_periods_still_records_the_confirmation():
    result = report({}, {}, rolled=True)
    assert result["restatement_check"]["periods_rolled_one_year_confirmed"] is True
    assert result["restatement_check"]["periods"] == []


def test_numeric_delta_remains_exact_beyond_default_decimal_context_precision():
    prior = Decimal("123456789012345678901234567890.0000")
    current = Decimal("123456789012345678901234567890.0001")
    result = report({"financials": {"FY-Current": {"revenue": prior}}},
                    {"financials": {"FY-Current": {"revenue": current}}})
    row = next(item for item in result["changes"] if item["path"] == "financials.FY-Current.revenue")
    assert row["delta"] == Decimal("0.0001")

