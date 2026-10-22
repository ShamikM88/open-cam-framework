"""Call accounting for the local live-model evaluation harness (issue #151).

A live evaluation spends real money, so the harness never starts a run it
can't afford and never lets a bug turn into an unbounded loop of API calls:

- `check_plan()` refuses, **before the first call**, a plan that exceeds the
  caller's `--max-calls`, or a `--max-calls` above the hard ceiling in code.
- `BudgetedClient` wraps whatever client does the real calls and refuses the
  call after the cap is reached, so even a runner bug can't overspend.

No `anthropic` dependency (the client is passed in), so all of this is
unit-tested in ordinary CI with a fake client and zero model calls.

**Read before relying on the cap (PR 2 requirements):**

- `CALLS_PER_RUN = 1` holds only if the pipeline cannot revise. `run_pipeline()` re-calls
  the live Maker after every REJECTED iteration (up to `MAX_REVIEW_ITERATIONS`), and a draft
  that fails deterministic checks is rejected even when the Checker is scripted to approve --
  exactly the draft the fabrication and canary cases provoke. The live runner must therefore
  pass `max_iterations=1`; the BudgetedClient is the backstop that aborts if it ever does not.
- The cap counts **logical** `messages.create()` calls. The SDK's own HTTP retries are
  invisible here, so the live client should be built with `max_retries=0`.
- The cap bounds **calls**, not tokens or money; usage is recorded, not capped.
"""

import threading

DEFAULT_REPEATS = 5
DEFAULT_MAX_CALLS = 80
ABSOLUTE_MAX_CALLS = 250  # no flag can raise the cap above this

# Every case costs exactly one live call per run: a Maker case scripts the
# Checker's response, a Checker case scripts the Maker's draft.
CALLS_PER_RUN = 1


class CallCapExceeded(RuntimeError):
    """The plan, or an actual call, would exceed the call cap."""


def planned_calls(case_count, repeats, calls_per_run=CALLS_PER_RUN):
    return case_count * repeats * calls_per_run


def check_plan(planned, max_calls):
    """Raise CallCapExceeded unless `planned` fits under `max_calls`, which
    itself must not exceed ABSOLUTE_MAX_CALLS. Called before any model call."""
    if max_calls < 1:
        raise ValueError("--max-calls must be at least 1")
    if planned < 0:
        raise ValueError("a plan cannot have a negative number of calls")
    if max_calls > ABSOLUTE_MAX_CALLS:
        raise CallCapExceeded(
            f"--max-calls {max_calls} exceeds the absolute ceiling of {ABSOLUTE_MAX_CALLS} calls "
            "per run (a constant in scripts/eval_budget.py, deliberately not overridable by a flag)."
        )
    if planned > max_calls:
        raise CallCapExceeded(
            f"This run would make {planned} model calls, over the cap of {max_calls}. "
            "Lower --repeats or the number of cases, or raise --max-calls "
            f"(up to {ABSOLUTE_MAX_CALLS})."
        )


class CallBudget:
    """Counts calls (and token usage, when the responses report it)."""

    def __init__(self, max_calls):
        check_plan(0, max_calls)  # validates max_calls itself
        self.max_calls = max_calls
        self._lock = threading.Lock()  # check-then-increment must be atomic if a runner ever parallelises
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def spend(self):
        """Reserve one call, or raise CallCapExceeded if none is left."""
        with self._lock:
            if self.calls >= self.max_calls:
                raise CallCapExceeded(
                    f"Refusing call {self.calls + 1}: the run's cap of {self.max_calls} calls is spent."
                )
            self.calls += 1

    def record_usage(self, response):
        usage = getattr(response, "usage", None)
        with self._lock:
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0


class _Messages:
    def __init__(self, client, budget):
        self._client = client
        self._budget = budget

    def create(self, **kwargs):
        self._budget.spend()  # raises before the real call is made
        response = self._client.messages.create(**kwargs)
        self._budget.record_usage(response)
        return response


class BudgetedClient:
    """Drop-in for the `client` argument of `orchestrator.run_pipeline()`:
    forwards `messages.create(**kwargs)` to `client` through a CallBudget."""

    def __init__(self, client, budget):
        self.budget = budget
        self.messages = _Messages(client, budget)
