"""Tests for scripts/transcription_check.py: the safeguards for /spread figures transcribed from an image (issue #132).

What is proved here is what code enforces: nothing is written to a deal before a read-back of the same figures was
produced and its digest quoted; a stale digest or an unresolved cross-foot discrepancy refuses the commit with nothing
written; the cross-foot arithmetic and its rounding tolerance; both /spread modes; and the disclosure that figures
were transcribed from an image. That the analyst actually said yes, and that an image input is routed through the
script at all, are instructions in .claude/commands/spread.md and are only pinned as text (test_prompt_consistency
style) at the bottom of this file. All data is synthetic.
"""
import json
from decimal import Decimal

import pytest

import state_manager
import transcription_check as tc
from source_manifest import read_manifest
from spreading_builder import FIELD_LABELS, evaluate_financial_model
from spreading_check import compute
from state_manager import SCHEMA_VERSION, SchemaVersionError, StateError, read_state, write_state

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic"

# A balance sheet that balances and a P&L whose subtotals follow from its lines.
LINES = {
    "revenue": "1,000", "cost_of_sales": "600", "admin_expenses": "100",
    "cash": "50", "trade_debtors": "150", "stock": "0", "other_current_assets": "0",
    "tangible_assets": "800", "intangible_assets": "0", "other_fixed_assets": "0",
    "trade_creditors": "100", "current_debt": "0", "overdraft": "0", "other_current_liabilities": "0",
    "long_term_debt": "400", "loan_notes": "0", "other_long_term_liabilities": "0", "provisions": "0",
    "share_capital": "100", "retained_profit": "400",
}
SUBTOTALS = {"gross_profit": "400", "current_assets": "200", "total_assets": "1,000", "current_liabilities": "100",
             "total_liabilities": "500", "total_equity": "500"}


def staged_data(tmp_path, mode="framework-computed", periods=None, acknowledged=None, **source):
    image = tmp_path / "shot.png"
    image.write_bytes(PNG)
    periods = periods if periods is not None else {"FY-Current": {"lines": dict(LINES), "subtotals": dict(SUBTOTALS)}}
    data = {"mode": mode,
            "source": {"file": str(image), "kind": "image", "description": "Synthetic Co accounts screenshot",
                       "unit": "GBP thousands", "cost_sign": "positive", "outflow_sign": "positive",
                       "liability_sign": "positive", **source},
            "periods": periods}
    if acknowledged:
        data["acknowledged"] = acknowledged
    return data


def staged(tmp_path, **kwargs):
    return tc.validate(staged_data(tmp_path, **kwargs))


def analyst_data(tmp_path, **kwargs):
    periods = {"FY-Current": {
        "subtotals": {"gross_profit": "400", "ebitda": "300", "tangible_net_worth": "500"},
        "ratios": {"dscr": "1.35x", "gearing": "45.2%"},
        "lines": {"revenue": "1,000", "cost_of_sales": "600"}}}
    return staged_data(tmp_path, mode="analyst-supplied", periods=periods, **kwargs)


