"""Baseline export and comparison for the evaluation harness (#151, PR 2).

A *baseline* is a small summary of one live run -- observed pass rates, the model, the prompt
and input hashes, the dataset version and content hash -- with **no model text**, so it is safe to
commit. The harness never writes a tracked file: `export_baseline()` returns a dict that the CLI
writes into the git-ignored results directory, and a person copies it into `evals/baselines/` by
hand, in a normal reviewed PR. `compare()` then lines a later run up against it.

A comparison is only meaningful like-for-like, so it first reports whether the model, prompt
hashes, input hashes and dataset content hash match, and says plainly when they do not. Deltas
are *observed* differences between two small samples of a non-deterministic model; the
regression flag is informational and its threshold is arbitrary until real run-to-run variance is
known. Nothing here gates anything.

No `anthropic` dependency and no I/O beyond reading the JSON files it is handed.
"""
import json
from datetime import datetime, timezone

from eval_report import DISCLAIMER, HARNESS_VERSION, pass_rate

BASELINE_KIND = "evaluation-baseline-summary"
REGRESSION_DROP = 0.4  # informational: an observed pass-rate drop of 40 points or more
COMPARED_FIELDS = (("models", "model / temperatures"), ("prompt_hashes", "agent prompt hashes"),
                   ("input_hashes", "template and settings hashes"))


class BaselineError(ValueError):
    """The record handed in cannot be turned into (or compared with) a baseline."""


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"{path}: {exc}") from exc


def export_baseline(record):
    """A baseline summary of a live results record (never contains model or exception text)."""
    try:
        return _export_baseline(record)
    except (KeyError, TypeError, AttributeError) as exc:
        raise BaselineError(f"not a usable live results record ({type(exc).__name__}: {str(exc)[:120]})") from exc


def _export_baseline(record):
    if record.get("mode") != "live":
        raise BaselineError("only a live run can be a baseline (this record is "
                            f"{record.get('mode')!r}: a dry run makes no model calls)")
    per_case, per_category = [], {}
    for case in record["cases"]:
        rate = pass_rate(case["runs"])
        errors = sum(1 for r in case["runs"] if r.get("status", "ok") != "ok")
        per_case.append({"id": case["id"], "category": case["category"], "mode": case["mode"],
                         "passed": rate["passed"], "total": rate["total"], "errored": errors,
                         "runs_planned": case.get("runs_planned", rate["total"])})
        bucket = per_category.setdefault(case["category"], {"passed": 0, "total": 0})
        bucket["passed"] += rate["passed"]
        bucket["total"] += rate["total"]
    if not any(c["total"] for c in per_case):
        raise BaselineError("this record has no completed live runs, so there is nothing to baseline")
    return {
        "kind": BASELINE_KIND,
        "harness_version": record.get("harness_version", HARNESS_VERSION),
        "created": datetime.now(timezone.utc).isoformat(),
        "source_run_id": record["run_id"],
        "dataset": record["dataset"],
        "models": record["models"],
        "prompt_hashes": record["prompt_hashes"],
        "input_hashes": record.get("input_hashes", {}),
        "repeats": record.get("repeats"),
        "aborted": bool(record.get("aborted")),
        # The CATEGORY only: the free-text reason can embed an API/exception message, and this file is
        # meant to be copied into a tracked path.
        "abort_category": record.get("abort_category"),
        # What the live runs actually sent: a change to the code that assembles the prompt (the grounding
        # context, the policy engine) is invisible to the file hashes but changes these.
        "prompt_hashes_by_case": {
            c["id"]: sorted({r["prompt_hash"] for r in c["runs"] if r.get("prompt_hash")})
            for c in record["cases"]},
        "selected_case_ids": record.get("selected_case_ids", [c["id"] for c in record["cases"]]),
        "per_case": per_case,
        "per_category": per_category,
        "disclaimer": DISCLAIMER,
    }


def _rate(passed, total):
    return passed / total if total else None


