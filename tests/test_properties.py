"""Property-based tests (hypothesis) for the logic that hand-picked examples under-test (issue #143).

During review of the calibrate overflow work the chunker and the merge loop were fuzzed by hand; none of that was
kept. These properties keep it, and each is also run against a deliberately broken implementation (the
`test_*_catches_*` tests) to prove it would notice a regression. Settings come from the profile registered in
conftest.py: a bounded, deterministic profile in CI, a larger random one with `HYPOTHESIS_PROFILE=explore`.

Covered:
- `calibrate._split_into_chunks`: lossless, every chunk within the limit, breaks only where it should;
- `calibrate._merge_parts`: always terminates with exactly one result, in at most n-1 merge calls;
- `policy_checks.values_match`: the tolerance rule's shape (monotonic, sign-symmetric, scale-invariant);
- `spreading_check.compute`: merging the same raw period twice changes nothing, and a partial update never
  drops a previously recorded field (the bug class fixed in #122);
- `spreading_builder.evaluate_financial_model`: zero or negative denominators give None, nothing raises or yields
  NaN/inf, net debt and scaling identities, determinism, and the caller's data is never mutated.

Negative denominators once gave a finite negative ratio, which let a borrower with negative EBITDA or equity PASS a
maximum-leverage or maximum-gearing covenant (found while writing these tests, tracked as #169, recorded here as
strict xfails until fixed). They are now N/A and the covenant is UNRESOLVABLE; the regression tests are below and
tests/test_nonpositive_denominators.py covers the covenant, downside and workbook sides.
"""
import builtins
import itertools
import math
import os
import tempfile
from contextlib import contextmanager

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import calibrate
import policy_engine
import spreading_builder
import spreading_check
from policy_checks import values_match

# When a property fails in more than one way, hypothesis reports an ExceptionGroup (Python 3.11+) instead of a bare
# AssertionError; either means "the property noticed the breakage".
PROPERTY_FAILED = (AssertionError,) + ((builtins.BaseExceptionGroup,) if hasattr(builtins, "BaseExceptionGroup") else ())

# ---------------------------------------------------------------------------
# calibrate._split_into_chunks
# ---------------------------------------------------------------------------

PIECES = ["word", "a", "é", "日本", "\n\n", "\n", " ", "  ", "\t", "x" * 30]
texts = st.one_of(
    st.text(max_size=300),
    st.lists(st.sampled_from(PIECES), max_size=80).map("".join),
)
SEPARATORS = ("\n\n", "\n", " ")