@pytest.fixture
def deal(tmp_path, monkeypatch):
    """Run in a scratch directory so deals/ lives there; return it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def deals_exist(root):
    return (root / "deals").exists()


# ---------------------------------------------------------------------------
# Figures as written
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, value, decimals", [
    ("1,234.5", "1234.5", 1), ("1234", "1234", 0), ("-500", "-500", 0), ("(500)", "-500", 0),
    ("(1,234.50)", "-1234.50", 2), ("0", "0", 0), ("12.50", "12.50", 2), ("−500", "-500", 0),
    ("  42  ", "42", 0),
])
def test_figures_are_read_exactly_as_written_keeping_sign_and_decimals(text, value, decimals):
    figure = tc.parse_figure(text, "x")
    assert figure.value == Decimal(value) and figure.decimals == decimals and figure.text == text.strip()


@pytest.mark.parametrize("text", ["", "  ", None, 12, "n/a", "-", "—", "£1,200", "$5", "1,5", "1,23,456",
                                  ".5", "1e3", "(500", "500)", "-(500)", "1 234", "12%", "+5"])
def test_a_figure_that_cannot_be_read_with_certainty_is_refused_not_guessed(text):
    with pytest.raises(tc.TranscriptionError, match="x"):
        tc.parse_figure(text, "x")


def test_ratio_suffixes_are_accepted_only_for_ratios_and_the_number_is_recorded_as_written():
    assert tc.parse_figure("1.35x", "r", allow_suffix=True).value == Decimal("1.35")
    assert tc.parse_figure("45.2%", "r", allow_suffix=True).value == Decimal("45.2")
    with pytest.raises(tc.TranscriptionError):
        tc.parse_figure("1.35x", "line")


def test_a_figure_is_recorded_as_an_int_without_decimals_and_a_float_with_them():
    assert tc.parse_figure("1,200", "x").number() == 1200 and isinstance(tc.parse_figure("1,200", "x").number(), int)
    assert tc.parse_figure("1,200.5", "x").number() == 1200.5


# ---------------------------------------------------------------------------
# The framework's own formulas decide which lines feed a subtotal
# ---------------------------------------------------------------------------

def test_the_weights_are_discovered_from_the_framework_formulas_not_written_down_again():
    assert tc.COEFFICIENTS["gross_profit"] == {"revenue": 1, "cost_of_sales": -1}
    assert "depreciation" not in tc.COEFFICIENTS["ebitda"]      # added back, so it cancels
    assert "depreciation" in tc.COEFFICIENTS["operating_profit"]
    assert tc.COEFFICIENTS["tangible_net_worth"]["intangible_assets"] == -1
    assert set(tc.BALANCE_COEFFICIENTS) >= {"cash", "tangible_assets", "trade_creditors", "share_capital"}


def test_every_subtotal_is_a_sum_of_raw_lines_so_the_weights_reproduce_the_framework_exactly():
    for seed in range(25):          # deterministic, varied (including negative) values for every raw field
        raw = {field: (index * 37 + seed * 101 + 11) % 997 - 200 for index, field in enumerate(FIELD_LABELS)}
        derived = evaluate_financial_model({"p": raw})["financials"]["p"]
        for name in tc.SUBTOTAL_FIELDS:
            weighted = sum(Decimal(raw[f]) * w for f, w in tc.COEFFICIENTS[name].items())
            assert weighted == Decimal(str(derived[name])), name


def test_the_name_lists_cover_the_framework_so_a_new_field_cannot_be_forgotten():
    grouped = [field for _, fields in tc.LINE_GROUPS for field in fields]
    assert sorted(grouped) == sorted(FIELD_LABELS)
    derived = evaluate_financial_model({"p": {"revenue": 1}})
    assert set(tc.SUBTOTAL_FIELDS) <= set(derived["financials"]["p"])
    assert set(tc.RATIO_FIELDS) <= set(derived["ratios"]["p"])


# ---------------------------------------------------------------------------
# Validation of the staged file
# ---------------------------------------------------------------------------

def mutate(data, path, value):
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return data


@pytest.mark.parametrize("change, message", [
    (lambda d: d.update(mode="guess"), "mode must be"),
    (lambda d: d.update(extra=1), "unknown key"),
    (lambda d: d["source"].update(unit=" "), "source.unit"),
    (lambda d: d["source"].pop("description"), "source.description"),
    (lambda d: d["source"].update(kind="pdf"), "source.kind"),
    (lambda d: d["source"].update(colour="x"), "unknown key"),
    (lambda d: d["source"].pop("cost_sign"), "source.cost_sign is required because"),
    (lambda d: d["source"].pop("liability_sign"), "source.liability_sign is required because"),
    (lambda d: d["source"].update(cost_sign="maybe"), "must be one of"),
    (lambda d: d["source"].update(liability_sign=None), "must be one of"),
    (lambda d: d.update(periods={}), "non-empty"),
    (lambda d: d.update(periods={"FY+1": {"lines": {"revenue": "1"}}}), "unknown period"),
    (lambda d: mutate(d, ["periods", "FY-Current", "lines", "reveneu"], "5"), "unknown name"),
    (lambda d: mutate(d, ["periods", "FY-Current", "lines", "revenue"], 1000), "write the figure as text"),
    (lambda d: mutate(d, ["periods", "FY-Current", "ratios"], {"dscr": "1.2"}), "recomputed"),
    (lambda d: d["periods"].update({"FY-Current": {"subtotals": {"gross_profit": "5"}}}), "needs the raw lines"),
    (lambda d: d["periods"].update({"FY-Current": {"lines": {"revenue": "1"}, "colour": 1}}), "unknown key"),
    (lambda d: d.update(acknowledged=[{"period": "FY-1", "check": "ebitda", "reason": "x"}]), "not a period"),
    (lambda d: d.update(acknowledged=[{"period": "FY-Current", "check": "dscr", "reason": "x"}]), "not a cross-foot"),
    (lambda d: d.update(acknowledged=[{"period": "FY-Current", "check": "ebitda", "reason": " "}]), "reason"),
    (lambda d: d.update(acknowledged=[{"period": "FY-Current", "check": "ebitda", "reason": "a"}] * 2), "more than once"),
    (lambda d: d.update(acknowledged="yes"), "must be a list"),
    (lambda d: d.update(acknowledged=["yes"]), "must be an object"),
])
def test_an_unusable_staged_file_is_refused_with_a_message_naming_the_problem(tmp_path, change, message):
    data = staged_data(tmp_path)
    change(data)
    with pytest.raises(tc.TranscriptionError, match=message):
        tc.validate(data)


def test_the_top_level_must_be_an_object_and_the_analyst_mode_needs_some_figure(tmp_path):
    with pytest.raises(tc.TranscriptionError, match="JSON object"):
        tc.validate([])
    data = analyst_data(tmp_path)
    data["periods"] = {"FY-Current": {}}
    with pytest.raises(tc.TranscriptionError, match="no figures"):
        tc.validate(data)


def test_load_reports_a_missing_or_malformed_file_cleanly(tmp_path):
    with pytest.raises(tc.TranscriptionError, match="cannot read"):
        tc.load(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(tc.TranscriptionError, match="not valid JSON"):
        tc.load(bad)


# ---------------------------------------------------------------------------
# The digest binds a confirmation to exactly what was shown
# ---------------------------------------------------------------------------

def test_the_digest_is_stable_and_changes_with_anything_the_analyst_was_shown(tmp_path):
    base = digest(staged_data(tmp_path))
    assert base == digest(staged_data(tmp_path)) and len(base) == tc.DIGEST_LENGTH
    variants = []
    data = staged_data(tmp_path); data["periods"]["FY-Current"]["lines"]["revenue"] = "1,001"; variants.append(data)
    data = staged_data(tmp_path); data["periods"]["FY-Current"]["lines"]["revenue"] = "1,000.0"; variants.append(data)
    data = staged_data(tmp_path); data["source"]["unit"] = "GBP millions"; variants.append(data)
    data = staged_data(tmp_path); data["source"]["kind"] = "scanned-document"; variants.append(data)
    data = staged_data(tmp_path); data["periods"]["FY-Current"]["subtotals"]["gross_profit"] = "399"; variants.append(data)
    data = staged_data(tmp_path); data["periods"]["FY-1"] = {"lines": {"revenue": "5"}}; variants.append(data)
    data = staged_data(tmp_path); data["acknowledged"] = [{"period": "FY-Current", "check": "ebitda", "reason": "r"}]; variants.append(data)
    data = staged_data(tmp_path); data["mode"] = "analyst-supplied"; variants.append(data)
    data = staged_data(tmp_path); data["source"]["cost_sign"] = "negative"; variants.append(data)
    data = staged_data(tmp_path); data["source"]["liability_sign"] = "negative"; variants.append(data)
    data = staged_data(tmp_path); data["source"]["description"] = "Another description"; variants.append(data)
    digests = {digest(v) for v in variants}
    assert base not in digests and len(digests) == len(variants)


# ---------------------------------------------------------------------------
# Cross-foot
# ---------------------------------------------------------------------------

def results(tmp_path, **kwargs):
    report = tc.cross_foot(staged(tmp_path, **kwargs))
    return {row["check"]: row for rows in report["periods"].values() for row in rows}, report


def test_a_transcription_that_foots_matches_every_assessable_check_and_is_ready(tmp_path):
    rows, report = results(tmp_path)
    assert {name: row["status"] for name, row in rows.items()} == {
        "gross_profit": "match", "current_assets": "match", "total_assets": "match", "current_liabilities": "match",
        "total_liabilities": "match", "total_equity": "match", "balance_sheet_balances": "match"}
    assert tc.BALANCE_CHECK == "balance_sheet_balances" and report["ready"] and report["unresolved"] == []


def test_a_misread_digit_is_a_mismatch_that_blocks_and_shows_the_discrepancy(tmp_path):
    periods = {"FY-Current": {"lines": {**LINES, "trade_debtors": "180"}, "subtotals": dict(SUBTOTALS)}}   # 150 misread
    rows, report = results(tmp_path, periods=periods)
    assert rows["current_assets"]["status"] == "mismatch" and Decimal(rows["current_assets"]["difference"]) == -30
    assert rows["total_assets"]["status"] == "mismatch" and rows["balance_sheet_balances"]["status"] == "mismatch"
    assert not report["ready"] and ["FY-Current", "current_assets"] in report["unresolved"]


def test_a_flipped_sign_is_caught_by_the_subtotal(tmp_path):
    periods = {"FY-Current": {"lines": {**LINES, "cost_of_sales": "(600)"}, "subtotals": {"gross_profit": "400"}}}
    rows, _ = results(tmp_path, periods=periods)
    assert rows["gross_profit"]["status"] == "mismatch" and Decimal(rows["gross_profit"]["computed"]) == 1600


def test_rounding_in_the_presented_statement_is_tolerated_but_only_as_far_as_the_figures_as_written_allow(tmp_path):
    # Two lines shown to 1 decimal, two zeros shown to 0, the total to 0: 100.4 + 200.4 = 300.8, shown as 301.
    # Allowed difference: 0.5 * (0.1 + 0.1 + 1 + 1 + 1) = 1.6.
    lines = {"cash": "100.4", "trade_debtors": "200.4", "stock": "0", "other_current_assets": "0"}
    ok, _ = results(tmp_path, periods={"FY-Current": {"lines": lines, "subtotals": {"current_assets": "301"}}})
    assert ok["current_assets"]["status"] == "match" and Decimal(ok["current_assets"]["tolerance"]) == Decimal("1.6")
    off, _ = results(tmp_path, periods={"FY-Current": {"lines": lines, "subtotals": {"current_assets": "303"}}})
    assert off["current_assets"]["status"] == "mismatch"          # 2.2 apart: more than rounding can explain
    # The same amounts written to two decimals leave far less room: 0.4 apart is not rounding any more.
    fine = {"cash": "100.40", "trade_debtors": "200.40", "stock": "0.00", "other_current_assets": "0.00"}
    exact, _ = results(tmp_path, periods={"FY-Current": {"lines": fine, "subtotals": {"current_assets": "301.20"}}})
    assert exact["current_assets"]["status"] == "mismatch" and Decimal(exact["current_assets"]["tolerance"]) == Decimal(
        "0.025")


def test_a_check_is_made_only_when_every_feeding_line_was_transcribed(tmp_path):
    lines = {k: v for k, v in LINES.items() if k != "stock"}
    rows, report = results(tmp_path, periods={"FY-Current": {"lines": lines, "subtotals": dict(SUBTOTALS)}})
    assert rows["current_assets"]["status"] == "not_assessed" and rows["current_assets"]["missing"] == ["stock"]
    assert rows["total_assets"]["status"] == "not_assessed"
    assert rows["gross_profit"]["status"] == "match"
    assert report["ready"], "not assessable is reported, never silently treated as a pass or a block"


def test_an_explicit_zero_is_a_transcribed_line_but_an_omitted_one_is_unknown(tmp_path):
    zero, _ = results(tmp_path, periods={"FY-Current": {"lines": {"revenue": "10", "cost_of_sales": "0"},
                                                         "subtotals": {"gross_profit": "10"}}})
    assert zero["gross_profit"]["status"] == "match"
    omitted, _ = results(tmp_path, periods={"FY-Current": {"lines": {"revenue": "10"}, "subtotals": {"gross_profit": "10"}}})
    assert omitted["gross_profit"]["status"] == "not_assessed"


def test_a_source_that_states_no_subtotal_and_no_balance_sheet_has_nothing_to_compare(tmp_path):
    rows, report = results(tmp_path, periods={"FY-Current": {"lines": {"revenue": "10", "cost_of_sales": "4"}}})
    assert rows == {} and report["ready"]
    text = tc.render_readback(staged(tmp_path, periods={"FY-Current": {"lines": {"revenue": "10"}}}),
                              {"periods": {"FY-Current": []}, "unresolved": [], "unused_acknowledgements": [],
                               "refused_acknowledgements": [], "ready": True}, "d", "f" * 64)
    assert "nothing to compare. This is not a check that passed." in text


def test_the_balance_sheet_identity_is_checked_without_any_stated_subtotal(tmp_path):
    lines = {**LINES, "retained_profit": "350"}
    rows, report = results(tmp_path, periods={"FY-Current": {"lines": lines}})
    assert list(rows) == ["balance_sheet_balances"] and rows["balance_sheet_balances"]["status"] == "mismatch"
    assert Decimal(rows["balance_sheet_balances"]["computed"]) == 50 and Decimal(
        rows["balance_sheet_balances"]["difference"]) == -50
    assert not report["ready"]


def test_a_discrepancy_is_resolved_only_by_the_analysts_stated_reason_for_that_exact_period_and_check(tmp_path):
    periods = {"FY-Current": {"lines": {**LINES, "retained_profit": "350"}, "subtotals": {"total_equity": "500"}}}
    ack = [{"period": "FY-Current", "check": "total_equity", "reason": "Other reserves of 50 are not a framework line"}]
    rows, report = results(tmp_path, periods=periods, acknowledged=ack)
    assert rows["total_equity"]["status"] == "acknowledged" and "Other reserves" in rows["total_equity"]["reason"]
    assert rows["balance_sheet_balances"]["status"] == "mismatch", "an acknowledgement covers only the check it names"
    assert report["unresolved"] == [["FY-Current", "balance_sheet_balances"]] and not report["ready"]


def test_an_acknowledgement_that_matches_no_discrepancy_is_reported_not_silently_used(tmp_path):
    ack = [{"period": "FY-Current", "check": "gross_profit", "reason": "r"}]
    _, report = results(tmp_path, acknowledged=ack)
    assert report["unused_acknowledgements"] == [["FY-Current", "gross_profit"]] and report["ready"]


def test_each_period_is_cross_footed_on_its_own(tmp_path):
    periods = {"FY-1": {"lines": dict(LINES), "subtotals": dict(SUBTOTALS)},
               "FY-Current": {"lines": {**LINES, "revenue": "1,010"}, "subtotals": dict(SUBTOTALS)}}
    report = tc.cross_foot(staged(tmp_path, periods=periods))
    assert report["unresolved"] == [["FY-Current", "gross_profit"]]
    assert all(row["status"] == "match" for row in report["periods"]["FY-1"])


# ---------------------------------------------------------------------------
# The read-back
# ---------------------------------------------------------------------------

def test_the_readback_groups_by_period_and_statement_and_keeps_the_figures_as_written(tmp_path):
    periods = {"FY-1": {"lines": {"revenue": "900.50", "cost_of_sales": "(500)"}},
               "FY-Current": {"lines": dict(LINES), "subtotals": dict(SUBTOTALS)}}
    shown = tc.readback(staged(tmp_path, periods=periods))
    text = shown["text"]
    assert text.index("## FY-1") < text.index("## FY-Current")
    assert "| Revenue | 900.50 | 900.50 | as written |" in text
    assert "| Cost of Goods Sold | (500) | -500 | as written |" in text      # declared positive: a credit, kept
    assert "Profit and loss" in text and "Balance sheet: assets" in text and "Subtotals stated by the source" in text
    assert "Unit: GBP thousands" in text and f"Digest: {shown['digest']}" in text
    assert "nothing has been recorded" in text and "STATUS: ready for the analyst's confirmation." in text
    assert f"--confirm {shown['digest']}" in text


def test_the_readback_of_a_blocked_transcription_says_so_and_names_the_way_out(tmp_path):
    periods = {"FY-Current": {"lines": {**LINES, "revenue": "1,100"}, "subtotals": dict(SUBTOTALS)}}
    text = tc.readback(staged(tmp_path, periods=periods))["text"]
    assert "MISMATCH" in text and "STATUS: BLOCKED" in text and "`acknowledged`" in text


def test_the_readback_says_how_ratios_are_treated_in_each_mode(tmp_path):
    assert "recomputed by the framework" in tc.readback(staged(tmp_path))["text"]
    text = tc.readback(tc.validate(analyst_data(tmp_path)))["text"]
    assert "not cross-footed" in text and "| DSCR" not in text and "Dscr | 1.35x | 1.35 |" in text


def test_reading_back_never_touches_a_deal(deal):
    staged_file = deal / "t.json"
    staged_file.write_text(json.dumps(staged_data(deal)), encoding="utf-8")
    before = sorted(p.name for p in deal.rglob("*"))
    tc.readback(tc.load(staged_file))
    assert sorted(p.name for p in deal.rglob("*")) == before and not deals_exist(deal)


# ---------------------------------------------------------------------------
# The commit: nothing is written unless the confirmed digest matches and the figures foot
# ---------------------------------------------------------------------------

def digest(data):
    return tc.readback(tc.validate(data))["digest"]


CORRECT = object()      # "quote the digest of the read-back for these exact figures"


def run_commit(data, confirm=CORRECT, **kwargs):
    staged_ = tc.validate(data)
    return tc.commit(staged_, digest(data) if confirm is CORRECT else confirm, "Synthetic Co", "Fleet Loan", **kwargs)


def test_a_commit_without_the_digest_of_these_figures_is_refused_and_writes_nothing(deal):
    data = staged_data(deal)
    for wrong in ("", "0000000000000000", None, "not-a-digest"):
        with pytest.raises(tc.TranscriptionError, match="does not match"):
            run_commit(data, confirm=wrong)
    assert not deals_exist(deal)


def test_correcting_a_figure_after_the_readback_makes_the_earlier_confirmation_stale(deal):
    data = staged_data(deal)
    first = digest(data)
    data["periods"]["FY-Current"]["lines"]["admin_expenses"] = "110"         # the analyst corrects a value
    with pytest.raises(tc.TranscriptionError, match="does not match"):
        run_commit(data, confirm=first)
    assert not deals_exist(deal)
    assert digest(data) != first
    result = run_commit(data)                                              # the revised read-back, re-confirmed
    assert result["committed"] and read_state("Synthetic Co", "Fleet Loan")["multi_period_financials"][
        "FY-Current"]["admin_expenses"] == 110


def test_an_unresolved_discrepancy_refuses_even_a_matching_digest_and_writes_nothing(deal):
    data = staged_data(deal)
    data["periods"]["FY-Current"]["lines"]["revenue"] = "1,100"
    with pytest.raises(tc.TranscriptionError, match="unresolved cross-foot discrepancies.*FY-Current/gross_profit"):
        run_commit(data)
    assert not deals_exist(deal)


def test_a_discrepancy_resolved_by_a_reason_is_committed_and_the_reason_is_recorded(deal):
    data = staged_data(deal)
    data["periods"]["FY-Current"]["lines"]["revenue"] = "1,100"
    data["acknowledged"] = [{"period": "FY-Current", "check": "gross_profit", "reason": "Source gross profit excludes a rebate"}]
    run_commit(data)
    record = read_state("Synthetic Co", "Fleet Loan")["financials_transcriptions"][0]
    assert record["cross_foot"]["acknowledged"][0]["reason"] == "Source gross profit excludes a rebate"
    assert record["cross_foot"]["acknowledged"][0]["difference"] == "-100"


def test_a_missing_source_file_a_missing_note_and_a_misplaced_note_are_refused_before_any_write(deal):
    data = staged_data(deal)
    data["source"]["file"] = str(deal / "gone.png")
    with pytest.raises(tc.TranscriptionError, match="cannot read the source file"):
        run_commit(data)
    with pytest.raises(tc.TranscriptionError, match="cannot read the source file"):
        tc.readback(tc.validate(data))
    with pytest.raises(tc.TranscriptionError, match="--source-note is only used"):
        run_commit(staged_data(deal), source_note="a note")
    with pytest.raises(tc.TranscriptionError, match="needs --source-note"):
        run_commit(analyst_data(deal))
    with pytest.raises(tc.TranscriptionError, match="needs --source-note"):
        run_commit(analyst_data(deal), source_note="   ")
    assert not deals_exist(deal)


def test_cancelling_means_never_committing_and_leaves_an_existing_deal_untouched(deal):
    write_state("Synthetic Co", "Fleet Loan", financials={"FY-Current": {"gross_profit": 7}}, steps_completed=["triage"])
    state_file = next(deal.glob("deals/Synthetic Co/Fleet Loan_*/state.json"))
    before = state_file.read_bytes()
    tc.readback(tc.validate(staged_data(deal)))                    # the read-back is shown, the analyst says no
    with pytest.raises(tc.TranscriptionError):                      # and even a wrong attempt cannot write
        run_commit(staged_data(deal), confirm="deadbeefdeadbeef")
    assert state_file.read_bytes() == before
    assert not (state_file.parent / "sources").exists()


def test_framework_computed_commit_recomputes_from_the_lines_exactly_like_spread(deal, tmp_path_factory):
    data = staged_data(deal)
    result = run_commit(data)
    state = read_state("Synthetic Co", "Fleet Loan")
    expected = evaluate_financial_model({"FY-Current": {k: tc.parse_figure(v, k).number() for k, v in LINES.items()}})
    assert state["financials"] == expected["financials"] and state["ratios"] == expected["ratios"]
    assert state["financials_source"] == "framework-computed" and "financials_source_note" not in state
    assert state["steps_completed"] == ["spread"]
    assert result["financials_source"] == "framework-computed" and result["periods"] == ["FY-Current"]
    # The stated subtotals were used to cross-foot only; they are not recorded as truth.
    assert "analyst_supplied_financials" not in state


def test_framework_computed_commit_equals_the_ordinary_path_given_the_same_numbers(deal, tmp_path_factory):
    run_commit(staged_data(deal))
    via_image = read_state("Synthetic Co", "Fleet Loan")
    compute("Other Co", "Other Loan", {"FY-Current": {k: tc.parse_figure(v, k).number() for k, v in LINES.items()}})
    ordinary = read_state("Other Co", "Other Loan")
    for key in ("financials", "ratios", "multi_period_financials", "financials_source"):
        assert via_image[key] == ordinary[key], key


def test_the_commit_saves_the_image_as_the_source_of_record_with_the_transcription_in_its_claim(deal):
    result = run_commit(staged_data(deal))
    entries = read_manifest("Synthetic Co", "Fleet Loan")
    assert len(entries) == 1 and entries[0]["step"] == "spread" and entries[0]["filename"] == result["source_file"]
    assert entries[0]["filename"].endswith(".png")
    assert "figures transcribed from image" in entries[0]["claim"] and result["digest"] in entries[0]["claim"]
    saved = next(deal.glob("deals/Synthetic Co/Fleet Loan_*/sources/" + entries[0]["filename"]))
    assert saved.read_bytes() == PNG


def test_the_state_records_that_the_figures_were_transcribed_from_an_image(deal):
    result = run_commit(staged_data(deal, kind="scanned-document"))
    state = read_state("Synthetic Co", "Fleet Loan")
    (record,) = state["financials_transcriptions"]
    assert record["method"] == "scanned-document" and record["mode"] == "framework-computed"
    assert record["unit"] == "GBP thousands" and record["periods"] == ["FY-Current"]
    assert record["source_file"] == result["source_file"] and record["confirmed_digest"] == result["digest"]
    assert record["cross_foot"]["ratios_cross_footed"] is False
    assert "FY-Current/gross_profit" in record["cross_foot"]["matched"]


def test_cross_foot_checks_that_could_not_be_made_are_recorded_as_not_assessed(deal):
    lines = {k: v for k, v in LINES.items() if k != "stock"}
    run_commit(staged_data(deal, periods={"FY-Current": {"lines": lines, "subtotals": dict(SUBTOTALS)}}))
    record = read_state("Synthetic Co", "Fleet Loan")["financials_transcriptions"][0]
    assert "FY-Current/current_assets" in record["cross_foot"]["not_assessed"]
    assert "FY-Current/gross_profit" in record["cross_foot"]["matched"]


def test_a_second_image_adds_a_record_and_keeps_the_first_and_the_step_once(deal):
    run_commit(staged_data(deal, periods={"FY-1": {"lines": {"revenue": "900", "cost_of_sales": "500"}}}))
    run_commit(staged_data(deal))
    state = read_state("Synthetic Co", "Fleet Loan")
    assert [r["periods"] for r in state["financials_transcriptions"]] == [["FY-1"], ["FY-Current"]]
    assert state["steps_completed"] == ["spread"] and set(state["financials"]) == {"FY-1", "FY-Current"}
    assert len(read_manifest("Synthetic Co", "Fleet Loan")) == 2


def test_analyst_supplied_commit_records_the_given_figures_exactly_and_never_recomputes(deal):
    result = run_commit(analyst_data(deal), source_note="Depreciation embedded in Cost of Goods Sold")
    state = read_state("Synthetic Co", "Fleet Loan")
    assert state["financials"] == {"FY-Current": {"gross_profit": 400, "ebitda": 300, "tangible_net_worth": 500}}
    assert state["ratios"] == {"FY-Current": {"dscr": 1.35, "gearing": 45.2}}
    assert state["analyst_supplied_financials"] == {"FY-Current": {"revenue": 1000, "cost_of_sales": 600}}
    assert state["financials_source"] == "analyst-supplied"
    assert "multi_period_financials" not in state, "the framework-computed store is never populated in this mode"
    assert state["steps_completed"] == ["spread"] and result["financials_source"] == "analyst-supplied"


def test_analyst_supplied_commit_discloses_the_transcription_in_the_source_note_the_caveat_cites(deal):
    result = run_commit(analyst_data(deal), source_note="Depreciation embedded in Cost of Goods Sold  ")
    note = read_state("Synthetic Co", "Fleet Loan")["financials_source_note"]
    assert note.startswith("Depreciation embedded in Cost of Goods Sold Figures were transcribed from an image (")
    assert result["source_file"] in note and note.endswith("confirmed line by line by the analyst against it.")
    assert result["disclosure"] in note


def test_analyst_supplied_commit_merges_with_what_the_deal_already_records(deal):
    write_state("Synthetic Co", "Fleet Loan", financials={"FY-1": {"gross_profit": 350}, "FY-Current": {"net_profit": 99}},
                ratios={"FY-1": {"dscr": 1.1}}, financials_source="analyst-supplied", steps_completed=["triage"],
                inputs={"pd": "0.2%"})
    run_commit(analyst_data(deal), source_note="n")
    state = read_state("Synthetic Co", "Fleet Loan")
    assert state["financials"]["FY-1"] == {"gross_profit": 350}
    assert state["financials"]["FY-Current"] == {"net_profit": 99, "gross_profit": 400, "ebitda": 300,
                                                  "tangible_net_worth": 500}
    assert state["ratios"]["FY-1"] == {"dscr": 1.1} and state["inputs"] == {"pd": "0.2%"}
    assert state["steps_completed"] == ["triage", "spread"]


def test_analyst_supplied_figures_are_cross_footed_against_their_own_lines_when_both_are_given(deal):
    data = analyst_data(deal)
    data["periods"]["FY-Current"]["subtotals"]["gross_profit"] = "410"      # a misread digit in the subtotal
    with pytest.raises(tc.TranscriptionError, match="FY-Current/gross_profit"):
        run_commit(data, source_note="n")
    assert not deals_exist(deal)


def test_a_malformed_transcription_record_in_the_state_is_reported_and_nothing_is_saved(deal):
    write_state("Synthetic Co", "Fleet Loan", financials_transcriptions="oops")
    with pytest.raises(StateError, match="financials_transcriptions"):
        run_commit(staged_data(deal))
    assert not list(deal.glob("deals/*/*/sources"))
    assert "financials" not in read_state("Synthetic Co", "Fleet Loan")


def test_a_wrongly_typed_financials_key_is_refused_by_the_state_check_before_anything_is_saved(deal):
    write_state("Synthetic Co", "Fleet Loan", financials=["not", "an", "object"])
    with pytest.raises(StateError):
        run_commit(analyst_data(deal), source_note="n")
    assert not list(deal.glob("deals/*/*/sources"))


# ---------------------------------------------------------------------------
# The ordinary (non-image) path is untouched
# ---------------------------------------------------------------------------

def test_the_ordinary_spreading_path_does_not_know_about_transcriptions(deal):
    state = compute("Synthetic Co", "Fleet Loan", {"FY-Current": {"revenue": 10, "cost_of_sales": 4}})
    assert "financials_transcriptions" not in state and state["financials_source"] == "framework-computed"
    import spreading_check
    with open(spreading_check.__file__, encoding="utf-8") as f:
        assert "transcription_check" not in f.read()


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def write_staged(tmp_path, data):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_main_prints_the_readback_by_default_and_json_on_request(deal, capsys):
    path = write_staged(deal, staged_data(deal))
    tc.main(["--transcription", str(path)])
    assert "TRANSCRIPTION READ-BACK" in capsys.readouterr().out
    tc.main(["--transcription", str(path), "--json"])
    shown = json.loads(capsys.readouterr().out)
    assert set(shown) == {"digest", "source_sha256", "report", "text"} and shown["report"]["ready"] is True
    assert not deals_exist(deal)


def test_main_commits_with_the_confirmed_digest_and_prints_a_summary(deal, capsys):
    data = staged_data(deal)
    path = write_staged(deal, data)
    tc.main(["--transcription", str(path), "--commit", "--confirm", digest(data), "--company", "Synthetic Co",
             "--proposal", "Fleet Loan"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["committed"] is True and summary["financials_source"] == "framework-computed"


def test_main_turns_a_refusal_into_one_error_line_and_a_nonzero_exit(deal, capsys):
    path = write_staged(deal, staged_data(deal))
    with pytest.raises(SystemExit) as exit_info:
        tc.main(["--transcription", str(path), "--commit", "--confirm", "0000000000000000", "--company", "A",
                 "--proposal", "B"])
    assert str(exit_info.value).startswith("error: refused:") and not deals_exist(deal)
    with pytest.raises(SystemExit) as missing:
        tc.main(["--transcription", str(deal / "absent.json")])
    assert str(missing.value).startswith("error: cannot read")


def test_main_requires_company_proposal_and_confirm_to_commit(deal):
    path = write_staged(deal, staged_data(deal))
    with pytest.raises(SystemExit) as exit_info:
        tc.main(["--transcription", str(path), "--commit"])
    assert exit_info.value.code == 2 and not deals_exist(deal)


def test_main_reports_an_unreadable_state_as_an_error_line(deal):
    data = staged_data(deal)
    path = write_staged(deal, data)
    write_state("Synthetic Co", "Fleet Loan", inputs={})
    state_file = next(deal.glob("deals/Synthetic Co/Fleet Loan_*/state.json"))
    state_file.write_text("{broken", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        tc.main(["--transcription", str(path), "--commit", "--confirm", digest(data), "--company", "Synthetic Co",
                 "--proposal", "Fleet Loan"])
    assert str(exit_info.value).startswith("error:") and state_file.read_text(encoding="utf-8") == "{broken"


# ---------------------------------------------------------------------------
# What only instructions can enforce is at least pinned as text
# ---------------------------------------------------------------------------

def test_spread_command_routes_image_figures_through_the_read_back_and_requires_explicit_confirmation():
    from pathlib import Path
    text = Path(tc.__file__).resolve().parents[1].joinpath(".claude", "commands", "spread.md").read_text(
        encoding="utf-8")
    flat = " ".join(text.split())
    for needle in ("transcription_check.py --transcription", "--commit", "--confirm", "financials_source_note",
                   "Silence", "never confirmation", "explicit", "cancel", "read back again", "cost_sign",
                   "ask the analyst, never guess", "cannot be acknowledged", "never mixes the two"):
        assert needle in flat, needle
    assert flat.index("transcription_check.py --transcription") < flat.index("--commit")


# ---------------------------------------------------------------------------
# The documentation quotes the real read-back
# ---------------------------------------------------------------------------

def test_the_workflows_page_quotes_what_the_read_back_of_the_synthetic_example_really_prints(monkeypatch):
    from pathlib import Path
    repo = Path(tc.__file__).resolve().parents[1]
    monkeypatch.chdir(repo)          # the example names its image relative to the repository root
    example = json.loads((repo / "docs" / "examples" / "synthetic_co" / "transcription_input.json").read_text(
        encoding="utf-8"))
    page = (repo / "docs" / "workflows.md").read_text(encoding="utf-8")
    good = tc.readback(tc.validate(example))
    assert good["report"]["ready"] and "STATUS: ready for the analyst's confirmation." in good["text"]
    assert f"`Digest: {good['digest']}`" in page and f"--confirm {good['digest']}" in page
    assert good["source_sha256"] in page, "the page shows the example image's fingerprint"

    import copy
    wrong_sign = copy.deepcopy(example)
    wrong_sign["source"]["cost_sign"] = "positive"          # the wrong declaration the page warns about
    sign_doubt = tc.readback(tc.validate(wrong_sign))
    example["periods"]["FY-Current"]["lines"]["trade_debtors"] = "260"      # the misread the page describes
    bad = tc.readback(tc.validate(example))
    assert not bad["report"]["ready"] and bad["digest"] != good["digest"]
    assert not sign_doubt["report"]["ready"] and "cannot cover this" in sign_doubt["text"]
    quoted = [line.strip() for line in page.splitlines() if line.strip().startswith("| FY-Current |")]
    assert len(quoted) == 5
    for row in quoted[:4]:
        assert row in bad["text"], row
    assert quoted[4] in sign_doubt["text"]
    for blocked in (bad, sign_doubt):
        assert blocked["digest"] not in page, "the page must not present a blocked read-back's digest as confirmable"
    assert "| Cost of Goods Sold | (700.0) | 700.0 | sign reversed: costs are written as negatives in this source |"         in good["text"]


# ---------------------------------------------------------------------------
# Signs: the source's presentation versus the framework's input convention
# ---------------------------------------------------------------------------

def gross_profit_periods(cost="(700)"):
    return {"FY-Current": {"lines": {"revenue": "1,200", "cost_of_sales": cost}, "subtotals": {"gross_profit": "500"}}}


def test_sign_classes_match_the_frameworks_own_input_semantics():
    """The class lists are the one thing written down by hand about signs, so tie them to the formulas."""
    fields = [f for members in tc.SIGN_CLASSES.values() for f in members]
    assert len(fields) == len(set(fields)) and set(fields) <= set(FIELD_LABELS)
    for field in tc.SIGN_CLASSES["cost_sign"] + tc.SIGN_CLASSES["outflow_sign"]:
        weights = [tc.COEFFICIENTS[name][field] for name in tc.SUBTOTAL_FIELDS if field in tc.COEFFICIENTS[name]]
        if field in ("depreciation", "amortisation"):        # subtracted from operating profit, added back in EBITDA
            assert tc.COEFFICIENTS["operating_profit"][field] < 0, field
        elif weights:                                          # (scheduled_principal feeds no subtotal at all)
            assert all(w < 0 for w in weights), field
    for field in tc.SIGN_CLASSES["liability_sign"]:
        assert tc.COEFFICIENTS["total_liabilities"][field] > 0, field
    for field in ("revenue", "other_income", "interest_received"):          # not in a class: recorded as written
        assert field not in tc.FIELD_SIGN_CLASS and any(
            tc.COEFFICIENTS[name].get(field, 0) > 0 for name in tc.SUBTOTAL_FIELDS)


def test_a_cost_in_brackets_read_as_a_positive_convention_is_a_sign_doubt_that_no_acknowledgement_can_cover(tmp_path):
    """The review's example: revenue 1,200, cost of sales (700), stated gross profit 500."""
    ack = [{"period": "FY-Current", "check": "gross_profit", "reason": "The source's gross profit is what it is"}]
    data = staged_data(tmp_path, periods=gross_profit_periods(), cost_sign="positive", acknowledged=ack)
    shown = tc.readback(tc.validate(data))
    row = shown["report"]["periods"]["FY-Current"][0]
    assert row["status"] == "mismatch" and Decimal(row["computed"]) == 1900
    assert "costs were read with the opposite sign" in row["sign_suspect"]
    assert shown["report"]["refused_acknowledgements"] == [["FY-Current", "gross_profit"]]
    assert not shown["report"]["ready"]
    assert "Acknowledgements REFUSED" in shown["text"] and "cannot cover this" in shown["text"]


