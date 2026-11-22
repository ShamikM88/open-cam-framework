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
    (change_att("period_start", "2025-02-30"), "ISO date"),
    (change_att("period_end", "2025-11-30"), "period_end"),
    (change_att("period_start", "2026-01-01"), "period_start"),
    (change_att("period_months", 10), "period_months"),
    (change_att("period_months", True), "positive integer"),
    (change_att("ownership_pct", 101), "cannot exceed 100"),
    (change_att("framework", "IFRS"), "framework"),
    (change_att("currency", "USD"), "currency"),
    (change_att("scale", 1), "scale"),
    (change_att("accounts_type", "mixed"), "accounts_type has an invalid value"),
    (change_att("exceptions", [" "]), "exceptions must be non-blank"),
    (change_att("controller", "synthetic-missing"), "chain"),
    (change_att("controller", O), "chain"),
    (change_att("controller", None), "one selected root"),
    (change_att("evidence", {}), "evidence"),
    (change_att("restricted_cash_amount", 21), "exceeds recorded cash"),
    (change_att("income_from_left_out", {"other_income": True}), "finite number"),
    (change_att("schedule", [{"counterparty": O, "line": "revenue", "amount": 1}]), "invalid participant"),
    (lambda p: p.update(skipped_chain_entities=[]), "skipped_chain"),
    (lambda p: p["skipped_chain_entities"].update({O: {}}), "distinct non-participant"),
    (lambda p: p.update(left_out={B: {}}), "left_out"),
    (lambda p: p["left_out"].update({O + "-left-out": {"reason": [], "external_debt": 0, "support": []}}),
     "left-out reason must be non-blank"),
    (lambda p: p.update(journals={}), "must be a list"),
    (lambda p: p["declarations"].append("bad declaration"), "must be an object"),
    (lambda p: p["declarations"].clear(), "borrower"),
    (lambda p: p["declarations"][0].pop("loan_id"), "loan_id"),
    (lambda p: p["declarations"][0].pop("evidence"), "evidence"),
    (lambda p: p["declarations"].append(p["declarations"][0]), "loan_id"),
    (lambda p: p["journals"].append(dict(p["journals"][0], journal_id="J2")), "loan_id"),
    (lambda p: p["journals"].append(dict(p["journals"][0], loan_id="L2")), "journal_id"),
    (change_journal(confirmed=False), "confirmed"),
    (change_side(confirmed=False), "confirmed"),
    (change_side(legal_entity="Synthetic Wrong Borrower"), "does not match"),
    (change_side(legal_entity=" "), "non-blank"),
    (change_side(counterparty_entity_id=B), "different participant"),
    (change_side(direction="asset"), "direction has an invalid value"),
    (change_side(amount=0), "must be positive"),
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


def _file_text_with_journal_amount(file, amount_text):
    """The request as JSON text whose first journal amount is written exactly as `amount_text` (not re-rounded)."""
    file["periods"][P]["journals"][0]["amount"] = "__JOURNAL_AMOUNT__"
    return json.dumps(file).replace('"__JOURNAL_AMOUNT__"', amount_text)


def test_rounding_a_journal_amount_cannot_create_an_exact_match(tmp_path):
    file = seed(tmp_path)
    path = tmp_path / "precision.json"
    path.write_text(_file_text_with_journal_amount(file, "20.000000000000001"), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match="precision"):
        ca.calculate_credit_aggregation(COMPANY, PROPOSAL, str(path), base_dir=str(tmp_path))
    assert _state_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize("number_text", ["1e9999999999999999999999999", "1" * 5000], ids=["exponent", "integer"])
def test_unrepresentable_json_number_is_a_named_refusal(tmp_path, number_text):
    path = tmp_path / "precision.json"
    path.write_text(_file_text_with_journal_amount(seed(tmp_path), number_text), encoding="utf-8")
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
    # Maintainer decision on #472: group net debt is a printed memo whenever the cash gate passes, even with EBITDA
    # withheld (total debt 390 less unrestricted cash 100).
    assert c["group_net_debt"] == 290


