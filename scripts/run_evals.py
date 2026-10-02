"""Local live-model evaluation harness -- CLI (issue #151).

Run explicitly, by hand. This is **not** part of the test suite or CI and is never imported by
them or by orchestrator.py.

  --validate                     load and strictly validate the dataset, print a summary
  --list                         list the cases
  --dry-run                      zero model calls: plan the calls, then run every case's scripted
                                 `good` and `bad` outputs through the deterministic oracles
  --live                         the real thing: run the cases against the configured model(s)
                                 with YOUR ANTHROPIC_API_KEY. Prints the plan first, refuses one
                                 over the call cap, and asks for confirmation (unless --yes)
  --export-baseline RESULTS      summarise a live results.json into baseline_summary.json (no
                                 model text) next to it; you copy it into evals/baselines/ by hand
  --compare BASELINE RESULTS     line a live run up against a committed baseline

`--live` spends real money: at most --max-calls calls (default 80, never above 250), one per
case per repeat. Everything is written under the git-ignored evals/results/ and nothing tracked is
ever modified. Observed pass rates are observations for one model / prompt / dataset
configuration -- not a proof of safety -- and most oracles check the form of the output, not
its judgement; see evals/README.md.
"""
import argparse
import os
import sys

import eval_budget
from eval_baseline import BaselineError
from eval_cases import DatasetError, load_dataset, validate_dataset
from eval_oracles import build_scripted_output, self_check
from eval_report import (
    DISCLAIMER,
    ResultsPathError,
    assert_results_dir_is_ignored,
    build_record,
    new_run_id,
    prepare_run_dir,
    prompt_hashes,
    scripted_run,
    write_results,
)
from eval_runner import RunnerSetupError


def _progress_line(done, total, case_id, run):
    """One line per finished run on stderr: ids and statuses only, never model text."""
    outcome = ("ERROR" if run.get("status", "ok") != "ok" else "pass" if run["passed"] else "FAIL")
    detail = f" [{run['stop_reason']}]" if run.get("stop_reason") else ""
    print(f"[{done}/{total}] {case_id} {run['label']}: {outcome}{detail}", file=sys.stderr, flush=True)


def make_client():
    """The real client (needs ANTHROPIC_API_KEY). Indirected so tests can substitute a fake."""
    import eval_runner
    return eval_runner.make_client()


def _scripted_runs(case):
    outcome = self_check(case)
    runs = []
    for label in ("good", "bad"):
        output = build_scripted_output(case, case["dry_run"][label])
        text = output.draft_text if case["mode"] == "maker" else f"verdict: {output.verdict}\n{output.notes}"
        runs.append(scripted_run(label, outcome[label], text or "", expected_pass=(label == "good")))
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


def _select_cases(dataset, wanted):
    if wanted is None:
        return dataset["cases"]
    ids = [part.strip() for part in wanted.split(",") if part.strip()]
    if not ids:  # "" or "," must not silently mean "everything" (or nothing)
        raise DatasetError("--cases was given but names no case ids; omit it to run every case")
    known = {c["id"]: c for c in dataset["cases"]}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise DatasetError(f"unknown case id(s) {unknown}; known: {sorted(known)}")
    return [known[i] for i in dict.fromkeys(ids)]


