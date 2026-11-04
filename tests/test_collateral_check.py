"""Deterministic collateral exposure calculator (issue #266, PR B).

Tests the pure, importable calculation and its CLI contract: F-049 to F-057 and
F-066 of the formula register, the asset_id grammar and uniqueness rule, the
input_digest / calculator_version provenance record, and the one-error-line
refusals with nothing written.

All figures are synthetic (docs/examples conventions); no real borrower data.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import collateral_check
from collateral_check import (
    CALCULATOR_VERSION,
    CollateralInputError,
    calculate_asset_results,
    calculate_collateral_analysis,
    calculate_deal_results,
    allocate_net_exposure,
    canonical_input_digest,
    collateral_projection,
    validate_collateral_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _asset(**overrides):
    """One synthetic asset input: the register's worked example (F-049 to F-057)."""
    asset = {
        "asset_id": "AST-001",
        "asset_class": "Synthetic excavator",
        "valuation": 100000,
        "down_payment_pct": 0.20,
        "est_rv_pct": 0.40,
        "number_of_units": 1,
        "cap_or_model_value": 60000,
        "non_recovery": 0,
        "costs": 0,
        "lgd_pct": 0.15,
        "perfection_status": "Pending",
    }
    asset.update(overrides)
    return asset


def _payload(assets=None, mitigants=None):
    return {
        "assets": assets if assets is not None else [_asset()],
        "mitigants": mitigants if mitigants is not None else [],
    }


# ---------------------------------------------------------------------------
# Validation: asset_id grammar and uniqueness (decision 3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("asset_id", ["A", "AST-001", "a" * 32, "Asset-9"])
def test_asset_id_grammar_accepts_letters_digits_and_hyphens(asset_id):
    payload = _payload(assets=[_asset(asset_id=asset_id)])
    assert validate_collateral_payload(payload)["assets"][0]["asset_id"] == asset_id


@pytest.mark.parametrize("asset_id", [
    "AST_001",                      # underscore is refused: keys must split unambiguously
    "AST 001",                      # space
    "AST.001",                      # dot
    "",                             # empty
    "a" * 33,                       # too long
    7,                              # not a string
    None,
])
def test_asset_id_grammar_refuses_anything_else(asset_id):
    with pytest.raises(CollateralInputError, match="asset_id"):
        validate_collateral_payload(_payload(assets=[_asset(asset_id=asset_id)]))


def test_duplicate_asset_ids_are_refused():
    payload = _payload(assets=[_asset(), _asset(valuation=50000)])
    with pytest.raises(CollateralInputError, match="duplicate asset_id"):
        validate_collateral_payload(payload)


# ---------------------------------------------------------------------------
# Validation: numeric ranges and cross-field rules (F-049 to F-057 edge rules)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field, value", [
    ("valuation", -1),
    ("valuation", "100000"),
    ("valuation", True),
    ("cap_or_model_value", -1),
    ("non_recovery", -1),
    ("costs", -1),
    ("number_of_units", -1),
])
def test_negative_or_non_numeric_monetary_inputs_are_refused(field, value):
    with pytest.raises(CollateralInputError):
        validate_collateral_payload(_payload(assets=[_asset(**{field: value})]))


def test_non_finite_numbers_are_refused():
    with pytest.raises(CollateralInputError, match="finite"):
        validate_collateral_payload(_payload(assets=[_asset(valuation=float("nan"))]))
    with pytest.raises(CollateralInputError, match="finite"):
        validate_collateral_payload(_payload(assets=[_asset(valuation=float("inf"))]))


@pytest.mark.parametrize("field", ["valuation", "down_payment_pct", "est_rv_pct"])
def test_required_numeric_inputs_must_be_present(field):
    asset = _asset()
    del asset[field]
    with pytest.raises(CollateralInputError, match=field):
        validate_collateral_payload(_payload(assets=[asset]))


@pytest.mark.parametrize("field, value", [
    ("down_payment_pct", -0.01),
    ("down_payment_pct", 1.01),
    ("est_rv_pct", -0.01),
    ("est_rv_pct", 1.01),
    ("lgd_pct", -0.01),
    ("lgd_pct", 1.01),
])
def test_percentages_outside_the_unit_interval_are_refused(field, value):
    with pytest.raises(CollateralInputError, match=field):
        validate_collateral_payload(_payload(assets=[_asset(**{field: value})]))