def test_group_net_debt_deducts_restricted_cash_and_survives_a_failed_ebitda_gate(tmp_path):
    def change(p):
        change_att("ebitda_intragroup", "not_offset")(p)
        change_att("restricted_cash_amount", 10, entity=H)(p)

    c = run(tmp_path, change)
    assert c["cash"] == {"total": 100, "restricted": 10, "unrestricted": 90}
    assert c["group_net_debt"] == 300
    assert c["ratios"]["net_debt_to_ebitda"] is None and c["withheld"]["net_debt_to_ebitda"]


def test_group_net_debt_is_withheld_when_the_cash_gate_fails(tmp_path):
    c = run(tmp_path, change_att("cash_no_group_balance", False))
    assert c["group_net_debt"] is None and c["cash"]["unrestricted"] is None


def test_a_matching_refused_journal_still_counts_against_the_line_cap(tmp_path):
    # Maintainer decision on #472 (CU628-2): every presented journal counts against the declared cap, so listing
    # more principal than the participant declared is an integrity refusal even if a journal is refused on matching.
    file = seed(tmp_path)
    file["periods"][P]["journals"].append(journal(journal_id="J2", loan_id="L-undeclared", debtor=O, amount=20))
    path = _state_file(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ca.AggregationError, match="cap"):
        calculate(tmp_path, file)

    assert path.read_bytes() == before


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


def test_nonpositive_denominators_are_not_numbers(tmp_path):
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    raw = state["financials"][P]["raw"]
    raw.update(revenue=100, interest_paid=-20)
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    calculate(tmp_path, file)
    c = _read_full(tmp_path)["credit_aggregation"][P]["computed"]
    assert c["financials"]["ebitda"] == 0
    for metric in ("gross_leverage", "net_debt_to_ebitda", "ebit_interest_cover", "ebitda_interest_cover"):
        assert c["ratios"][metric] is None and "zero or negative" in c["withheld"][metric]


@pytest.mark.parametrize("income_line", ["revenue", "other_income"])
def test_schedule_must_reconcile_each_counterparty_and_amount(tmp_path, income_line):
    def change(p):
        p["attestations"][B].update(
            ebitda_intragroup="offset" if income_line == "revenue" else "offset_other_income_only",
            unoffset_other_income_amount=0,
            schedule=[{"counterparty": O, "line": income_line, "amount": 10}],
        )
        p["attestations"][O].update(ebitda_intragroup="offset",
                                    schedule=[{"counterparty": B, "line": "cost_of_sales", "amount": 11}])
    c = run(tmp_path, change)
    assert c["financials"]["ebitda"] is None and "reconcile" in c["withheld"]["ebitda"]


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
                         "debt_id": debt, "covered_principal": value, "evidence": REF,
                         **({"overlap_confirmed": True} if debt is not None and [d for d, _ in coverage].count(debt) > 1
                            else {})}
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
                             "covered_principal": amount, "evidence": REF,
                             "overlap_confirmed": True}],
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


def test_participant_guarantees_are_disclosure_and_not_debt(tmp_path):
    row = {"provider": B, "beneficiary": O, "amount": 25, "evidence": REF}
    computed = run(tmp_path, lambda p: p["participant_guarantees"].append(row))
    assert computed["participant_guarantees"] == [row]
    assert computed["debt_memo"]["value"] == computed["financials"]["total_debt"] == 390

    row["beneficiary"] = B
    before = _state_file(tmp_path).read_bytes()
    file = {"periods": {P: copy.deepcopy(_read_full(tmp_path)["credit_aggregation"][P]["input"])}}
    file["periods"][P]["participant_guarantees"] = [row]
    with pytest.raises(ca.AggregationError, match="two different participants"):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


def test_supported_principal_in_conflict_with_attested_external_debt_is_unknown(tmp_path):
    def change(p):
        p["left_out"]["synthetic-left-out"] = {
            "reason": "Not selected", "external_debt": 10,
            "support": [{"participant": B, "type": "guarantee", "debt_id": "D1",
                         "covered_principal": 30, "evidence": REF}],
        }
    computed = run(tmp_path, change)
    assert computed["debt_memo"]["value"] == "unknown"
    assert "conflicts with external debt" in computed["debt_memo"]["reason"]
    assert computed["financials"]["total_debt"] == 390