def run_live_command(dataset, args):
    selected = _select_cases(dataset, args.cases)
    planned = eval_budget.planned_calls(len(selected), args.repeats)
    print(f"Plan: {len(selected)} cases x {args.repeats} repeats = {planned} LIVE model calls "
          f"(cap {args.max_calls}, absolute ceiling {eval_budget.ABSOLUTE_MAX_CALLS}), one per case per repeat.")
    eval_budget.check_plan(planned, args.max_calls)  # refused here, before any key is touched

    if not args.yes:
        try:
            answer = input(f"This will spend up to {planned} live model calls with your API key. "
                           "Type 'yes' to continue: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "yes":
            print("Not confirmed; nothing was run.", file=sys.stderr)
            return 2

    import eval_runner
    client = make_client()
    run_dir = prepare_run_dir(new_run_id(), out_root=args.out)
    record = eval_runner.run_live(dataset, selected, args.repeats, args.max_calls, run_dir, client,
                                  keep_work=args.keep_work, progress=_progress_line)
    out_dir = write_results(record, out_root=args.out)

    print(f"Model calls made: {record['usage']['calls']} of a planned {planned} "
          f"({record['usage']['input_tokens']} input / {record['usage']['output_tokens']} output tokens).")
    print("Observed pass rates (live runs; k passed / n runs):")
    for category, rate in sorted(record["summary"]["observed_pass_rate_by_category"].items()):
        print(f"  {category:22} {rate['passed']} / {rate['total']}")
    errored = sum(1 for c in record["cases"] for r in c["runs"] if r.get("status", "ok") != "ok")
    if errored:
        print(f"{errored} run(s) errored (counted as non-passes).")
    if record["aborted"]:
        print(f"ABORTED early ({record['abort_category']}): {record['abort_reason']}", file=sys.stderr)
    print(DISCLAIMER)
    print(f"Wrote {out_dir} (results.json, review_pack.md, runs.jsonl). Read the review pack's "
          "human-review section before drawing any conclusion.")
    return 1 if record["aborted"] else 0


def export_baseline_command(results_path):
    import eval_baseline
    record = eval_baseline.load_json(results_path)
    summary = eval_baseline.export_baseline(record)
    out_dir = os.path.dirname(os.path.abspath(results_path))
    assert_results_dir_is_ignored(out_dir, probe_names=("baseline_summary.json",))
    target = os.path.join(out_dir, "baseline_summary.json")
    if os.path.exists(target):
        raise ResultsPathError(f"{target} already exists; refusing to overwrite it")
    import json
    with open(target, "x", encoding="utf-8") as f:  # "x": never overwrite, even if it appears after the check
        json.dump(summary, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Wrote {target}\nIt holds pass rates, hashes and the model -- no model text. To adopt it as a "
          "baseline, copy it by hand to evals/baselines/ and commit it in its own reviewed PR; this "
          "harness never writes a tracked file.")
    return 0


def compare_command(baseline_path, results_path):
    import eval_baseline
    result = eval_baseline.compare(eval_baseline.load_json(baseline_path), eval_baseline.load_json(results_path))
    print(eval_baseline.render_comparison(result))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Local live-model evaluation harness: dataset validation, a zero-model-call dry run, "
                    "and (with --live and your own API key) a capped live evaluation.")
    parser.add_argument("--dataset", default="v1", help="dataset version under evals/dataset/ (default v1)")
    parser.add_argument("--validate", action="store_true", help="validate the dataset and exit")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan the calls, then self-check the oracles with scripted outputs (0 model calls)")
    parser.add_argument("--live", action="store_true",
                        help="run the cases against the configured model(s); spends real API calls")
    parser.add_argument("--cases", default=None, help="with --live: comma-separated case ids (default: all)")
    parser.add_argument("--yes", action="store_true", help="with --live: skip the confirmation prompt")
    parser.add_argument("--keep-work", action="store_true",
                        help="with --live: keep each run's isolated working directory (inside the results dir)")
    parser.add_argument("--export-baseline", metavar="RESULTS_JSON", default=None,
                        help="summarise a live results.json into baseline_summary.json (no model text)")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE_JSON", "RESULTS_JSON"), default=None,
                        help="compare a live results.json against a baseline summary")
    parser.add_argument("--repeats", type=int, default=eval_budget.DEFAULT_REPEATS,
                        help=f"repeats per case (default {eval_budget.DEFAULT_REPEATS})")
    parser.add_argument("--max-calls", type=int, default=eval_budget.DEFAULT_MAX_CALLS,
                        help=f"call cap (default {eval_budget.DEFAULT_MAX_CALLS}, "
                             f"never above {eval_budget.ABSOLUTE_MAX_CALLS})")
    parser.add_argument("--out", default=None,
                        help="results directory (default evals/results/; a path inside the repo must be "
                             "git-ignored, a path outside it is allowed)")
    args = parser.parse_args(argv)

    modes = [bool(args.dry_run), bool(args.live), bool(args.export_baseline), bool(args.compare)]
    if sum(modes) > 1:
        parser.error("--dry-run, --live, --export-baseline and --compare are mutually exclusive")
    if not (args.validate or args.list or any(modes)):
        print("Nothing to do. Use --validate, --list, --dry-run, --live, --export-baseline or --compare.",
              file=sys.stderr)
        return 2
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.max_calls < 1:
        parser.error("--max-calls must be at least 1")

    try:
        if args.export_baseline:
            return export_baseline_command(args.export_baseline)
        if args.compare:
            return compare_command(*args.compare)
    except (BaselineError, ResultsPathError, FileExistsError) as exc:  # a bad file/guard: clean error, no traceback
        print(f"Error: {exc}", file=sys.stderr)
        return 1

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
    try:
        if args.dry_run:
            return run_dry(dataset, args.repeats, args.max_calls, args.out)
        if args.live:
            return run_live_command(dataset, args)
    except eval_budget.CallCapExceeded as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    except DatasetError as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 1
    except (RunnerSetupError, ResultsPathError) as exc:  # setup problems (no key, guard): clean errors
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    sys.exit(main())