def test_a_residual_above_the_financed_share_is_refused():
    # est_rv_pct 0.90 > 1 - down_payment_pct 0.20: the residual cannot exceed the
    # financed share (PX266-2 / CG266-11, maintainer decision 6565421162).
    with pytest.raises(CollateralInputError, match="financed share"):
        validate_collateral_payload(_payload(assets=[_asset(est_rv_pct=0.90)]))


# ---------------------------------------------------------------------------
# Validation: mitigants (decision 2, F-052)
# ---------------------------------------------------------------------------

def test_a_missing_mitigant_eligibility_flag_refuses_the_input():
    payload = _payload(mitigants=[{"mitigant_id": "MIT-001", "amount": 10000}])
    with pytest.raises(CollateralInputError, match="eligible_for_netting"):
        validate_collateral_payload(payload)


def test_mitigants_require_a_non_negative_amount():
    payload = _payload(mitigants=[{"amount": -100, "eligible_for_netting": True}])
    with pytest.raises(CollateralInputError, match="amount"):
        validate_collateral_payload(payload)


def test_mitigant_ids_follow_the_same_grammar_and_unique_rule():
    payload = _payload(mitigants=[{"mitigant_id": "MIT_1", "amount": 0, "eligible_for_netting": False}])
    with pytest.raises(CollateralInputError, match="mitigant_id"):
        validate_collateral_payload(payload)
    duplicated = _payload(mitigants=[
        {"mitigant_id": "MIT-001", "amount": 0, "eligible_for_netting": False},
        {"mitigant_id": "MIT-001", "amount": 0, "eligible_for_netting": True},
    ])
    with pytest.raises(CollateralInputError, match="duplicate"):
        validate_collateral_payload(duplicated)


def test_mitigants_are_optional_and_may_be_omitted():
    payload = {"assets": [_asset()]}
    assert validate_collateral_payload(payload)["mitigants"] == []


def test_a_non_boolean_eligibility_flag_is_refused():
    payload = _payload(mitigants=[{"amount": 10000, "eligible_for_netting": "yes"}])
    with pytest.raises(CollateralInputError, match="eligible_for_netting"):
        validate_collateral_payload(payload)


def test_a_mitigant_that_is_not_an_object_is_refused():
    with pytest.raises(CollateralInputError, match="mitigants"):
        validate_collateral_payload(_payload(mitigants=["not an object"]))


def test_a_mitigant_without_an_amount_is_refused():
    with pytest.raises(CollateralInputError, match="amount"):
        validate_collateral_payload(_payload(mitigants=[{"eligible_for_netting": True}]))


def test_asset_class_must_be_a_non_empty_string_when_present():
    with pytest.raises(CollateralInputError, match="asset_class"):
        validate_collateral_payload(_payload(assets=[_asset(asset_class=7)]))
    with pytest.raises(CollateralInputError, match="perfection_status"):
        validate_collateral_payload(_payload(assets=[_asset(perfection_status=" ")]))


def test_optional_monetary_inputs_default_to_zero_through_validation():
    asset = _asset()
    for field in ("cap_or_model_value", "non_recovery", "costs", "number_of_units"):
        del asset[field]
    validated = validate_collateral_payload(_payload(assets=[asset]))["assets"][0]
    assert validated["cap_or_model_value"] == 0.0
    assert validated["non_recovery"] == 0.0
    assert validated["costs"] == 0.0
    assert validated["number_of_units"] == 0


# ---------------------------------------------------------------------------
# Validation: payload shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    [], "not an object", 7,
    {"mitigants": []},                       # no assets key
    {"assets": {"AST-001": {}}},             # assets must be a list
    {"assets": ["not an object"]},           # each asset must be an object
    {"assets": [{}]},                        # asset_id is required
    {"assets": [_asset()], "mitigants": {}},  # mitigants must be a list
])
def test_a_malformed_payload_is_refused(payload):
    with pytest.raises(CollateralInputError):
        validate_collateral_payload(payload)


def test_an_empty_asset_list_is_accepted():
    analysis = calculate_collateral_analysis({"assets": [], "mitigants": []})
    assert analysis["assets"] == []
    assert analysis["results"]["collateral_max_lgd_pct"] is None


# ---------------------------------------------------------------------------
# Per-asset results: the register's worked example (F-049 to F-055, F-066)
# ---------------------------------------------------------------------------

def test_asset_results_match_the_register_worked_example():
    results = calculate_asset_results(_asset())
    assert results["non_rv_funded_exposure"] == pytest.approx(40000)
    assert results["rv_exposure"] == pytest.approx(40000)
    assert results["total_exposure"] == pytest.approx(80000)
    assert results["collateral_value"] == pytest.approx(60000)
    assert results["coverage_pct"] == pytest.approx(0.75)
    assert results["uncovered_risk"] == pytest.approx(20000)
    assert results["lgd_pct"] == pytest.approx(0.15)