def test_the_refused_acknowledgement_cannot_be_committed_even_with_the_matching_digest(deal):
    ack = [{"period": "FY-Current", "check": "gross_profit", "reason": "Trust me"}]
    data = staged_data(deal, periods=gross_profit_periods(), cost_sign="positive", acknowledged=ack)
    with pytest.raises(tc.TranscriptionError, match="unresolved cross-foot discrepancies.*gross_profit"):
        run_commit(data)
    assert not deals_exist(deal)


def test_declaring_brackets_as_presentation_gives_the_framework_a_positive_cost_and_shows_both_values(deal):
    data = staged_data(deal, periods=gross_profit_periods(), cost_sign="negative")
    shown = tc.readback(tc.validate(data))
    assert shown["report"]["ready"] and shown["report"]["periods"]["FY-Current"][0]["status"] == "match"
    assert "| Cost of Goods Sold | (700) | 700 | sign reversed: costs are written as negatives in this source |" \
        in shown["text"]
    assert "Sign convention, costs: written as negative numbers" in shown["text"]
    run_commit(data)
    state = read_state("Synthetic Co", "Fleet Loan")
    assert state["multi_period_financials"]["FY-Current"] == {"revenue": 1200, "cost_of_sales": 700}
    assert state["financials"]["FY-Current"]["gross_profit"] == 500
    record = state["financials_transcriptions"][0]
    assert record["sign_conventions"] == {"cost_sign": "negative", "outflow_sign": "positive",
                                          "liability_sign": "positive"}