def compare(baseline, record):
    """Compare a live `record` with a `baseline` summary. Returns a dict with `like_for_like`
    (field -> bool plus a list of mismatches) and per-case / per-category rows."""
    try:
        return _compare(baseline, record)
    except (KeyError, TypeError, AttributeError) as exc:
        raise BaselineError(f"cannot compare these files ({type(exc).__name__}: {str(exc)[:120]})") from exc


def _compare(baseline, record):
    if baseline.get("kind") != BASELINE_KIND:
        raise BaselineError("the first file is not an evaluation baseline summary")
    current = export_baseline(record)
    mismatches = []
    if baseline.get("harness_version") != current.get("harness_version"):
        mismatches.append("harness version")
    if baseline.get("repeats") != current.get("repeats"):
        mismatches.append("repeats per case")
    for key, label in COMPARED_FIELDS:
        if baseline.get(key) != current.get(key):
            mismatches.append(label)
    if baseline["dataset"].get("content_hash") != current["dataset"].get("content_hash"):
        mismatches.append("dataset content (a case was added, removed or edited)")
    changed_prompts = sorted(
        case_id for case_id, hashes in current["prompt_hashes_by_case"].items()
        if case_id in (baseline.get("prompt_hashes_by_case") or {}) and hashes
        and baseline["prompt_hashes_by_case"][case_id] and baseline["prompt_hashes_by_case"][case_id] != hashes)
    if changed_prompts:
        mismatches.append(f"the assembled prompts for {len(changed_prompts)} case(s) "
                          "(the code that builds them changed)")
    rows = []
    by_id = {c["id"]: c for c in current["per_case"]}
    for base in baseline["per_case"]:
        now = by_id.get(base["id"])
        if now is None:
            rows.append({"id": base["id"], "status": "not in this run", "baseline": base, "current": None})
            continue
        before, after = _rate(base["passed"], base["total"]), _rate(now["passed"], now["total"])
        delta = None if before is None or after is None else after - before
        rows.append({"id": base["id"], "category": base["category"], "baseline": base, "current": now,
                     "delta": delta,
                     "flag": ("possible regression"
                          if delta is not None and round(delta, 6) <= -REGRESSION_DROP else None),
                     "status": "compared"})
    for case_id in sorted(set(by_id) - {b["id"] for b in baseline["per_case"]}):
        rows.append({"id": case_id, "status": "new case (no baseline)", "baseline": None, "current": by_id[case_id]})
    return {"like_for_like": not mismatches, "mismatches": mismatches, "rows": rows,
            "baseline_run": baseline.get("source_run_id"), "current_run": record["run_id"],
            "baseline_aborted": baseline.get("aborted", False), "current_aborted": current["aborted"]}


def render_comparison(result):
    lines = [f"Comparing run {result['current_run']} against baseline from run {result['baseline_run']}"]
    if result["like_for_like"]:
        lines.append("Like-for-like: model, prompt hashes, input hashes and dataset content all match.")
    else:
        lines.append("NOT like-for-like -- these differ, so a delta below may reflect that and not the "
                     "behaviour under test: " + "; ".join(result["mismatches"]) + ".")
    for side, key in (("baseline", "baseline_aborted"), ("this run", "current_aborted")):
        if result[key]:
            lines.append(f"Note: the {side} was aborted part-way, so some cases have fewer runs.")
    lines.append("")
    lines.append(f"{'case':44} {'baseline':>9} {'now':>9}  note")
    for row in result["rows"]:
        base, now = row["baseline"], row["current"]
        fmt = lambda c: "-" if c is None else f"{c['passed']}/{c['total']}"  # noqa: E731
        note = row["status"] if row["status"] != "compared" else (row.get("flag") or "")
        lines.append(f"{row['id']:44} {fmt(base):>9} {fmt(now):>9}  {note}")
    lines += ["", "Observed pass rates on small samples of a non-deterministic model; the flag is "
                  "informational (threshold arbitrary until run-to-run variance is known) and gates nothing."]
    return "\n".join(lines)
