"""Result records and the human-readable review pack for the evaluation
harness (#151).

Three kinds of result are kept visibly separate, in the data and in the pack:

1. **Deterministic oracle results** -- per-run pass/fail from code, no trust in the
   model required (scripts/eval_oracles.py). Scripted dry-run rows are labelled as
   such, with the outcome they are *expected* to have, so a caught `bad` output
   does not read as a failure.
2. **Observed pass rates** -- for live runs, k passes out of N repeats per case
   and per category. This is what was *observed for this model / prompt /
   dataset configuration*; it is never described as "proven" or "safe". A live
   run that errored stays in the denominator as a non-pass.
3. **Human-review observations** -- the qualitative questions each case poses,
   plus any *unscored* oracle observation (e.g. a canary echoed by a Checker),
   shown beside the output for a person to read. No pass/fail.

Output is only ever written under a git-ignored results directory
(`evals/results/` by default), refused otherwise, so the harness cannot modify
a tracked file. Imports `orchestrator` (lazily, for its prompt-hash helper) and
nothing that makes a network call.
"""
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone

from eval_oracles import run_passed

HARNESS_VERSION = "0.1-pr1"
RESULTS_ROOT = os.path.join("evals", "results")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCERPT_LIMIT = 24000  # chars: a Maker's max_tokens=4000 is ~16k characters