def test_a_genuine_credit_is_not_forced_positive_under_either_convention(deal):
    # Costs written positive: (50) of admin expenses is a credit and stays -50.
    lines = {"revenue": "1,000", "cost_of_sales": "600", "admin_expenses": "(50)", "depreciation": "0",
             "amortisation": "0", "other_income": "0"}
    periods = {"FY-Current": {"lines": lines, "subtotals": {"operating_profit": "450"}}}
    positive = staged_data(deal, periods=periods, cost_sign="positive")
    shown = tc.readback(tc.validate(positive))
    assert shown["report"]["ready"] and "| Admin Expenses | (50) | -50 | as written |" in shown["text"]
    assert "recorded as negative, so a credit, a reversal or a deficit: Admin Expenses (written (50), recorded -50)" \
        in shown["text"]
    run_commit(positive)
    assert read_state("Synthetic Co", "Fleet Loan")["multi_period_financials"]["FY-Current"]["admin_expenses"] == -50

    # Costs written negative: a plain 50 is the credit, and the brackets are the ordinary cost.
    lines_negative = {**lines, "cost_of_sales": "(600)", "admin_expenses": "50"}
    negative = staged_data(deal, periods={"FY-Current": {"lines": lines_negative,
                                                          "subtotals": {"operating_profit": "450"}}},
                           cost_sign="negative")
    shown = tc.readback(tc.validate(negative))
    assert shown["report"]["ready"] and "| Admin Expenses | 50 | -50 | sign reversed" in shown["text"]
    assert "| Cost of Goods Sold | (600) | 600 | sign reversed" in shown["text"]


