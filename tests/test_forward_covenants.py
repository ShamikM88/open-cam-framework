"""Covenants against every projected forward base-case year (issue #176).

Before, `covenant_results` looked at FY-Current only and `downside_covenant_breaches` reported only a covenant that
PASSes in the base case and breaks under stress, so a covenant that FAILs or cannot be tested (N/A ratio) in a
forward year's own base case was recorded nowhere. The decided semantics, pinned here:

- a covenant applies to every forward year (FY+1..FY+3) with a recorded, non-empty ratio set; a year with nothing
  recorded is not evaluated;
- `policy_state["forward_covenant_results"]` is the complete record (PASS, FAIL, UNRESOLVABLE), one entry per year
  and covenant, each with year, metric, status, actual, reason and a stable `forward_id`;
- it is DISCLOSURE ONLY: no code-enforced reject reason is ever derived from it;
- the model-facing context shows only the non-PASS entries, as data;
- FY-Current results and downside breaches are unchanged, and nothing is disclosed twice.
"""
import json

import pytest
from test_orchestrator import MockClient, _approved_json, _compliant_draft
from test_policy_check import _compliant_draft as _policy_check_draft

import policy_checks
import policy_engine
from orchestrator import _build_grounding_context, run_pipeline
from policy_check import compute
from spreading_builder import evaluate_downside_case, evaluate_financial_model
from state_manager import read_state, write_state

HEALTHY = {"revenue": 1000, "cost_of_sales": 400, "admin_expenses": 200, "interest_paid": 50, "scheduled_principal": 100,
           "trade_debtors": 120, "stock": 60, "trade_creditors": 80, "cash": 100, "current_debt": 100,
           "long_term_debt": 400, "share_capital": 100, "retained_profit": 400}
LOSS = dict(HEALTHY, revenue=500)                                  # EBITDA 500 - 400 - 200 = -100
HEAVY_DEBT = dict(HEALTHY, long_term_debt=2000)                    # leverage (100 + 2000) / 400 = 5.25

