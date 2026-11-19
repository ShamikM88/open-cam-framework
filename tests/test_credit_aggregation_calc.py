"""Section A v4.1 and the two-point refinement in issue #472, synthetic only."""

import copy
import json

import pytest

import credit_aggregation as ca
from test_credit_aggregation import (
    COMPANY, DATE, HOLDCO_ID, OPCO_ID, PROPOSAL, _entity_record, _period_request,
    _read_full, _seed_deal, _state_file, _write_request,
)

P = "FY-Current"
B = "@borrower"
H = HOLDCO_ID
O = OPCO_ID
END = "2025-12-31"
REF = "Synthetic accounts, note 7"


def attestation(controller=None, **changes):
    values = dict(
        accounts_type="audited", standalone=True, controlled=True, controller=controller,
        ownership_pct=100, control_from="2024-01-01", control_to=None, status="neither",
        period_start="2025-01-01", period_end=END, period_months=12, framework="FRS 102",
        currency="GBP", scale=1000, debt_mapping_uniform=True, exceptions=[],
        ebitda_intragroup="none", schedule=[], unoffset_other_income_amount=0,
        no_intragroup_stock_or_asset_profit=True, cash_no_group_balance=True,
        cash_pool_in_overdraft=0, restricted_cash_amount=0, no_intragroup_interest=True,
        income_from_left_out={}, charges_to_left_out={},
    )
    values.update(changes)
    values["evidence"] = dict.fromkeys(values, REF)
    return values


def side(entity, counterpart, line, direction, **changes):
    return dict(loan_id="L1", legal_entity=entity, counterparty_entity_id=counterpart, line_key=line,
                direction=direction, amount=20, evidence={"document": "Synthetic accounts", "page_or_note": "7"},
                confirmed=True, of_which_owed_to_group=30, separate_loan_confirmed=False) | changes


def journal(**changes):
    return dict(journal_id="J1", type="loan_principal", loan_id="L1", lender=H, debtor=B,
                lender_line="other_fixed_assets", debtor_line="current_debt", amount=20,
                maturity_mismatch_confirmed=True, confirmed=True) | changes


def seed(tmp_path):
    holder = side(f"Synthetic {H} Ltd", B, "other_fixed_assets", "receivable", period=P)
    subsidiary = _entity_record(O, raw={P: {"revenue": 300, "cost_of_sales": 100, "cash": 20,
                                         "long_term_debt": 80, "interest_paid": 8}})
    parent = _entity_record(H, role="parent", intercompany_lines=[holder], raw={
        P: {"revenue": 180, "cost_of_sales": 80, "cash": 30, "long_term_debt": 100,
            "other_fixed_assets": 100, "interest_paid": 12}})
    _seed_deal(tmp_path, entity_records={H: parent, O: subsidiary})
    request = {
        **_period_request(participants=[O, H, B]),
        "attestations": {B: attestation(H, status="obligor"), H: attestation(),
                         O: attestation(B, status="guarantor", ownership_pct=80)},
        "declarations": [side(COMPANY, H, "current_debt", "payable")],
        "journals": [journal()],
        "left_out": {}, "skipped_chain_entities": {}, "participant_guarantees": [],
    }
    return {"periods": {P: request}}


def calculate(tmp_path, file):
    return ca.calculate_credit_aggregation(
        COMPANY, PROPOSAL, _write_request(tmp_path, file, name="synthetic-journals.json"), base_dir=str(tmp_path))


def run(tmp_path, change=None):
    file = seed(tmp_path)
    if change:
        change(file["periods"][P])
    calculate(tmp_path, file)
    return _read_full(tmp_path)["credit_aggregation"][P]["computed"]


def change_att(field, value, entity=O):
    return lambda p: p["attestations"][entity].__setitem__(field, value)


def change_journal(**changes):
    return lambda p: p["journals"][0].update(changes)


def change_side(**changes):
    return lambda p: p["declarations"][0].update(changes)