def test_support_by_an_unselected_entity_refuses_without_writing(tmp_path):
    file = seed(tmp_path)
    file["periods"][P]["left_out"]["synthetic-left-out"] = {
        "reason": "Not selected", "external_debt": "unknown",
        "support": [{"participant": "synthetic-nonparticipant", "type": "guarantee", "debt_id": "D1",
                     "covered_principal": 30, "evidence": REF}],
    }
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match="support must be provided by a participant"):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


def test_equal_partial_support_on_one_debt_is_unknown_unless_the_overlap_is_attested(tmp_path):
    # Maintainer decision on #472 (PX628-108 / CG628-8): equal amounts under one debt_id could be two disjoint slices.
    def change(p):
        p["left_out"]["synthetic-left-out"] = {
            "reason": "Outside synthetic scope", "external_debt": 100,
            "support": [{"participant": participant, "type": "guarantee", "debt_id": "D1",
                         "covered_principal": 40, "evidence": REF} for participant in (B, H)],
        }
    memo = run(tmp_path, change)["debt_memo"]
    assert memo["value"] == "unknown" and "overlap of covered principal on debt D1 is not attested" in memo["reason"]


@pytest.mark.parametrize("start", ["2025-10-01", "2025-07-01", "2024-12-24"],
                         ids=["three-months", "six-months", "just-over-53-weeks"])
def test_a_declared_length_that_contradicts_the_dates_is_an_integrity_refusal(tmp_path, start):
    # Maintainer decision on #472 (PX628-102 / CG628-2): twelve months declared over a shorter span is refused.
    file = seed(tmp_path)
    for attestation in file["periods"][P]["attestations"].values():
        attestation["period_start"] = start
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match=r"period_months \(12\) does not match the declared dates"):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize(("start", "months"), [
    ("2025-01-02", 12),     # 364 days: a 52-week year ending on the common date
    ("2024-12-26", 12),     # 371 days: a 53-week year
    ("2025-01-01", 12), ("2025-04-01", 9),
], ids=["52-weeks", "53-weeks", "calendar-year", "nine-months"])
def test_a_52_or_53_week_year_and_whole_months_pass_the_length_check(tmp_path, start, months):
    c = run(tmp_path, lambda p: [a.update(period_start=start, period_months=months) for a in p["attestations"].values()])
    assert c["financials"]["total_debt"] == 390


def test_a_schedule_larger_than_the_recorded_line_withholds_ebitda_with_a_named_reason(tmp_path):
    # PX628-103 / CG628-4: a reciprocal schedule cannot offset an amount absent from the accounts.
    def change(p):
        p["attestations"][B].update(ebitda_intragroup="offset",
                                    schedule=[{"counterparty": O, "line": "revenue", "amount": 10 ** 7}])
        p["attestations"][O].update(ebitda_intragroup="offset",
                                    schedule=[{"counterparty": B, "line": "cost_of_sales", "amount": 10 ** 7}])
    c = run(tmp_path, change)
    assert c["financials"]["ebitda"] is None and all(v is None for v in c["ratios"].values())
    assert "exceeds the" in c["withheld"]["ebitda"] and "recorded in its accounts" in c["withheld"]["ebitda"]


def test_negative_cash_for_one_participant_withholds_the_cash_outputs_even_when_the_total_is_positive(tmp_path):
    # PX628-106 / CG628-7.
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    state["entity_records"][O]["multi_period_financials"][P]["cash"] = -10
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    calculate(tmp_path, file)
    c = _read_full(tmp_path)["credit_aggregation"][P]["computed"]
    assert c["cash"] == {"total": None, "restricted": 0, "unrestricted": None}
    assert c["group_net_debt"] is None and c["ratios"]["net_debt_to_ebitda"] is None
    assert "negative" in c["withheld"]["net_debt_to_ebitda"] and O in c["withheld"]["net_debt_to_ebitda"]