def test_changing_the_interpretation_makes_the_earlier_confirmation_stale(deal):
    brackets_as_presentation = staged_data(deal, periods=gross_profit_periods(), cost_sign="negative")
    confirmed = digest(brackets_as_presentation)
    other_reading = staged_data(deal, periods=gross_profit_periods(), cost_sign="positive")
    assert digest(other_reading) != confirmed
    with pytest.raises(tc.TranscriptionError, match="does not match"):
        run_commit(other_reading, confirm=confirmed)
    assert not deals_exist(deal)
    run_commit(brackets_as_presentation, confirm=confirmed)


def test_liabilities_in_brackets_and_cash_outflows_follow_their_own_declarations(tmp_path):
    lines = {**LINES, "trade_creditors": "(100)", "long_term_debt": "(400)", "capex": "(30)"}
    periods = {"FY-Current": {"lines": lines, "subtotals": dict(SUBTOTALS)}}
    data = staged_data(tmp_path, periods=periods, liability_sign="negative", outflow_sign="negative")
    staged_ = tc.validate(data)
    recorded = staged_["periods"]["FY-Current"]["lines"]
    assert recorded["trade_creditors"].value == 100 and recorded["long_term_debt"].value == 400
    assert recorded["capex"].value == 30 and recorded["capex"].written_value == -30
    assert tc.readback(staged_)["report"]["ready"]
    wrong = staged_data(tmp_path, periods=periods, liability_sign="positive", outflow_sign="negative")
    rows = {r["check"]: r for r in tc.readback(tc.validate(wrong))["report"]["periods"]["FY-Current"]}
    assert rows["total_liabilities"]["status"] == "mismatch"
    assert "liabilities were read with the opposite sign" in rows["total_liabilities"]["sign_suspect"]


