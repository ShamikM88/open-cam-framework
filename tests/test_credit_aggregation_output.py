"""B4 (issue #472): the committee summary shows a credit aggregation per period, output only.

Synthetic data. `computed` is hand-built because the accounting-gated B2b step does not exist yet.
"""
import copy

import pytest

import committee_summary as cs

PERIOD = "FY-Current"
COMPUTED = {"financials": {"ebitda": 500.0, "total_debt": 900.0}, "ratios": {"gross_leverage": 1.8}}


def _state():
    return {
        "financials": {PERIOD: {"ebitda": 100.0}},
        "ratios": {PERIOD: {"gross_leverage": 5.0}},
        "credit_aggregation": {PERIOD: {
            "manifest": {
                "participants": ["@borrower", "sub_a"], "period_end": "2025-12-31",
                "period_end_evidence": "Synthetic accounts",
                "scope_confirmations": {"sub_a": {"confirmed": True}},
            },
            "input_fingerprint": "ab" * 32,
            "computed": copy.deepcopy(COMPUTED),
        }},
    }


def _render(state):
    data = {"company": "Co", "proposal": "P", "date": "2026-01-01", "state": state,
            "policy": {"covenant_results": [], "security_gaps": [], "required_conditions_precedent": [],
                       "required_conditions_subsequent": [], "downside_covenant_breaches": [],
                       "forward_covenant_results": []},
            "manifest_count": 0, "sources_declared": "none", "sources_missing": False}
    return cs.render_markdown(data)


@pytest.fixture
def status(monkeypatch):
    def set_status(value, reason=None):
        monkeypatch.setattr(cs, "aggregation_period_status", lambda st, period: {
            "status": value, "reason": reason, "input_fingerprint": "ab" * 32})
    return set_status


def test_no_aggregation_section_for_a_deal_without_one():
    assert "Credit aggregation" not in _render({"financials": {}, "ratios": {}})


def test_a_current_period_shows_scope_period_end_fingerprint_and_computed_figures(status):
    status("current")
    text = _render(_state())
    assert "## Credit aggregation (not a consolidation)" in text
    assert "@borrower" in text and "sub_a" in text
    assert "2025-12-31" in text and "Synthetic accounts" in text
    assert "abababab" in text
    assert "current" in text and "gross_leverage" in text and "1.8" in text


def test_the_limitations_are_always_stated(status):
    status("current")
    text = _render(_state())
    assert "never a statutory consolidation" in text
    for item in ("equity", "goodwill", "non-controlling interests"):
        assert item in text


@pytest.mark.parametrize("value", ["not_calculated", "stale", "invalid", "absent"])
def test_a_period_that_is_not_current_shows_its_status_and_reason_but_no_figures(status, value):
    status(value, f"reason for {value}")
    text = _render(_state())
    assert value.replace("_", " ") in text and f"reason for {value}" in text
    assert "1.8" not in text and "500" not in text


def test_the_borrower_ratios_are_never_summed_with_entity_figures(status):
    status("current")
    text = _render(_state())
    assert "5.0" in text or "5" in text  # the borrower's own figure stays in its own table
    assert "6.8" not in text and "600" not in text