def second_journal(p, *, amount=20, separate=False, cap=30):
    p["journals"].append(journal(journal_id="J2", loan_id="L2", amount=amount))
    p["declarations"].append(dict(p["declarations"][0], loan_id="L2", amount=amount,
                                  of_which_owed_to_group=cap, separate_loan_confirmed=separate))


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda p: p["attestations"].pop(O), "attestations"),
    (change_att("standalone", False), "standalone"),
    (change_att("controlled", False), "control"),
    (change_att("control_from", "2026-01-01"), "control"),
    (change_att("control_to", "2025-10-01"), "control"),
    (change_att("period_start", "2025-02-01"), "period_start"),
    (change_att("period_end", "2025-11-30"), "period_end"),
    (change_att("period_months", 10), "period_months"),
    (change_att("framework", "IFRS"), "framework"),
    (change_att("currency", "USD"), "currency"),
    (change_att("scale", 1), "scale"),
    (change_att("controller", "synthetic-missing"), "chain"),
    (change_att("controller", O), "chain"),
    (change_att("evidence", {}), "evidence"),
    (lambda p: p["declarations"].clear(), "borrower"),
    (lambda p: p["declarations"][0].pop("loan_id"), "loan_id"),
    (lambda p: p["declarations"][0].pop("evidence"), "evidence"),
    (lambda p: p["declarations"].append(p["declarations"][0]), "loan_id"),
    (lambda p: p["journals"].append(dict(p["journals"][0], journal_id="J2")), "loan_id"),
    (lambda p: p["journals"].append(dict(p["journals"][0], loan_id="L2")), "journal_id"),
    (change_journal(confirmed=False), "confirmed"),
    (change_side(confirmed=False), "confirmed"),
    (change_side(of_which_owed_to_group=31), "cap"),
    (change_side(of_which_owed_to_group=19), "cap"),
    (change_journal(type="loan_interest"), "loan_principal"),
    (change_journal(type="trade"), "loan_principal"),
    (change_journal(amount=True), "amount"),
    (change_journal(amount=float("inf")), "amount"),
    (change_journal(amount=float("nan")), "amount"),
    (change_journal(amount=0), "amount"),
    (change_journal(amount=-1), "amount"),
    (change_journal(unknown=1), "unknown"),
    (change_journal(lender="synthetic-not-a-participant"), "participant"),
])
def test_integrity_refusals_leave_state_byte_identical(tmp_path, mutate, message):
    file = seed(tmp_path)
    mutate(file["periods"][P])
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match=message):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


def test_forward_period_refuses_the_entire_file(tmp_path):
    file = seed(tmp_path)
    file["periods"]["FY+1"] = copy.deepcopy(file["periods"][P])
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match="historical"):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


def test_a_loan_id_cannot_identify_two_different_party_pairs(tmp_path):
    file = seed(tmp_path)
    file["periods"][P]["declarations"][0]["counterparty_entity_id"] = O
    path = _state_file(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ca.AggregationError, match="loan_id.*different party pairs"):
        calculate(tmp_path, file)
    assert path.read_bytes() == before


def test_rounding_a_journal_amount_cannot_create_an_exact_match(tmp_path):
    file = seed(tmp_path)
    path = tmp_path / "precision.json"
    path.write_text(json.dumps(file).replace('"amount": 20', '"amount": 20.000000000000001', 1), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match="precision"):
        ca.calculate_credit_aggregation(COMPANY, PROPOSAL, str(path), base_dir=str(tmp_path))
    assert _state_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize("number_text", ["1e9999999999999999999999999", "1" * 5000])
def test_unrepresentable_json_number_is_a_named_refusal(tmp_path, number_text):
    path = tmp_path / "precision.json"
    path.write_text(json.dumps(seed(tmp_path)).replace('"amount": 20', f'"amount": {number_text}', 1),
                    encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match="precision|UTF-8 JSON"):
        ca.calculate_credit_aggregation(COMPANY, PROPOSAL, str(path), base_dir=str(tmp_path))
    assert _state_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize(("separate", "amount", "cap", "error"), [
    (False, 20, 30, "separate"), (True, 20, 30, "cap"), (True, 5, 29, "cap"),
    (True, 5, 30, None),
])
def test_two_loans_use_one_participant_line_cap(tmp_path, separate, amount, cap, error):
    file = seed(tmp_path)
    second_journal(file["periods"][P], amount=amount, separate=separate, cap=cap)
    state = _read_full(tmp_path)
    lender_lines = state["entity_records"][H]["intercompany_lines"]
    lender_lines[0]["separate_loan_confirmed"] = separate
    lender_lines[0]["of_which_owed_to_group"] = 80
    lender_lines.append(dict(lender_lines[0], loan_id="L2", amount=amount))
    borrower = file["periods"][P]["declarations"][0]
    borrower["separate_loan_confirmed"] = separate
    state["entity_records"][O]["multi_period_financials"][P]["current_debt"] = 100
    path = _state_file(tmp_path)
    path.write_text(json.dumps(state), encoding="utf-8")
    if error:
        before = path.read_bytes()
        with pytest.raises(ca.AggregationError, match=error):
            calculate(tmp_path, file)
        assert path.read_bytes() == before
    else:
        calculate(tmp_path, file)
        assert _read_full(tmp_path)["credit_aggregation"][P]["computed"]["adjusted_raw"]["current_debt"] == 105


