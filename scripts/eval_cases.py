"""Case schema, loader and validator for the live-model evaluation dataset (#151).

A dataset lives in `evals/dataset/<version>/`: a `dataset.json` (version and
description) plus one JSON file per case under `cases/`. Every case is
**synthetic**: invented names that start with "Synthetic ", no real-looking
data (scripts/pii_scan.py must find nothing), and the planted canary tokens
are invented too. Validation is strict because a malformed case would
otherwise pass or fail vacuously:

- a canary-based oracle needs a canary, and the canary must really be planted
  in every surface the case names;
- every case carries scripted `good` and `bad` outputs, and the case is only
  valid if its assertions pass `good` and fail `bad` (see
  eval_oracles.self_check) -- proving the oracles discriminate.

Case shape (see evals/README.md for the prose version)::

    {"id", "category", "mode": "maker"|"checker", "description",
     "deal": {company, proposal, deal_type, pd, lgd, multi_period_financials?,
              financials?, ratios?, financials_source?, collateral?, ...},
     "config_files": {style_guide?, credit_policy?, credit_policy_notes?,
                      deal_learnings?, company_learnings?},   # free-text surfaces
     "source_block": {"label", "text"},                       # optional
     "scripted": {"maker_draft"},                             # checker mode only
     "canary": {"token", "planted_in": [...], "parts"?},      # optional
     "assertions": [{"oracle", "params"?}],
     "human_review": ["question", ...],
     "dry_run": {"good": {...}, "bad": {...}}}

No `anthropic` dependency and no network.
"""
import json
import os
import re

from eval_oracles import CANARY_TOKEN_RE, ORACLES, VERDICTS, canary_planted, self_check
from pii_scan import scan_for_likely_real_data

DATASET_ROOT = os.path.join("evals", "dataset")
CATEGORIES = (
    "fabrication", "contradiction", "unsupported-claim", "analyst-supplied",
    "conflicting-sources", "injection", "checker-judgement",
)
MODES = ("maker", "checker")
CONFIG_FILE_KEYS = ("style_guide", "credit_policy", "credit_policy_notes",
                    "deal_learnings", "company_learnings")
CANARY_SURFACES = ("collateral_description", "company_learning", "credit_policy_note",
                   "style_guide", "source_block", "maker_draft")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SYNTHETIC_NAME_RE = re.compile(r"^Synthetic [A-Za-z0-9 ]+$")
CANARY_ORACLES = {"canary_absent"}


class DatasetError(ValueError):
    """The dataset on disk is missing, unreadable or invalid."""


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"{path}: {exc}") from exc


def load_dataset(version="v1", root=None):
    """Return {"version", "description", "cases": [...]}, cases sorted by id.
    Raises DatasetError if the files are unreadable; call validate_dataset()
    for the content checks."""
    base = os.path.join(root or DATASET_ROOT, version)
    meta = _read_json(os.path.join(base, "dataset.json"))
    cases_dir = os.path.join(base, "cases")
    if not os.path.isdir(cases_dir):
        raise DatasetError(f"{cases_dir}: no cases directory")
    cases = []
    for name in sorted(os.listdir(cases_dir)):
        if name.endswith(".json"):
            case = _read_json(os.path.join(cases_dir, name))
            if isinstance(case, dict):
                case["_file"] = name
            cases.append(case)
    return {"version": meta.get("version", version), "description": meta.get("description", ""),
            "cases": sorted(cases, key=lambda c: str(c.get("id", "")) if isinstance(c, dict) else "")}


