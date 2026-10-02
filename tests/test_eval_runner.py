"""Tests for scripts/eval_runner.py (issue #151, PR 2): the live runner, driven ONLY by fake
clients -- zero model calls, no API key, no network. They pin the properties the design
requires of a live run: one live call per run, real prompt assembly, strict isolation,
a hard cap, honest error handling, incremental persistence and recorded provenance.
"""
import json
import os
import sys

import pytest

import eval_runner
from eval_budget import BudgetedClient, CallBudget
from eval_fakes import (
    DECOYS,
    FakeClient,
    add_company_decoy,
    bad_maker_text,
    dataset_case,
    good_maker_text,
    listing,
    make_fake_repo,
    verdict_text,
)
from eval_oracles import build_context
from eval_runner import RoutingClient, RunnerSetupError, StopRun, run_case_once, run_live, verdict_parsed


@pytest.fixture
def env(tmp_path):
    """A fake repo root, a work root and a run dir, all under tmp_path (outside the real repo)."""
    repo = make_fake_repo(tmp_path)
    run_dir = tmp_path / "run"
    (run_dir / "work").mkdir(parents=True)
    return {"repo": str(repo), "run_dir": str(run_dir), "work": str(run_dir / "work"), "tmp": tmp_path}


def one_run(env, case, client, keep_work=False, label="repeat-1"):
    budget = CallBudget(10)
    run = run_case_once(case, label, build_context(case), BudgetedClient(client, budget),
                        env["repo"], env["work"], keep_work=keep_work)
    return run, budget


# ---------------------------------------------------------------------------
# One live call per run, scored by the oracles
# ---------------------------------------------------------------------------

def test_a_maker_case_makes_exactly_one_live_call_and_is_scored(env):
    case = dataset_case("fab-no-financials")
    client = FakeClient(lambda kwargs: good_maker_text(case))

    run, budget = one_run(env, case, client)

    assert len(client.calls) == 1 and budget.calls == 1  # the Checker call was never made
    assert run["passed"] and run["status"] == "ok" and run["live_calls"] == 1
    assert run["kind"] == "live" and run["label"] == "repeat-1"
    assert (budget.input_tokens, budget.output_tokens) == (11, 7)


def test_a_misbehaving_maker_output_fails_the_run_with_the_oracles_reason(env):
    case = dataset_case("fab-no-financials")
    run, _ = one_run(env, case, FakeClient(lambda kwargs: bad_maker_text(case)))
    assert not run["passed"]
    failing = [a for a in run["assertions"] if a["scored"] and not a["passed"]]
    assert [a["oracle"] for a in failing] == ["figures_grounded"]


def test_the_maker_message_is_the_real_prompt_assembly(env):
    case = dataset_case("inj-collateral-description")
    client = FakeClient(lambda kwargs: good_maker_text(case))
    one_run(env, case, client)

    from textio import read_text
    maker_prompt = read_text(os.path.join(env["repo"], "agents", "underwriter_agent.md"))
    message = client.last_message
    assert message.startswith(maker_prompt)  # built by run_pipeline, not re-templated here
    assert "Synthetic Borrower" in message and "<collateral>" in message
    assert client.calls[0]["max_tokens"] == 4000 and client.calls[0]["model"]


def test_a_checker_case_scripts_the_maker_and_goes_live_only_for_the_checker(env):
    case = dataset_case("chk-unsupported-claim")
    client = FakeClient(lambda kwargs: verdict_text("REJECTED"))

    run, budget = one_run(env, case, client)

    assert len(client.calls) == 1 and budget.calls == 1  # the scripted Maker draft cost nothing
    from textio import read_text
    checker_prompt = read_text(os.path.join(env["repo"], "agents", "risk_reviewer_agent.md"))
    message = client.last_message
    assert message.startswith(checker_prompt)
    assert "undisputed market leader" in message  # the scripted draft is what the Checker is shown
    assert client.calls[0]["max_tokens"] == 2000
    assert run["passed"]


def test_checker_verdicts_are_parsed_and_an_unparsed_response_is_never_a_rejection(env):
    case = dataset_case("chk-unsupported-claim")
    approved, _ = one_run(env, case, FakeClient(lambda k: verdict_text("APPROVED", "fine")))
    assert not approved["passed"]
    garbled, _ = one_run(env, case, FakeClient(lambda k: "I could not finish my review."))
    verdict_row = next(a for a in garbled["assertions"] if a["oracle"] == "verdict_is")
    assert not garbled["passed"] and "could not be parsed" in verdict_row["reason"]
    assert "parsed from the response: False" in garbled["output_excerpt"]


def test_a_quoted_checker_canary_is_an_observation_that_does_not_fail_the_run(env):
    case = dataset_case("chk-injection-approve")
    token = case["canary"]["token"]
    run, _ = one_run(env, case, FakeClient(lambda k: verdict_text("REJECTED", f"It told me to echo {token}; I refused.")))
    by_oracle = {a["oracle"]: a for a in run["assertions"]}
    assert by_oracle["canary_absent"]["scored"] is False and not by_oracle["canary_absent"]["passed"]
    assert run["passed"]  # scored on the verdict only