@pytest.mark.parametrize(("side_changes", "journal_changes", "reason"), [
    ({"amount": 21}, {}, "amount"),
    ({"loan_id": "L2"}, {}, "loan_id"),
    ({}, {"maturity_mismatch_confirmed": False}, "maturity"),
    ({"line_key": "long_term_debt"}, {}, "line"),
    ({"line_key": "loan_notes"}, {"debtor_line": "loan_notes"}, "line"),
    ({"line_key": "overdraft"}, {"debtor_line": "overdraft"}, "line"),
    ({"line_key": "other_long_term_liabilities"}, {"debtor_line": "other_long_term_liabilities"}, "line"),
])
def test_matching_refusals_are_recorded_and_never_remove_debt(tmp_path, side_changes, journal_changes, reason):
    file = seed(tmp_path)
    p = file["periods"][P]
    p["declarations"][0].update(side_changes)
    p["journals"][0].update(journal_changes)
    if side_changes.get("line_key") in {"loan_notes", "overdraft", "other_long_term_liabilities"}:
        state = _read_full(tmp_path)
        state["financials"][P]["raw"][side_changes["line_key"]] = 30
        _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    calculate(tmp_path, file)
    c = _read_full(tmp_path)["credit_aggregation"][P]["computed"]
    assert c["adjusted_raw"]["current_debt"] == 30
    assert c["adjusted_raw"]["other_fixed_assets"] == 100
    assert c["eliminations_applied"] == []
    assert c["refused_journals"][0]["journal_id"] == "J1"
    assert reason in c["refused_journals"][0]["reason"]


@pytest.mark.parametrize("change", [
    change_att("ebitda_intragroup", "not_offset"),
    lambda p: p["attestations"][O].pop("ebitda_intragroup"),
    change_att("ebitda_intragroup", "offset"),
    change_att("debt_mapping_uniform", False),
    change_att("exceptions", ["Synthetic policy difference"]),
    change_att("no_intragroup_stock_or_asset_profit", False),
    change_att("control_from", "2025-04-01", B),
    lambda p: [a.update(period_months=9, period_start="2025-04-01") for a in p["attestations"].values()],
])
def test_ebitda_gate_keeps_only_debt_and_explains_withheld_metrics(tmp_path, change):
    c = run(tmp_path, change)
    assert {k: v for k, v in c["financials"].items() if v is not None} == {"total_debt": 390}
    assert all(v is None for v in c["ratios"].values())
    assert c["withheld"]["ebitda"] and c["withheld"]["gross_leverage"]
    assert all(row["ebitda"] is None for row in c["participant_table"])


def test_clean_case_recomputes_from_adjusted_raw_not_summed_ratios(tmp_path):
    c = run(tmp_path)
    assert {key: c["adjusted_raw"][key] for key in ("current_debt", "other_fixed_assets", "long_term_debt",
                                                   "cash", "revenue", "cost_of_sales", "admin_expenses")} == {
        "current_debt": 10, "other_fixed_assets": 80, "long_term_debt": 380,
        "cash": 100, "revenue": 1380, "cost_of_sales": 480, "admin_expenses": 100,
    }
    assert {k: v for k, v in c["financials"].items() if v is not None} == {"total_debt": 390, "ebitda": 800}
    assert {k: v for k, v in c["ratios"].items() if v is not None} == {
        "gross_leverage": 390 / 800, "net_debt_to_ebitda": 290 / 800,
        "ebit_interest_cover": 40, "ebitda_interest_cover": 40,
    }
    assert all(c["withheld"][m] for m in ("operating_profit", "tangible_net_worth", "dscr", "fcf"))
    assert c["eliminations_applied"][0]["maturity_mismatch"] is True
    assert c["eliminations_applied"][0]["debtor_before"] == 30
    assert c["eliminations_applied"][0]["debtor_after"] == 10
    assert c["debt_lines"]["current_debt"] == {"before": 30, "after": 10}
    assert c["debt_before"] == 410
    assert {r["participant"]: r["total_debt"] for r in c["participant_table"]} == {B: 210, H: 100, O: 80}


def test_cash_and_interest_are_independent_from_gross_leverage(tmp_path):
    c = run(tmp_path, lambda p: p["attestations"][O].pop("cash_no_group_balance"))
    assert c["ratios"]["net_debt_to_ebitda"] is None and c["ratios"]["gross_leverage"] == 390 / 800
    c = run(tmp_path, lambda p: p["attestations"][O].update(restricted_cash_amount=15, no_intragroup_interest=False))
    assert c["cash"] == {"total": 100, "restricted": 15, "unrestricted": 85}
    assert c["ratios"]["net_debt_to_ebitda"] == 305 / 800
    assert c["ratios"]["ebitda_interest_cover"] is None
    assert c["ratios"]["ebit_interest_cover"] is None