def check_chunker(split):
    @given(texts, st.integers(min_value=1, max_value=60))
    def prop(text, limit):
        chunks = split(text, limit)
        assert "".join(chunks) == text                       # lossless
        assert all(0 < len(chunk) <= limit for chunk in chunks)   # none empty, none over the limit
        for chunk in chunks[:-1]:
            if len(chunk) < limit:                           # a short non-final chunk ended on a separator...
                assert chunk.endswith(SEPARATORS), repr(chunk)
            elif not chunk.endswith(SEPARATORS):             # ...and a hard cut happened only when no separator
                assert all(chunk.rfind(sep) < limit // 2 for sep in SEPARATORS), repr(chunk)   # was usable
        assert len(chunks) >= math.ceil(len(text) / limit)  # no chunk is bigger than allowed
    prop()


def test_the_chunker_is_lossless_bounded_and_breaks_on_separators():
    check_chunker(calibrate._split_into_chunks)


def _lossy(text, limit):
    return calibrate._split_into_chunks(text, limit)[:-1] if len(text) > limit else calibrate._split_into_chunks(text, limit)


def _oversized(text, limit):
    return calibrate._split_into_chunks(text, limit + 1)


def _hard_cut_everywhere(text, limit):
    return [text[i:i + limit] for i in range(0, len(text), limit)]   # lossless and bounded, but ignores separators


@pytest.mark.parametrize("broken", [_lossy, _oversized, _hard_cut_everywhere],
                         ids=["drops-the-tail", "exceeds-the-limit", "ignores-separators"])
def test_the_chunker_property_catches_each_deliberate_breakage(broken):
    with pytest.raises(PROPERTY_FAILED):
        check_chunker(broken)


@given(st.integers(min_value=1, max_value=4000))
def test_a_text_of_exactly_the_limit_is_one_chunk_and_one_more_is_two(limit):
    assert len(calibrate._split_into_chunks("x" * limit, limit)) == 1
    assert len(calibrate._split_into_chunks("x" * (limit + 1), limit)) == 2


# ---------------------------------------------------------------------------
# calibrate._merge_parts
# ---------------------------------------------------------------------------

def run_merge(parts, budget, output_sizes, monkeypatch, merge=None):
    calls = []
    sizes = itertools.cycle(output_sizes or [1])

    def fake_complete(model, prompt, max_tokens=3000):
        calls.append(prompt)
        return "M" * next(sizes)

    monkeypatch.setattr(calibrate, "_complete", fake_complete)
    monkeypatch.setattr(calibrate, "MERGE_CHAR_BUDGET", budget)
    result = (merge or calibrate._merge_parts)(list(parts), "{text}", "model-x", "style")
    return result, calls


def check_merge(merge, monkeypatch):
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(st.lists(st.text(max_size=120), min_size=1, max_size=40),
           st.integers(min_value=1, max_value=400),
           st.lists(st.integers(min_value=0, max_value=500), min_size=1, max_size=8))
    def prop(parts, budget, output_sizes):
        result, calls = run_merge(parts, budget, output_sizes, monkeypatch, merge)
        assert isinstance(result, str)                         # exactly one result...
        assert len(calls) <= len(parts) - 1                    # ...reached by at most n-1 merges (each folds >= 2 into 1)
        if len(parts) == 1:
            assert calls == [] and result == parts[0]          # nothing to merge, no call spent
        for prompt in calls:
            assert "Part 1:" in prompt and "Part 2:" in prompt   # every call merges at least two parts
    prop()


def test_the_merge_loop_always_terminates_with_one_result_in_at_most_n_minus_one_calls(monkeypatch):
    check_merge(calibrate._merge_parts, monkeypatch)


def _one_group_of_one(parts, merge_prompt, model, label):
    result = calibrate._merge_parts(parts, merge_prompt, model, label)
    calibrate._complete(model, "extra call")                    # spends a call it did not need
    return result


def _returns_a_list(parts, merge_prompt, model, label):
    return [calibrate._merge_parts(parts, merge_prompt, model, label)]


@pytest.mark.parametrize("broken", [_one_group_of_one, _returns_a_list], ids=["extra-call", "not-one-result"])
def test_the_merge_property_catches_each_deliberate_breakage(monkeypatch, broken):
    with pytest.raises(PROPERTY_FAILED):
        check_merge(broken, monkeypatch)


def test_merging_one_part_costs_no_call_and_empty_input_is_an_index_error(monkeypatch):
    result, calls = run_merge(["only"], 10, [5], monkeypatch)
    assert result == "only" and calls == []
    with pytest.raises(IndexError):          # documented limit: callers never pass an empty list (chunks are non-empty)
        run_merge([], 10, [5], monkeypatch)


# ---------------------------------------------------------------------------
# policy_checks.values_match
# ---------------------------------------------------------------------------

finite = st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False)
nonzero = finite.filter(lambda v: abs(v) > 1e-6)
tolerance = st.floats(min_value=1e-6, max_value=0.5)


def check_values_match(match):
    @given(nonzero, tolerance)
    def reflexive(value, tol):
        assert match(value, value, tol)

    @given(finite, nonzero, tolerance, st.floats(min_value=0.0, max_value=10.0))
    def monotonic_in_tolerance(reported, actual, tol, extra):
        if match(reported, actual, tol):
            assert match(reported, actual, tol + extra)

    @given(nonzero, st.floats(min_value=0.0, max_value=1.0), st.floats(min_value=0.0, max_value=1.0), tolerance)
    def monotonic_in_distance(actual, near_fraction, far_fraction, tol):
        near, far = sorted((near_fraction, far_fraction))
        reported_near = actual * (1 + near)
        reported_far = actual * (1 + far)
        if match(reported_far, actual, tol):
            assert match(reported_near, actual, tol)

    @given(finite, nonzero, tolerance)
    def sign_symmetric(reported, actual, tol):
        assert match(reported, actual, tol) == match(-reported, -actual, tol)

    @given(finite, nonzero)
    def honours_the_tolerance_it_is_given(reported, actual):
        error = abs(reported - actual) / abs(actual)
        assert match(reported, actual, error + 1e-6)           # a tolerance just above the error accepts it
        if error > 1e-4:
            assert not match(reported, actual, error * 0.5)    # a tolerance well below the error rejects it

    @given(finite, nonzero, tolerance, st.floats(min_value=0.5, max_value=2000.0))
    def scale_invariant(reported, actual, tol, k):
        ratio = abs(reported - actual) / abs(actual)
        if abs(ratio - tol) > 1e-6:   # not within rounding of the boundary
            assert match(reported * k, actual * k, tol) == match(reported, actual, tol)

    for prop in (reflexive, monotonic_in_tolerance, monotonic_in_distance, sign_symmetric,
                 honours_the_tolerance_it_is_given, scale_invariant):
        prop()


