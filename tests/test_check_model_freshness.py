"""Offline tests for the SDK-table model freshness report."""
import ast
import json
from pathlib import Path
from typing import Literal, Union

import pytest

from check_model_freshness import build_report, main, read_default_model, load_model_sources, render_markdown

REPO_ROOT = Path(__file__).resolve().parents[1]


def fake_model_type(*model_ids):
    """Shape the fake SDK exposes: a Union whose first member is Literal[known model IDs]."""
    return Union[Literal.__getitem__(tuple(model_ids)), str]


def model_item(model_id, source="test:setting"):
    return {"source": source, "model_id": model_id}


def report_for(model_ids, known, deprecated=None):
    return build_report([model_item(model_id) for model_id in model_ids], fake_model_type(*known),
                        {} if deprecated is None else deprecated, "test-sdk")


def test_newer_stable_version_is_reported_only_within_the_same_family():
    report = report_for(
        ["claude-sonnet-5"],
        ["claude-sonnet-5", "claude-sonnet-5-5", "claude-opus-8-9"],
    )
    model = report["models"][0]
    assert model["newer_model"] == "claude-sonnet-5-5"
    assert {f["type"] for f in report["findings"]} == {"newer_version"}
    assert report["summary"]["has_findings"] is True


def test_a_model_with_no_newer_stable_version_has_no_finding():
    report = report_for(
        ["claude-opus-5-5"],
        ["claude-opus-4-6", "claude-opus-5", "claude-opus-5-5", "claude-sonnet-9-9"],
    )
    assert report["models"][0]["newer_model"] is None
    assert report["findings"] == []
    assert report["summary"] == {"models_checked": 1, "finding_count": 0, "has_findings": False}


def test_deprecated_model_includes_the_sdk_end_of_life_date():
    report = report_for(
        ["claude-sonnet-4-5"],
        ["claude-sonnet-4-5"],
        {"claude-sonnet-4-5": "November 30th, 2026"},
    )
    assert report["models"][0]["deprecated"] is True
    assert report["models"][0]["end_of_life"] == "November 30th, 2026"
    assert report["findings"] == [{
        "type": "deprecated_model",
        "source": "test:setting",
        "model_id": "claude-sonnet-4-5",
        "end_of_life": "November 30th, 2026",
        "message": "claude-sonnet-4-5 is deprecated; SDK end-of-life date: November 30th, 2026",
    }]


def test_unparseable_or_unlisted_id_is_reported_as_unknown_not_guessed():
    report = report_for(["claude-sonnet-experimental"], ["claude-sonnet-5", "claude-sonnet-5-5"])
    assert report["models"][0]["status"] == "unknown"
    assert report["models"][0]["newer_model"] is None
    assert any(f["type"] == "unknown_model" and f["model_id"] == "claude-sonnet-experimental"
               for f in report["findings"])


def test_a_newer_model_from_another_family_is_never_suggested():
    report = report_for(
        ["claude-opus-5"],
        ["claude-opus-5", "claude-sonnet-9-9"],
    )
    assert report["models"][0]["newer_model"] is None
    assert all(f.get("suggested_model") != "claude-sonnet-9-9" for f in report["findings"])


@pytest.mark.parametrize("model_id", [
    "claude-sonnet-5-20251001",
    "claude-sonnet-5-latest",
    "claude-opus-5-preview",
])
def test_dated_latest_and_preview_ids_are_not_used_for_version_comparison(model_id):
    report = report_for(
        [model_id],
        [model_id, "claude-sonnet-6", "claude-opus-6"],
    )
    assert report["models"][0]["status"] == "ignored variant"
    assert report["models"][0]["newer_model"] is None
    assert report["findings"] == []


@pytest.mark.parametrize(("model_type", "deprecated_table", "missing_table"), [
    (Union[str, int], {}, "anthropic.types.Model"),
    (fake_model_type("claude-sonnet-5"), [], "anthropic.resources.messages.messages.DEPRECATED_MODELS"),
    (None, [], None),
])
def test_missing_or_reshaped_sdk_tables_are_reported_without_guessing(model_type, deprecated_table, missing_table):
    report = build_report([model_item("claude-sonnet-5")], model_type, deprecated_table, "test-sdk")
    unavailable = [details for details in report["sdk_tables"].values() if details["status"] == "SDK table not found"]
    assert unavailable
    assert all(f["type"] == "sdk_table_not_found" for f in report["findings"][:len(unavailable)])
    markdown = render_markdown(report)
    assert markdown.count("SDK table not found") >= len(unavailable)
    assert len(unavailable) == (1 if missing_table else 2)
    if missing_table:
        key = "known_models" if missing_table == "anthropic.types.Model" else "deprecated_models"
        assert report["sdk_tables"][key]["status"] == "SDK table not found"


