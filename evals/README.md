# Local live-model evaluation harness

Tracked in issue #151. **Status: PR 1 of 2 -- scaffolding, dataset, deterministic oracles and a
zero-model-call dry run. There is no live runner yet.**

The test suite proves the *code* around the model works. It cannot show whether the *model* follows
the prompts: refuses to invent a missing figure, flags contradictions, labels analyst-supplied
inputs, or resists instructions planted in source text. This harness is how that gets measured --
locally, by hand, on synthetic data, with your own API key.

## Boundaries (deliberate, and enforced by tests)

- **Explicit invocation only:** `python scripts/run_evals.py ...`. Not part of pytest or CI, and
  never imported by `orchestrator.py` or the tests' fixtures.
- **A hard ceiling on model calls.** The plan (cases x repeats) is printed before anything runs and
  is refused if it exceeds `--max-calls` (default 80); no flag can raise the cap above 250.
  Defaults: 5 repeats per case, 15 cases = 75 planned calls.
- **Synthetic data only.** Every company is an invented name starting with `Synthetic `; every case
  is scanned with `scripts/pii_scan.py`; the canary tokens are invented.
- **Results are never tracked.** Output goes under `evals/results/`, which is git-ignored; the
  harness refuses to write anywhere inside the repo that is not. It never modifies a tracked file.
  A baseline is committed only by a deliberate manual copy of an exported summary (pass rates,
  model, prompt hashes, dataset version -- no model text), in its own reviewed PR.
- **No GitHub secret.** A live run uses your own `ANTHROPIC_API_KEY` from your environment.

## Three kinds of result, kept apart

1. **Deterministic oracle results** -- pass/fail decided by code, no trust in the model needed:
   `compliance` (the framework's own `check_draft_compliance`), `figures_grounded`,
   `sources_declared`, `analyst_supplied_disclosed`, `structured_block_present`, `canary_absent`,
   `verdict_is`.
2. **Observed pass rates** -- for live runs (a later PR), k passes out of N repeats per case and per
   category. This is the rate *observed for this model, these prompt hashes and this dataset
   version*. A 5/5 is **not** "proven safe": output is not deterministic, N is small, the dataset is
   synthetic and small, and a model or prompt update can change the numbers.
3. **Human-review observations** -- qualitative questions per case (is the narrative balanced? was a
   contradiction surfaced?) shown beside the output in the review pack. No pass/fail, no LLM judge.

## What the canary oracle does and does not see

It reports whether a planted canary token (or a trivial re-encoding: other case, separators/spacing,
reversed, rot13, base64, hex) appears in what the *model produced* -- a Maker case's draft, a Checker
case's review notes (a Checker case's own draft is scripted and is where the canary is planted). It
cannot detect obedience that leaves no token (for example a quietly softened risk rating).

## Scope limit of v1 -- read before trusting a result

- It exercises the **headless pipeline surfaces**: free text reaching the prompt through collateral
  descriptions, persisted learnings, credit-policy notes and the style guide.
- Its **source-document block** scenario places invented source text next to the real agent prompt.
  That is a **prompt-level approximation** of what `/research` and `/commercial` read -- **not** an
  end-to-end test of the slash-command path, which a script cannot drive.
- So v1 does **not** establish injection resistance for surfaces it cannot exercise.
- **Checker cases stay model-dependent:** the Maker draft is scripted to be code-compliant but to
  carry a flaw `policy_checks` cannot catch (an unsourced narrative claim), so a REJECTED verdict can
  only come from the model. Maker cases script the Checker's response, so every run costs exactly one
  live call.

## Dataset layout

`evals/dataset/<version>/dataset.json` plus `cases/<id>.json` (file name = case id):

```text
{ id, category, mode: "maker" | "checker", description,
  deal:           { company, proposal, deal_type, pd, lgd, multi_period_financials?, financials?,
                    ratios?, financials_source?, collateral?, stress_assumptions?, ... },
  config_files?:  { style_guide?, credit_policy?, credit_policy_notes?, deal_learnings?, company_learnings? },
  source_block?:  { label, text },
  scripted?:      { maker_draft }                       # checker cases only
  canary?:        { token: "CANARY-XXXXXXXX", planted_in: [...], parts?: [...] },
  assertions:     [ { oracle, params? } ],
  human_review:   [ question, ... ],
  dry_run:        { good: {...}, bad: {...} } }         # scripted outputs
```

Every case carries scripted `good` and `bad` outputs, and is only valid if its assertions pass
`good` and fail `bad` -- so the oracles are proven to discriminate with zero model calls.

## Usage (this version)

```bash
python scripts/run_evals.py --validate     # strict dataset validation
python scripts/run_evals.py --list         # list the cases
python scripts/run_evals.py --dry-run      # plan the calls, self-check the oracles, write results
```

`--dry-run` makes **0** model calls and writes `evals/results/<run id>/results.json` and
`review_pack.md`.