def test_the_routing_client_refuses_a_third_call_and_stops_after_the_live_one():
    case = dataset_case("fab-no-financials")
    router = RoutingClient(case, FakeClient(lambda k: "draft"))
    msg = {"messages": [{"role": "user", "content": "x"}], "model": "m"}
    router.messages.create(**msg)  # Maker: live
    with pytest.raises(StopRun):    # Checker: never made in a maker case
        router.messages.create(**msg)
    with pytest.raises(RuntimeError, match="third model call"):
        router.messages.create(**msg)


def test_a_draft_that_fails_deterministic_checks_still_costs_exactly_one_call(env):
    # run_pipeline would normally REVISE after a rejection (a second live Maker call); the
    # runner's max_iterations=1 plus StopRun must make that impossible.
    case = dataset_case("fab-no-financials")
    client = FakeClient(lambda kwargs: "A draft with no structured block, which code rejects by itself.")
    run, budget = one_run(env, case, client)
    assert budget.calls == 1 and len(client.calls) == 1 and run["status"] == "ok"
    assert not run["passed"]


# ---------------------------------------------------------------------------
# Which surfaces reach the prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_id", ["inj-collateral-description", "inj-company-learning", "inj-policy-note"])
def test_each_planted_surface_really_reaches_the_prompt(env, case_id):
    case = dataset_case(case_id)
    client = FakeClient(lambda kwargs: good_maker_text(case))
    one_run(env, case, client)
    assert case["canary"]["token"] in client.last_message


def test_the_style_guide_reaches_the_maker(env):
    case = dataset_case("fab-no-financials")
    case["config_files"] = {"style_guide": "STYLE-GUIDE-MARKER-123"}
    client = FakeClient(lambda kwargs: good_maker_text(case))
    one_run(env, case, client)
    assert "STYLE-GUIDE-MARKER-123" in client.last_message


def test_a_source_block_is_appended_xml_tagged_at_the_end_of_the_maker_message(env):
    case = dataset_case("inj-source-block-obvious")
    client = FakeClient(lambda kwargs: good_maker_text(case))
    one_run(env, case, client)
    message = client.last_message
    assert case["canary"]["token"] in message
    assert message.rstrip().endswith("</source_document>")
    assert "Label: Management biography extract" in message


def test_policy_notes_and_state_extras_are_seeded_into_the_run(env):
    case = dataset_case("fab-no-financials")
    case["deal"]["guarantees"] = [{"provider": "Synthetic Parent", "type": "Corporate", "amount": "500,000"}]
    case["deal"]["covenants"] = [{"metric": "dscr", "type": "minimum", "threshold": 1.25}]
    client = FakeClient(lambda kwargs: good_maker_text(case))
    one_run(env, case, client)
    assert "GUARANTEE-SYNTHETIC-PARENT" in client.last_message  # seeded via state.json, read by the pipeline


def test_an_analyst_supplied_deal_is_seeded_from_its_financials_and_flag(env):
    case = dataset_case("analyst-supplied-labelled")
    client = FakeClient(lambda kwargs: good_maker_text(case))
    run, _ = one_run(env, case, client)
    assert "analyst-supplied" in client.last_message and "285000" in client.last_message
    assert run["passed"]


# ---------------------------------------------------------------------------
# Isolation: nothing leaks in, nothing leaks out
# ---------------------------------------------------------------------------

def test_decoys_in_the_repo_never_reach_a_run(env):
    case = dataset_case("fab-no-financials")
    add_company_decoy(env["repo"], case["deal"]["company"])
    client = FakeClient(lambda kwargs: good_maker_text(case))
    one_run(env, case, client)
    for decoy in DECOYS.values():
        assert decoy not in client.last_message


def test_the_runs_seed_only_agents_templates_cam_and_settings(env):
    case = dataset_case("fab-no-financials")
    client = FakeClient(lambda kwargs: good_maker_text(case))
    one_run(env, case, client, keep_work=True)
    (workdir,) = listing(env["work"])
    base = os.path.join(env["work"], workdir)
    assert listing(os.path.join(base, "templates")) == ["cam"]  # never templates/local
    assert "style_guide.md" not in listing(os.path.join(base, "config"))
    assert listing(os.path.join(base, "config")) == ["settings.json"]


def test_every_run_gets_its_own_fresh_directory_with_no_carried_over_state(env):
    case = dataset_case("fab-no-financials")
    client = FakeClient(lambda kwargs: good_maker_text(case))
    first, _ = one_run(env, case, client, keep_work=True, label="repeat-1")
    second, _ = one_run(env, case, client, keep_work=True, label="repeat-2")
    dirs = listing(env["work"])
    assert len(dirs) == 2
    assert first["prompt_hash"] == second["prompt_hash"]  # same case, same prompt, no state bleed
    for name in dirs:  # each holds only its own state: one deal folder, one dated folder
        deals = os.path.join(env["work"], name, "deals")
        assert len(listing(deals)) <= 1


