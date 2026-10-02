"""Tests for scripts/eval_budget.py: the evaluation harness's call accounting
(issue #151). No client, network or model is involved -- a fake client stands in.
"""
from types import SimpleNamespace

import pytest

import eval_budget
from eval_budget import (
    ABSOLUTE_MAX_CALLS,
    DEFAULT_MAX_CALLS,
    DEFAULT_REPEATS,
    BudgetedClient,
    CallBudget,
    CallCapExceeded,
    check_plan,
    planned_calls,
)


def test_the_agreed_defaults_are_pinned():
    assert (DEFAULT_REPEATS, DEFAULT_MAX_CALLS, ABSOLUTE_MAX_CALLS) == (5, 80, 250)


def test_planned_calls_is_cases_times_repeats_times_one_live_call_per_run():
    assert planned_calls(15, 5) == 75
    assert planned_calls(15, 1) == 15
    assert planned_calls(0, 5) == 0


def test_a_plan_within_the_cap_is_accepted():
    check_plan(75, 80)
    check_plan(80, 80)


def test_a_plan_over_the_cap_is_refused_with_a_clear_message():
    with pytest.raises(CallCapExceeded, match=r"81 model calls.*cap of 80"):
        check_plan(81, 80)


def test_a_cap_above_the_absolute_ceiling_is_refused_even_for_a_small_plan():
    with pytest.raises(CallCapExceeded, match="absolute ceiling of 250"):
        check_plan(10, 251)
    check_plan(250, 250)  # the ceiling itself is allowed


def test_a_cap_below_one_is_invalid():
    with pytest.raises(ValueError):
        check_plan(0, 0)


def test_the_default_dataset_plan_fits_the_default_cap():
    # 15 cases x 5 repeats = 75 <= 80: guards against the dataset quietly outgrowing the cap.
    from eval_cases import load_dataset
    cases = len(load_dataset("v1")["cases"])
    check_plan(planned_calls(cases, DEFAULT_REPEATS), DEFAULT_MAX_CALLS)


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text="ok")],
                               usage=SimpleNamespace(input_tokens=11, output_tokens=7))


def test_budget_allows_exactly_max_calls_then_refuses():
    budget = CallBudget(3)
    for _ in range(3):
        budget.spend()
    with pytest.raises(CallCapExceeded, match="call 4"):
        budget.spend()
    assert budget.calls == 3


def test_budget_itself_rejects_a_cap_above_the_ceiling():
    with pytest.raises(CallCapExceeded):
        CallBudget(ABSOLUTE_MAX_CALLS + 1)


def test_budgeted_client_forwards_calls_and_counts_usage():
    fake, budget = FakeClient(), CallBudget(5)
    client = BudgetedClient(fake, budget)

    response = client.messages.create(model="m", max_tokens=10, messages=[])

    assert response.content[0].text == "ok"
    assert fake.calls == [{"model": "m", "max_tokens": 10, "messages": []}]
    assert (budget.calls, budget.input_tokens, budget.output_tokens) == (1, 11, 7)


def test_budgeted_client_refuses_the_call_past_the_cap_without_making_it():
    fake, budget = FakeClient(), CallBudget(2)
    client = BudgetedClient(fake, budget)
    client.messages.create(model="m")
    client.messages.create(model="m")

    with pytest.raises(CallCapExceeded):
        client.messages.create(model="m")

    assert len(fake.calls) == 2  # the third never reached the real client


def test_a_failing_call_still_counts_against_the_cap():
    class Exploding(FakeClient):
        def _create(self, **kwargs):
            raise RuntimeError("API error")

    budget = CallBudget(2)
    client = BudgetedClient(Exploding(), budget)
    with pytest.raises(RuntimeError):
        client.messages.create(model="m")
    assert budget.calls == 1  # a retry loop cannot overspend by failing


def test_responses_without_usage_are_tolerated():
    class NoUsage(FakeClient):
        def _create(self, **kwargs):
            return SimpleNamespace(content=[])

    budget = CallBudget(2)
    BudgetedClient(NoUsage(), budget).messages.create(model="m")
    assert (budget.input_tokens, budget.output_tokens) == (0, 0)
    assert eval_budget.CALLS_PER_RUN == 1

def test_a_negative_plan_is_invalid():
    with pytest.raises(ValueError):
        check_plan(-1, 80)


def test_concurrent_spends_never_exceed_the_cap():
    import threading
    budget = CallBudget(10)
    outcomes = []

    def worker():
        try:
            budget.spend()
            outcomes.append("ok")
        except CallCapExceeded:
            outcomes.append("refused")

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 10 and outcomes.count("refused") == 30 and budget.calls == 10


def test_the_pr2_requirements_are_written_down_next_to_the_cap():
    doc = " ".join(eval_budget.__doc__.split())
    assert "max_iterations=1" in doc and "max_retries=0" in doc and "bounds **calls**" in doc

def test_spend_and_usage_recording_take_the_lock():
    class CountingLock:
        def __init__(self):
            self.entries = 0

        def __enter__(self):
            self.entries += 1

        def __exit__(self, *exc):
            return False

    budget = CallBudget(5)
    budget._lock = CountingLock()
    budget.spend()
    budget.record_usage(SimpleNamespace(usage=SimpleNamespace(input_tokens=1, output_tokens=2)))
    assert budget._lock.entries == 2  # check-then-increment is atomic, not merely lucky under the GIL