def _string_leaves(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _string_leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from _string_leaves(v)


def _surface_texts(case):
    """The text of each surface a canary may be planted in."""
    config = case.get("config_files") or {}
    deal = case.get("deal") or {}
    return {
        "collateral_description": "\n".join(
            str(a.get("description", "")) for a in deal.get("collateral", []) if isinstance(a, dict)),
        "company_learning": config.get("company_learnings", ""),
        "credit_policy_note": config.get("credit_policy_notes", ""),
        "style_guide": config.get("style_guide", ""),
        "source_block": (case.get("source_block") or {}).get("text", ""),
        "maker_draft": (case.get("scripted") or {}).get("maker_draft", ""),
    }


def validate_case(case):
    """Return a list of human-readable problems with one case (empty = valid)."""
    if not isinstance(case, dict):
        return ["case is not a JSON object"]
    problems = []
    case_id = case.get("id")
    label = f"case {case_id!r}"

    def need(condition, message):
        if not condition:
            problems.append(f"{label}: {message}")

    need(isinstance(case_id, str) and ID_RE.match(case_id or ""), "id must match [a-z0-9][a-z0-9-]*")
    if isinstance(case_id, str) and case.get("_file"):
        need(case["_file"] == f"{case_id}.json", f"file name {case['_file']!r} must be {case_id}.json")
    need(case.get("category") in CATEGORIES, f"category must be one of {CATEGORIES}")
    need(case.get("mode") in MODES, f"mode must be one of {MODES}")
    need(isinstance(case.get("description"), str) and case["description"].strip(), "description required")

    deal = case.get("deal")
    if not isinstance(deal, dict):
        problems.append(f"{label}: deal must be an object")
        deal = {}
    for key in ("company", "proposal"):
        need(isinstance(deal.get(key), str) and SYNTHETIC_NAME_RE.match(deal.get(key) or ""),
             f"deal.{key} must be an invented name starting with 'Synthetic '")
    for key in ("deal_type", "pd", "lgd"):
        need(isinstance(deal.get(key), str) and deal[key].strip(), f"deal.{key} required")
    if deal.get("financials_source") == "analyst-supplied":
        need(not deal.get("multi_period_financials"),
             "an analyst-supplied deal supplies financials/ratios, not multi_period_financials")

    config = case.get("config_files") or {}
    need(isinstance(config, dict) and set(config) <= set(CONFIG_FILE_KEYS),
         f"config_files keys must be within {CONFIG_FILE_KEYS}")

    mode = case.get("mode")
    scripted = case.get("scripted") or {}
    if mode == "checker":
        need(isinstance(scripted.get("maker_draft"), str) and scripted["maker_draft"].strip(),
             "a checker case needs scripted.maker_draft (a Maker draft scripted to carry the flaw "
             "under test, that deterministic policy_checks cannot catch)")
    elif mode == "maker":
        need("maker_draft" not in scripted, "a maker case must not script the Maker's draft")

    assertions = case.get("assertions")
    if not (isinstance(assertions, list) and assertions):
        problems.append(f"{label}: assertions must be a non-empty list")
        assertions = []
    for assertion in assertions:
        name = assertion.get("oracle") if isinstance(assertion, dict) else None
        if name not in ORACLES:
            problems.append(f"{label}: unknown oracle {name!r} (known: {sorted(ORACLES)})")
            continue
        if name in CANARY_ORACLES:
            need(case.get("canary") is not None, f"oracle {name!r} needs a canary")
        if name == "verdict_is":
            need((assertion.get("params") or {}).get("expected") in VERDICTS,
                 f"verdict_is needs params.expected in {VERDICTS}")
            need(mode == "checker", "verdict_is only makes sense for a checker case")
        if name == "analyst_supplied_disclosed":
            need(deal.get("financials_source") == "analyst-supplied",
                 "analyst_supplied_disclosed needs deal.financials_source == 'analyst-supplied'")
    if mode == "checker":
        need(any(isinstance(a, dict) and a.get("oracle") == "verdict_is" for a in assertions),
             "a checker case needs a verdict_is assertion")

    canary = case.get("canary")
    if canary is not None:
        token = canary.get("token") if isinstance(canary, dict) else None
        need(isinstance(token, str) and CANARY_TOKEN_RE.match(token or ""),
             "canary.token must look like CANARY-XXXXXXXX (8 upper-case letters/digits)")
        surfaces = canary.get("planted_in") if isinstance(canary, dict) else None
        need(isinstance(surfaces, list) and surfaces and set(surfaces) <= set(CANARY_SURFACES),
             f"canary.planted_in must be a non-empty list within {CANARY_SURFACES}")
        if isinstance(token, str) and isinstance(surfaces, list):
            texts = _surface_texts(case)
            for surface in surfaces:
                if surface in texts:
                    need(canary_planted(canary, texts[surface]),
                         f"canary is not actually planted in {surface} (token or every part must appear)")

    human = case.get("human_review", [])
    need(isinstance(human, list) and all(isinstance(q, str) and q.strip() for q in human),
         "human_review must be a list of questions")

    dry = case.get("dry_run")
    need(isinstance(dry, dict) and isinstance(dry.get("good"), dict) and isinstance(dry.get("bad"), dict),
         "dry_run needs scripted 'good' and 'bad' outputs")

    # Synthetic-data check: nothing mechanically real-looking anywhere in the case.
    for text in _string_leaves({k: v for k, v in case.items() if k != "_file"}):
        for finding in scan_for_likely_real_data(text):
            problems.append(f"{label}: looks like real data ({finding['reason']}: {finding['snippet']!r})")

    if not problems:
        try:
            result = self_check(case)
        except Exception as exc:  # noqa: BLE001 - a broken scripted spec is a validation problem
            problems.append(f"{label}: scripted dry-run outputs could not be evaluated ({exc!r})")
        else:
            need(result["good_all_pass"], "scripted 'good' output fails an assertion: "
                 + "; ".join(f"{r['oracle']}: {r['reason']}" for r in result["good"] if not r["passed"]))
            need(result["bad_fails_at_least_one"],
                 "scripted 'bad' output passes every assertion, so the oracles don't discriminate")
    return problems


def validate_dataset(dataset):
    """All problems across the dataset, including duplicate ids."""
    problems = []
    seen = set()
    cases = dataset["cases"]
    if not cases:
        problems.append("dataset has no cases")
    for case in cases:
        problems.extend(validate_case(case))
        case_id = case.get("id") if isinstance(case, dict) else None
        if case_id in seen:
            problems.append(f"duplicate case id {case_id!r}")
        seen.add(case_id)
    return problems
