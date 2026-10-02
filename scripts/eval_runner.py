"""Live runner core for the local evaluation harness (issue #151, PR 2).

Runs each case through the framework's REAL prompt assembly -- `orchestrator.run_pipeline()`
unchanged -- inside a fresh, isolated working directory, with exactly ONE live model call per
run, and scores the output with the deterministic oracles. Nothing here is imported by
pytest, CI or `orchestrator.py`; it is reached only from `run_evals.py --live`, and the tests
exercise it exclusively with fake clients (zero model calls).

How one run works
-----------------
`run_pipeline()` reads everything relative to the current directory (agents/, config/,
templates/, deals/) and resumes any earlier state for the same company/proposal, so each run
gets its own temporary directory under the run's git-ignored `work/` folder, seeded with ONLY:
the shipped agent prompts, `config/settings.json` (the models/temperatures a real run would use),
`templates/cam/` (never `templates/local/`, so a developer's calibrated override cannot leak
in), the case's own `config_files`, and the case's `state.json` extras. The process `chdir`s
into it, so runs are strictly serial.

`run_pipeline()` is driven with `max_iterations=1` and a **routing client**:

- Maker case: call 1 (the Maker) is the live call; the routing client then stops the
  pipeline at call 2 (the Checker), which is never made. One live call.
- Checker case: call 1 (the Maker) is answered with the case's scripted draft -- no model, no
  budget spent; call 2 (the Checker) is the live call, after which the run is stopped. One live call.

Both calls' real messages are built by `run_pipeline()`, so prompt assembly cannot drift from
what ships. The live client is a `BudgetedClient` (hard cap, counted before each call) built
with `max_retries=0`, so the cap counts logical calls and a retry loop cannot overspend.

The one construct that does NOT come from the real pipeline is a case's `source_block`: it
is appended, XML-tagged, to the end of the Maker's message. That is a prompt-level approximation of
what `/research` and `/commercial` read -- not an end-to-end test of the slash-command path.
"""
import hashlib
import io
import json
import os
import shutil
import tempfile
from contextlib import redirect_stdout
from types import SimpleNamespace

from eval_budget import BudgetedClient, CallBudget, CallCapExceeded
from eval_oracles import RunOutput, build_context, evaluate_case
from eval_report import REPO_ROOT, append_run_line, build_record, live_run
from policy_checks import FENCED_JSON_RE

MAX_CONSECUTIVE_ERRORS = 3
CLIENT_TIMEOUT_SECONDS = 180
CONFIG_FILE_PATHS = {
    "style_guide": os.path.join("config", "style_guide.md"),
    "credit_policy": os.path.join("config", "credit_policy.md"),
    "credit_policy_notes": os.path.join("config", "credit_policy_notes.md"),
    "deal_learnings": os.path.join("config", "deal_learnings.md"),
}
STATE_EXTRAS = ("covenants", "security_package", "guarantees")


class RunnerSetupError(RuntimeError):
    """The environment can't support a live run (no API key, missing shipped files)."""


class StopRun(Exception):
    """Control flow: raised by the routing client to end run_pipeline() right after the live call."""