def test_a_declaration_nothing_uses_is_not_needed_and_a_missing_one_is_refused_only_where_used(tmp_path):
    data = staged_data(tmp_path, periods={"FY-Current": {"lines": {"revenue": "10"}}})
    for key in ("cost_sign", "outflow_sign", "liability_sign"):
        data["source"].pop(key)
    assert "cost_sign" not in tc.validate(data)["source"]                  # nothing needs a convention
    data["periods"]["FY-Current"]["lines"]["capex"] = "(5)"
    with pytest.raises(tc.TranscriptionError, match="source.outflow_sign is required.*never guessed"):
        tc.validate(data)


def test_subtotals_and_ratios_keep_their_own_sign_whatever_the_conventions(deal):
    periods = {"FY-Current": {"subtotals": {"net_profit": "(120)", "gross_profit": "400"}, "ratios": {"dscr": "(0.5)x"},
                              "lines": {"revenue": "1,000", "cost_of_sales": "(600)"}}}
    data = staged_data(deal, mode="analyst-supplied", periods=periods, cost_sign="negative")
    run_commit(data, source_note="Depreciation embedded in Cost of Goods Sold")
    state = read_state("Synthetic Co", "Fleet Loan")
    assert state["financials"]["FY-Current"] == {"net_profit": -120, "gross_profit": 400}
    assert state["ratios"]["FY-Current"] == {"dscr": -0.5}
    assert state["analyst_supplied_financials"]["FY-Current"] == {"revenue": 1000, "cost_of_sales": 600}


def test_an_acknowledged_definition_difference_is_still_possible_when_no_sign_reading_explains_it(tmp_path):
    periods = {"FY-Current": {"lines": {**LINES, "retained_profit": "350"}, "subtotals": {"total_equity": "500"}}}
    ack = [{"period": "FY-Current", "check": "total_equity", "reason": "Other reserves of 50 have no framework line"}]
    rows, report = results(tmp_path, periods=periods, acknowledged=ack)
    assert rows["total_equity"]["status"] == "acknowledged" and "sign_suspect" not in rows["total_equity"]
    assert report["refused_acknowledgements"] == []


# ---------------------------------------------------------------------------
# One deal, one basis
# ---------------------------------------------------------------------------

def state_file(root):
    (path,) = root.glob("deals/Synthetic Co/Fleet Loan_*/state.json")
    return path


def nothing_new_saved(root):
    return not list(root.glob("deals/Synthetic Co/Fleet Loan_*/sources"))


@pytest.mark.parametrize("existing_mode, staged_mode, builder", [
    ("analyst-supplied", "framework-computed", staged_data),
    ("framework-computed", "analyst-supplied", analyst_data),
])
def test_a_transcription_in_the_other_mode_is_refused_before_anything_is_saved_or_written(deal, existing_mode,
                                                                                         staged_mode, builder):
    write_state("Synthetic Co", "Fleet Loan", financials={"FY-1": {"gross_profit": 350}},
                financials_source=existing_mode, steps_completed=["triage"])
    path = state_file(deal)
    before = path.read_bytes()
    with pytest.raises(tc.TranscriptionError, match=f"financials_source is '{existing_mode}'.*{staged_mode}"):
        run_commit(builder(deal), source_note="a convention" if staged_mode == "analyst-supplied" else None)
    assert path.read_bytes() == before and nothing_new_saved(deal)


def test_the_refusal_is_one_clear_message_from_the_command_line(deal):
    write_state("Synthetic Co", "Fleet Loan", financials={"FY-1": {"gross_profit": 350}},
                financials_source="analyst-supplied")
    data = staged_data(deal)
    path = write_staged(deal, data)
    with pytest.raises(SystemExit) as exit_info:
        tc.main(["--transcription", str(path), "--commit", "--confirm", digest(data), "--company", "Synthetic Co",
                 "--proposal", "Fleet Loan"])
    message = str(exit_info.value)
    assert message.startswith("error: refused: this deal's financials_source is 'analyst-supplied'")
    assert "never mixes the two bases" in message and "\n" not in message


@pytest.mark.parametrize("store", ["financials", "ratios", "multi_period_financials", "analyst_supplied_financials"])
def test_figures_with_no_recorded_basis_are_not_assumed_to_be_either_mode(deal, store):
    write_state("Synthetic Co", "Fleet Loan", **{store: {"FY-1": {"revenue": 1}}})
    path = state_file(deal)
    before = path.read_bytes()
    for mode_data, note in ((staged_data(deal), None), (analyst_data(deal), "convention")):
        with pytest.raises(tc.TranscriptionError, match="no financials_source, so their basis is unknown"):
            run_commit(mode_data, source_note=note)
    assert path.read_bytes() == before and nothing_new_saved(deal)


def test_a_deal_with_no_figures_yet_and_a_repeat_in_the_same_mode_are_accepted(deal):
    write_state("Synthetic Co", "Fleet Loan", inputs={"pd": "0.2%"}, steps_completed=["triage"])
    run_commit(staged_data(deal, periods={"FY-1": {"lines": {"revenue": "900", "cost_of_sales": "500"}}}))
    run_commit(staged_data(deal))
    state = read_state("Synthetic Co", "Fleet Loan")
    assert state["financials_source"] == "framework-computed" and len(state["financials_transcriptions"]) == 2


def test_an_analyst_supplied_deal_accepts_a_second_analyst_supplied_transcription(deal):
    run_commit(analyst_data(deal), source_note="Convention A")
    second = analyst_data(deal)
    second["periods"] = {"FY-1": second["periods"]["FY-Current"]}
    run_commit(second, source_note="Convention A")
    state = read_state("Synthetic Co", "Fleet Loan")
    assert set(state["financials"]) == {"FY-1", "FY-Current"} and len(state["financials_transcriptions"]) == 2


