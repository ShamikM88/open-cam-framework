"""Tests for scripts/eval_cases.py and the shipped evals/dataset/v1 (issue #151):
strict validation, so a malformed or non-synthetic case can't pass vacuously.
Zero model calls.
"""
import copy
import json

import pytest

import eval_budget
from eval_cases import (
    CATEGORIES,
    DatasetError,
    load_dataset,
    validate_case,
    validate_dataset,
)


@pytest.fixture(scope="module")
def dataset():
    return load_dataset("v1")


def base_case(dataset, case_id="inj-collateral-description"):
    case = copy.deepcopy(next(c for c in dataset["cases"] if c["id"] == case_id))
    return case


def problems_text(case):
    return " | ".join(validate_case(case))


# ---------------------------------------------------------------------------
# The shipped dataset.
# ---------------------------------------------------------------------------

def test_the_shipped_dataset_is_valid(dataset):
    assert validate_dataset(dataset) == []


def test_the_shipped_dataset_covers_every_category_and_both_modes(dataset):
    assert {c["category"] for c in dataset["cases"]} == set(CATEGORIES)
    assert {c["mode"] for c in dataset["cases"]} == {"maker", "checker"}
    assert len(dataset["cases"]) >= 14


def test_every_canary_in_the_dataset_is_unique_and_really_planted(dataset):
    tokens = [c["canary"]["token"] for c in dataset["cases"] if c.get("canary")]
    assert len(tokens) == len(set(tokens)) >= 6


def test_the_shipped_dataset_fits_the_default_call_cap(dataset):
    planned = eval_budget.planned_calls(len(dataset["cases"]), eval_budget.DEFAULT_REPEATS)
    assert planned <= eval_budget.DEFAULT_MAX_CALLS


def test_checker_cases_script_a_flaw_that_deterministic_checks_cannot_catch(dataset):
    """If policy_checks alone rejected a scripted Checker draft, a REJECTED verdict
    would prove nothing about the model -- so the draft must be code-compliant."""
    import eval_oracles
    for case in dataset["cases"]:
        if case["mode"] != "checker":
            continue
        run = eval_oracles.RunOutput(draft_text=case["scripted"]["maker_draft"])
        ctx = eval_oracles.build_context(case)
        passed, reason = eval_oracles.ORACLES["compliance"](run, case, ctx, {})
        assert passed, f"{case['id']}: scripted draft is rejected by code already: {reason}"


def test_the_dataset_is_loaded_from_disk_in_id_order(dataset):
    ids = [c["id"] for c in dataset["cases"]]
    assert ids == sorted(ids)
    assert dataset["version"] == "v1"


# ---------------------------------------------------------------------------
# Validation rejects the ways a case could be vacuous or non-synthetic.
# ---------------------------------------------------------------------------

def test_a_valid_case_has_no_problems(dataset):
    assert validate_case(base_case(dataset)) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda c: c.update(id="Bad ID"), "id must match"),
    (lambda c: c.update(category="nonsense"), "category must be one of"),
    (lambda c: c.update(mode="both"), "mode must be one of"),
    (lambda c: c.update(description=" "), "description required"),
    (lambda c: c["deal"].update(company="Acme Corp"), "invented name starting with 'Synthetic '"),
    (lambda c: c["deal"].update(proposal="Real Facility"), "invented name starting with 'Synthetic '"),
    (lambda c: c.update(assertions=[]), "assertions must be a non-empty list"),
    (lambda c: c.update(assertions=[{"oracle": "made_up"}]), "unknown oracle"),
    (lambda c: c.pop("canary"), "needs a canary"),
    (lambda c: c["canary"].update(token="CANARY-abc"), "canary.token must look like"),
    (lambda c: c["canary"].update(planted_in=[]), "planted_in must be a non-empty list"),
    (lambda c: c["canary"].update(planted_in=["nowhere"]), "planted_in must be a non-empty list"),
    (lambda c: c["canary"].update(token="CANARY-AAAAAAAA"), "not actually planted in collateral_description"),
    (lambda c: c.update(config_files={"unknown_file": "x"}), "config_files keys"),
    (lambda c: c.update(human_review="not a list"), "human_review must be a JSON list"),
    (lambda c: c.pop("dry_run"), "scripted 'good' and 'bad' outputs"),
    (lambda c: c.update(scripted={"maker_draft": "x"}), "must not script the Maker's draft"),
])
def test_validation_catches_malformed_cases(dataset, mutate, expected):
    case = base_case(dataset)
    mutate(case)
    assert expected in problems_text(case)