def test_values_match_keeps_its_tolerance_shape():
    check_values_match(values_match)


def _tolerance_ignored(reported, actual, tol=0.005):
    return values_match(reported, actual, 0.005)


def _symmetric_in_its_arguments(reported, actual, tol=0.005):
    return values_match(reported, actual, tol) or values_match(actual, reported, tol)


def _absolute_not_relative(reported, actual, tol=0.005):
    return abs(float(reported) - float(actual)) <= tol


@pytest.mark.parametrize("broken", [_tolerance_ignored, _absolute_not_relative],
                         ids=["ignores-the-tolerance", "absolute-not-relative"])
def test_the_values_match_property_catches_each_deliberate_breakage(broken):
    with pytest.raises(PROPERTY_FAILED):
        check_values_match(broken)


def test_values_match_is_relative_to_the_actual_value_so_it_is_not_symmetric():
    """The issue text asks for a "symmetric" tolerance. The rule divides by the ACTUAL value, so swapping the two
    arguments can change the answer; that is intended ("a fixed 0.5% of the actual figure"), and is pinned here."""
    assert values_match(100.0, 200.0, 0.5) is True       # |100-200| / |200| = 0.5
    assert values_match(200.0, 100.0, 0.5) is False      # |200-100| / |100| = 1.0: same two numbers, swapped


def test_values_match_handles_zero_and_non_numbers():
    assert values_match(0, 0) and values_match(1e-12, 0) and not values_match(0.1, 0)
    assert not values_match("abc", 1) and not values_match(None, 1) and not values_match(1, None)


# ---------------------------------------------------------------------------
# spreading_check.compute: merging
# ---------------------------------------------------------------------------

RAW_FIELDS = sorted(spreading_builder.FIELD_LABELS)
amounts = st.integers(min_value=-5_000_000, max_value=5_000_000)
raw_periods = st.dictionaries(st.sampled_from(RAW_FIELDS), amounts, min_size=1, max_size=12)
period_names = st.sampled_from(["FY-2", "FY-1", "FY-Current", "FY+1", "FY+2"])
counter = itertools.count()


@contextmanager
def deal_dir():
    """A fresh working directory per example (compute() keeps its state under ./deals/)."""
    previous = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            yield
        finally:
            os.chdir(previous)


def fresh_deal():
    return f"Synthetic Co {next(counter)}", "Synthetic Loan"


def check_merge_behaviour(compute):
    @settings(max_examples=40)
    @given(period_names, raw_periods)
    def idempotent(period, raw):
        with deal_dir():
            company, proposal = fresh_deal()
            first = compute(company, proposal, {period: raw})
            second = compute(company, proposal, {period: raw})
            for key in ("multi_period_financials", "financials", "ratios"):
                assert first[key] == second[key], key

    @settings(max_examples=40)
    @given(period_names, raw_periods, raw_periods)
    def partial_update_keeps_every_other_field(period, original, update):
        with deal_dir():
            company, proposal = fresh_deal()
            compute(company, proposal, {period: original})
            merged = compute(company, proposal, {period: update})
            assert merged["multi_period_financials"][period] == {**original, **update}
            direct = spreading_builder.evaluate_financial_model({period: {**original, **update}})
            assert merged["financials"][period] == direct["financials"][period]

    @settings(max_examples=40)
    @given(period_names, period_names, raw_periods, raw_periods)
    def other_periods_are_untouched(first_period, second_period, first_raw, second_raw):
        if first_period == second_period:
            return
        with deal_dir():
            company, proposal = fresh_deal()
            compute(company, proposal, {first_period: first_raw})
            state = compute(company, proposal, {second_period: second_raw})
            assert state["multi_period_financials"][first_period] == first_raw
            assert state["multi_period_financials"][second_period] == second_raw
            assert set(state["financials"]) == {first_period, second_period}

    for prop in (idempotent, partial_update_keeps_every_other_field, other_periods_are_untouched):
        prop()


def test_compute_merges_periods_idempotently_without_dropping_fields():
    check_merge_behaviour(spreading_check.compute)