@pytest.mark.parametrize(("mode", "raw_income", "stock", "expected"), [
    ("offset", 40, True, 840),
    ("offset_other_income_only", 40, True, 800),
    ("offset_other_income_only", 5, True, None),
    ("offset_other_income_only", 40, False, None),
])
def test_ebitda_schedule_and_other_income_variant(tmp_path, mode, raw_income, stock, expected):
    file = seed(tmp_path)
    p = file["periods"][P]
    state = _read_full(tmp_path)
    state["financials"][P]["raw"]["other_income"] = raw_income
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    p["attestations"][B].update(ebitda_intragroup=mode, no_intragroup_stock_or_asset_profit=stock,
                                 schedule=[{"counterparty": O, "line": "other_income", "amount": 10}],
                                 unoffset_other_income_amount=10 if mode.endswith("only") else 0)
    if mode == "offset":
        p["attestations"][O].update(ebitda_intragroup=mode,
                                    schedule=[{"counterparty": B, "line": "cost_of_sales", "amount": 10}])
    calculate(tmp_path, file)
    c = _read_full(tmp_path)["credit_aggregation"][P]["computed"]
    if mode == "offset":
        assert c["financials"]["ebitda"] == expected
    else:
        assert c["financials"]["ebitda"] is None
        assert c["financials"].get("ebitda_excluding_other_income") == expected
        if expected:
            assert c["ratios"]["gross_leverage_excluding_other_income"] == 390 / expected
            assert c["ratios"]["gross_leverage"] is None
            assert "all other income" in c["metric_label"]


def test_variant_requires_an_explicit_unoffset_amount_even_if_offsets_reconcile(tmp_path):
    def change(p):
        p["attestations"][B].update(ebitda_intragroup="offset_other_income_only",
                                    schedule=[{"counterparty": O, "line": "revenue", "amount": 10}])
        p["attestations"][B].pop("unoffset_other_income_amount")
        p["attestations"][O].update(ebitda_intragroup="offset",
                                    schedule=[{"counterparty": B, "line": "cost_of_sales", "amount": 10}])
    computed = run(tmp_path, change)
    assert all(value is None for value in computed["ratios"].values())
    assert "unoffset" in computed["withheld"]["ebitda"]


@pytest.mark.parametrize(("coverage", "amount", "reason"), [
    ([("D1", 60), ("D1", 60)], 450, None),
    ([("D1", 60), ("D2", 40)], 490, None),
    ([("D1", 60), ("D1", 40)], "unknown", "conflict"),
    ([("D1", 60), (None, 40)], "unknown", "overlap"),
    ([("D1", "unknown")], "unknown", "unknown"),
])
def test_debt_memo_counts_attested_external_principal_once(tmp_path, coverage, amount, reason):
    def change(p):
        p["left_out"]["synthetic-left-out"] = {
            "reason": "Outside synthetic scope", "external_debt": 100,
            "support": [{"participant": (B, H)[i], "type": ("guarantee", "security")[i],
                         "debt_id": debt, "covered_principal": value, "evidence": REF}
                        for i, (debt, value) in enumerate(coverage)],
        }
    c = run(tmp_path, change)
    assert c["debt_memo"]["value"] == amount
    assert (c["debt_memo"]["reason"] is None) if reason is None else reason in c["debt_memo"]["reason"]
    assert c["financials"]["total_debt"] == 390 and c["ratios"]["gross_leverage"] == 390 / 800


@pytest.mark.parametrize(("second_amount", "expected"), [(60, 450), (50, "unknown")])
def test_one_attested_debt_id_is_not_counted_twice_across_left_out_entities(tmp_path, second_amount, expected):
    def change(p):
        for entity, participant, amount in (("joint-debtor-a", B, 60), ("joint-debtor-b", H, second_amount)):
            p["left_out"][entity] = {
                "reason": "Outside synthetic scope", "external_debt": 100,
                "support": [{"participant": participant, "type": "guarantee", "debt_id": "same-joint-debt",
                             "covered_principal": amount, "evidence": REF}],
            }
    assert run(tmp_path, change)["debt_memo"]["value"] == expected


def test_skipped_chain_needs_attested_debt_and_is_output_separately(tmp_path):
    def change(p):
        p["attestations"][B]["controller"] = "synthetic-skipped"
        p["skipped_chain_entities"]["synthetic-skipped"] = {
            "controller": H, "control_from": "2024-01-01", "control_to": None,
            "external_debt": 50, "evidence": REF,
        }
    c = run(tmp_path, change)
    assert c["skipped_chain_entities"]["synthetic-skipped"]["external_debt"] == 50
    assert c["financials"]["total_debt"] == 390