def test_exact_zero_ebitda_stays_zero_and_its_ratios_are_not_available(tmp_path):
    # PX628-104 / CG628-5 (maintainer decision on #472): 0.1 - 0.3 + 0.2 is exactly zero, not 2.8e-17.
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    pnl = {"revenue": 0, "cost_of_sales": 0, "admin_expenses": 0, "other_income": 0, "depreciation": 0,
           "amortisation": 0, "exceptional_costs": 0}
    state["financials"][P]["raw"].update(pnl | {"revenue": 0.1})
    state["entity_records"][O]["multi_period_financials"][P].update(pnl | {"cost_of_sales": 0.3})
    state["entity_records"][H]["multi_period_financials"][P].update(pnl | {"other_income": 0.2})
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    calculate(tmp_path, file)
    c = _read_full(tmp_path)["credit_aggregation"][P]["computed"]
    assert c["financials"]["ebitda"] == 0
    assert c["ratios"]["gross_leverage"] is None and "zero or negative" in c["withheld"]["gross_leverage"]
    assert c["financials"]["total_debt"] == 390


def test_the_calculation_refuses_an_amount_too_large_to_evaluate_exactly(tmp_path):
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    state["financials"][P]["raw"]["revenue"] = 1e305
    state["financials"][P]["raw"]["cost_of_sales"] = 0.1
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match="too large or too precise"):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


def test_a_legacy_state_without_a_company_is_current_straight_after_the_write(tmp_path):
    # PX628-105 / CG628-6: the identity fingerprinted is the one the write stamps.
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    state.pop("company", None)
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    calculate(tmp_path, file)
    after = _read_full(tmp_path)
    assert after["company"] == COMPANY
    assert ca.aggregation_period_status(after, P)["status"] == "current"


def test_cancellation_across_participants_is_exact_zero_ebitda(tmp_path):
    # PX628-109 / PX628-113 / CG628-10 / Cubic: the aggregate of a line is exact, so 0.08 - 0.08 + 1e-17 - 1e-17 is 0.
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    pnl = {"revenue": 0, "cost_of_sales": 0, "admin_expenses": 0, "other_income": 0, "depreciation": 0,
           "amortisation": 0, "exceptional_costs": 0}
    state["financials"][P]["raw"].update(pnl | {"revenue": 0.08, "cost_of_sales": 0.08})
    state["entity_records"][O]["multi_period_financials"][P].update(pnl | {"revenue": 1e-17, "admin_expenses": 1e-17})
    state["entity_records"][H]["multi_period_financials"][P].update(pnl)
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    calculate(tmp_path, file)
    c = _read_full(tmp_path)["credit_aggregation"][P]["computed"]
    assert c["financials"]["ebitda"] == 0
    assert c["ratios"]["gross_leverage"] is None and "zero or negative" in c["withheld"]["gross_leverage"]


def test_a_large_balance_does_not_erase_a_small_one_on_the_same_line(tmp_path):
    # PX628-109 (second part) / Cubic: 900719925474099 plus 0.01 used to lose the 0.01 in a float before evaluation.
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    pnl = {"revenue": 0, "cost_of_sales": 0, "admin_expenses": 0, "other_income": 0, "depreciation": 0,
           "amortisation": 0, "exceptional_costs": 0}
    state["financials"][P]["raw"].update(pnl | {"revenue": 900719925474099, "cost_of_sales": 900719925474099})
    state["entity_records"][O]["multi_period_financials"][P].update(pnl | {"revenue": 0.01})
    state["entity_records"][H]["multi_period_financials"][P].update(pnl)
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    calculate(tmp_path, file)
    c = _read_full(tmp_path)["credit_aggregation"][P]["computed"]
    assert c["financials"]["ebitda"] == 0.01 and c["ratios"]["gross_leverage"] is not None


def test_an_unknown_raw_line_name_is_refused_naming_participant_and_line(tmp_path):
    # PX628-110 / CG628-11: long_term_debt_typo must not shrink total debt silently.
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    lines = state["entity_records"][O]["multi_period_financials"][P]
    lines["long_term_debt_typo"] = lines.pop("long_term_debt")
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match=rf"{O}: unknown raw line-item name\(s\) 'long_term_debt_typo'"):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