def test_asset_results_floor_the_collateral_value_at_zero():
    results = calculate_asset_results(_asset(cap_or_model_value=10000, non_recovery=8000, costs=5000))
    assert results["collateral_value"] == 0
    assert results["uncovered_risk"] == pytest.approx(80000)


def test_zero_exposure_makes_coverage_na_never_zero():
    results = calculate_asset_results(_asset(valuation=0, cap_or_model_value=0))
    assert results["total_exposure"] == 0
    assert results["coverage_pct"] is None
    assert results["uncovered_risk"] == 0


def test_missing_lgd_is_stored_as_none_not_zero():
    asset = _asset()
    del asset["lgd_pct"]
    results = calculate_asset_results(asset)
    assert results["lgd_pct"] is None


def test_omitted_monetary_inputs_default_to_zero():
    asset = _asset()
    for field in ("cap_or_model_value", "non_recovery", "costs", "number_of_units"):
        del asset[field]
    results = calculate_asset_results(asset)
    assert results["collateral_value"] == 0


# ---------------------------------------------------------------------------
# Pro rata mitigant allocation (F-052, F-066): the register's two-asset example
# ---------------------------------------------------------------------------

def test_mitigants_are_allocated_pro_rata_to_total_exposure():
    assets = [
        _asset(asset_id="A", valuation=100, down_payment_pct=0.0, est_rv_pct=0.0, cap_or_model_value=0),
        _asset(asset_id="B", valuation=300, down_payment_pct=0.0, est_rv_pct=0.0, cap_or_model_value=0),
    ]
    validated = validate_collateral_payload(_payload(assets=assets))
    net = allocate_net_exposure(validated["assets"], 100)
    assert net["A"] == pytest.approx(75)
    assert net["B"] == pytest.approx(225)


def test_mitigants_above_total_exposure_floor_net_exposure_at_zero():
    assets = [_asset(valuation=100, down_payment_pct=0.20, est_rv_pct=0.0)]
    validated = validate_collateral_payload(_payload(assets=assets))["assets"]
    net = allocate_net_exposure(validated, 90)
    assert net["AST-001"] == 0


def test_zero_total_exposure_allocates_nothing():
    assets = [_asset(valuation=0, down_payment_pct=0.0, est_rv_pct=0.0)]
    validated = validate_collateral_payload(_payload(assets=assets))["assets"]
    net = allocate_net_exposure(validated, 50)
    assert net["AST-001"] == 0


def test_only_eligible_mitigants_are_deducted():
    payload = _payload(mitigants=[
        {"mitigant_id": "MIT-001", "amount": 30000, "eligible_for_netting": True},
        {"mitigant_id": "MIT-002", "amount": 50000, "eligible_for_netting": False},
    ])
    analysis = calculate_collateral_analysis(payload)
    assert analysis["results"]["gross_uncovered_risk"] == pytest.approx(20000)
    assert analysis["results"]["net_uncovered_risk"] == 0
    assert analysis["results"]["excess_mitigant"] == 0


# ---------------------------------------------------------------------------
# Deal-level results (decision 4)
# ---------------------------------------------------------------------------

def test_deal_results_report_the_agreed_outputs():
    payload = _payload()
    analysis = calculate_collateral_analysis(payload)
    results = analysis["results"]
    assert results["total_exposure"] == pytest.approx(80000)
    assert results["total_collateral_value"] == pytest.approx(60000)
    assert results["coverage_pct"] == pytest.approx(0.75)
    assert results["gross_uncovered_risk"] == pytest.approx(20000)
    assert results["net_uncovered_risk"] == pytest.approx(20000)
    assert results["excess_mitigant"] == pytest.approx(0)
    assert results["collateral_max_lgd_pct"] == pytest.approx(0.15)
    assert results["collateral_avg_lgd_pct"] == pytest.approx(0.15)


def test_excess_eligible_mitigant_is_reported_separately():
    payload = _payload(mitigants=[{"amount": 90000, "eligible_for_netting": True}])
    results = calculate_collateral_analysis(payload)["results"]
    assert results["net_uncovered_risk"] == 0
    assert results["excess_mitigant"] == pytest.approx(10000)