MAX_LEVERAGE = {"metric": "gross_leverage", "type": "maximum", "threshold": 3.5}
MIN_DSCR = {"metric": "dscr", "type": "minimum", "threshold": 1.25}
MAX_GEARING = {"metric": "gearing", "type": "maximum", "threshold": 2.0}
COVENANT_CS_IDS = ("MI-REPORTING", "CS-COVENANT-COMPLIANCE")       # any covenant adds this standing condition


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    """The minimum a headless run needs: both agent prompts, a CAM template and a settings file."""
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "underwriter_agent.md").write_text("MAKER PROMPT", encoding="utf-8")
    (tmp_path / "agents" / "risk_reviewer_agent.md").write_text("CHECKER PROMPT", encoding="utf-8")
    (tmp_path / "templates" / "cam").mkdir(parents=True)
    (tmp_path / "templates" / "cam" / "corporate_credit_cam.md").write_text("TEMPLATE", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.json").write_text(json.dumps({"maker_model": "claude-test-model"}),
                                                       encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def state_for(periods, covenants, downside=None):
    out = evaluate_financial_model(periods)
    state = {"financials": out["financials"], "ratios": out["ratios"], "covenants": covenants}
    if downside:
        state["downside_case"] = {"financials": downside["financials"], "ratios": downside["ratios"]}
    return state


def forward(periods, covenants):
    return policy_engine.evaluate_deal_policy(state_for(periods, covenants))["forward_covenant_results"]


def by_year_metric(results):
    return {(r["year"], r["metric"]): r for r in results}


# ---------------------------------------------------------------------------
# The outcomes
# ---------------------------------------------------------------------------

def test_a_healthy_forward_year_is_recorded_as_pass():
    results = forward({"FY-Current": HEALTHY, "FY+1": HEALTHY}, [MAX_LEVERAGE])
    assert len(results) == 1
    result = results[0]
    assert result["year"] == "FY+1" and result["metric"] == "gross_leverage" and result["status"] == "PASS"
    assert result["actual"] == pytest.approx(500 / 400) and result["reason"] is None
    assert result["type"] == "maximum" and result["threshold"] == 3.5
    assert result["headroom_pct"] == pytest.approx((3.5 - 500 / 400) / 3.5)
    assert result["forward_id"] == "FORWARD-FY-1-GROSS-LEVERAGE"


def test_a_forward_year_fail():
    result = forward({"FY-Current": HEALTHY, "FY+1": HEAVY_DEBT}, [MAX_LEVERAGE])[0]
    assert result["status"] == "FAIL" and result["actual"] == pytest.approx(2100 / 400) and result["reason"] is None


def test_a_forward_year_unresolvable_names_the_metric_and_denominator():
    """The reported defect: FY+1 negative EBITDA, so its leverage is N/A."""
    result = forward({"FY-Current": HEALTHY, "FY+1": LOSS}, [MAX_LEVERAGE])[0]
    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None and result["headroom_pct"] is None
    assert result["reason"] == "gross leverage not meaningful: EBITDA is negative"


def test_a_forward_minimum_covenant_on_an_na_ratio_is_unresolvable_too():
    result = forward({"FY-Current": HEALTHY, "FY+1": dict(HEALTHY, scheduled_principal=0, interest_paid=0)},
                     [MIN_DSCR])[0]
    assert result["status"] == "UNRESOLVABLE" and result["actual"] is None and "debt service" in result["reason"]


def test_mixed_outcomes_across_years_and_covenants_in_year_then_covenant_order():
    periods = {"FY-Current": HEALTHY, "FY+1": HEALTHY, "FY+2": HEAVY_DEBT, "FY+3": LOSS}
    results = forward(periods, [MAX_LEVERAGE, MIN_DSCR])
    assert [(r["year"], r["metric"], r["status"]) for r in results] == [
        ("FY+1", "gross_leverage", "PASS"), ("FY+1", "dscr", "PASS"),
        ("FY+2", "gross_leverage", "FAIL"), ("FY+2", "dscr", "PASS"),
        ("FY+3", "gross_leverage", "UNRESOLVABLE"), ("FY+3", "dscr", "FAIL"),   # dscr -100/150: a real number, below 1.25
    ]
    assert len({r["forward_id"] for r in results}) == 6


def test_the_same_input_always_gives_the_same_list():
    periods = {"FY-Current": HEALTHY, "FY+1": LOSS, "FY+2": HEAVY_DEBT}
    assert forward(periods, [MAX_LEVERAGE, MIN_DSCR]) == forward(periods, [MAX_LEVERAGE, MIN_DSCR])


def test_two_covenants_naming_the_same_metric_get_distinct_stable_ids():
    both = [MAX_LEVERAGE, {"metric": "gross_leverage", "type": "minimum", "threshold": 0.5}]
    results = forward({"FY-Current": HEALTHY, "FY+1": HEALTHY}, both)
    assert [r["forward_id"] for r in results] == ["FORWARD-FY-1-GROSS-LEVERAGE", "FORWARD-FY-1-GROSS-LEVERAGE-2"]


def test_an_unusable_covenant_is_still_recorded_with_a_reason_and_never_crashes():
    results = forward({"FY-Current": HEALTHY, "FY+1": HEALTHY},
                      [{"metric": "gross_leverage", "type": "sideways", "threshold": 3.5},
                       {"metric": "gross_leverage", "type": "maximum", "threshold": "high"},
                       {"metric": "no_such_ratio", "type": "maximum", "threshold": 1}, {}])
    assert [r["status"] for r in results] == ["UNRESOLVABLE"] * 4
    assert all(r["reason"] for r in results)
    assert len({r["forward_id"] for r in results}) == 4


# ---------------------------------------------------------------------------
# Current year and forward years are independent
# ---------------------------------------------------------------------------

def test_current_year_pass_with_a_forward_year_fail():
    policy = policy_engine.evaluate_deal_policy(
        state_for({"FY-Current": HEALTHY, "FY+1": HEAVY_DEBT}, [MAX_LEVERAGE]))
    assert [r["status"] for r in policy["covenant_results"]] == ["PASS"]
    assert [r["status"] for r in policy["forward_covenant_results"]] == ["FAIL"]


def test_current_year_pass_with_a_forward_year_unresolvable():
    policy = policy_engine.evaluate_deal_policy(
        state_for({"FY-Current": HEALTHY, "FY+1": LOSS}, [MAX_LEVERAGE]))
    assert [r["status"] for r in policy["covenant_results"]] == ["PASS"]
    assert [r["status"] for r in policy["forward_covenant_results"]] == ["UNRESOLVABLE"]
    assert policy["downside_covenant_breaches"] == []     # no stress given: nothing is hidden OR double-reported


def test_covenant_results_stay_current_year_only_and_keep_their_shape():
    policy = policy_engine.evaluate_deal_policy(
        state_for({"FY-Current": HEALTHY, "FY+1": LOSS}, [MAX_LEVERAGE, MIN_DSCR]))
    assert len(policy["covenant_results"]) == 2
    assert all(set(r) == {"metric", "type", "threshold", "actual", "status", "headroom_pct", "reason"}
               for r in policy["covenant_results"])
    assert all(set(r) == {"metric", "type", "threshold", "actual", "status", "headroom_pct", "reason", "year",
                          "forward_id"} for r in policy["forward_covenant_results"])


def test_a_failing_current_year_does_not_change_the_forward_list():
    healthy_now = forward({"FY-Current": HEALTHY, "FY+1": HEALTHY}, [MAX_LEVERAGE])
    failing_now = forward({"FY-Current": HEAVY_DEBT, "FY+1": HEALTHY}, [MAX_LEVERAGE])
    assert healthy_now == failing_now


# ---------------------------------------------------------------------------
# Which years are evaluated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ratios", [
    {}, {"FY-Current": {"gross_leverage": 1.0}}, {"FY-2": {"gross_leverage": 1.0}, "FY-1": {"gross_leverage": 1.0}},
    {"FY-Current": {"gross_leverage": 1.0}, "FY+1": None}, {"FY-Current": {"gross_leverage": 1.0}, "FY+1": {}},
    {"FY-Current": {"gross_leverage": 1.0}, "FY+1": []}, {"FY-Current": {"gross_leverage": 1.0}, "FY+1": "n/a"},
    None, [],
], ids=["empty", "current-only", "history-only", "null-year", "empty-year", "list-year", "text-year", "none",
        "empty-list"])
