"""Result records and the human-readable review pack for the evaluation
harness (#151).

Three kinds of result are kept visibly separate, in the data and in the pack:

1. **Deterministic oracle results** -- per-run pass/fail from code, no trust in
   the model required (scripts/eval_oracles.py).
2. **Observed pass rates** -- for live runs, k passes out of N repeats per case
   and per category. This is what was *observed for this model / prompt /
   dataset configuration*; it is never described as "proven" or "safe".
3. **Human-review observations** -- the qualitative questions each case poses,
   shown beside the output for a person to read. No pass/fail.

Output is only ever written under a git-ignored results directory
(`evals/results/` by default), refused otherwise, so the harness cannot modify
a tracked file. Imports `orchestrator` (lazily, for its prompt-hash helper) and
nothing that makes a network call.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

HARNESS_VERSION = "0.1-pr1"
RESULTS_ROOT = os.path.join("evals", "results")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DISCLAIMER = (
    "Pass rates below are the *observed* rate for this model, these prompt hashes and this "
    "dataset version -- not a proof of safety. Model output is not deterministic, the "
    "dataset is small and synthetic, and a model or prompt update can change the numbers."
)


class ResultsPathError(RuntimeError):
    """Refusing to write results somewhere that isn't a git-ignored results dir."""


def prompt_hashes(repo_root=None):
    """Short content hashes of the shipped agent prompts, computed exactly as
    orchestrator.py's model_provenance does (its `_content_hash`)."""
    from orchestrator import _content_hash  # lazy: keeps this module free of anthropic until needed
    from textio import read_text
    root = repo_root or REPO_ROOT
    return {
        "underwriter_prompt_hash": _content_hash(read_text(os.path.join(root, "agents", "underwriter_agent.md"))),
        "risk_reviewer_prompt_hash": _content_hash(read_text(os.path.join(root, "agents", "risk_reviewer_agent.md"))),
    }