def test_missing_lgd_makes_max_and_average_lgd_na():
    asset = _asset()
    del asset["lgd_pct"]
    results = calculate_collateral_analysis(_payload(assets=[asset]))["results"]
    assert results["collateral_max_lgd_pct"] is None
    assert results["collateral_avg_lgd_pct"] is None


def test_average_lgd_is_exposure_weighted():
    payload = _payload(assets=[
        _asset(asset_id="A", valuation=1000, down_payment_pct=0.0, est_rv_pct=0.0, lgd_pct=0.40,
               cap_or_model_value=0),
        _asset(asset_id="B", valuation=9000, down_payment_pct=0.0, est_rv_pct=0.0, lgd_pct=0.80,
               cap_or_model_value=0),
    ])
    results = calculate_collateral_analysis(payload)["results"]
    assert results["collateral_max_lgd_pct"] == pytest.approx(0.80)
    # (0.40 x 1000 + 0.80 x 9000) / 10000
    assert results["collateral_avg_lgd_pct"] == pytest.approx(0.76)


def test_zero_total_exposure_makes_deal_coverage_na():
    payload = _payload(assets=[_asset(valuation=0, down_payment_pct=0.0, est_rv_pct=0.0)])
    results = calculate_collateral_analysis(payload)["results"]
    assert results["coverage_pct"] is None
    assert results["collateral_avg_lgd_pct"] is None


# ---------------------------------------------------------------------------
# Provenance: input_digest and calculator_version (decision 5)
# ---------------------------------------------------------------------------

def test_the_digest_is_a_sha256_of_the_canonical_inputs():
    payload = _payload()
    validated = validate_collateral_payload(payload)
    digest = canonical_input_digest(validated)
    assert len(digest) == 64 and int(digest, 16) >= 0
    assert digest == hashlib.sha256(
        json.dumps(validated, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def test_the_digest_changes_when_any_input_changes():
    first = calculate_collateral_analysis(_payload())
    second = calculate_collateral_analysis(_payload(assets=[_asset(valuation=100001)]))
    assert first["input_digest"] != second["input_digest"]


def test_the_stored_digest_recomputes_from_the_stored_inputs():
    analysis = calculate_collateral_analysis(_payload())
    recomputed = canonical_input_digest({
        "assets": [dict(asset["inputs"], asset_id=asset["asset_id"]) for asset in analysis["assets"]],
        "mitigants": analysis["mitigants"],
    })
    assert recomputed == analysis["input_digest"]


def test_calculator_version_is_recorded_with_the_results():
    analysis = calculate_collateral_analysis(_payload())
    assert analysis["calculator_version"] == CALCULATOR_VERSION
    assert isinstance(CALCULATOR_VERSION, str) and CALCULATOR_VERSION


# ---------------------------------------------------------------------------
# The backward-compatible collateral projection (decision 2)
# ---------------------------------------------------------------------------

def test_the_legacy_projection_carries_exposure_and_collateral_value():
    analysis = calculate_collateral_analysis(_payload())
    projection = collateral_projection(analysis)
    assert len(projection) == 1
    row = projection[0]
    assert row["asset_class"] == "Synthetic excavator"
    assert row["exposure"] == 80000                       # exposure = total_exposure
    assert row["number_of_units"] == 1
    assert row["cap_value"] == 60000
    assert row["non_recovery"] == 0
    assert row["costs"] == 0
    assert row["collateral_value"] == 60000
    assert row["perfection_status"] == "Pending"


def test_the_legacy_projection_passes_the_378_validator():
    from numeric_validation import validate_collateral_inputs
    projection = collateral_projection(calculate_collateral_analysis(_payload()))
    assert validate_collateral_inputs(projection) == projection


# ---------------------------------------------------------------------------
# Full analysis shape (decision 2: per-asset inputs and results, mitigants list)
# ---------------------------------------------------------------------------

def test_the_analysis_stores_inputs_and_results_per_asset():
    analysis = calculate_collateral_analysis(_payload(mitigants=[
        {"mitigant_id": "MIT-001", "amount": 10000, "eligible_for_netting": True},
    ]))
    assert analysis["assets"][0]["asset_id"] == "AST-001"
    assert analysis["assets"][0]["inputs"]["valuation"] == 100000
    assert analysis["assets"][0]["results"]["total_exposure"] == 80000
    assert analysis["assets"][0]["results"]["net_exposure"] == 70000
    assert analysis["assets"][0]["results"]["net_uncovered_risk"] == 10000
    assert analysis["mitigants"] == [{"mitigant_id": "MIT-001", "amount": 10000, "eligible_for_netting": True}]


def test_the_analysis_is_deterministic():
    assert calculate_collateral_analysis(_payload()) == calculate_collateral_analysis(_payload())


# ---------------------------------------------------------------------------
# CLI: one error line, exit 1, nothing written; success writes atomically
# ---------------------------------------------------------------------------

def _child_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("ANTHROPIC")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_cli(*args, cwd, payload_text=None):
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "collateral_check.py"), *map(str, args)],
        cwd=cwd, env=_child_env(), capture_output=True, text=True, encoding="utf-8", timeout=120,
        input=payload_text,
    )