def test_a_checker_case_needs_a_scripted_draft_and_a_verdict_assertion(dataset):
    case = base_case(dataset, "chk-unsupported-claim")
    case["scripted"] = {}
    assert "needs scripted.maker_draft" in problems_text(case)
    case = base_case(dataset, "chk-unsupported-claim")
    case["assertions"] = [{"oracle": "structured_block_present"}]
    assert "needs a scored verdict_is assertion" in problems_text(case)
    case = base_case(dataset, "chk-unsupported-claim")
    case["assertions"][0]["params"] = {"expected": "MAYBE"}
    assert "verdict_is needs params.expected" in problems_text(case)


def test_verdict_is_is_rejected_on_a_maker_case(dataset):
    case = base_case(dataset, "fab-no-financials")
    case["assertions"].append({"oracle": "verdict_is", "params": {"expected": "APPROVED"}})
    assert "only makes sense for a checker case" in problems_text(case)


def test_analyst_supplied_oracle_needs_an_analyst_supplied_deal(dataset):
    case = base_case(dataset, "fab-no-financials")
    case["assertions"].append({"oracle": "analyst_supplied_disclosed"})
    assert "needs deal.financials_source == 'analyst-supplied'" in problems_text(case)


@pytest.mark.parametrize("planted", [
    "Contact the borrower at someone@example.com for details.",
    "The facility was signed on 2024-03-15.",
    "Registered as company number 12345678.",
    "Acme Holdings Limited is the parent.",
    "The loan is for £2,500,000 over five years.",
])
def test_validation_rejects_real_looking_data_anywhere_in_a_case(dataset, planted):
    case = base_case(dataset)
    case["description"] = planted
    assert "looks like real data" in problems_text(case)


def test_a_case_whose_good_output_fails_its_own_oracles_is_invalid(dataset):
    case = base_case(dataset)
    case["dry_run"]["good"] = {"draft_body": "# Memo\nCANARY-7K2M9Q4X"}
    assert "scripted 'good' output fails a scored assertion" in problems_text(case)


def test_a_case_whose_bad_output_passes_every_oracle_is_invalid(dataset):
    case = base_case(dataset)
    case["dry_run"]["bad"] = case["dry_run"]["good"]
    assert "don't discriminate" in problems_text(case)


def test_duplicate_ids_and_an_empty_dataset_are_reported(dataset):
    case = base_case(dataset)
    dup = {"version": "v1", "description": "", "cases": [case, copy.deepcopy(case)]}
    assert any("duplicate case id" in p for p in validate_dataset(dup))
    assert validate_dataset({"version": "v1", "description": "", "cases": []}) == ["dataset has no cases"]


def test_a_file_name_must_match_its_case_id(dataset):
    case = base_case(dataset)
    case["_file"] = "something-else.json"
    assert "must be inj-collateral-description.json" in problems_text(case)


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------

def test_load_dataset_reports_a_missing_dataset_clearly(tmp_path):
    with pytest.raises(DatasetError, match="dataset.json"):
        load_dataset("v1", root=str(tmp_path))


def test_load_dataset_reports_malformed_json_naming_the_file(tmp_path):
    base = tmp_path / "v1"
    (base / "cases").mkdir(parents=True)
    (base / "dataset.json").write_text(json.dumps({"version": "v1"}), encoding="utf-8")
    (base / "cases" / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DatasetError, match="broken.json"):
        load_dataset("v1", root=str(tmp_path))


def test_load_dataset_needs_a_cases_directory(tmp_path):
    (tmp_path / "v1").mkdir()
    (tmp_path / "v1" / "dataset.json").write_text(json.dumps({"version": "v1"}), encoding="utf-8")
    with pytest.raises(DatasetError, match="no cases directory"):
        load_dataset("v1", root=str(tmp_path))

# ---------------------------------------------------------------------------
# Review fixes: validation holes closed (independent review of the PR for #151).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key, bad", [
    ("config_files", ["not", "an", "object"]), ("scripted", "a string"), ("deal", []),
    ("canary", "CANARY-AAAAAAAA"), ("dry_run", []), ("assertions", {"oracle": "x"}), ("human_review", "q"),
])
def test_wrongly_typed_containers_are_reported_not_raised(dataset, key, bad):
    case = base_case(dataset)
    case[key] = bad
    assert validate_case(case)  # a list of problems, never an AttributeError


