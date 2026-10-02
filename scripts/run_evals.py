"""Local live-model evaluation harness -- CLI (issue #151).

Run explicitly, by hand:  python scripts/run_evals.py --dry-run

This is **not** part of the test suite or CI and is never imported by them or
by orchestrator.py. In this version (PR 1) it makes **zero model calls**:

  --validate   load and strictly validate the dataset, print a summary
  --list       list the cases
  --dry-run    plan the calls a live run would make (refusing a plan over the
               cap), then run every case's scripted `good` and `bad` outputs
               through the deterministic oracles, proving the oracles pass a
               well-behaved output and catch a misbehaving one, and write a
               results file and review pack under the git-ignored evals/results/

The live runner arrives in a later PR; until then running without one of the
flags above does nothing and exits non-zero.
"""
import argparse
import sys

import eval_budget
from eval_cases import DatasetError, load_dataset, validate_dataset
from eval_oracles import build_scripted_output, self_check
from eval_report import build_record, new_run_id, prompt_hashes, scripted_run, write_results


def _scripted_runs(case):
    outcome = self_check(case)
    runs = []
    for label in ("good", "bad"):
        output = build_scripted_output(case, case["dry_run"][label])
        text = output.draft_text if case["mode"] == "maker" else f"verdict: {output.verdict}\n{output.notes}"
        runs.append(scripted_run(label, outcome[label], (text or "")[:1500]))
    return outcome, runs


def run_dry(dataset, repeats, max_calls, out_root):
    planned = eval_budget.planned_calls(len(dataset["cases"]), repeats)
    print(f"Plan: {len(dataset['cases'])} cases x {repeats} repeats = {planned} live model calls "
          f"(cap {max_calls}, absolute ceiling {eval_budget.ABSOLUTE_MAX_CALLS}). "
          "This dry run makes 0 calls.")
    eval_budget.check_plan(planned, max_calls)  # refuses a plan a live run could not afford

    cases = []
    for case in dataset["cases"]:
        outcome, runs = _scripted_runs(case)
        cases.append({
            "id": case["id"], "category": case["category"], "mode": case["mode"],
            "description": case["description"], "human_review": case.get("human_review", []),
            "self_check": {k: outcome[k] for k in ("good_all_pass", "bad_fails_at_least_one", "ok")},
            "runs": runs,
        })
    record = build_record("dry-run", dataset, cases, new_run_id(), hashes=prompt_hashes(),
                          planned_live_calls=planned, call_cap=max_calls)
    out_dir = write_results(record, out_root=out_root)
    failed = [c["id"] for c in cases if not c["self_check"]["ok"]]
    print(f"Scripted self-check: {len(cases) - len(failed)} / {len(cases)} cases sound.")
    for case_id in failed:
        print(f"  NOT SOUND: {case_id}")
    print(f"Wrote {out_dir}")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Local live-model evaluation harness. PR 1: dataset validation and a "
                    "zero-model-call dry run only.")
    parser.add_argument("--dataset", default="v1", help="dataset version under evals/dataset/ (default v1)")
    parser.add_argument("--validate", action="store_true", help="validate the dataset and exit")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan the calls, then self-check the oracles with scripted outputs (0 model calls)")
    parser.add_argument("--repeats", type=int, default=eval_budget.DEFAULT_REPEATS,
                        help=f"repeats per case in the planned live run (default {eval_budget.DEFAULT_REPEATS})")
    parser.add_argument("--max-calls", type=int, default=eval_budget.DEFAULT_MAX_CALLS,
                        help=f"call cap (default {eval_budget.DEFAULT_MAX_CALLS}, "
                             f"never above {eval_budget.ABSOLUTE_MAX_CALLS})")
    parser.add_argument("--out", default=None,
                        help="results directory (default evals/results/; must be git-ignored)")
    args = parser.parse_args(argv)

    if not (args.validate or args.list or args.dry_run):
        print("Nothing to do: live evaluation is not implemented in this version. "
              "Use --validate, --list or --dry-run.", file=sys.stderr)
        return 2
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    try:
        dataset = load_dataset(args.dataset)
    except DatasetError as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 1
    problems = validate_dataset(dataset)
    if problems:
        print(f"Dataset {dataset['version']} is INVALID ({len(problems)} problems):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.list:
        for case in dataset["cases"]:
            print(f"{case['id']:42} {case['mode']:8} {case['category']}")
    if args.validate:
        print(f"Dataset {dataset['version']}: {len(dataset['cases'])} cases, all valid "
              "(synthetic-data scan clean; scripted good/bad outputs behave as expected).")
    if args.dry_run:
        try:
            return run_dry(dataset, args.repeats, args.max_calls, args.out)
        except eval_budget.CallCapExceeded as exc:
            print(f"Refused: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    sys.exit(main())