def test_missing_deprecation_table_does_not_hide_newer_version_finding():
    report = build_report(
        [model_item("claude-sonnet-5")],
        fake_model_type("claude-sonnet-5", "claude-sonnet-5-5"),
        None,
        "test-sdk",
    )
    assert report["models"][0]["newer_model"] == "claude-sonnet-5-5"
    assert any(f["type"] == "newer_version" for f in report["findings"])
    assert report["sdk_tables"]["deprecated_models"]["status"] == "SDK table not found"


def test_report_notes_that_freshness_depends_on_the_installed_sdk():
    report = build_report([model_item("claude-sonnet-5")], fake_model_type("claude-sonnet-5"), {}, "test-sdk")
    markdown = __import__("check_model_freshness").render_markdown(report)
    assert "only as fresh as the installed Anthropic SDK" in markdown
    assert "does not query the API" in markdown


def test_main_writes_the_documented_json_shape_and_exits_zero(tmp_path, capsys):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "maker_model": "claude-sonnet-5",
        "checker_model": "claude-opus-5",
        "calibrate_model": "claude-haiku-4-5",
    }), encoding="utf-8")
    calibrate_path = tmp_path / "calibrate.py"
    calibrate_path.write_text('_DEFAULT_MODEL = "claude-sonnet-5"\n', encoding="utf-8")
    json_path = tmp_path / "report.json"

    result = main(
        ["--json", str(json_path)],
        settings_path=settings_path,
        calibrate_path=calibrate_path,
        model_type=fake_model_type("claude-sonnet-5", "claude-sonnet-5-5", "claude-opus-5",
                                   "claude-opus-5-5", "claude-haiku-4-5"),
        deprecated_table={},
        sdk_version="test-sdk",
    )

    assert result == 0
    assert "only as fresh as the installed Anthropic SDK" in capsys.readouterr().out
    with open(json_path, encoding="utf-8") as f:
        payload = json.load(f)
    assert set(payload) == {"report_version", "sdk_version", "freshness_note", "sdk_tables", "models", "findings", "summary"}
    assert payload["report_version"] == 1 and payload["sdk_version"] == "test-sdk"
    assert {model["source"] for model in payload["models"]} == {
        "config/settings.json:maker_model",
        "config/settings.json:checker_model",
        "config/settings.json:calibrate_model",
        "scripts/calibrate.py:_DEFAULT_MODEL",
    }
    assert payload["summary"]["finding_count"] == len(payload["findings"])
    assert payload["summary"]["has_findings"] is True


def test_unreadable_configuration_returns_two_and_prints_one_error_line(tmp_path, capsys):
    missing = tmp_path / "missing-settings.json"
    calibrate_path = tmp_path / "calibrate.py"
    calibrate_path.write_text('_DEFAULT_MODEL = "claude-sonnet-5"\n', encoding="utf-8")

    result = main(
        [],
        settings_path=missing,
        calibrate_path=calibrate_path,
        model_type=fake_model_type("claude-sonnet-5"),
        deprecated_table={},
        sdk_version="test-sdk",
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.startswith("error: cannot read configuration")
    assert captured.err.count("\n") == 1


@pytest.mark.parametrize("source", [
    "_DEFAULT_MODEL = 123\n",
    "DEFAULT_MODEL = 'claude-sonnet-5'\n",
    "_DEFAULT_MODEL = 'one'\n_DEFAULT_MODEL = 'two'\n",
])
def test_default_model_ast_reader_rejects_missing_or_non_string_or_duplicate_assignment(tmp_path, source):
    path = tmp_path / "calibrate.py"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="_DEFAULT_MODEL"):
        read_default_model(path)


def test_load_model_sources_reads_settings_but_never_imports_calibrate(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"maker_model": "claude-sonnet-5"}), encoding="utf-8")
    calibrate_path = tmp_path / "calibrate.py"
    calibrate_path.write_text('_DEFAULT_MODEL = "claude-sonnet-5"\nraise RuntimeError("must not run")\n',
                              encoding="utf-8")
    sources = load_model_sources(settings_path, calibrate_path)
    assert sources == [
        {"source": "config/settings.json:maker_model", "model_id": "claude-sonnet-5"},
        {"source": "scripts/calibrate.py:_DEFAULT_MODEL", "model_id": "claude-sonnet-5"},
    ]


@pytest.mark.parametrize("module_name", ["orchestrator.py", "calibrate.py", "run_evals.py"])
def test_runtime_entry_points_do_not_import_model_freshness_diagnostic(module_name):
    path = REPO_ROOT / "scripts" / module_name
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert not any("check_model_freshness" in name for name in imports)