def _replaces_the_period(company, proposal, multi_period_financials=None, **kwargs):
    """The #122 bug class: a period given again replaces the recorded one instead of merging into it."""
    from state_manager import read_state
    state = read_state(company, proposal) or {}
    if multi_period_financials and state.get("multi_period_financials"):
        for period in multi_period_financials:
            state["multi_period_financials"].pop(period, None)
        import state_manager
        state_manager.write_state(company, proposal, multi_period_financials=state["multi_period_financials"])
    return spreading_check.compute(company, proposal, multi_period_financials, **kwargs)


def _forgets_earlier_periods(company, proposal, multi_period_financials=None, **kwargs):
    import state_manager
    state_manager.write_state(company, proposal, multi_period_financials={})
    return spreading_check.compute(company, proposal, multi_period_financials, **kwargs)


@pytest.mark.parametrize("broken", [_replaces_the_period, _forgets_earlier_periods],
                         ids=["replaces-instead-of-merging", "forgets-other-periods"])
def test_the_merge_properties_catch_each_deliberate_breakage(broken):
    with pytest.raises(PROPERTY_FAILED):
        check_merge_behaviour(broken)


# ---------------------------------------------------------------------------
# spreading_builder.evaluate_financial_model: edge cases and identities
# ---------------------------------------------------------------------------

raw_figures = st.dictionaries(st.sampled_from(RAW_FIELDS), st.one_of(amounts, st.none()), max_size=len(RAW_FIELDS))
# Denominators of each ratio, as (raw, financials) -> number. Zero or negative means the ratio is N/A (issue #169).
DENOMINATORS = {
    "dscr": lambda raw, fin: (raw.get("interest_paid") or 0) + (raw.get("scheduled_principal") or 0),
    "gross_leverage": lambda raw, fin: fin["ebitda"],
    "net_debt_to_ebitda": lambda raw, fin: fin["ebitda"],
    "current_ratio": lambda raw, fin: fin["current_liabilities"],
    "gearing": lambda raw, fin: fin["total_equity"],
    "ebit_interest_cover": lambda raw, fin: raw.get("interest_paid") or 0,
    "ebitda_interest_cover": lambda raw, fin: raw.get("interest_paid") or 0,
    "fcf_conversion_pct": lambda raw, fin: fin["ebitda"],
    "trade_debtor_days": lambda raw, fin: raw.get("revenue") or 0,
    "trade_creditor_days": lambda raw, fin: raw.get("cost_of_sales") or 0,
    "stock_days": lambda raw, fin: raw.get("cost_of_sales") or 0,
}


def check_zero_denominators(evaluate):
    @given(raw_figures)
    def prop(raw):
        out = evaluate({"FY-Current": raw})
        financials, ratios = out["financials"]["FY-Current"], out["ratios"]["FY-Current"]
        for name, denominator in DENOMINATORS.items():
            assert (ratios[name] is None) == (denominator(raw, financials) <= 0), name
        days = [ratios[k] for k in ("trade_debtor_days", "stock_days", "trade_creditor_days")]
        assert (ratios["working_capital_cycle_days"] is None) == (None in days)
        assert ratios["EBIT/Interest"] == ratios["ebit_interest_cover"]
        assert ratios["EBITDA/Interest"] == ratios["ebitda_interest_cover"]
        for name, value in ratios.items():
            assert value is None or (isinstance(value, (int, float)) and math.isfinite(value)), name
    prop()


def test_a_zero_or_negative_denominator_gives_none_and_nothing_raises_or_goes_nan_or_infinite():
    check_zero_denominators(spreading_builder.evaluate_financial_model)


def _zero_instead_of_none(multi_period_data):
    out = spreading_builder.evaluate_financial_model(multi_period_data)
    for ratios in out["ratios"].values():
        for key, value in ratios.items():
            if value is None:
                ratios[key] = 0
    return out


def _none_for_a_nonzero_denominator(multi_period_data):
    out = spreading_builder.evaluate_financial_model(multi_period_data)
    for ratios in out["ratios"].values():
        ratios["gearing"] = None
    return out


def _a_number_for_a_negative_denominator(multi_period_data):
    """The #169 defect itself: divide by a negative denominator anyway."""
    out = spreading_builder.evaluate_financial_model(multi_period_data)
    for period, ratios in out["ratios"].items():
        for name, denominator in DENOMINATORS.items():
            if denominator(multi_period_data[period], out["financials"][period]) < 0:
                ratios[name] = -1.0
    return out


@pytest.mark.parametrize("broken", [_zero_instead_of_none, _none_for_a_nonzero_denominator,
                                    _a_number_for_a_negative_denominator],
                         ids=["zero-instead-of-none", "none-when-defined", "number-when-negative"])
def test_the_denominator_property_catches_each_deliberate_breakage(broken):
    with pytest.raises(PROPERTY_FAILED):
        check_zero_denominators(broken)