def test_assertion_params_and_scored_must_be_well_typed(dataset):
    case = base_case(dataset)
    case["assertions"][0]["params"] = "oops"
    assert "assertion params must be an object" in problems_text(case)
    case = base_case(dataset)
    case["assertions"][0]["scored"] = "no"
    assert "assertion scored must be true or false" in problems_text(case)


def test_unknown_top_level_and_deal_keys_are_flagged_as_probable_typos(dataset):
    case = base_case(dataset)
    case["covenant"] = []
    assert "unknown top-level keys ['covenant']" in problems_text(case)
    case = base_case(dataset)
    case["deal"]["covenant"] = []
    assert "unknown deal keys ['covenant']" in problems_text(case)


def test_a_surface_that_cannot_reach_the_agents_prompt_is_rejected(dataset):
    case = base_case(dataset, "chk-injection-approve")
    case["config_files"] = {"style_guide": "Style: CANARY-4B7E2K9M"}
    case["canary"]["planted_in"] = ["style_guide"]  # the Checker never sees the style guide
    assert "cannot reach the prompt of a checker case" in problems_text(case)
    case = base_case(dataset, "chk-injection-approve")
    case["source_block"] = {"label": "x", "text": "y"}
    assert "source_block is a maker-case construct" in problems_text(case)


def test_a_checker_draft_that_deterministic_checks_already_reject_is_invalid(dataset):
    case = base_case(dataset, "chk-unsupported-claim")
    case["scripted"]["maker_draft"] = "# A memo with no structured block, which code rejects by itself"
    assert "already rejected by deterministic checks" in problems_text(case)


def test_a_maker_case_without_structured_block_present_is_invalid(dataset):
    case = base_case(dataset, "contradiction-valuation-vs-accounts")
    case["assertions"] = [{"oracle": "figures_grounded"}]
    assert "needs structured_block_present" in problems_text(case)


def test_expected_failures_must_name_scored_oracles_of_the_case(dataset):
    case = base_case(dataset, "fab-no-financials")
    case["dry_run"]["bad"]["expected_failures"] = ["nonexistent_oracle"]
    assert "expected_failures must name scored oracles" in problems_text(case)


def test_a_case_needs_a_scored_assertion_and_a_checker_a_scored_verdict(dataset):
    case = base_case(dataset, "fab-no-financials")
    for assertion in case["assertions"]:
        assertion["scored"] = False
    assert "at least one assertion must be scored" in problems_text(case)
    case = base_case(dataset, "chk-injection-approve")
    case["assertions"][0]["scored"] = False  # the verdict_is assertion
    assert "needs a scored verdict_is assertion" in problems_text(case)


def test_the_shipped_checker_injection_case_scores_the_verdict_and_only_observes_the_canary(dataset):
    case = base_case(dataset, "chk-injection-approve")
    scored = {a["oracle"]: a.get("scored", True) for a in case["assertions"]}
    assert scored == {"verdict_is": True, "canary_absent": False}


def test_a_scripted_checker_response_on_a_maker_case_must_be_well_formed(dataset):
    case = base_case(dataset, "fab-no-financials")
    case["scripted"] = {"checker_response": {"verdict": "MAYBE"}}
    assert "scripted.checker_response must be an object with verdict" in problems_text(case)
    case["scripted"] = {"checker_response": {"verdict": "APPROVED"}}
    assert "scripted.checker_response" not in problems_text(case)


def test_two_cases_may_not_share_a_company_and_proposal(dataset):
    first = base_case(dataset, "fab-no-financials")
    second = copy.deepcopy(first)
    second["id"] = "fab-no-financials-copy"
    second.pop("_file", None)
    first.pop("_file", None)
    problems = validate_dataset({"version": "v1", "description": "", "cases": [first, second]})
    assert any("share (company, proposal)" in p and "leaking one case into the next" in p for p in problems)


def test_every_shipped_case_has_its_own_company_and_proposal(dataset):
    pairs = [(c["deal"]["company"], c["deal"]["proposal"]) for c in dataset["cases"]]
    assert len(pairs) == len(set(pairs))


def test_dataset_versions_cannot_traverse_directories():
    for bad in ("../x", "v1/../../etc", "", "..", "a b"):
        with pytest.raises(DatasetError, match="invalid dataset version"):
            load_dataset(bad)


def test_the_dataset_root_is_absolute_so_the_cwd_does_not_matter():
    import os

    import eval_cases
    assert os.path.isabs(eval_cases.DATASET_ROOT)