def test_mode_conflict_is_decided_by_the_recorded_basis_alone():
    assert tc.mode_conflict({}, "analyst-supplied") is None
    assert tc.mode_conflict({"financials_source": "framework-computed"}, "framework-computed") is None
    assert "never mixes" in tc.mode_conflict({"financials_source": "framework-computed"}, "analyst-supplied")
    assert "unknown" in tc.mode_conflict({"ratios": {"FY-1": {"dscr": 1}}}, "framework-computed")
    assert tc.mode_conflict({"inputs": {}, "steps_completed": ["triage"]}, "framework-computed") is None


# ---------------------------------------------------------------------------
# The read-back and the saved source are the same image
# ---------------------------------------------------------------------------

def test_the_readback_shows_the_images_fingerprint_and_the_digest_covers_it(tmp_path):
    import hashlib
    data = staged_data(tmp_path)
    shown = tc.readback(tc.validate(data))
    image = tmp_path / "shot.png"
    assert shown["source_sha256"] == hashlib.sha256(image.read_bytes()).hexdigest() == tc.file_sha256(image)
    assert f"Source fingerprint (SHA-256): {shown['source_sha256']}" in shown["text"]
    image.write_bytes(PNG + b" replaced")
    assert tc.readback(tc.validate(data))["digest"] != shown["digest"]


def test_replacing_the_image_after_the_readback_invalidates_the_confirmation_and_writes_nothing(deal):
    data = staged_data(deal)
    confirmed = digest(data)
    (deal / "shot.png").write_bytes(PNG + b" a different screenshot")
    with pytest.raises(tc.TranscriptionError, match="the image file itself makes an earlier confirmation stale"):
        run_commit(data, confirm=confirmed)
    assert not deals_exist(deal)
    run_commit(data)                                    # a fresh read-back of the new image can be confirmed


def test_a_missing_image_refuses_the_commit(deal):
    data = staged_data(deal)
    confirmed = digest(data)
    (deal / "shot.png").unlink()
    with pytest.raises(tc.TranscriptionError, match="cannot read the source file"):
        run_commit(data, confirm=confirmed)
    assert not deals_exist(deal)


def test_the_saved_copy_is_the_confirmed_image_and_the_state_holds_its_fingerprint_not_its_bytes(deal):
    result = run_commit(staged_data(deal))
    state_bytes = state_file(deal).read_bytes()
    (saved,) = deal.glob("deals/Synthetic Co/Fleet Loan_*/sources/*.png")
    assert tc.file_sha256(saved) == result["source_sha256"]
    record = read_state("Synthetic Co", "Fleet Loan")["financials_transcriptions"][0]
    assert record["source_sha256"] == result["source_sha256"]
    assert PNG not in state_bytes and b"synthetic" not in state_bytes
    assert result["source_sha256"][:16] in read_manifest("Synthetic Co", "Fleet Loan")[0]["claim"]


def test_an_image_that_changes_while_it_is_being_saved_is_not_saved(deal, monkeypatch):
    data = staged_data(deal)
    confirmed = digest(data)
    real = tc.file_sha256
    calls = []

    def changing(path):
        calls.append(path)
        return real(path) if len(calls) == 1 else "0" * 64          # the staging copy no longer matches

    monkeypatch.setattr(tc, "file_sha256", changing)
    with pytest.raises(tc.TranscriptionError, match="changed while it was being read; nothing was saved"):
        run_commit(data, confirm=confirmed)
    assert not deals_exist(deal)


def test_a_saved_copy_that_does_not_match_is_reported_and_no_figures_are_recorded(deal, monkeypatch):
    import shutil
    data = staged_data(deal)
    confirmed = digest(data)
    real_copy = shutil.copyfile
    copies = []

    def corrupting(source, destination, **kwargs):
        copies.append(destination)
        real_copy(source, destination, **kwargs)
        if len(copies) == 2:                                          # the copy into sources/
            with open(destination, "ab") as f:
                f.write(b"corrupt")
        return destination

    monkeypatch.setattr(shutil, "copyfile", corrupting)
    with pytest.raises(tc.TranscriptionError, match="saved copy .* does not match the fingerprint"):
        run_commit(data, confirm=confirmed)
    assert "financials" not in (read_state("Synthetic Co", "Fleet Loan") or {})


# ---------------------------------------------------------------------------
# Predictable state failures are caught before the image is saved
# ---------------------------------------------------------------------------