def test_missing_and_none_inputs_are_treated_as_zero_and_the_empty_model_is_empty():
    assert spreading_builder.evaluate_financial_model({}) == {"financials": {}, "ratios": {}}
    assert spreading_builder.evaluate_financial_model(None) == {"financials": {}, "ratios": {}}
    out = spreading_builder.evaluate_financial_model({"FY-Current": {"revenue": None, "cash": None}})
    assert out["financials"]["FY-Current"]["total_assets"] == 0
    assert all(value is None for value in out["ratios"]["FY-Current"].values())   # nothing to divide by anywhere


@given(raw_figures)
def test_net_debt_to_ebitda_is_total_debt_less_cash_over_ebitda(raw):
    out = spreading_builder.evaluate_financial_model({"FY-Current": raw})
    financials, ratios = out["financials"]["FY-Current"], out["ratios"]["FY-Current"]
    if financials["ebitda"] <= 0:
        assert ratios["net_debt_to_ebitda"] is None
    else:
        net_debt = financials["total_debt"] - (raw.get("cash") or 0)
        assert ratios["net_debt_to_ebitda"] * financials["ebitda"] == pytest.approx(net_debt, rel=1e-9, abs=1e-6)


@given(raw_figures, st.integers(min_value=1, max_value=5000))
def test_scaling_every_amount_scales_money_and_leaves_ratios_alone(raw, k):
    scaled_raw = {key: (value * k if value is not None else None) for key, value in raw.items()}
    base = spreading_builder.evaluate_financial_model({"FY-Current": raw})
    scaled = spreading_builder.evaluate_financial_model({"FY-Current": scaled_raw})
    for name, value in base["financials"]["FY-Current"].items():
        if name == "raw":
            continue
        assert scaled["financials"]["FY-Current"][name] == value * k, name
    for name, value in base["ratios"]["FY-Current"].items():
        other = scaled["ratios"]["FY-Current"][name]
        if value is None:
            assert other is None, name
        else:
            assert other == pytest.approx(value, rel=1e-9, abs=1e-12), name


@given(raw_figures)
def test_the_model_is_deterministic_and_never_mutates_or_aliases_its_input(raw):
    import copy
    original = copy.deepcopy(raw)
    first = spreading_builder.evaluate_financial_model({"FY-Current": raw})
    second = spreading_builder.evaluate_financial_model({"FY-Current": raw})
    assert first == second
    assert raw == original                                      # the caller's dict is untouched
    first["financials"]["FY-Current"]["raw"]["revenue"] = "mutated"
    assert raw == original                                      # and the output holds a copy, not the same object


# ---------------------------------------------------------------------------
# Negative denominators are N/A (issue #169; these were strict xfails until it was fixed)
# ---------------------------------------------------------------------------

LOSS_MAKING = {"revenue": 1000, "cost_of_sales": 1200, "admin_expenses": 100, "interest_paid": 50,
               "scheduled_principal": 20, "long_term_debt": 500, "share_capital": 100, "retained_profit": -300,
               "cash": 10}


@pytest.mark.parametrize("ratio", ["gross_leverage", "net_debt_to_ebitda", "fcf_conversion_pct"])
def test_a_negative_ebitda_gives_na_for_the_ratios_it_divides(ratio):
    model = spreading_builder.evaluate_financial_model({"FY-Current": LOSS_MAKING})
    assert model["financials"]["FY-Current"]["ebitda"] < 0
    assert model["ratios"]["FY-Current"][ratio] is None


def test_negative_equity_gives_na_gearing():
    model = spreading_builder.evaluate_financial_model({"FY-Current": LOSS_MAKING})
    assert model["financials"]["FY-Current"]["total_equity"] < 0
    assert model["ratios"]["FY-Current"]["gearing"] is None


def test_a_loss_making_borrower_does_not_pass_maximum_leverage_and_gearing_covenants():
    model = spreading_builder.evaluate_financial_model({"FY-Current": LOSS_MAKING})
    state = {"financials": model["financials"], "ratios": model["ratios"], "covenants": [
        {"metric": "gross_leverage", "type": "maximum", "threshold": 3.5},
        {"metric": "gearing", "type": "maximum", "threshold": 2.0}]}
    statuses = {r["metric"]: r["status"] for r in policy_engine.evaluate_deal_policy(state)["covenant_results"]}
    assert statuses == {"gross_leverage": "UNRESOLVABLE", "gearing": "UNRESOLVABLE"}   # never PASS, never a false FAIL
