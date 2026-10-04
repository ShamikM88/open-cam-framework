# Evaluation harness

> **Status.** This page was assembled in the documentation tranche of issue #117 from material that used to live in `README.md` and `CLAUDE.md`, moved with only the textual fixes recorded in the [move ledger](move-ledger.md). A later tranche adds worked examples and explanation around it. The source of truth for behaviour is the code and tests; see the [index](README.md).

How the local live-model evaluation harness is built and what it can and cannot show. This page is evergreen: it does not record whether a live run has happened. Current status is tracked in issue #151 (and dated in the [index](README.md#project-status)). The dataset-local companion is [`evals/README.md`](../evals/README.md).

## In brief

Separately, `evals/` holds a **local-only live-model evaluation harness** (`python scripts/run_evals.py`,
see [`evals/README.md`](../evals/README.md)) for measuring what those tests cannot -- whether the actual
model follows the prompts (no invented figures, planted instructions not obeyed). It is run by hand with
your own API key, on synthetic data only, under a hard call cap, and writes only to the git-ignored
`evals/results/`; nothing runs it automatically, and the tests only ever drive it with fake clients. It ships its dataset, deterministic oracles, a zero-model-call `--dry-run`, a capped `--live` runner and baseline export/compare. Read its README before trusting any
result: most oracles check the *form* of the output, not its judgement.

## How it works

**`scripts/run_evals.py`, `eval_cases.py`, `eval_oracles.py`, `eval_report.py`, `eval_budget.py`,
`eval_runner.py`, `eval_baseline.py`** (issue #151; see `evals/README.md`) — the **local live-model
evaluation harness**, which measures what the test suite cannot: whether the *actual model* follows
the prompts (no invented figures, analyst-supplied inputs labelled, planted instructions not obeyed).
The dataset, oracles and dry run and the live runner with baseline export/compare are built; a live run and a first baseline are deliberate, manual steps taken after review (current status is tracked in issue #151, not in this page). Deliberate boundaries, enforced by `tests/test_eval_*.py` and
`tests/test_run_evals.py`: run only by explicit `python scripts/run_evals.py`; nothing runs it
automatically (it is never imported by `orchestrator.py`, `conftest.py` or CI), though the tests
exercise its validation, oracles, budget, writer, runner and CLI with scripted outputs and **fake
clients -- zero model calls, no API key**; a **hard call ceiling** (plan printed first and refused over
`--max-calls`, default 80, never above 250, *before the API key is even read*; default 5 repeats x 15
cases = 75; it bounds calls, not tokens); **synthetic data only** (invented `Synthetic ...` names, one
company/proposal per case, scanned with `pii_scan.py`, strict case validation that also rejects raw
figure names the framework would silently read as 0 and any mistyped key); results only under the
**git-ignored `evals/results/`** (the writer resolves symlinks/junctions, refuses any path in the repo
whose output files are not ignored, never overwrites an earlier run, and never modifies a tracked
file -- a baseline is committed only by a deliberate manual copy of an exported summary that holds
rates, hashes and the model but no model text); no GitHub secret (a live run uses the user's own
`ANTHROPIC_API_KEY`). **The live runner** (`--live`, with a typed confirmation unless `--yes`) drives
`orchestrator.run_pipeline()` unchanged with `max_iterations=1` and a routing client, so each run costs
exactly one live call (a Maker case stops before the Checker; a Checker case scripts the Maker draft)
and the prompts are the real ones; every run gets a fresh isolated working directory (agents,
`config/settings.json`, `templates/cam/` only -- never `templates/local/`) and runs serially because it
`chdir`s; the live client is a `BudgetedClient` with `max_retries=0` that wraps only the live client;
each finished run is appended to `runs.jsonl` immediately; a run that errors stays in the pass-rate
denominator, and three errors in a row abort. Every non-`ok` run status (`error`, including a scoring
failure after a paid-for call, whose output is still kept; `no_text`, e.g. a refusal; `interrupted`,
Ctrl-C after a call was attempted -- in flight, or during scoring or cleanup with the output kept) is a
recorded non-pass, so per-run call counts sum to the budget (a second Ctrl-C during handling of the
first is the one unrecoverable window; `runs.jsonl` still holds every earlier run). A response's stop
state (`complete` / `refusal` / `incomplete`) is recorded too: a refusal is a result in its own right
(it is scored on any text it returned, so it can still satisfy some form-checking oracles and
contribute to a pass rate -- counted separately and documented in `evals/README.md`), an incomplete
one makes a baseline partial. The reply is read by joining text blocks, error strings are scrubbed of
the API key, and the per-run `stop_reason`/token counts/served model are recorded (a baseline keeps
the served model beside the requested one), with incomplete output flagged in the pack. The plan shows
the models, `max_tokens`, output-token bound and endpoint before the `yes`, and the runner refuses an
installed `anthropic` older than `requirements.txt`'s floor (read from that file at run time, no second copy; the installed version is recorded). A
baseline keeps only an abort *category* (never the free-text reason), and `--export-baseline`
**refuses a partial run** (aborted, errored, interrupted, truncated or incomplete) unless
`--allow-partial`, which marks the file `partial`. Three result types are kept apart: **deterministic
oracle results** (an assertion marked `"scored": false` is an observation that never counts toward
pass/fail), **observed pass rates** over repeated live runs (never described as "proven safe"), and
**human-review observations** (no pass/fail). **Most oracles are format / self-declaration checks, not
judgement:** `figures_grounded` passes when the model declares no figure at all, so those category
pass rates mean "emitted a well-formed, self-consistent block", with the real judgement in human
review. Every case carries scripted `good`/`bad` outputs and is valid only if its scored oracles pass
`good` and `bad` fails exactly the oracle(s) in `bad.expected_failures`. **Scope limit:** v1 exercises
the headless pipeline surfaces (collateral text, persisted learnings and policy notes reach both
agents; the style guide reaches only the Maker) plus a synthetic source-document block that the runner
appends to the Maker message and that is only a *prompt-level approximation* of `/research` -- not an
end-to-end test of the slash-command path -- so it does not establish injection resistance for
surfaces it cannot exercise. The canary oracle reports that a token *appeared* (or a trivial
re-encoding of it) in what the *model produced*; appearing is not the same as obeying (a model that
quotes it while refusing is a hit, which is why a Checker case scores the verdict and only observes
the canary), and it cannot see obedience that leaves no token:
```
python scripts/run_evals.py --dry-run          # 0 model calls
python scripts/run_evals.py --live             # spends real API calls; your key; prints the plan first
python scripts/run_evals.py --export-baseline evals/results/<run>/results.json
python scripts/run_evals.py --compare evals/baselines/<file>.json evals/results/<run>/results.json
```
