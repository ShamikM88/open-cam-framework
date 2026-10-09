# Evaluation harness

> The sections "In brief" and "How it works" were moved from the README and `CLAUDE.md` (unchanged apart from the fixes in the [move ledger](move-ledger.md)); the sections between them were written for readers. The source of truth for behaviour is the code and tests; see the [index](README.md).

How the local live-model evaluation harness is built and what it can and cannot show. This page is evergreen: it does not record whether a live run has happened. Current status is tracked in issue #151 (and dated in the [index](README.md#project-status)). The dataset-local companion is [`evals/README.md`](../evals/README.md).

## In brief

Separately, `evals/` holds a **local-only live-model evaluation harness** (`python scripts/run_evals.py`,
see [`evals/README.md`](../evals/README.md)) for measuring what those tests cannot -- whether the actual
model follows the prompts (no invented figures, planted instructions not obeyed). It is run by hand with
your own API key, on synthetic data only, under a hard call cap, and writes only to the git-ignored
`evals/results/`; nothing runs it automatically, and the tests only ever drive it with fake clients. It ships its dataset, deterministic oracles, a zero-model-call `--dry-run`, a capped `--live` runner and baseline export/compare. Read its README before trusting any
result: most oracles check the *form* of the output, not its judgement.

## What the harness can and cannot show

The test suite proves the code around the model works; this harness is the only thing that measures the model. What a result means depends on three things you should read together: which **kind** of result it is, what the **oracle** actually checks, and which **surface** the case exercises.

**Three kinds of result, kept apart.** A *deterministic oracle result* is decided by code and needs no trust in the model (a figure matches, a block parses, a canary token is absent). An *observed pass rate* is k passes out of N repeats for one model, one set of prompt hashes and one dataset version; it is an observation, never "proven safe". A *human-review observation* is a question for a person with no pass or fail. The harness reports them separately so a form check is not mistaken for a judgement.

**What an oracle passing means.** Most oracles check form or self-declaration. A "no invented figures" case passing means the model emitted a well-formed block whose declared figures match the computed ones, including when it declared none; it does not mean the narrative was careful. Read the review pack for the judgement. A canary oracle reports that a planted token appeared in the output, not that it was obeyed, and cannot see obedience that leaves no token.

**What the harness cannot show.**

- It runs the headless pipeline's surfaces. The slash-command path (`/research`, `/commercial`, `/review` as a session runs them) cannot be driven by a script, so the harness cannot say whether a session follows its command text, which is where the code-enforced rejection is applied by the model rather than by Python ([AI assurance](ai-assurance.md#where-the-enforcement-runs-matters)).
- Its source-document case is a prompt-level approximation, so it does not establish injection resistance for fetched web content.
- A small synthetic dataset cannot show how the model behaves on a real, messy deal.
- A rate holds for the model and prompts that produced it. A different model, a prompt edit or a new dataset version is a new measurement.

## Reading a result

1. **Look at the denominator.** A run that errored, was refused or was cut off at the token limit stays in the count as a non-pass, so a high rate over few completed runs is weaker than it looks. Check the status counts, then the rate.
2. **Look at the model and hashes.** A result is tied to the requested and served model, the prompt hashes and the dataset version. Compare two results only when those match, or when the difference *is* what you are testing.
3. **Read the failures and the review pack before the headline.** A single failing case with its output tells you more than a category rate; a Checker that rejects a clean control for a reason you disagree with is information about the case as much as the model.
4. **Treat small differences as noise.** Output is not deterministic and N is small. A change of one run in five is not a trend; repeat or widen the dataset before drawing a conclusion.
5. **Never quote a rate without its context** (model, prompt hashes, dataset version, N), and never as a safety claim.

## Baselines

A baseline is a committed summary of a complete live run: pass rates per category and case, the requested and served model, the prompt hashes, the dataset version and hash, and no model text. It exists so that a later prompt or model change can be compared with something real (`--compare`), which is why prompt hardening (#150) waits for one ([AI assurance](ai-assurance.md#why-prompt-hardening-150-waits-for-a-baseline)).

- A baseline is **valid only for the hashes it records.** Edit a prompt, change the model or change the dataset, and it describes something else; `--compare` says whether two runs are like-for-like and lists what differs, so a delta is not read across a changed prompt, model or dataset.
- A **partial** run (aborted, errored, interrupted, truncated, or fewer runs than planned) is not exported as a baseline unless you pass `--allow-partial`, which marks it partial. A partial file is a diagnostic, not a reference.
- A baseline is **adopted deliberately**: copied by hand into `evals/baselines/<file>.json` and committed in its own pull request, never by a script and never as a side effect of another change. The procedure and the gates are in the [operations runbook](operations.md#live-evaluation-execution-and-baseline-boundaries).
- The record of what has been run, and of any attempt that failed (for example one rejected by the API for credit), belongs in issue #151 and the dated [project status](README.md#project-status), not on this page.

## Cost and safety

- The **call cap bounds calls, not tokens or money.** Before the key is read, the plan is printed and refused if it is over `--max-calls` (default 80, never above 250). The plan also shows the models, `max_tokens`, the output-token bound and the endpoint; read it before typing `yes`.
- A live run uses **your own key**, held in your environment only. No GitHub secret, no key in a file, and no automated session or CI job ever runs it.
- Each run is **isolated**: a fresh working directory containing only the agent prompts, `config/settings.json` and the shipped CAM templates, never `templates/local/` or a real deal. The data is synthetic, and the writer refuses any output path inside the repository that is not git-ignored.

## How this fits the other layers

The harness measures what [AI assurance](ai-assurance.md#what-the-prompts-expect-of-the-model) calls prompt expectations, which are not demonstrated until it has been run. It complements, and never replaces, the deterministic checks (which hold whatever the model writes) and the human review (which is the only thing that judges the narrative). Running it is a maintainer action with its own boundaries in the [runbook](operations.md#live-evaluation-execution-and-baseline-boundaries); the dataset layout and the oracles' own documentation are in [`evals/README.md`](../evals/README.md).

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