@pytest.fixture
def workdir(tmp_path):
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


def test_cli_help_prints_usage_and_exits_zero(workdir):
    result = _run_cli("--help", cwd=workdir)
    assert result.returncode == 0
    assert result.stdout.startswith("usage:")
    assert "--input-file" in result.stdout and "--company" in result.stdout and "--proposal" in result.stdout


def test_cli_reports_a_refusal_as_one_error_line_and_writes_nothing(workdir):
    payload = workdir / "payload.json"
    payload.write_text(json.dumps(_payload(assets=[_asset(valuation=-50000)])), encoding="utf-8")
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", payload, cwd=workdir)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert result.stderr.startswith("error: ")
    assert not (workdir / "deals").exists()


def test_cli_writes_the_analysis_and_marks_the_step_complete(workdir):
    payload = workdir / "payload.json"
    payload.write_text(json.dumps(_payload()), encoding="utf-8")
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", payload, cwd=workdir)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["results"]["total_exposure"] == 80000
    state_files = list(workdir.glob("deals/Acme/Loan_*/state.json"))
    assert len(state_files) == 1
    state = json.loads(state_files[0].read_text(encoding="utf-8"))
    assert state["collateral_analysis"]["input_digest"] == output["input_digest"]
    assert state["collateral_analysis"]["calculator_version"] == CALCULATOR_VERSION
    assert state["collateral"][0]["exposure"] == 80000
    assert "collateral" in state["steps_completed"]


def test_cli_reads_the_payload_from_stdin(workdir):
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", "-", cwd=workdir,
                      payload_text=json.dumps(_payload()))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["results"]["total_exposure"] == 80000
    assert list(workdir.glob("deals/Acme/Loan_*/state.json"))


def test_cli_does_not_duplicate_the_completed_step(workdir):
    payload = workdir / "payload.json"
    payload.write_text(json.dumps(_payload()), encoding="utf-8")
    for _ in range(2):
        assert _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", payload,
                        cwd=workdir).returncode == 0
    state = json.loads(next(workdir.glob("deals/Acme/Loan_*/state.json")).read_text(encoding="utf-8"))
    assert state["steps_completed"].count("collateral") == 1


def test_cli_refusal_leaves_an_existing_state_byte_for_byte_unchanged(workdir):
    good = workdir / "payload.json"
    good.write_text(json.dumps(_payload()), encoding="utf-8")
    assert _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", good, cwd=workdir).returncode == 0
    state_path = next(workdir.glob("deals/Acme/Loan_*/state.json"))
    before = state_path.read_bytes()
    bad = workdir / "bad.json"
    bad.write_text(json.dumps(_payload(assets=[_asset(est_rv_pct=0.90)])), encoding="utf-8")
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", bad, cwd=workdir)
    assert result.returncode == 1
    assert state_path.read_bytes() == before


def test_cli_reports_a_malformed_payload_file_as_one_error_line(workdir):
    payload = workdir / "payload.json"
    payload.write_text("{not json", encoding="utf-8")
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", payload, cwd=workdir)
    assert result.returncode == 1
    assert result.stderr.startswith("error: ")
    assert not (workdir / "deals").exists()


def test_cli_reports_a_missing_input_file_as_one_error_line(workdir):
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file",
                      workdir / "no-such-payload.json", cwd=workdir)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert result.stderr.count("\n") == 1
    assert not (workdir / "deals").exists()


def test_cli_reports_a_corrupted_state_as_one_error_line(workdir):
    # A state.json the code cannot read is an error line, never a traceback
    # (issues #170 and #171 apply to this CLI as to every other).
    state_dir = workdir / "deals" / "Acme" / "Loan_2026-11-04"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text("{not json", encoding="utf-8")
    payload = workdir / "payload.json"
    payload.write_text(json.dumps(_payload()), encoding="utf-8")
    result = _run_cli("--company", "Acme", "--proposal", "Loan", "--input-file", payload, cwd=workdir)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert result.stderr.count("\n") == 1
    assert "Traceback" not in result.stderr