def test_no_projection_means_nothing_to_evaluate(ratios):
    policy = policy_engine.evaluate_deal_policy({"ratios": ratios, "covenants": [MAX_LEVERAGE]})
    assert policy["forward_covenant_results"] == []


def test_a_state_with_no_ratios_at_all_still_works():
    assert policy_engine.evaluate_deal_policy({})["forward_covenant_results"] == []
    assert policy_engine.evaluate_deal_policy(None)["forward_covenant_results"] == []


def test_no_covenants_means_no_forward_results():
    assert forward({"FY-Current": HEALTHY, "FY+1": LOSS}, []) == []


def test_only_the_forward_period_keys_count_and_a_sparse_projection_is_fine():
    ratios = {"FY-Current": {"gross_leverage": 1.0}, "FY+3": {"gross_leverage": 9.0}, "FY+9": {"gross_leverage": 9.0},
              "Budget": {"gross_leverage": 9.0}}
    results = policy_engine.evaluate_deal_policy({"ratios": ratios, "covenants": [MAX_LEVERAGE]})[
        "forward_covenant_results"]
    assert [(r["year"], r["status"]) for r in results] == [("FY+3", "FAIL")]


def test_a_legacy_state_without_financials_still_gets_a_generic_reason():
    """Ratios recorded without the financials behind them (an older checkpoint): None is still UNRESOLVABLE."""
    policy = policy_engine.evaluate_deal_policy(
        {"ratios": {"FY-Current": {"gross_leverage": 1.0}, "FY+1": {"gross_leverage": None}},
         "covenants": [MAX_LEVERAGE]})
    result = policy["forward_covenant_results"][0]
    assert result["status"] == "UNRESOLVABLE" and result["reason"] == "gross_leverage has no computed value for this period"


def test_a_recorded_non_positive_denominator_overrides_a_stored_number_in_a_forward_year():
    """Defence in depth (#169) applies to forward years too: a stale negative leverage cannot pass."""
    state = state_for({"FY-Current": HEALTHY, "FY+1": LOSS}, [MAX_LEVERAGE])
    state["ratios"]["FY+1"]["gross_leverage"] = -1.0
    result = policy_engine.evaluate_deal_policy(state)["forward_covenant_results"][0]
    assert result["status"] == "UNRESOLVABLE" and "ignored" in result["reason"]


# ---------------------------------------------------------------------------
# Downside interaction: no change to downside semantics, no duplicate disclosure
# ---------------------------------------------------------------------------

STRESS = {"revenue_haircut_pct": 60}                               # revenue 1000 -> 400: EBITDA 400 -> -200


def downside_policy(periods, covenants):
    out = evaluate_downside_case(periods, STRESS)
    return policy_engine.evaluate_deal_policy(state_for(periods, covenants, downside=out))