def short_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def make_client():
    """The real Anthropic client: `max_retries=0` so the budget counts logical calls only."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RunnerSetupError("ANTHROPIC_API_KEY is not set in your environment; a live run needs your own key "
                               "(it is never stored or sent anywhere except to Anthropic).")
    import anthropic  # lazy: the rest of the harness never imports it
    return anthropic.Anthropic(api_key=key, max_retries=0, timeout=CLIENT_TIMEOUT_SECONDS)


def verdict_parsed(raw_text):
    """True if `raw_text` carries a fenced JSON block with a recognised verdict -- i.e. exactly
    when `orchestrator.parse_verdict` did NOT fall back to its default REJECTED."""
    for match in reversed(list(FENCED_JSON_RE.finditer(raw_text or ""))):
        try:
            payload = json.loads(match.group(1))
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        if str(payload.get("verdict", "")).strip().upper() in ("APPROVED", "REJECTED"):
            return True
    return False


def _response(text):
    return SimpleNamespace(content=[SimpleNamespace(text=text)], usage=None)


class RoutingClient:
    """Stands in for the `client` run_pipeline() calls: answers the scripted call itself, sends
    the live one through the budgeted client, captures both responses, and stops the pipeline
    right after the live call."""

    def __init__(self, case, live_client):
        self.case = case
        self.live = live_client
        self.calls = []       # one dict per call, in order (no message text is kept)
        self.responses = {}   # role -> response text
        self.live_message = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        index = len(self.calls) + 1
        if index > 2:
            raise RuntimeError("run_pipeline made a third model call; it must run with max_iterations=1")
        role = "maker" if index == 1 else "checker"
        mode = self.case["mode"]
        is_live = role == mode  # the Maker call is live in a maker case, the Checker call in a checker case

        if not is_live:
            if role == "checker":      # maker case: the Checker is never needed
                self.calls.append({"role": role, "live": False, "model": None, "prompt_hash": None, "stopped": True})
                raise StopRun()
            text = self.case["scripted"]["maker_draft"]  # checker case: the scripted Maker draft
            self.calls.append({"role": role, "live": False, "model": None, "prompt_hash": None})
            self.responses[role] = text
            return _response(text)

        message = kwargs["messages"][0]["content"]
        source_block = self.case.get("source_block")
        if role == "maker" and source_block:
            from orchestrator import _xml_block  # lazy
            message = message + "\n\n" + _xml_block(
                "source_document", f"Label: {source_block['label']}\n{source_block['text']}")
            kwargs = {**kwargs, "messages": [{"role": "user", "content": message}]}
        self.live_message = message
        response = self.live.messages.create(**kwargs)
        text = response.content[0].text
        self.calls.append({"role": role, "live": True, "model": kwargs.get("model"),
                           "prompt_hash": short_hash(message)})
        self.responses[role] = text
        if role == "checker":
            raise StopRun()            # checker case: nothing after the verdict is needed
        return response


def prepare_workdir(case, repo_root, workdir):
    """Seed an isolated working directory for one run (see the module docstring)."""
    for folder in ("agents", os.path.join("templates", "cam")):  # never templates/local
        source = os.path.join(repo_root, folder)
        if not os.path.isdir(source):
            raise RunnerSetupError(f"{source} is missing; cannot seed an isolated run")
        shutil.copytree(source, os.path.join(workdir, folder))
    settings = os.path.join(repo_root, "config", "settings.json")
    if not os.path.isfile(settings):
        raise RunnerSetupError(f"{settings} is missing; the models a live run uses come from it")
    os.makedirs(os.path.join(workdir, "config"), exist_ok=True)
    shutil.copyfile(settings, os.path.join(workdir, "config", "settings.json"))

    config = (case.get("config_files") or {}) if case else {}
    for key, relative in CONFIG_FILE_PATHS.items():
        if key in config:
            with open(os.path.join(workdir, relative), "w", encoding="utf-8") as f:
                f.write(config[key])
    if "company_learnings" in config:
        from state_manager import sanitize_path_component
        company = sanitize_path_component(case["deal"]["company"], "company")
        folder = os.path.join(workdir, "deals", company)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "_learnings.md"), "w", encoding="utf-8") as f:
            f.write(config["company_learnings"])


def input_hashes(workdir):
    """Short hashes of every file a run's prompts are built from, as actually used in the
    isolated copy (agent prompts, templates, settings) -- not the repo's, which could differ.
    Computed with orchestrator's own `_content_hash` over the decoded text, so they equal the
    hashes `model_provenance` records and do not change between CRLF and LF checkouts."""
    from orchestrator import _content_hash
    from textio import read_text
    hashes = {}
    for folder in ("agents", os.path.join("templates", "cam"), "config"):
        base = os.path.join(workdir, folder)
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if os.path.isfile(path):
                hashes[f"{folder.replace(os.sep, '/')}/{name}"] = _content_hash(read_text(path))
    return hashes


def describe_inputs(repo_root, work_root):
    """Models, prompt hashes and input hashes a run WOULD use, taken from an isolated copy.
    Run once before any call is spent, so a missing settings file fails the run early."""
    import orchestrator
    os.makedirs(work_root, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="inputs-", dir=work_root)
    original = os.getcwd()
    try:
        prepare_workdir(None, repo_root, workdir)
        os.chdir(workdir)
        config = orchestrator._resolve_maker_checker_config()
        hashes = input_hashes(workdir)
    finally:
        os.chdir(original)
        shutil.rmtree(workdir, ignore_errors=True)
    models = {"maker_model": config["maker_model"], "checker_model": config["checker_model"],
              "maker_temperature": config.get("maker_temperature"),
              "checker_temperature": config.get("checker_temperature")}
    prompts = {"underwriter_prompt_hash": hashes["agents/underwriter_agent.md"],
               "risk_reviewer_prompt_hash": hashes["agents/risk_reviewer_agent.md"]}
    return {"models": models, "prompt_hashes": prompts, "input_hashes": hashes}


def run_case_once(case, label, ctx, live_client, repo_root, work_root, keep_work=False):
    """One run of one case; returns a `live_run` record. Raises CallCapExceeded (the whole
    evaluation must stop); any other failure is recorded as an errored run."""
    import orchestrator
    deal = case["deal"]
    workdir = tempfile.mkdtemp(prefix=f"{case['id']}-", dir=work_root)
    original = os.getcwd()
    router = RoutingClient(case, live_client)
    error = None
    try:
        prepare_workdir(case, repo_root, workdir)
        os.chdir(workdir)
        extras = {k: deal[k] for k in STATE_EXTRAS if deal.get(k)}
        if not deal.get("multi_period_financials"):
            for key in ("financials", "ratios", "financials_source", "downside_case"):
                if deal.get(key):
                    extras[key] = deal[key]
        if extras:
            from state_manager import write_state
            write_state(deal["company"], deal["proposal"], **extras)
        try:
            with redirect_stdout(io.StringIO()):  # the pipeline's progress lines are noise here
                orchestrator.run_pipeline(
                    deal["company"], deal["proposal"], deal["pd"], deal["lgd"], deal["deal_type"],
                    multi_period_financials=deal.get("multi_period_financials"),
                    collateral_data=deal.get("collateral"),
                    stress_assumptions=deal.get("stress_assumptions"),
                    client=router, max_iterations=1)
        except StopRun:
            pass
    except CallCapExceeded:
        raise
    except SystemExit as exc:  # run_pipeline() exits on a REJECTED verdict; the runner stops it first,
        error = f"SystemExit: the pipeline exited unexpectedly (code {exc.code})"  # so this is an anomaly
    except Exception as exc:  # noqa: BLE001 - an API or runner failure is a recorded, non-passing run
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        os.chdir(original)
        if not keep_work:
            shutil.rmtree(workdir, ignore_errors=True)

    live_calls = [c for c in router.calls if c["live"]]
    extra = {"prompt_hash": live_calls[0]["prompt_hash"] if live_calls else None,
             "model": live_calls[0]["model"] if live_calls else None, "live_calls": len(live_calls)}
    role = case["mode"]
    if error or role not in router.responses:
        return live_run(label, [], status="error", error=error or "the live call produced no response", extra=extra)

    raw = router.responses[role]
    if role == "maker":
        output = RunOutput(draft_text=raw)
        excerpt = raw
    else:
        verdict, notes = orchestrator.parse_verdict(raw)
        output = RunOutput(draft_text=case["scripted"]["maker_draft"], verdict=verdict,
                           notes=notes if isinstance(notes, str) else "", raw_text=raw,
                           verdict_parsed=verdict_parsed(raw))
        excerpt = f"verdict: {verdict} (parsed from the response: {output.verdict_parsed})\n{raw}"
    results = evaluate_case(case, output, ctx)
    return live_run(label, results, excerpt, extra=extra, output_text=raw)


def run_live(dataset, cases, repeats, max_calls, run_dir, client, repo_root=None, keep_work=False):
    """Run `cases` x `repeats` live, serially. Returns the run record (not yet written).
    Stops early -- recording why -- if the cap is spent or MAX_CONSECUTIVE_ERRORS runs in a row
    errored. Each finished run is appended to `runs.jsonl` immediately."""
    repo_root = repo_root or REPO_ROOT
    work_root = os.path.join(run_dir, "work")
    inputs = describe_inputs(repo_root, work_root)

    budget = CallBudget(max_calls)
    live_client = BudgetedClient(client, budget)
    out_cases, aborted, abort_reason, consecutive_errors = [], False, None, 0
    for case in cases:
        ctx = build_context(case)
        runs = []
        for repeat in range(1, repeats + 1):
            if aborted:
                break
            try:
                run = run_case_once(case, f"repeat-{repeat}", ctx, live_client, repo_root, work_root, keep_work)
            except CallCapExceeded as exc:
                aborted, abort_reason = True, f"call cap: {exc}"
                break
            runs.append(run)
            append_run_line(run_dir, case["id"], run)
            consecutive_errors = consecutive_errors + 1 if run["status"] != "ok" else 0
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                aborted = True
                abort_reason = f"{MAX_CONSECUTIVE_ERRORS} consecutive errored runs (last: {run.get('error')})"
        out_cases.append({
            "id": case["id"], "category": case["category"], "mode": case["mode"],
            "description": case["description"], "human_review": case.get("human_review", []),
            "runs_planned": repeats, "runs": runs,
        })
        if aborted:
            for later in cases[len(out_cases):]:  # cases never reached still appear, with no runs
                out_cases.append({"id": later["id"], "category": later["category"], "mode": later["mode"],
                                  "description": later["description"], "human_review": later.get("human_review", []),
                                  "runs_planned": repeats, "runs": []})
            break

    if not keep_work:
        shutil.rmtree(work_root, ignore_errors=True)
    record = build_record(
        "live", dataset, out_cases, os.path.basename(run_dir), models=inputs["models"],
        hashes=inputs["prompt_hashes"], planned_live_calls=len(cases) * repeats,
        usage={"calls": budget.calls, "input_tokens": budget.input_tokens, "output_tokens": budget.output_tokens},
        call_cap=max_calls, input_hashes=inputs["input_hashes"], repeats=repeats,
        aborted=aborted, abort_reason=abort_reason)
    record["selected_case_ids"] = [c["id"] for c in cases]
    return record
