"""Case schema, loader and validator for the live-model evaluation dataset (#151).

A dataset lives in `evals/dataset/<version>/`: a `dataset.json` (version and
description) plus one JSON file per case under `cases/`. Every case is
**synthetic**: invented names that start with "Synthetic ", no real-looking
data (scripts/pii_scan.py must find nothing), and the planted canary tokens
are invented too. Validation is strict because a malformed case would
otherwise pass or fail vacuously:

- a canary-based oracle needs a canary, and the canary must really be planted
  in every surface the case names -- and each surface must be a route that can
  actually reach the prompt of the agent the case exercises;
- every case carries scripted `good` and `bad` outputs, and the case is only
  valid if its scored assertions pass `good` and fail `bad` (see
  eval_oracles.self_check) -- shown, on one author-written bad output per case,
  to discriminate. `bad.expected_failures` names the oracles it is aimed at;
- a Checker case's scripted Maker draft must already be code-compliant, or a
  REJECTED verdict would prove nothing about the model;
- every case has its own (company, proposal), because a run resumes any earlier
  state for the same pair, which would leak one case into the next.

Case shape (see evals/README.md for the prose version)::

    {"id", "category", "mode": "maker"|"checker", "description",
     "deal": {company, proposal, deal_type, pd, lgd, multi_period_financials?,
              financials?, ratios?, financials_source?, downside_case?,
              stress_assumptions?, collateral?, covenants?, security_package?,
              guarantees?},
     "config_files": {style_guide?, credit_policy?, credit_policy_notes?,
                      deal_learnings?, company_learnings?},   # free-text surfaces
     "source_block": {"label", "text"},                       # maker cases only
     "scripted": {"maker_draft"},                             # checker cases only
     "canary": {"token", "planted_in": [...], "parts"?},      # optional
     "assertions": [{"oracle", "params"?, "scored"?}],
     "human_review": ["question", ...],
     "dry_run": {"good": {...}, "bad": {..., "expected_failures"?}}}

No `anthropic` dependency and no network.
"""
import json
import math
import os
import re

from eval_oracles import (
    CANARY_TOKEN_RE,
    ORACLES,
    VERDICTS,
    RunOutput,
    build_context,
    canary_planted,
    self_check,
)
from pii_scan import scan_for_likely_real_data
from spreading_builder import FIELD_LABELS, FORWARD_PERIOD_KEYS, HISTORICAL_PERIOD_KEYS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_ROOT = os.path.join(REPO_ROOT, "evals", "dataset")  # absolute: independent of the cwd
CATEGORIES = (
    "fabrication", "contradiction", "unsupported-claim", "analyst-supplied",
    "conflicting-sources", "injection", "checker-judgement",
)
MODES = ("maker", "checker")
CONFIG_FILE_KEYS = ("style_guide", "credit_policy", "credit_policy_notes",
                    "deal_learnings", "company_learnings")
CANARY_SURFACES = ("collateral_description", "company_learning", "credit_policy_note",
                   "style_guide", "source_block", "maker_draft")
# Which surfaces really reach the prompt of the agent each mode exercises (from
# orchestrator.py): collateral, learnings and policy notes are in the grounding
# context both agents see; the style guide is Maker-only; the Checker sees the
# Maker's draft; a source block is a Maker-case, prompt-level approximation only.
SURFACES_BY_MODE = {
    "maker": {"collateral_description", "company_learning", "credit_policy_note",
              "style_guide", "source_block"},
    "checker": {"collateral_description", "company_learning", "credit_policy_note", "maker_draft"},
}
CASE_KEYS = {"id", "category", "mode", "description", "deal", "config_files", "source_block",
             "scripted", "canary", "assertions", "human_review", "dry_run", "_file"}
DEAL_KEYS = {"company", "proposal", "deal_type", "pd", "lgd", "multi_period_financials", "financials",
             "ratios", "financials_source", "downside_case", "stress_assumptions", "collateral",
             "covenants", "security_package", "guarantees"}
CANARY_KEYS = {"token", "planted_in", "parts"}
DRY_RUN_KEYS = {"good", "bad"}
SPEC_KEYS = {"draft_body", "structured", "omit_structured_block", "verdict", "notes", "raw_text",
             "verdict_parsed", "expected_failures"}