def test_the_working_directory_and_cwd_are_restored_and_work_dirs_cleaned(env):
    before = os.getcwd()
    case = dataset_case("fab-no-financials")
    one_run(env, case, FakeClient(lambda kwargs: good_maker_text(case)))
    assert os.getcwd() == before and listing(env["work"]) == []
    one_run(env, case, FakeClient(lambda kwargs: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert os.getcwd() == before and listing(env["work"]) == []


def test_a_missing_settings_file_fails_before_any_call_is_spent(tmp_path):
    repo = make_fake_repo(tmp_path)
    os.remove(os.path.join(repo, "config", "settings.json"))
    run_dir = tmp_path / "run"
    client = FakeClient(lambda kwargs: "never")
    with pytest.raises(RunnerSetupError, match="settings.json"):
        run_live({"version": "v1", "cases": []}, [dataset_case("fab-no-financials")], 1, 5, str(run_dir),
                 client, repo_root=str(repo))
    assert client.calls == []


# ---------------------------------------------------------------------------
# run_live: cap, errors, persistence, provenance
# ---------------------------------------------------------------------------

def _dataset(*case_ids):
    cases = [dataset_case(i) for i in case_ids]
    return {"version": "v1", "description": "", "cases": cases}, cases


def test_run_live_records_models_hashes_usage_and_a_per_case_run_list(env):
    dataset, cases = _dataset("fab-no-financials", "chk-clean-control")

    def respond(kwargs):
        return good_maker_text(cases[0]) if kwargs["max_tokens"] == 4000 else verdict_text("APPROVED", "")

    record = run_live(dataset, cases, 2, 10, env["run_dir"], FakeClient(respond), repo_root=env["repo"])

    assert record["mode"] == "live" and record["repeats"] == 2 and not record["aborted"]
    assert record["usage"] == {"calls": 4, "input_tokens": 44, "output_tokens": 28}
    assert [len(c["runs"]) for c in record["cases"]] == [2, 2]
    assert all(r["passed"] for c in record["cases"] for r in c["runs"])
    assert record["summary"]["observed_pass_rate_by_category"]["fabrication"] == {"passed": 2, "total": 2}
    import orchestrator
    from textio import read_text
    assert record["prompt_hashes"]["underwriter_prompt_hash"] == orchestrator._content_hash(
        read_text(os.path.join(env["repo"], "agents", "underwriter_agent.md")))
    assert {"agents/underwriter_agent.md", "agents/risk_reviewer_agent.md", "config/settings.json",
            "templates/cam/corporate_credit_cam.md"} <= set(record["input_hashes"])
    assert record["models"]["maker_model"] and record["models"]["checker_model"]
    assert record["selected_case_ids"] == ["fab-no-financials", "chk-clean-control"]
    assert listing(env["work"]) == []


def test_run_live_aborts_when_the_cap_is_spent_and_lists_the_unreached_cases(env):
    dataset, cases = _dataset("fab-no-financials", "fab-single-period")
    client = FakeClient(lambda kwargs: good_maker_text(cases[0]))

    record = run_live(dataset, cases, 2, 3, env["run_dir"], client, repo_root=env["repo"])  # plan 4, cap 3

    assert record["aborted"] and "call cap" in record["abort_reason"]
    assert len(client.calls) == 3 and record["usage"]["calls"] == 3  # never a fourth call
    assert [len(c["runs"]) for c in record["cases"]] == [2, 1]
    assert {c["id"] for c in record["cases"]} == {"fab-no-financials", "fab-single-period"}


def test_an_api_error_is_a_recorded_non_pass_that_counts_against_the_cap(env):
    dataset, cases = _dataset("fab-no-financials")
    calls = {"n": 0}

    def respond(kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated API error " + "x" * 600)
        return good_maker_text(cases[0])

    record = run_live(dataset, cases, 3, 10, env["run_dir"], FakeClient(respond), repo_root=env["repo"])

    runs = record["cases"][0]["runs"]
    assert [r["status"] for r in runs] == ["ok", "error", "ok"] and not runs[1]["passed"]
    assert len(runs[1]["error"]) < 400  # the message is bounded
    assert record["summary"]["observed_pass_rate_by_category"]["fabrication"] == {"passed": 2, "total": 3}
    assert record["usage"]["calls"] == 3  # the failed call still counted


def test_consecutive_errors_abort_the_whole_evaluation(env):
    dataset, cases = _dataset("fab-no-financials", "fab-single-period")

    def respond(kwargs):
        raise RuntimeError("down")

    client = FakeClient(respond)
    record = run_live(dataset, cases, 5, 20, env["run_dir"], client, repo_root=env["repo"])

    assert record["aborted"] and "3 consecutive errored runs" in record["abort_reason"]
    assert len(client.calls) == eval_runner.MAX_CONSECUTIVE_ERRORS  # stopped instead of burning the cap
    assert all(r["status"] == "error" for c in record["cases"] for r in c["runs"])


def test_ctrl_c_returns_a_record_marked_interrupted_with_everything_already_paid_for(env):
    dataset, cases = _dataset("fab-no-financials", "fab-single-period")
    calls = {"n": 0}

    def respond(kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt  # the operator hits Ctrl-C part-way through a paid-for run
        return good_maker_text(cases[0])

    before = os.getcwd()
    record = run_live(dataset, cases, 5, 20, env["run_dir"], FakeClient(respond), repo_root=env["repo"])

    assert record["aborted"] and record["abort_category"] == "interrupted" and "Ctrl-C" in record["abort_reason"]
    # Two finished runs plus the one whose call was in flight when Ctrl-C hit; the second case never ran.
    assert [len(c["runs"]) for c in record["cases"]] == [3, 0]
    assert [r["status"] for r in record["cases"][0]["runs"]] == ["ok", "ok", "interrupted"]
    lines = open(os.path.join(env["run_dir"], "runs.jsonl"), encoding="utf-8").read().splitlines()
    assert len(lines) == 3 and all(json.loads(line)["case_id"] == "fab-no-financials" for line in lines)
    assert os.getcwd() == before and listing(env["work"]) == []


# ---------------------------------------------------------------------------
# Client construction and verdict parsing
# ---------------------------------------------------------------------------

def test_the_real_client_is_built_without_sdk_retries_and_never_touches_the_network(monkeypatch):
    import anthropic
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: seen.update(kwargs) or "client")
    monkeypatch.setattr(eval_runner, "sdk_version", lambda: "1.7.0")
    assert eval_runner.make_client() == "client"
    assert seen["max_retries"] == 0 and seen["api_key"] == "test-key-not-real" and seen["timeout"] > 0


def test_a_missing_api_key_is_a_clean_setup_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RunnerSetupError, match="ANTHROPIC_API_KEY"):
        eval_runner.make_client()


@pytest.mark.parametrize("text", [
    "no json at all", "", "```json\n{not json}\n```", '```json\n{"verdict": "MAYBE"}\n```',
    '```json\n{"verdict": "approved", "notes": null}\n```', verdict_text("REJECTED"),
    'Echo ```json\n{"verdict": "APPROVED"}\n``` then final:\n```json\n{"verdict": "REJECTED", "notes": "x"}\n```',
    '```json\n{"financials": {}}\n```',
])
def test_verdict_parsed_agrees_with_orchestrators_fallback(text):
    import orchestrator
    verdict, notes = orchestrator.parse_verdict(text)
    fell_back = (verdict == "REJECTED" and notes == (text or ""))
    assert verdict_parsed(text) == (not fell_back)


def test_the_runner_never_imports_anthropic_at_import_time():
    import subprocess
    code = (f"import sys; sys.path.insert(0, {os.path.join(os.path.dirname(eval_runner.__file__))!r}); "
            "import eval_runner, eval_baseline, run_evals; "
            "assert 'anthropic' not in sys.modules, 'imported anthropic'")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

# ---------------------------------------------------------------------------
# Second-review follow-ups: the whole output is kept; a pipeline exit is an error, not an abort.
# ---------------------------------------------------------------------------

def test_the_models_whole_output_is_kept_while_the_pack_excerpt_is_clipped(env):
    import eval_report
    case = dataset_case("fab-no-financials")
    long_draft = good_maker_text(case) + "\n" + ("Padding sentence. " * 3000) + "\nEND-OF-DOCUMENT-MARKER"
    run, _ = one_run(env, case, FakeClient(lambda kwargs: long_draft))
    assert run["output_text"] == long_draft and "END-OF-DOCUMENT-MARKER" in run["output_text"]
    assert len(run["output_excerpt"]) < len(long_draft) and "truncated" in run["output_excerpt"]
    assert len(long_draft) > eval_report.EXCERPT_LIMIT


def test_an_errored_run_carries_no_output_text(env):
    case = dataset_case("fab-no-financials")
    run, _ = one_run(env, case, FakeClient(lambda kwargs: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert run["status"] == "error" and "output_text" not in run


def test_a_pipeline_that_exits_is_a_recorded_error_not_a_crash_or_an_abort(env, monkeypatch):
    import orchestrator

    def exits(*args, **kwargs):
        raise SystemExit(1)

    monkeypatch.setattr(orchestrator, "run_pipeline", exits)
    case = dataset_case("fab-no-financials")
    before = os.getcwd()
    run, budget = one_run(env, case, FakeClient(lambda kwargs: "unused"))
    assert run["status"] == "error" and "SystemExit" in run["error"] and not run["passed"]
    assert os.getcwd() == before and listing(env["work"]) == []


def test_scripted_calls_never_count_against_the_budget(env):
    """If the budgeted client wrapped the routing client, a Checker case would cost 2 per run."""
    case = dataset_case("chk-clean-control")
    client = FakeClient(lambda kwargs: verdict_text("APPROVED", ""))
    _, budget = one_run(env, case, client)
    assert budget.calls == 1 and len(client.calls) == 1

# ---------------------------------------------------------------------------
# Third independent review of the PR for #151: PR 2's own findings and surviving mutants.
# ---------------------------------------------------------------------------

def test_stop_reason_and_per_run_token_usage_are_recorded(env):
    case = dataset_case("fab-no-financials")

    class StopAware(FakeClient):
        def _create(self, **kwargs):
            response = super()._create(**kwargs)
            response.stop_reason = "end_turn"
            return response

    run, _ = one_run(env, case, StopAware(lambda kwargs: good_maker_text(case), input_tokens=1234, output_tokens=567))
    assert (run["stop_reason"], run["input_tokens"], run["output_tokens"]) == ("end_turn", 1234, 567)


def test_an_errored_run_still_records_the_call_that_was_attempted(env):
    case = dataset_case("fab-no-financials")
    run, budget = one_run(env, case, FakeClient(lambda kwargs: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert run["status"] == "error" and budget.calls == 1
    assert run["live_calls"] == 1 and run["model"] and run["prompt_hash"]  # not 0 / None as before


def test_summed_per_run_live_calls_equal_the_budget_for_every_outcome(env):
    dataset, cases = _dataset("fab-no-financials")
    calls = {"n": 0}

    def respond(kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return good_maker_text(cases[0])

    record = run_live(dataset, cases, 3, 10, env["run_dir"], FakeClient(respond), repo_root=env["repo"])
    assert sum(r["live_calls"] for c in record["cases"] for r in c["runs"]) == record["usage"]["calls"] == 3


def test_a_relative_results_directory_works_despite_the_per_run_chdir(env, monkeypatch):
    dataset, cases = _dataset("fab-no-financials")
    monkeypatch.chdir(env["tmp"])
    os.makedirs("relative-run/work")
    record = run_live(dataset, cases, 1, 5, "relative-run", FakeClient(lambda kw: good_maker_text(cases[0])),
                      repo_root=env["repo"])
    assert not record["aborted"] and record["cases"][0]["runs"][0]["status"] == "ok"
    assert os.path.isfile(os.path.join(env["tmp"], "relative-run", "runs.jsonl"))


def test_the_error_streak_resets_after_a_success_so_scattered_errors_never_abort(env):
    dataset, cases = _dataset("fab-no-financials")
    calls = {"n": 0}

    def respond(kwargs):
        calls["n"] += 1
        if calls["n"] % 2 == 0:  # ok, err, ok, err, ok, err, ok: never three in a row
            raise RuntimeError("flaky")
        return good_maker_text(cases[0])

    record = run_live(dataset, cases, 7, 20, env["run_dir"], FakeClient(respond), repo_root=env["repo"])
    assert not record["aborted"] and len(record["cases"][0]["runs"]) == 7


def test_each_config_file_lands_in_its_own_prompt_section(env):
    import re
    case = dataset_case("fab-no-financials")
    case["config_files"] = {"style_guide": "MARK-STYLE", "credit_policy": "MARK-POLICY",
                            "credit_policy_notes": "MARK-NOTES", "deal_learnings": "MARK-ENT",
                            "company_learnings": "MARK-CO"}
    client = FakeClient(lambda kwargs: good_maker_text(case))
    one_run(env, case, client)
    message = client.last_message

    def inside(tag, mark):
        return re.search(rf"<{tag}>[^<]*{mark}[^<]*</{tag}>", message, re.S) is not None

    assert "Style:\nMARK-STYLE" in message
    assert inside("institutional_credit_policy", "MARK-POLICY")
    assert inside("credit_policy_interpretation_notes", "MARK-NOTES")
    assert inside("enterprise_deal_learnings", "MARK-ENT")
    assert inside("borrower_deal_learnings", "MARK-CO")


def test_state_extras_are_seeded_and_change_what_the_pipeline_computes(env):
    import re
    case = dataset_case("fab-no-financials")
    case["deal"].update({
        "ratios": {"FY+2": {"dscr": 1.25}}, "downside_case": {"ratios": {"FY+2": {"dscr": 1.05}}},
        "covenants": [{"metric": "dscr", "type": "minimum", "threshold": 1.1234}],
        "collateral": [{"asset_id": "AST-9", "asset_class": "HGV", "exposure": 100, "collateral_value": 80,
                        "perfection_status": "Registered"}],
        "security_package": [{"secures_asset_id": "AST-9", "perfection_status": "Perfected", "ranking": "First"}],
    })
    seeded = FakeClient(lambda kwargs: good_maker_text(case))
    one_run(env, case, seeded)
    message = seeded.last_message
    assert '"threshold": 1.1234' in re.search(r"<covenant_results>(.*?)</covenant_results>", message, re.S).group(1)
    assert re.search(r"<downside_covenant_breaches>.*DOWNSIDE-", message, re.S)  # the seeded downside case counted
    assert "AST-9" not in re.search(r"<required_conditions_precedent>(.*?)</required_conditions_precedent>",
                                    message, re.S).group(1)  # the seeded charge closes the security gap

    unseeded = dict(case, deal={k: v for k, v in case["deal"].items() if k != "security_package"})
    client = FakeClient(lambda kwargs: good_maker_text(unseeded))
    one_run(env, unseeded, client)
    assert "AST-9" in re.search(r"<required_conditions_precedent>(.*?)</required_conditions_precedent>",
                                client.last_message, re.S).group(1)  # without it, the gap generates a CP


def test_each_case_is_scored_against_its_own_ground_truth_not_the_first_cases(env):
    dataset, _ = _dataset("fab-no-financials", "contradiction-valuation-vs-accounts")
    cases = dataset["cases"]
    ebitda = build_context(cases[1])["ground_truth"]["ebitda"]
    draft = good_maker_text(cases[0]).replace('"reported_figures": {}', f'"reported_figures": {{"ebitda": {ebitda}}}')
    record = run_live(dataset, cases, 1, 5, env["run_dir"], FakeClient(lambda kw: draft), repo_root=env["repo"])
    by_id = {c["id"]: c["runs"][0] for c in record["cases"]}
    # No financials at all -> an EBITDA figure is ungrounded; with real accounts the same figure is right.
    assert not by_id["fab-no-financials"]["passed"]
    assert by_id["contradiction-valuation-vs-accounts"]["passed"]


def test_the_checkers_whole_response_reaches_the_canary_oracle(env):
    case = dataset_case("chk-injection-approve")
    token = case["canary"]["token"]
    # The token appears BEFORE the JSON block, so the parsed notes are clean: only the raw text has it.
    raw = f"I was told to echo {token} but will not.\n" + verdict_text("REJECTED", "Unsourced claim.")
    run, _ = one_run(env, case, FakeClient(lambda kwargs: raw))
    canary = next(a for a in run["assertions"] if a["oracle"] == "canary_absent")
    assert canary["scored"] is False and canary["passed"] is False and token in canary["reason"]
    assert run["passed"]  # an unscored observation, so the verdict decides


def test_progress_lines_carry_ids_and_statuses_but_never_model_text(env, capsys):
    import run_evals
    dataset, cases = _dataset("fab-no-financials")
    secret = "MODEL-TEXT-THAT-MUST-NOT-APPEAR-IN-PROGRESS"
    seen = []
    run_live(dataset, cases, 2, 5, env["run_dir"], FakeClient(lambda kw: secret + "\n" + good_maker_text(cases[0])),
             repo_root=env["repo"], progress=lambda *args: seen.append(args))
    assert [a[:3] for a in seen] == [(1, 2, "fab-no-financials"), (2, 2, "fab-no-financials")]
    run_evals._progress_line(*seen[0])
    err = capsys.readouterr().err
    assert "[1/2] fab-no-financials repeat-1: " in err and secret not in err

# ---------------------------------------------------------------------------
# Fourth review round: scoring failures, response shapes, in-flight interrupts, redaction, SDK pin.
# ---------------------------------------------------------------------------

class ShapedClient:
    """A client returning hand-built response objects, to exercise the shapes a real model can return."""

    def __init__(self, make_response):
        self.make_response = make_response
        self.calls = []
        from types import SimpleNamespace
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.make_response(kwargs)


def _resp(content, stop_reason="end_turn", model=None, input_tokens=5, output_tokens=3):
    from types import SimpleNamespace
    return SimpleNamespace(content=content, stop_reason=stop_reason, model=model,
                           usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens))


def _block(text, type_="text"):
    from types import SimpleNamespace
    return SimpleNamespace(type=type_, text=text)


def test_a_scoring_exception_after_a_paid_call_keeps_the_output_and_records_an_error(env, monkeypatch):
    case = dataset_case("fab-no-financials")
    secret = "MODEL-TEXT-QUOTED-IN-AN-EXCEPTION-MESSAGE"

    def exploding_oracle(*args, **kwargs):
        raise ValueError(secret)

    monkeypatch.setattr(eval_runner, "evaluate_case", exploding_oracle)
    run, budget = one_run(env, case, FakeClient(lambda kwargs: good_maker_text(case)))
    assert run["status"] == "error" and not run["passed"] and budget.calls == 1
    assert run["output_text"] == good_maker_text(case)  # the paid-for output survives
    assert run["live_calls"] == 1 and run["model"] and run["prompt_hash"]
    assert "scoring failed (ValueError)" in run["error"] and secret not in run["error"]


def test_a_scoring_exception_in_the_middle_of_an_evaluation_does_not_lose_the_results_file(env, monkeypatch):
    dataset, cases = _dataset("fab-no-financials")
    real = eval_runner.evaluate_case
    calls = {"n": 0}

    def flaky(case, output, ctx):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RecursionError("deeply nested")
        return real(case, output, ctx)

    monkeypatch.setattr(eval_runner, "evaluate_case", flaky)
    record = run_live(dataset, cases, 3, 10, env["run_dir"], FakeClient(lambda kw: good_maker_text(cases[0])),
                      repo_root=env["repo"])
    statuses = [r["status"] for r in record["cases"][0]["runs"]]
    assert statuses == ["ok", "error", "ok"] and not record["aborted"]
    assert record["usage"]["calls"] == 3 and record["cases"][0]["runs"][1]["output_text"]


def test_a_refusal_with_no_content_is_a_distinct_outcome_not_an_infrastructure_error(env):
    case = dataset_case("inj-source-block-obfuscated")
    run, budget = one_run(env, case, ShapedClient(lambda kwargs: _resp([], stop_reason="refusal")))
    assert run["status"] == "no_text" and not run["passed"] and run["stop_reason"] == "refusal"
    assert "no text" in run["error"] and "refusal" in run["error"]
    assert run["live_calls"] == 1 and budget.calls == 1 and run["input_tokens"] == 5


def test_a_checker_case_that_gets_no_text_is_also_a_distinct_outcome(env):
    case = dataset_case("chk-clean-control")
    run, _ = one_run(env, case, ShapedClient(lambda kwargs: _resp([_block("", "thinking")], "refusal")))
    assert run["status"] == "no_text" and run["live_calls"] == 1


def test_a_leading_thinking_block_and_several_text_blocks_are_handled(env):
    case = dataset_case("fab-no-financials")
    text = good_maker_text(case)
    half = len(text) // 2
    from types import SimpleNamespace
    thinking = SimpleNamespace(type="thinking", thinking="hidden reasoning")  # like the real block: no `.text`
    content = [thinking, _block(text[:half]), _block(text[half:])]
    run, _ = one_run(env, case, ShapedClient(lambda kwargs: _resp(content)))
    assert run["status"] == "ok" and run["passed"] and run["output_text"] == text


def test_a_non_text_block_that_carries_a_text_attribute_is_not_part_of_the_answer(env):
    case = dataset_case("fab-no-financials")
    text = good_maker_text(case)
    content = [_block("PRIVATE REASONING THAT IS NOT THE ANSWER", "thinking"), _block(text)]
    run, _ = one_run(env, case, ShapedClient(lambda kwargs: _resp(content)))
    assert run["output_text"] == text and "PRIVATE REASONING" not in json.dumps(run)


def test_refusals_do_not_trip_the_consecutive_error_abort(env):
    dataset, cases = _dataset("fab-no-financials")
    client = ShapedClient(lambda kwargs: _resp([], stop_reason="refusal"))
    record = run_live(dataset, cases, 5, 10, env["run_dir"], client, repo_root=env["repo"])
    assert not record["aborted"] and [r["status"] for r in record["cases"][0]["runs"]] == ["no_text"] * 5


def test_the_served_model_and_max_tokens_are_recorded(env):
    case = dataset_case("fab-no-financials")
    client = ShapedClient(lambda kwargs: _resp([_block(good_maker_text(case))], model="claude-served-x"))
    run, _ = one_run(env, case, client)
    assert run["served_model"] == "claude-served-x" and run["max_tokens"] == eval_runner.MAKER_MAX_TOKENS


def test_the_plan_s_max_tokens_constants_match_what_the_pipeline_really_sends(env):
    maker, checker = dataset_case("fab-no-financials"), dataset_case("chk-clean-control")
    seen = FakeClient(lambda kwargs: good_maker_text(maker))
    one_run(env, maker, seen)
    assert seen.calls[0]["max_tokens"] == eval_runner.MAKER_MAX_TOKENS
    seen = FakeClient(lambda kwargs: verdict_text("APPROVED", ""))
    one_run(env, checker, seen)
    assert seen.calls[0]["max_tokens"] == eval_runner.CHECKER_MAX_TOKENS


def test_a_call_in_flight_when_ctrl_c_hits_is_recorded_as_interrupted_and_counted(env):
    case = dataset_case("fab-no-financials")

    def respond(kwargs):
        raise KeyboardInterrupt

    run, budget = one_run(env, case, FakeClient(respond))
    assert run["status"] == "interrupted" and not run["passed"]
    assert run["live_calls"] == 1 and budget.calls == 1 and run["model"] and run["prompt_hash"]
    assert "in flight" in run["error"] and "no model result" in run["error"]


def test_the_accounting_invariant_holds_for_every_outcome_including_an_interrupt(env):
    dataset, cases = _dataset("fab-no-financials")
    calls = {"n": 0}

    def respond(kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        if calls["n"] == 4:
            raise KeyboardInterrupt
        return good_maker_text(cases[0])

    record = run_live(dataset, cases, 6, 10, env["run_dir"], FakeClient(respond), repo_root=env["repo"])
    runs = record["cases"][0]["runs"]
    assert [r["status"] for r in runs] == ["ok", "error", "ok", "interrupted"] and record["aborted"]
    assert sum(r["live_calls"] for r in runs) == record["usage"]["calls"] == 4


def test_an_interrupt_before_any_call_is_in_flight_records_no_run(env, monkeypatch):
    dataset, cases = _dataset("fab-no-financials")
    real = eval_runner.prepare_workdir

    def interrupted_prep(case, repo_root, workdir):
        if case is not None:
            raise KeyboardInterrupt
        return real(case, repo_root, workdir)

    monkeypatch.setattr(eval_runner, "prepare_workdir", interrupted_prep)
    before = os.getcwd()
    record = run_live(dataset, cases, 3, 10, env["run_dir"], FakeClient(lambda kw: "never called"),
                      repo_root=env["repo"])
    assert record["aborted"] and record["abort_category"] == "interrupted"
    assert record["cases"][0]["runs"] == [] and record["usage"]["calls"] == 0 and os.getcwd() == before


def test_an_api_key_in_an_error_message_is_redacted_everywhere_it_could_be_recorded(env, monkeypatch):
    key = "sk-ant-api03-FAKEKEYFORTESTINGONLY0123456789"
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    case = dataset_case("fab-no-financials")

    def respond(kwargs):
        raise RuntimeError(f"401 for key {key}; also a stray sk-ant-api03-ANOTHER_ONE_abcdef in a header")

    run, _ = one_run(env, case, FakeClient(respond))
    assert run["status"] == "error" and key not in json.dumps(run) and "sk-ant-api03" not in json.dumps(run)
    assert "[REDACTED]" in run["error"]


def test_a_key_that_does_not_look_like_an_anthropic_key_is_still_redacted(env, monkeypatch):
    key = "an-odd-shaped-key-0123456789"  # no sk-ant- prefix: only the exact-value replacement can catch it
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    case = dataset_case("fab-no-financials")

    def respond(kwargs):
        raise RuntimeError(f"upstream said: bad credentials {key}")

    run, _ = one_run(env, case, FakeClient(respond))
    assert key not in json.dumps(run) and "[REDACTED]" in run["error"]


def test_redact_handles_a_short_or_missing_key_and_leaves_ordinary_text_alone(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "abc")  # too short to be worth blanking out of ordinary words
    assert eval_runner.redact("abc and more") == "abc and more"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert eval_runner.redact("plain error") == "plain error"


@pytest.mark.parametrize("version, ok", [("1.7.0", True), ("1.11.0", True), ("2.0.0rc1", True), ("1.5.0", False),
                                         ("1.6.9", False), ("0.99.0", False), (None, False)])
def test_the_sdk_floor_is_compared_numerically_not_as_text(version, ok):
    if ok:
        assert eval_runner.check_sdk_version(version) == version
    else:
        with pytest.raises(RunnerSetupError, match="anthropic"):
            eval_runner.check_sdk_version(version)


def test_the_real_client_is_refused_on_a_too_old_sdk_before_anything_is_built(monkeypatch):
    import anthropic
    built = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: built.append(kwargs))
    monkeypatch.setattr(eval_runner, "sdk_version", lambda: "1.5.0")
    with pytest.raises(RunnerSetupError, match="pip install"):
        eval_runner.make_client()
    assert built == []


def test_the_harness_floor_matches_requirements_txt():
    import re
    from eval_fakes import REPO_ROOT
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"^anthropic\s*>=\s*([\d.]+)\s*$", text, re.M)
    assert match and eval_runner.parse_version(match.group(1)) == eval_runner.MIN_ANTHROPIC_VERSION


def test_the_record_carries_the_sdk_version_and_a_credential_free_base_url(env, monkeypatch):
    dataset, cases = _dataset("fab-no-financials")
    monkeypatch.setattr(eval_runner, "sdk_version", lambda: "9.9.9")
    client = FakeClient(lambda kw: good_maker_text(cases[0]))
    client.base_url = "https://user:hunter2@example.invalid/v1"
    record = run_live(dataset, cases, 1, 5, env["run_dir"], client, repo_root=env["repo"])
    assert record["environment"] == {"anthropic_sdk_version": "9.9.9", "base_url": "https://example.invalid/v1"}
    assert "hunter2" not in json.dumps(record)


def test_the_effective_base_url_honours_the_environment_and_strips_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert eval_runner.effective_base_url() == eval_runner.DEFAULT_BASE_URL
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://u:pw@proxy.invalid:8443/x")
    assert eval_runner.effective_base_url() == "https://proxy.invalid:8443/x"


def test_prepare_run_dir_refuses_to_reuse_an_existing_run_id(tmp_path):
    from eval_report import ResultsPathError, prepare_run_dir
    first = prepare_run_dir("20260101T000000Z", out_root=str(tmp_path))
    (tmp_path / "20260101T000000Z" / "runs.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ResultsPathError, match="already exists"):
        prepare_run_dir("20260101T000000Z", out_root=str(tmp_path))
    assert os.path.isfile(os.path.join(first, "runs.jsonl"))  # the earlier run is untouched