def deal_files(root):
    """Every file under deals/ with its bytes: equal before and after means nothing was written, copied or recorded."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in (root / "deals").rglob("*")
            if path.is_file()}


def set_state_key(root, key, value):
    path = state_file(root)
    state = json.loads(path.read_text(encoding="utf-8"))
    state[key] = value
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


@pytest.mark.parametrize("mode, builder", [("framework-computed", staged_data), ("analyst-supplied", analyst_data)])
def test_a_newer_schema_refuses_the_commit_before_the_image_is_saved_in_both_modes(deal, mode, builder):
    write_state("Synthetic Co", "Fleet Loan", inputs={"pd": "0.2%"}, steps_completed=["triage"])
    set_state_key(deal, "schema_version", "9.0.0")
    data = builder(deal)
    confirmed = digest(data)
    before = deal_files(deal)
    assert read_state("Synthetic Co", "Fleet Loan")["schema_version"] == "9.0.0"        # readable, but not writable
    with pytest.raises(SchemaVersionError, match="newer framework .*schema_version 9.0.0.*nothing was changed"):
        run_commit(data, confirm=confirmed, source_note="convention" if mode == "analyst-supplied" else None)
    assert deal_files(deal) == before and nothing_new_saved(deal)


@pytest.mark.parametrize("recorded", ["v1", "1.2", None, 7])
def test_a_schema_version_that_cannot_be_compared_also_refuses_before_anything_is_saved(deal, recorded):
    write_state("Synthetic Co", "Fleet Loan", inputs={})
    set_state_key(deal, "schema_version", recorded)
    before = deal_files(deal)
    with pytest.raises(SchemaVersionError, match="Refusing to write"):
        run_commit(staged_data(deal))
    assert deal_files(deal) == before and nothing_new_saved(deal)


def test_the_refusal_to_write_a_newer_schema_is_still_state_managers_and_still_one_error_line(deal):
    write_state("Synthetic Co", "Fleet Loan", inputs={})
    set_state_key(deal, "schema_version", "9.0.0")
    data = staged_data(deal)
    path = write_staged(deal, data)
    before = deal_files(deal)
    with pytest.raises(SystemExit) as exit_info:
        tc.main(["--transcription", str(path), "--commit", "--confirm", digest(data), "--company", "Synthetic Co",
                 "--proposal", "Fleet Loan"])
    message = str(exit_info.value)
    assert message.startswith("error: Refusing to write") and "would downgrade it" in message
    assert len(message.splitlines()) == 1 and deal_files(deal) == before


def test_a_current_or_older_schema_is_still_accepted(deal):
    write_state("Synthetic Co", "Fleet Loan", inputs={})
    set_state_key(deal, "schema_version", "0.0.0")
    run_commit(staged_data(deal))
    assert read_state("Synthetic Co", "Fleet Loan")["schema_version"] == SCHEMA_VERSION


def test_a_malformed_stress_assumptions_value_of_the_wrong_shape_is_refused_before_the_image_is_saved(deal):
    write_state("Synthetic Co", "Fleet Loan", inputs={})
    set_state_key(deal, "stress_assumptions", ["revenue_haircut_pct", 5])
    before = deal_files(deal)
    with pytest.raises(StateError, match="stress_assumptions"):
        run_commit(staged_data(deal))
    assert deal_files(deal) == before and nothing_new_saved(deal)


def test_stress_assumptions_the_computation_cannot_apply_are_refused_before_the_image_is_saved(deal):
    # A forward year is on file (as after /project), so the stored stress shock is applied to it on every spread.
    write_state("Synthetic Co", "Fleet Loan", financials_source="framework-computed",
                multi_period_financials={"FY+1": {"revenue": 100, "cost_of_sales": 60}},
                stress_assumptions={"revenue_haircut_pct": "five percent"})
    before = deal_files(deal)
    with pytest.raises(tc.TranscriptionError,
                       match=r"cannot be computed against this deal's stored state \(TypeError.*stress_assumptions"):
        run_commit(staged_data(deal))
    assert deal_files(deal) == before and nothing_new_saved(deal)


def test_a_stored_stress_assumption_that_can_be_applied_does_not_block_a_commit(deal):
    write_state("Synthetic Co", "Fleet Loan", financials_source="framework-computed",
                multi_period_financials={"FY+1": {"revenue": 100, "cost_of_sales": 60}},
                stress_assumptions={"revenue_haircut_pct": 5})
    run_commit(staged_data(deal))
    state = read_state("Synthetic Co", "Fleet Loan")
    assert state["downside_case"]["financials"] and state["financials_source"] == "framework-computed"


def test_stress_assumptions_are_not_read_by_the_analyst_supplied_path_so_they_do_not_block_it(deal):
    write_state("Synthetic Co", "Fleet Loan", stress_assumptions=["not", "an", "object"])
    run_commit(analyst_data(deal), source_note="convention")
    assert read_state("Synthetic Co", "Fleet Loan")["financials_source"] == "analyst-supplied"


@pytest.mark.parametrize("content", ["{not json", "{}", '"text"', "\x00\x00"])
def test_an_unreadable_or_non_list_source_manifest_is_refused_before_the_image_is_copied(deal, content):
    write_state("Synthetic Co", "Fleet Loan", inputs={})
    sources = state_file(deal).parent / "sources"
    sources.mkdir()
    (sources / "manifest.json").write_text(content, encoding="utf-8")
    before = deal_files(deal)
    with pytest.raises(tc.TranscriptionError, match="sources/manifest.json .*nothing was saved"):
        run_commit(staged_data(deal))
    assert deal_files(deal) == before and list(sources.iterdir()) == [sources / "manifest.json"]


def test_every_other_predictable_refusal_leaves_the_whole_deal_byte_identical(deal):
    write_state("Synthetic Co", "Fleet Loan", financials_source="analyst-supplied",
                financials={"FY-1": {"gross_profit": 1}}, financials_transcriptions="oops")
    before = deal_files(deal)
    with pytest.raises(StateError, match="financials_transcriptions"):
        run_commit(analyst_data(deal), source_note="convention")
    assert deal_files(deal) == before
    write_state("Synthetic Co", "Fleet Loan", financials_transcriptions=[])
    with pytest.raises(tc.TranscriptionError, match="never mixes the two bases"):
        run_commit(staged_data(deal))
    assert nothing_new_saved(deal)


def test_the_preflight_returns_what_commit_needs_and_changes_nothing(deal):
    write_state("Synthetic Co", "Fleet Loan", inputs={"pd": "0.2%"}, financials_transcriptions=[{"periods": ["FY-1"]}])
    before = deal_files(deal)
    existing, earlier = tc.preflight(tc.validate(staged_data(deal)), "Synthetic Co", "Fleet Loan")
    assert existing["inputs"] == {"pd": "0.2%"} and earlier == [{"periods": ["FY-1"]}]
    assert deal_files(deal) == before
    assert tc.preflight(tc.validate(staged_data(deal)), "Brand New Co", "Loan") == ({}, None)


# ---------------------------------------------------------------------------
# One state update carries the figures, the provenance record and the step together
# ---------------------------------------------------------------------------

@pytest.fixture
def state_writes(monkeypatch):
    """Every call that would write state.json (state_manager's lowest-level writer), recorded by the fields it was
    given. `fail_on` makes the nth call raise instead of writing."""
    calls = []
    real = state_manager._merge_and_write
    behaviour = {"fail_on": None}

    def spy(path, company, proposal, date_str, base_dir, fields):
        calls.append(dict(fields))
        if behaviour["fail_on"] == len(calls):
            raise OSError("injected disk failure")
        return real(path, company, proposal, date_str, base_dir, fields)

    monkeypatch.setattr(state_manager, "_merge_and_write", spy)
    spy.calls, spy.behaviour = calls, behaviour
    return spy


@pytest.mark.parametrize("builder, note, expected", [
    (staged_data, None, {"financials", "ratios", "multi_period_financials", "financials_source",
                         "steps_completed", "financials_transcriptions"}),
    (analyst_data, "convention", {"financials", "ratios", "analyst_supplied_financials", "financials_source",
                                  "financials_source_note", "steps_completed", "financials_transcriptions"}),
])
def test_a_successful_commit_makes_exactly_one_state_update_carrying_everything(deal, state_writes, builder, note,
                                                                               expected):
    run_commit(builder(deal), source_note=note)
    assert len(state_writes.calls) == 1
    assert expected <= set(state_writes.calls[0])
    state = read_state("Synthetic Co", "Fleet Loan")
    assert state["steps_completed"] == ["spread"] and len(state["financials_transcriptions"]) == 1
    assert state["financials"] and state["financials_source"] == builder(deal)["mode"]


def test_the_framework_computed_update_includes_the_downside_fields_when_they_are_derivable(deal, state_writes):
    write_state("Synthetic Co", "Fleet Loan", financials_source="framework-computed",
                multi_period_financials={"FY+1": {"revenue": 100, "cost_of_sales": 60}},
                stress_assumptions={"revenue_haircut_pct": 5})
    state_writes.calls.clear()
    run_commit(staged_data(deal))
    assert len(state_writes.calls) == 1
    assert {"downside_case", "stress_assumptions", "multi_period_financials"} <= set(state_writes.calls[0])
    state = read_state("Synthetic Co", "Fleet Loan")
    assert set(state["multi_period_financials"]) == {"FY+1", "FY-Current"} and state["downside_case"]["financials"]


@pytest.mark.parametrize("builder, note", [(staged_data, None), (analyst_data, "convention")])
def test_a_failure_at_the_one_state_write_records_no_figures_at_all(deal, state_writes, builder, note):
    state_writes.behaviour["fail_on"] = 1
    with pytest.raises(OSError, match="injected disk failure"):
        run_commit(builder(deal), source_note=note)
    assert len(state_writes.calls) == 1, "there was no earlier state write for the figures alone"
    assert not list(deal.glob("deals/*/*/state.json")), "no state.json: no figures, no record, no step"


def test_a_failed_final_write_leaves_an_existing_state_byte_identical_and_reports_the_source_that_remains(deal,
                                                                                                         state_writes):
    write_state("Synthetic Co", "Fleet Loan", inputs={"pd": "0.2%"}, steps_completed=["triage"])
    path = state_file(deal)
    before = path.read_bytes()
    state_writes.calls.clear()
    state_writes.behaviour["fail_on"] = 1
    with pytest.raises(OSError):
        run_commit(staged_data(deal))
    assert path.read_bytes() == before
    # The documented limitation: the verified image and its manifest entry were saved before the state update.
    assert len(read_manifest("Synthetic Co", "Fleet Loan")) == 1
    assert len(list(path.parent.glob("sources/*.png"))) == 1


# ---------------------------------------------------------------------------
# The deal is looked at again, after the image is saved, before the one write
# ---------------------------------------------------------------------------

def after_the_source_is_saved(monkeypatch, action):
    """Run `action()` right after tc.save_source() returns, standing in for another session changing the deal."""
    real = tc.save_source

    def wrapper(*args, **kwargs):
        entry = real(*args, **kwargs)
        action()
        return entry

    monkeypatch.setattr(tc, "save_source", wrapper)


def test_a_deal_that_changes_basis_after_the_preflight_is_refused_at_the_final_check(deal, monkeypatch, state_writes):
    write_state("Synthetic Co", "Fleet Loan", inputs={})
    after_the_source_is_saved(monkeypatch, lambda: write_state("Synthetic Co", "Fleet Loan",
                                                               financials={"FY-1": {"gross_profit": 1}},
                                                               financials_source="analyst-supplied"))
    state_writes.calls.clear()
    with pytest.raises(tc.TranscriptionError, match="state changed after the preflight.*never mixes the two bases"
                                                    ".*were saved; no figures were recorded"):
        run_commit(staged_data(deal))
    assert len(state_writes.calls) == 1, "only the other session's write; this commit wrote nothing"
    assert "financials_transcriptions" not in read_state("Synthetic Co", "Fleet Loan")


def test_a_deal_that_becomes_unwritable_after_the_preflight_is_refused_at_the_final_check(deal, monkeypatch):
    write_state("Synthetic Co", "Fleet Loan", inputs={})
    after_the_source_is_saved(monkeypatch, lambda: set_state_key(deal, "schema_version", "9.0.0"))
    with pytest.raises(tc.TranscriptionError, match="state changed after the preflight.*newer framework"):
        run_commit(staged_data(deal))
    assert "financials" not in json.loads(state_file(deal).read_text(encoding="utf-8"))


def test_the_figures_are_planned_from_the_deal_as_it_is_just_before_the_write(deal, monkeypatch):
    """Another session records an earlier period in the same mode between the preflight and the write: it is kept,
    not overwritten by figures planned from a stale read."""
    write_state("Synthetic Co", "Fleet Loan", financials_source="framework-computed")
    after_the_source_is_saved(monkeypatch, lambda: compute_other_period())
    from spreading_check import compute as ordinary_compute

    def compute_other_period():
        ordinary_compute("Synthetic Co", "Fleet Loan", {"FY-1": {"revenue": 900, "cost_of_sales": 500}})

    run_commit(staged_data(deal))
    state = read_state("Synthetic Co", "Fleet Loan")
    assert set(state["financials"]) == {"FY-1", "FY-Current"} and set(state["multi_period_financials"]) == {
        "FY-1", "FY-Current"}