def test_forward_pass_with_a_downside_breach_keeps_the_downside_report_and_a_pass_forward_entry():
    policy = downside_policy({"FY-Current": HEALTHY, "FY+1": HEALTHY}, [MAX_LEVERAGE])
    assert [b["breach_id"] for b in policy["downside_covenant_breaches"]] == ["DOWNSIDE-FY-1-GROSS-LEVERAGE"]
    assert [(r["status"], r["forward_id"]) for r in policy["forward_covenant_results"]] == [
        ("PASS", "FORWARD-FY-1-GROSS-LEVERAGE")]


def test_a_forward_base_failure_is_not_also_reported_as_a_downside_breach():
    """Downside reports base-PASS -> stressed-non-PASS only, as before; a year already failing in the base case is
    disclosed once, in the forward list."""
    policy = downside_policy({"FY-Current": HEALTHY, "FY+1": HEAVY_DEBT}, [MAX_LEVERAGE])
    assert [r["status"] for r in policy["forward_covenant_results"]] == ["FAIL"]
    assert policy["downside_covenant_breaches"] == []


def test_downside_output_is_identical_with_and_without_the_forward_layer():
    periods = {"FY-Current": HEALTHY, "FY+1": HEALTHY, "FY+2": HEAVY_DEBT}
    policy = downside_policy(periods, [MAX_LEVERAGE, MAX_GEARING])
    state = state_for(periods, [MAX_LEVERAGE, MAX_GEARING], downside=evaluate_downside_case(periods, STRESS))
    assert policy["downside_covenant_breaches"] == policy_engine._evaluate_downside_covenants(
        [MAX_LEVERAGE, MAX_GEARING], state["ratios"], state["downside_case"]["ratios"], state["financials"],
        state["downside_case"]["financials"])


# ---------------------------------------------------------------------------
# Disclosure only: never a code-enforced rejection
# ---------------------------------------------------------------------------

def test_forward_failures_add_no_reject_reason_to_a_compliant_draft():
    periods = {"FY-Current": HEALTHY, "FY+1": LOSS, "FY+2": HEAVY_DEBT}
    state = state_for(periods, [MAX_LEVERAGE, MIN_DSCR])
    policy = policy_engine.evaluate_deal_policy(state)
    assert {r["status"] for r in policy["forward_covenant_results"]} >= {"FAIL", "UNRESOLVABLE"}
    draft = _policy_check_draft(cs_ids=COVENANT_CS_IDS)
    ground_truth = policy_checks.ground_truth_figures(state["financials"], state["ratios"], [], {})
    assert policy_checks.check_draft_compliance(draft, policy, ground_truth) == []


def test_the_same_policy_state_with_the_forward_key_removed_gives_the_same_reasons():
    """An older policy_state has no `forward_covenant_results`; the checks neither need nor read it."""
    state = state_for({"FY-Current": HEAVY_DEBT, "FY+1": LOSS}, [MAX_LEVERAGE])
    policy = policy_engine.evaluate_deal_policy(state)
    legacy = {k: v for k, v in policy.items() if k != "forward_covenant_results"}
    ground_truth = policy_checks.ground_truth_figures(state["financials"], state["ratios"], [], {})
    draft = _policy_check_draft(cs_ids=COVENANT_CS_IDS)
    assert policy_checks.check_draft_compliance(draft, policy, ground_truth) == \
        policy_checks.check_draft_compliance(draft, legacy, ground_truth)


def test_policy_check_compute_reports_the_forward_results_and_stays_compliant(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = evaluate_financial_model({"FY-Current": HEALTHY, "FY+1": LOSS})
    write_state("Acme Corp", "Fleet Loan", financials=model["financials"], ratios=model["ratios"],
                covenants=[MAX_LEVERAGE])
    draft = tmp_path / "draft.md"
    draft.write_text(_policy_check_draft(cs_ids=COVENANT_CS_IDS), encoding="utf-8")

    result = compute("Acme Corp", "Fleet Loan", draft_path=str(draft))

    assert result["compliant"] is True and result["reasons"] == []
    forward_results = result["policy_state"]["forward_covenant_results"]
    assert [(r["year"], r["status"]) for r in forward_results] == [("FY+1", "UNRESOLVABLE")]
    json.dumps(result)                                       # the CLI prints it: it must serialise


# ---------------------------------------------------------------------------
# What the model sees: non-PASS entries only, as data
# ---------------------------------------------------------------------------

def context_for(periods, covenants):
    state = state_for(periods, covenants)
    policy = policy_engine.evaluate_deal_policy(state)
    return _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)",
        {"financials": state["financials"], "ratios": state["ratios"]}, [], policy), policy