def test_a_second_journal_cannot_reuse_an_already_claimed_borrower_declaration(tmp_path):
    # PX628-114: one borrower declaration backs one loan; a sibling journal with neither side is an integrity refusal.
    file = seed(tmp_path)
    file["periods"][P]["journals"].append(journal(journal_id="J2", loan_id="L2", amount=5))
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match="J2 has no unclaimed deal-borrower declaration"):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


def test_a_registered_basis_that_contradicts_the_declared_framework_is_refused(tmp_path):
    # PX628-115: a registered IFRS source with a declared FRS 102 framework is not "uniform".
    file = seed(tmp_path)
    state = _read_full(tmp_path)
    state["entity_records"][O]["source"]["accounting_basis"] = "IFRS"
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match="contradicts the declared framework"):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize("gate_key", ["ebitda_intragroup", "cash_no_group_balance", "no_intragroup_interest"])
def test_every_supplied_gate_answer_needs_its_evidence_reference(tmp_path, gate_key):
    # CG628-9 was checked and is not a defect: the evidence map must name every supplied answer, gate keys included.
    file = seed(tmp_path)
    file["periods"][P]["attestations"][O]["evidence"].pop(gate_key)
    before = _state_file(tmp_path).read_bytes()
    with pytest.raises(ca.AggregationError, match=rf"evidence is missing: {gate_key}"):
        calculate(tmp_path, file)
    assert _state_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize("order", [(True, False), (False, True)], ids=["confirmed-first", "confirmed-last"])
def test_the_overlap_result_does_not_depend_on_the_order_of_the_rows(tmp_path, order):
    # PX628-111: one row attesting the overlap is not enough; every statement about the debt must, in any order.
    def change(p):
        p["left_out"]["synthetic-left-out"] = {
            "reason": "Outside synthetic scope", "external_debt": 100,
            "support": [{"participant": participant, "type": "guarantee", "debt_id": "D1", "covered_principal": 40,
                         "evidence": REF, "overlap_confirmed": flag} for participant, flag in zip((B, H), order, strict=True)],
        }
    memo = run(tmp_path, change)["debt_memo"]
    assert memo["value"] == "unknown" and "not attested for" in memo["reason"]


def test_overlap_confirmed_must_be_true_or_false(tmp_path):
    file = seed(tmp_path)
    file["periods"][P]["left_out"]["synthetic-left-out"] = {
        "reason": "Outside synthetic scope", "external_debt": 100,
        "support": [{"participant": B, "type": "guarantee", "debt_id": "D1", "covered_principal": 40,
                     "evidence": REF, "overlap_confirmed": "yes"}],
    }
    with pytest.raises(ca.AggregationError, match="overlap_confirmed"):
        calculate(tmp_path, file)


@pytest.mark.parametrize("answer", ["schedule", "unoffset_other_income_amount"])
def test_a_null_gate_answer_withholds_the_metrics_instead_of_refusing_the_run(tmp_path, answer):
    # PX628-112: the documented contract says a missing or null gate answer withholds; it does not reject.
    c = run(tmp_path, change_att(answer, None))
    assert c["financials"]["total_debt"] == 390 and c["financials"]["ebitda"] == 800


def test_both_gate_reasons_are_kept_when_the_ebitda_and_cash_gates_fail_together(tmp_path):
    # PX628-117: a negative-cash reason does not disappear behind a failed EBITDA gate.
    file = seed(tmp_path)
    file["periods"][P]["attestations"][O]["ebitda_intragroup"] = "not_offset"
    state = _read_full(tmp_path)
    state["entity_records"][O]["multi_period_financials"][P]["cash"] = -10
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    calculate(tmp_path, file)
    reason = _read_full(tmp_path)["credit_aggregation"][P]["computed"]["withheld"]["net_debt_to_ebitda"]
    assert "not attested" in reason and "Cash is negative" in reason