def new_run_id(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def pass_rate(runs):
    """{"passed": k, "total": n} over live runs whose every assertion passed."""
    live = [r for r in runs if r.get("kind") == "live"]
    return {"passed": sum(1 for r in live if r["passed"]), "total": len(live)}


def summarize(cases):
    """Per-category observed pass rates over live runs, plus the scripted
    self-check status for each case."""
    categories = {}
    for case in cases:
        rate = pass_rate(case["runs"])
        bucket = categories.setdefault(case["category"], {"passed": 0, "total": 0})
        bucket["passed"] += rate["passed"]
        bucket["total"] += rate["total"]
    self_checks = [c for c in cases if "self_check" in c]
    return {
        "observed_pass_rate_by_category": categories,
        "scripted_self_checks": {
            "cases": len(self_checks),
            "ok": sum(1 for c in self_checks if c["self_check"]["ok"]),
        },
    }


def build_record(mode, dataset, cases, run_id, models=None, hashes=None, planned_live_calls=0,
                 usage=None, call_cap=None):
    return {
        "harness_version": HARNESS_VERSION,
        "run_id": run_id,
        "mode": mode,  # "dry-run" (no model) or "live"
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset": {"version": dataset["version"], "case_count": len(dataset["cases"])},
        "models": models or {"maker_model": None, "checker_model": None},
        "prompt_hashes": hashes or {},
        "planned_live_calls": planned_live_calls,
        "call_cap": call_cap,
        "usage": usage or {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "disclaimer": DISCLAIMER,
        "cases": cases,
        "summary": summarize(cases),
    }


def assert_results_dir_is_ignored(path, repo_root=None):
    """Refuse `path` unless it is git-ignored (or entirely outside the repo)."""
    root = os.path.abspath(repo_root or REPO_ROOT)
    absolute = os.path.abspath(path)
    inside_repo = os.path.commonpath([root, absolute]) == root
    if not inside_repo:
        return
    # Probe a file *inside* the directory: git matches a directory-only rule such
    # as `evals/results/` against a path under it even before the directory
    # exists, but not against the bare (not-yet-created) directory path itself.
    probe = os.path.join(absolute, "results.json")
    try:
        result = subprocess.run(["git", "check-ignore", "-q", probe], cwd=root, capture_output=True)
    except OSError as exc:
        raise ResultsPathError(f"cannot verify {path} is git-ignored (git unavailable: {exc})") from exc
    if result.returncode != 0:
        raise ResultsPathError(
            f"refusing to write results to {path}: it is inside the repository but not git-ignored. "
            "Results may contain model text and must never land in a tracked path."
        )


def render_review_pack(record):
    lines = [
        f"# Evaluation review pack -- run {record['run_id']}",
        "",
        f"- Mode: **{record['mode']}**" + (" (scripted outputs only; no model was called)"
                                         if record["mode"] == "dry-run" else ""),
        f"- Dataset: version `{record['dataset']['version']}`, {record['dataset']['case_count']} cases",
        f"- Models: maker `{record['models'].get('maker_model')}`, checker `{record['models'].get('checker_model')}`",
        f"- Prompt hashes: {json.dumps(record['prompt_hashes'])}",
        f"- Harness version: {record['harness_version']}",
        f"- Model calls: {record['usage']['calls']} made (planned for a live run: {record['planned_live_calls']}, "
        f"cap: {record['call_cap']})",
        "",
        f"> {DISCLAIMER}",
        "",
        "## 1. Deterministic oracle results",
        "",
        "Pass/fail decided by code on each run; no trust in the model needed.",
        "",
        "| Case | Run | Result | Failing oracles |",
        "|---|---|---|---|",
    ]
    for case in record["cases"]:
        for run in case["runs"]:
            failing = "; ".join(f"{a['oracle']}: {a['reason']}" for a in run["assertions"] if not a["passed"])
            verdict = "pass" if run["passed"] else "FAIL"
            lines.append(f"| `{case['id']}` | {run['label']} ({run['kind']}) | {verdict} | {failing or '-'} |")
    lines += ["", "## 2. Observed pass rates (live runs)", ""]
    rates = record["summary"]["observed_pass_rate_by_category"]
    if not any(r["total"] for r in rates.values()):
        lines.append("_No live runs in this record, so there are no pass rates to report._")
    else:
        lines += ["| Category | Passed / runs |", "|---|---|"]
        lines += [f"| {cat} | {r['passed']} / {r['total']} |" for cat, r in sorted(rates.items()) if r["total"]]
        lines += ["", "Per case:", ""]
        for case in record["cases"]:
            rate = pass_rate(case["runs"])
            if rate["total"]:
                lines.append(f"- `{case['id']}`: {rate['passed']} / {rate['total']}")
    if record["mode"] == "dry-run":
        checks = record["summary"]["scripted_self_checks"]
        lines += ["", f"Scripted self-checks (oracles pass the `good` output and catch the `bad` one): "
                      f"{checks['ok']} / {checks['cases']} cases."]
    lines += ["", "## 3. Human-review observations", "",
              "Qualitative questions for a person to answer from the output. **No pass/fail.**", ""]
    for case in record["cases"]:
        questions = case.get("human_review") or []
        if not questions:
            continue
        lines.append(f"### `{case['id']}` ({case['category']}, {case['mode']} case)")
        lines.append(case["description"])
        lines += [f"- [ ] {q}" for q in questions]
        for run in case["runs"]:
            excerpt = (run.get("output_excerpt") or "").strip()
            if excerpt:
                lines += ["", f"_{run['label']} output ({run['kind']}), excerpt:_", "", "```text", excerpt, "```"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_results(record, out_root=None, repo_root=None):
    """Write results.json and review_pack.md under `<out_root>/<run_id>/` and
    return that directory. Refuses a path that is not git-ignored."""
    root = out_root or os.path.join(repo_root or REPO_ROOT, RESULTS_ROOT)
    assert_results_dir_is_ignored(root, repo_root)
    out_dir = os.path.join(root, record["run_id"])
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "review_pack.md"), "w", encoding="utf-8") as f:
        f.write(render_review_pack(record))
    return out_dir


def scripted_run(label, results, output_excerpt=""):
    """A run record for a scripted (non-model) output."""
    return {"label": label, "kind": "scripted", "assertions": results,
            "passed": all(r["passed"] for r in results), "output_excerpt": output_excerpt}