ASSERTION_KEYS = {"oracle", "params", "scored"}
SOURCE_BLOCK_KEYS = {"label", "text"}
RAW_PERIODS = set(HISTORICAL_PERIOD_KEYS) | set(FORWARD_PERIOD_KEYS)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SYNTHETIC_NAME_RE = re.compile(r"\ASynthetic [A-Za-z0-9 ]+\Z")  # \Z, not $: "$" accepts a trailing newline
CANARY_ORACLES = {"canary_absent"}


class DatasetError(ValueError):
    """The dataset on disk is missing, unreadable or invalid."""


def _no_constants(name):
    raise ValueError(f"{name} is not valid JSON data here")


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise ValueError(f"duplicate JSON keys {duplicates} (the last would silently win)")
    return dict(pairs)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f, parse_constant=_no_constants, object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError, RecursionError) as exc:  # JSONDecodeError is a ValueError
        raise DatasetError(f"{path}: {type(exc).__name__}: {str(exc)[:200]}") from exc


def load_dataset(version="v1", root=None):
    """Return {"version", "description", "cases": [...]}, cases sorted by id.
    Raises DatasetError if the files are unreadable; call validate_dataset()
    for the content checks."""
    if not VERSION_RE.match(version or "") or ".." in version:
        raise DatasetError(f"invalid dataset version {version!r} (letters, digits, '.', '_', '-' only)")
    base = os.path.join(root or DATASET_ROOT, version)
    meta = _read_json(os.path.join(base, "dataset.json"))
    if not isinstance(meta, dict):
        raise DatasetError(f"{os.path.join(base, 'dataset.json')}: must be a JSON object")
    if meta.get("version", version) != version:
        raise DatasetError(f"dataset.json says version {meta.get('version')!r} but lives in {version!r}")
    cases_dir = os.path.join(base, "cases")
    if not os.path.isdir(cases_dir):
        raise DatasetError(f"{cases_dir}: no cases directory")
    cases, stray = [], []
    for name in sorted(os.listdir(cases_dir)):
        if name.endswith(".json"):
            case = _read_json(os.path.join(cases_dir, name))
            if isinstance(case, dict):
                case["_file"] = name
            cases.append(case)
        else:
            stray.append(name)  # e.g. "Case.JSON": silently ignored files are a trap
    return {"version": version, "description": meta.get("description", ""), "stray_files": stray,
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


def _validate_shapes(case, need):
    """Type checks first, so later checks can rely on them. Returns True if the
    case is well-formed enough to go on."""
    ok = True
    for key, kind in (("deal", dict), ("config_files", dict), ("scripted", dict), ("source_block", dict),
                      ("canary", dict), ("dry_run", dict), ("assertions", list), ("human_review", list)):
        if key in case and not isinstance(case[key], kind):
            need(False, f"{key} must be a JSON {kind.__name__}")
            ok = False
    unknown = set(case) - CASE_KEYS
    need(not unknown, f"unknown top-level keys {sorted(unknown)} (a typo would be silently ignored)")
    if isinstance(case.get("deal"), dict):
        unknown_deal = set(case["deal"]) - DEAL_KEYS
        need(not unknown_deal, f"unknown deal keys {sorted(unknown_deal)} (a typo would be silently ignored)")
    for key, allowed in (("canary", CANARY_KEYS), ("dry_run", DRY_RUN_KEYS), ("source_block", SOURCE_BLOCK_KEYS)):
        if isinstance(case.get(key), dict):
            unknown = set(case[key]) - allowed
            need(not unknown, f"unknown {key} keys {sorted(unknown)} (a typo would be silently ignored)")
    if isinstance(case.get("dry_run"), dict):
        for label in ("good", "bad"):
            spec = case["dry_run"].get(label)
            if isinstance(spec, dict):
                unknown = set(spec) - SPEC_KEYS
                need(not unknown, f"unknown dry_run.{label} keys {sorted(unknown)} (a typo would be silently ignored)")
    for assertion in case.get("assertions", []) if isinstance(case.get("assertions"), list) else []:
        if not isinstance(assertion, dict):
            need(False, "each assertion must be an object")
            ok = False
            continue
        if "params" in assertion and not isinstance(assertion["params"], dict):
            need(False, "assertion params must be an object")
            ok = False
        if "scored" in assertion and not isinstance(assertion["scored"], bool):
            need(False, "assertion scored must be true or false")
            ok = False
        unknown = set(assertion) - ASSERTION_KEYS
        if unknown:
            need(False, f"unknown assertion keys {sorted(unknown)} (a typo such as 'score' would silently mean scored)")
            ok = False
        if not isinstance(assertion.get("oracle"), str):
            need(False, "each assertion needs an oracle name (a string)")
            ok = False
    return ok


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_nonneg_number(value):
    return _is_number(value) and value >= 0


def _known_deal_types():
    folder = os.path.join(REPO_ROOT, "templates", "cam")
    try:
        return sorted(name[:-len("_cam.md")] for name in os.listdir(folder) if name.endswith("_cam.md"))
    except OSError:
        return []


def _validate_inputs(deal, need):
    """The deal's financial inputs must be in the shapes the framework actually reads. In
    particular a raw figure under a name `evaluate_financial_model` does not know is silently
    read as 0, which would give the model a ground truth that contradicts the case's own text."""
    periods = deal.get("multi_period_financials")
    if periods is not None:
        if not (isinstance(periods, dict) and periods):
            need(False, "multi_period_financials must be a non-empty object of period -> raw figures")
        else:
            for period, raw in periods.items():
                need(period in RAW_PERIODS, f"unknown period {period!r} in multi_period_financials "
                                            f"(known: {sorted(RAW_PERIODS)})")
                if not isinstance(raw, dict) or not raw:
                    need(False, f"period {period!r} must be a non-empty object of raw figures")
                    continue
                unknown = sorted(set(raw) - set(FIELD_LABELS))
                need(not unknown, f"period {period!r} has raw fields the framework ignores (read as 0): "
                                  f"{unknown}; the known raw fields are {sorted(FIELD_LABELS)}")
                bad = sorted(k for k, v in raw.items() if not _is_number(v))
                need(not bad, f"period {period!r} has non-numeric values for {bad}")
    for key in ("financials", "ratios"):
        value = deal.get(key)
        need(value is None or (isinstance(value, dict) and all(
            isinstance(inner, dict) and all(_is_number(v) for v in inner.values()) for inner in value.values())),
            f"deal.{key} must be an object of period -> object of finite numbers")
    need(deal.get("downside_case") is None or isinstance(deal["downside_case"], dict),
         "deal.downside_case must be an object")
    stress = deal.get("stress_assumptions")
    need(stress is None or (isinstance(stress, dict) and all(_is_number(v) for v in stress.values())),
         "deal.stress_assumptions must be an object of numbers")
    for key in ("collateral", "covenants", "security_package", "guarantees"):
        value = deal.get(key)
        need(value is None or (isinstance(value, list) and all(isinstance(x, dict) for x in value)),
             f"deal.{key} must be a list of objects")
    for asset in (deal.get("collateral") or []):
        if isinstance(asset, dict):
            for field in ("exposure", "collateral_value"):
                need(field not in asset or _is_nonneg_number(asset[field]),
                     f"collateral {field} must be a finite number >= 0")
    for covenant in (deal.get("covenants") or []):
        if isinstance(covenant, dict):
            need("threshold" not in covenant or _is_number(covenant["threshold"]),
                 "covenant threshold must be a finite number")


def _validate_specs(case, assertions, need):
    """The scripted good/bad outputs must be well-formed, and `bad` must say which scored
    oracle(s) it is aimed at -- a mistyped key must not silently fall back to "any oracle fails"."""
    dry = case.get("dry_run")
    if not isinstance(dry, dict):
        return
    for label in ("good", "bad"):
        spec = dry.get(label)
        if not isinstance(spec, dict):
            continue
        if case.get("mode") == "maker":
            need(isinstance(spec.get("draft_body"), str), f"dry_run.{label}.draft_body must be a string")
            need(isinstance(spec.get("structured", {}), dict), f"dry_run.{label}.structured must be an object")
        elif case.get("mode") == "checker":
            need(spec.get("verdict") in VERDICTS, f"dry_run.{label}.verdict must be one of {VERDICTS}")
            need(isinstance(spec.get("notes", ""), str), f"dry_run.{label}.notes must be a string")
    bad = dry.get("bad")
    if isinstance(bad, dict):
        names = {a.get("oracle") for a in assertions if isinstance(a, dict) and a.get("scored", True)}
        expected = bad.get("expected_failures")
        need(isinstance(expected, list) and expected and all(isinstance(n, str) for n in expected)
             and set(expected) <= names,
             "dry_run.bad.expected_failures is required and must name scored oracles of this case")


def validate_case(case):
    """Return a list of human-readable problems with one case (empty = valid). Never raises:
    a structure so malformed that checking it throws is itself reported as a problem."""
    try:
        return _validate_case(case)
    except Exception as exc:  # noqa: BLE001 - the backstop behind the targeted type checks
        label = case.get("id") if isinstance(case, dict) else None
        return [f"case {label!r}: could not be validated -- malformed structure ({type(exc).__name__}: {exc})"]


def _validate_case(case):
    if not isinstance(case, dict):
        return ["case is not a JSON object"]
    problems = []
    case_id = case.get("id")
    label = f"case {str(case_id)[:60]!r}"

    def need(condition, message):
        if not condition:
            problems.append(f"{label}: {message}")

    shapes_ok = _validate_shapes(case, need)
    need(isinstance(case_id, str) and ID_RE.match(case_id or ""), "id must match [a-z0-9][a-z0-9-]*")
    if isinstance(case_id, str) and case.get("_file"):
        need(case["_file"] == f"{case_id}.json", f"file name {case['_file']!r} must be {str(case_id)[:60]}.json")
    need(case.get("category") in CATEGORIES, f"category must be one of {CATEGORIES}")
    need(case.get("mode") in MODES, f"mode must be one of {MODES}")
    need(isinstance(case.get("description"), str) and case["description"].strip(), "description required")
    if not shapes_ok:
        return problems

    deal = case.get("deal")
    if not isinstance(deal, dict):
        problems.append(f"{label}: deal must be an object")
        deal = {}
    for key in ("company", "proposal"):
        need(isinstance(deal.get(key), str) and SYNTHETIC_NAME_RE.match(deal.get(key) or ""),
             f"deal.{key} must be an invented name starting with 'Synthetic '")
    for key in ("deal_type", "pd", "lgd"):
        need(isinstance(deal.get(key), str) and deal[key].strip(), f"deal.{key} required")
    known_types = _known_deal_types()
    need(not known_types or deal.get("deal_type") in known_types,
         f"deal.deal_type {deal.get('deal_type')!r} has no shipped template in templates/cam "
         f"(known: {known_types}); the live runner seeds only templates/cam, so the prompt would differ")
    _validate_inputs(deal, need)
    if deal.get("financials_source") == "analyst-supplied":
        need(not deal.get("multi_period_financials"),
             "an analyst-supplied deal supplies financials/ratios, not multi_period_financials")

    config = case.get("config_files") or {}
    need(set(config) <= set(CONFIG_FILE_KEYS), f"config_files keys must be within {CONFIG_FILE_KEYS}")
    need(all(isinstance(v, str) for v in config.values()), "config_files values must be strings")

    mode = case.get("mode")
    scripted = case.get("scripted") or {}
    need(set(scripted) <= {"maker_draft"}, "scripted may only contain maker_draft")
    if mode == "checker":
        need(isinstance(scripted.get("maker_draft"), str) and scripted["maker_draft"].strip(),
             "a checker case needs scripted.maker_draft (a Maker draft scripted to carry the flaw "
             "under test, that deterministic policy_checks cannot catch)")
        need(not case.get("source_block"), "source_block is a maker-case construct (no real route to the Checker)")
    elif mode == "maker":
        need("maker_draft" not in scripted, "a maker case must not script the Maker's draft")

    block = case.get("source_block")
    if block is not None:
        need(isinstance(block.get("label"), str) and block["label"].strip()
             and isinstance(block.get("text"), str) and block["text"].strip(),
             "source_block needs a non-empty string label and text")

    assertions = case.get("assertions")
    if not (isinstance(assertions, list) and assertions):
        problems.append(f"{label}: assertions must be a non-empty list")
        assertions = []
    for assertion in assertions:
        name = assertion.get("oracle")
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
    if assertions:
        need(any(a.get("scored", True) for a in assertions if isinstance(a, dict)),
             "at least one assertion must be scored")
    if mode == "checker":
        need(any(a.get("oracle") == "verdict_is" and a.get("scored", True) for a in assertions),
             "a checker case needs a scored verdict_is assertion")
    if mode == "maker":
        need(any(a.get("oracle") == "structured_block_present" and a.get("scored", True) for a in assertions),
             "a maker case needs a SCORED structured_block_present, or an empty or garbage reply could "
             "pass figures_grounded / sources_declared vacuously")

    canary = case.get("canary")
    if canary is not None:
        token = canary.get("token")
        need(isinstance(token, str) and CANARY_TOKEN_RE.match(token or ""),
             "canary.token must look like CANARY-XXXXXXXX (8 upper-case letters/digits)")
        parts = canary.get("parts")
        need(parts is None or (isinstance(parts, list) and parts and all(isinstance(p, str) and p.strip() for p in parts)),
             "canary.parts must be a non-empty list of non-empty strings")
        surfaces = canary.get("planted_in")
        need(isinstance(surfaces, list) and surfaces and set(surfaces) <= set(CANARY_SURFACES),
             f"canary.planted_in must be a non-empty list within {CANARY_SURFACES}")
        if isinstance(surfaces, list) and mode in SURFACES_BY_MODE:
            unreachable = sorted(set(surfaces) & set(CANARY_SURFACES) - SURFACES_BY_MODE[mode])
            need(not unreachable, f"canary surface(s) {unreachable} cannot reach the prompt of a "
                                  f"{mode} case, so canary_absent would pass vacuously")
        if isinstance(token, str) and isinstance(surfaces, list):
            texts = _surface_texts(case)
            for surface in surfaces:
                if surface in texts:
                    need(canary_planted(canary, texts[surface]),
                         f"canary is not actually planted in {surface} (token or every part must appear)")

    human = case.get("human_review", [])
    need(human and all(isinstance(q, str) and q.strip() for q in human),
         "human_review must be a non-empty list of questions (the oracles check form, not judgement)")

    dry = case.get("dry_run")
    need(isinstance(dry, dict) and isinstance(dry.get("good"), dict) and isinstance(dry.get("bad"), dict),
         "dry_run needs scripted 'good' and 'bad' outputs")
    _validate_specs(case, assertions, need)

    # Synthetic-data check: nothing mechanically real-looking anywhere in the case.
    for text in _string_leaves({k: v for k, v in case.items() if k != "_file"}):
        for finding in scan_for_likely_real_data(text):
            problems.append(f"{label}: looks like real data ({finding['reason']}: {finding['snippet']!r})")

    if not problems:
        problems.extend(_semantic_problems(case, label, need))
    return problems


def _semantic_problems(case, label, need):
    """Checks that evaluate the case's scripted content (run only once it is well-formed)."""
    extra = []

    def need_local(condition, message):
        if not condition:
            extra.append(f"{label}: {message}")

    if case["mode"] == "checker":
        ctx = build_context(case)
        run = RunOutput(draft_text=case["scripted"]["maker_draft"])
        passed, reason = ORACLES["compliance"](run, case, ctx, {})
        need_local(passed, "the scripted Maker draft is already rejected by deterministic checks "
                           f"({reason}); a REJECTED verdict would prove nothing about the model")
    try:
        result = self_check(case)
    except Exception as exc:  # noqa: BLE001 - a broken scripted spec is a validation problem
        extra.append(f"{label}: scripted dry-run outputs could not be evaluated ({exc!r})")
        return extra
    need_local(result["good_all_pass"], "scripted 'good' output fails a scored assertion: "
               + "; ".join(f"{r['oracle']}: {r['reason']}" for r in result["good"]
                           if r["scored"] and not r["passed"]))
    need_local(result["bad_fails_at_least_one"],
               "scripted 'bad' output does not fail the scored assertion(s) it is aimed at "
               "(bad.expected_failures, else any), so the oracles don't discriminate")
    return extra


def validate_dataset(dataset):
    """All problems across the dataset, including duplicate ids and any two
    cases sharing a (company, proposal) pair."""
    problems = []
    seen_ids, seen_deals = set(), {}
    cases = dataset["cases"]
    if not cases:
        problems.append("dataset has no cases")
    for name in dataset.get("stray_files", []):
        problems.append(f"cases/{name} is not a .json file and would be silently ignored")
    for case in cases:
        problems.extend(validate_case(case))
        if not isinstance(case, dict):
            continue
        case_id = repr(case.get("id"))  # repr: an unhashable id must not crash the duplicate check
        if case_id in seen_ids:
            problems.append(f"duplicate case id {case_id}")
        seen_ids.add(case_id)
        deal = case.get("deal") if isinstance(case.get("deal"), dict) else {}
        pair = (repr(deal.get("company")), repr(deal.get("proposal")))
        if "None" not in pair:
            if pair in seen_deals:
                problems.append(
                    f"cases {seen_deals[pair]!r} and {case_id!r} share (company, proposal) {pair}: "
                    "a run resumes any earlier state for the same pair, leaking one case into the next")
            seen_deals.setdefault(pair, case_id)
    return problems