def test_the_context_carries_only_the_non_pass_forward_entries():
    context, policy = context_for({"FY-Current": HEALTHY, "FY+1": HEALTHY, "FY+2": HEAVY_DEBT, "FY+3": LOSS},
                                  [MAX_LEVERAGE])
    block = context.split("<forward_covenant_results>")[1].split("</forward_covenant_results>")[0]
    shown = json.loads(block)
    assert [(r["year"], r["status"]) for r in shown] == [("FY+2", "FAIL"), ("FY+3", "UNRESOLVABLE")]
    assert shown == [r for r in policy["forward_covenant_results"] if r["status"] != "PASS"]
    assert context.count("<forward_covenant_results>") == 1


def test_the_context_has_no_forward_block_when_every_forward_year_passes_or_none_exist():
    for periods in ({"FY-Current": HEALTHY, "FY+1": HEALTHY}, {"FY-Current": HEALTHY}):
        context, _ = context_for(periods, [MAX_LEVERAGE])
        assert "forward_covenant_results" not in context


def test_the_context_for_an_older_policy_state_without_the_key_is_unchanged():
    context = _build_grounding_context(
        "Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", {"financials": {}, "ratios": {}}, [],
        {"covenant_results": [], "required_conditions_precedent": []})
    assert "forward_covenant_results" not in context


def test_the_forward_block_is_neutral_data_with_no_rejection_wording():
    context, _ = context_for({"FY-Current": HEALTHY, "FY+1": LOSS}, [MAX_LEVERAGE])
    heading = [line for line in context.splitlines() if line.startswith("Forward-year covenant results")]
    assert len(heading) == 1
    lowered = heading[0].lower()
    assert not any(word in lowered for word in ("reject", "must", "required", "acknowledged"))


def test_the_current_year_and_downside_blocks_are_untouched_by_the_forward_block():
    with_forward, policy = context_for({"FY-Current": HEALTHY, "FY+1": LOSS}, [MAX_LEVERAGE])
    covenant_block = "<covenant_results>\n" + json.dumps(policy["covenant_results"], indent=2) + "\n</covenant_results>"
    assert covenant_block in with_forward
    assert "downside_covenant_breaches" not in with_forward


# ---------------------------------------------------------------------------
# End to end through the pipeline: persisted, shown, and never a rejection
# ---------------------------------------------------------------------------

def test_run_pipeline_persists_the_full_forward_results_shows_the_model_the_non_pass_ones_and_approves(project_root):
    write_state("Acme Corp", "Fleet Loan", covenants=[MAX_LEVERAGE])
    client = MockClient([_compliant_draft(cs_ids=list(COVENANT_CS_IDS)), _approved_json()])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials={"FY-Current": HEALTHY, "FY+1": HEALTHY, "FY+2": LOSS}, client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["review_verdict"] == "APPROVED"            # a forward UNRESOLVABLE never rejects by itself
    stored = state["policy_state"]["forward_covenant_results"]
    assert [(r["year"], r["status"]) for r in stored] == [("FY+1", "PASS"), ("FY+2", "UNRESOLVABLE")]   # complete record
    assert state["policy_state"]["covenant_results"][0]["status"] == "PASS"

    for call in client.calls:                                # Maker and Checker get the same shown entries
        content = call["messages"][0]["content"]
        shown = json.loads(content.split("<forward_covenant_results>")[1].split("</forward_covenant_results>")[0])
        assert [(r["year"], r["status"]) for r in shown] == [("FY+2", "UNRESOLVABLE")]


def test_run_pipeline_with_no_projection_is_unchanged(project_root):
    write_state("Acme Corp", "Fleet Loan", covenants=[MAX_LEVERAGE])
    client = MockClient([_compliant_draft(cs_ids=list(COVENANT_CS_IDS)), _approved_json()])

    run_pipeline("Acme Corp", "Fleet Loan", "0.20%", "LGD 3 (15%)", "corporate_credit",
                 multi_period_financials={"FY-Current": HEALTHY}, client=client)

    state = read_state("Acme Corp", "Fleet Loan")
    assert state["review_verdict"] == "APPROVED"
    assert state["policy_state"]["forward_covenant_results"] == []
    assert all("forward_covenant_results" not in call["messages"][0]["content"] for call in client.calls)


@pytest.mark.parametrize("ratios", [None, [], "text", 3])
def test_the_helper_itself_tolerates_ratios_that_are_not_an_object(ratios):
    """evaluate_deal_policy() never hands it one (state validation and `or {}` come first); it must still not crash."""
    assert policy_engine._evaluate_forward_covenants([MAX_LEVERAGE], ratios) == []