DISCLAIMER = (
    "Pass rates below are the *observed* rate for this model, these prompt hashes and this "
    "dataset version -- not a proof of safety. Model output is not deterministic, the "
    "dataset is small and synthetic, and a model or prompt update can change the numbers. "
    "Most oracles check the *form* of the output (a well-formed, self-consistent structured "
    "block, a verdict) -- they do not judge the narrative; that is what the human-review "
    "section is for."
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


def dataset_hash(dataset):
    """sha256 of the dataset's canonical content, so editing a case *inside* a
    version changes what the record identifies, not only the version label."""
    cases = [{k: v for k, v in c.items() if k != "_file"} for c in dataset["cases"]]
    blob = json.dumps({"version": dataset["version"], "cases": cases}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def new_run_id(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")


def clip(text, limit=EXCERPT_LIMIT):
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n[... truncated, {len(text) - limit} more characters]"


def scripted_run(label, results, output_excerpt="", expected_pass=None):
    """A run record for a scripted (non-model) output. `expected_pass` is the
    outcome this scripted output is meant to have (True for `good`, False for a
    `bad` one), shown in the pack so a caught `bad` is not read as a failure."""
    return {"label": label, "kind": "scripted", "assertions": results, "passed": run_passed(results),
            "expected_pass": expected_pass, "status": "ok", "output_excerpt": clip(output_excerpt)}


def live_run(label, results, output_excerpt="", status="ok", error=None, extra=None, output_text=None):
    """A run record for a live (model) output. A run that errored (status != "ok")
    is never a pass, and still counts in the pass-rate denominator. `extra` carries
    per-run provenance (prompt hash, model) without a model's text. `output_text` is the
    model's WHOLE output, kept in results.json / runs.jsonl (git-ignored) so a reviewer can read
    the end of a long draft; the review pack shows the clipped excerpt. It never reaches a baseline."""
    passed = status == "ok" and run_passed(results)
    record = {"label": label, "kind": "live", "assertions": results, "passed": passed, "status": status,
              "error": error, "output_excerpt": clip(output_excerpt)}
    if output_text is not None:
        record["output_text"] = output_text
    record.update(extra or {})
    return record


def pass_rate(runs):
    """{"passed": k, "total": n} over live runs (errored runs count in `total`)."""
    live = [r for r in runs if r.get("kind") == "live"]
    return {"passed": sum(1 for r in live if r["passed"] and r.get("status", "ok") == "ok"),
            "total": len(live)}


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
                 usage=None, call_cap=None, input_hashes=None, repeats=None, aborted=False, abort_reason=None):
    return {
        "harness_version": HARNESS_VERSION,
        "run_id": run_id,
        "mode": mode,  # "dry-run" (no model) or "live"
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset": {"version": dataset["version"], "case_count": len(dataset["cases"]),
                    "content_hash": dataset_hash(dataset)},
        "models": models or {"maker_model": None, "checker_model": None},
        "prompt_hashes": hashes or {},
        "input_hashes": input_hashes or {},
        "repeats": repeats,
        "aborted": aborted,
        "abort_reason": abort_reason,
        "planned_live_calls": planned_live_calls,
        "call_cap": call_cap,
        "usage": usage or {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "disclaimer": DISCLAIMER,
        "cases": cases,
        "summary": summarize(cases),
    }


def assert_results_dir_is_ignored(path, repo_root=None, probe_names=("results.json",)):
    """Refuse `path` unless every file this run will write there is git-ignored
    (or the path is entirely outside the repository). Paths are resolved with
    `realpath`, so a symlink or junction pointing into a tracked directory is
    judged by where it really leads."""
    root = os.path.realpath(repo_root or REPO_ROOT)
    absolute = os.path.realpath(path)
    try:
        inside_repo = os.path.commonpath([root, absolute]) == root
    except ValueError:  # e.g. a different drive on Windows: certainly outside the repo
        inside_repo = False
    if not inside_repo:
        return
    # Probe the actual files, not the bare directory: git matches a directory-only rule such
    # as `evals/results/` against a path under it even before the directory exists, but not
    # against the bare (not-yet-created) directory path itself.
    for name in probe_names:
        try:
            result = subprocess.run(["git", "check-ignore", "-q", os.path.join(absolute, name)],
                                    cwd=root, capture_output=True)
        except OSError as exc:
            raise ResultsPathError(f"cannot verify {path} is git-ignored (git unavailable: {exc})") from exc
        if result.returncode not in (0, 1):  # git failed (not a repository, bad path, ...): fail closed
            raise ResultsPathError(f"cannot verify {path} is git-ignored (git check-ignore exited "
                                   f"{result.returncode}: {result.stderr.decode('utf-8', 'replace').strip()[:200]})")
        if result.returncode == 1:
            raise ResultsPathError(
                f"refusing to write results to {path}: {name} would land inside the repository at a "
                "path that is not git-ignored. Results may contain model text and must never be tracked."
            )


def _cell(text, limit=300):
    """Model-derived (or error) text made safe for a Markdown table cell or bullet: collapsed to
    one line, bounded, and shown as an inline code span with pipes escaped, so a pipe, newline,
    heading marker, link, backtick or HTML in model output cannot forge table rows, headings or
    markup in the review pack."""
    flat = " ".join(str(text if text is not None else "").split())
    if len(flat) > limit:
        flat = flat[:limit] + "..."
    flat = flat.replace("|", "\\|")
    fence = "`" * (max((len(run) for run in re.findall(r"`+", flat)), default=0) + 1)
    pad = " " if flat.startswith("`") or flat.endswith("`") else ""
    return f"{fence}{pad}{flat}{pad}{fence}"


def _fence_for(text):
    """A code fence longer than any backtick run in `text`, so a model draft that
    itself contains a ```json block cannot close it early."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _row_result(run):
    if run["kind"] == "scripted" and run.get("expected_pass") is not None:
        if run["passed"] == run["expected_pass"]:
            return "pass" if run["passed"] else "caught (expected)"
        return "UNEXPECTED " + ("pass" if run["passed"] else "FAIL")
    if run.get("status", "ok") != "ok":
        return f"ERROR {_cell(run.get('error') or run['status'])}"
    return "pass" if run["passed"] else "FAIL"


def render_review_pack(record):
    lines = [
        f"# Evaluation review pack -- run {record['run_id']}",
        "",
        f"- Mode: **{record['mode']}**" + (" (scripted outputs only; no model was called)"
                                         if record["mode"] == "dry-run" else ""),
        f"- Dataset: version `{record['dataset']['version']}` (content hash `{record['dataset'].get('content_hash')}`), "
        f"{record['dataset']['case_count']} cases",
        f"- Models: maker `{record['models'].get('maker_model')}`, checker `{record['models'].get('checker_model')}`",
        f"- Prompt hashes: {json.dumps(record['prompt_hashes'])}",
        f"- Harness version: {record['harness_version']}",
        f"- Model calls: {record['usage']['calls']} made (planned for a live run: {record['planned_live_calls']}, "
        f"cap: {record['call_cap']})",
        "",
        f"> {record['disclaimer']}",
        "",
        "## 1. Deterministic oracle results",
        "",
        "Pass/fail decided by code on each run; no trust in the model needed. Scripted rows show the "
        "outcome they are meant to have: a `bad` output that is *caught* is the oracle working.",
        "",
        "| Case | Run | Result | Failing scored oracles |",
        "|---|---|---|---|",
    ]
    for case in record["cases"]:
        for run in case["runs"]:
            failing = "; ".join(f"{a['oracle']}: {a['reason']}" for a in run["assertions"]
                                if a.get("scored", True) and not a["passed"])
            lines.append(f"| `{case['id']}` | {run['label']} ({run['kind']}) | {_row_result(run)} | "
                         f"{_cell(failing) if failing else '-'} |")
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
        lines += ["", f"Scripted self-checks (the scored oracles pass the `good` output and catch the `bad` one): "
                      f"{checks['ok']} / {checks['cases']} cases."]
    lines += ["", "## 3. Human-review observations", "",
              "Qualitative questions, and any *unscored* oracle observations, for a person to read. "
              "**No pass/fail.**", ""]
    for case in record["cases"]:
        questions = case.get("human_review") or []
        observations = [(run, a) for run in case["runs"] for a in run["assertions"] if not a.get("scored", True)]
        if not questions and not observations:
            continue
        lines.append(f"### `{case['id']}` ({case['category']}, {case['mode']} case)")
        lines.append(case["description"])
        lines += [f"- [ ] {q}" for q in questions]
        for run, assertion in observations:
            lines.append(f"- _observation, {run['label']} ({run['kind']}), unscored_ `{assertion['oracle']}`: "
                         f"{_cell(assertion['reason'], limit=600)}")
        for run in case["runs"]:
            excerpt = (run.get("output_excerpt") or "").strip()
            if excerpt:
                fence = _fence_for(excerpt)
                lines += ["", f"_{run['label']} output ({run['kind']}), excerpt:_", "", f"{fence}text", excerpt, fence]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def prepare_run_dir(run_id, out_root=None, repo_root=None):
    """Create (and guard) `<out_root>/<run_id>/` before a live run starts, so work
    directories and incremental output have somewhere safe to go. Refuses a path that
    is not git-ignored or a run id that already exists."""
    root = out_root or os.path.join(repo_root or REPO_ROOT, RESULTS_ROOT)
    out_dir = os.path.join(root, run_id)
    assert_results_dir_is_ignored(out_dir, repo_root, probe_names=("results.json", "review_pack.md", "runs.jsonl",
                                                                  "work/probe"))
    try:
        os.makedirs(out_dir, exist_ok=False)
    except FileExistsError as exc:
        raise ResultsPathError(f"{out_dir} already exists; refusing to reuse an earlier run") from exc
    return out_dir


def append_run_line(run_dir, case_id, run):
    """Append one finished run to `runs.jsonl` and flush, so a crash part-way through a
    paid-for evaluation loses nothing already completed."""
    with open(os.path.join(run_dir, "runs.jsonl"), "a", encoding="utf-8", errors="backslashreplace") as f:
        f.write(json.dumps({"case_id": case_id, **run}, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def write_results(record, out_root=None, repo_root=None):
    """Write results.json and review_pack.md under `<out_root>/<run_id>/` and
    return that directory. Refuses a path that is not git-ignored, and never
    overwrites an existing run directory."""
    root = out_root or os.path.join(repo_root or REPO_ROOT, RESULTS_ROOT)
    out_dir = os.path.join(root, record["run_id"])
    assert_results_dir_is_ignored(out_dir, repo_root, probe_names=("results.json", "review_pack.md"))
    os.makedirs(out_dir, exist_ok=True)  # a live run creates its directory up front, for incremental output
    if os.path.exists(os.path.join(out_dir, "results.json")):
        raise ResultsPathError(f"{out_dir} already holds results.json; refusing to overwrite an earlier run")
    # Render BOTH files before writing either, then write each atomically (temp file + replace): a
    # failure while rendering can no longer leave a truncated results.json after the live calls are
    # spent. errors="backslashreplace": a lone surrogate in model text must not abort the write.
    documents = (("results.json", json.dumps(record, indent=2, ensure_ascii=False)),
                 ("review_pack.md", render_review_pack(record)))
    for name, text in documents:
        target = os.path.join(out_dir, name)
        with open(target + ".tmp", "w", encoding="utf-8", errors="backslashreplace") as f:
            f.write(text)
        os.replace(target + ".tmp", target)
    return out_dir
