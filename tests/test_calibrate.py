"""Tests for scripts/calibrate.py's model configuration (issue #54).

calibrate.py used to hardcode `model="claude-3-7-sonnet-20250219"` in both
of its Anthropic calls, never reading config/settings.json at all -- the
same class of gap issue #34 already fixed for orchestrator.py. These tests
cover _resolve_calibrate_model()'s resolution order in isolation, then
prove end-to-end that run_calibration() actually passes the resolved model
into both real API calls (not just that the resolver function returns the
right string).
"""
import json
import os
from types import SimpleNamespace

import pytest

import calibrate
from calibrate import _resolve_calibrate_model, run_calibration


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


class MockClient:
    """Stand-in for calibrate.py's module-level `client` -- records every
    .messages.create() call's kwargs and returns a canned response, so
    run_calibration() can be exercised without a real Anthropic call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.responses.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


# ---------------------------------------------------------------------------
# _resolve_calibrate_model(): resolution order is calibrate_model ->
# maker_model -> _DEFAULT_MODEL, with a soft (non-raising) fallback on a
# missing/malformed config -- deliberately softer than orchestrator.py's
# fail-loud _resolve_maker_checker_config(), since calibration is a one-time
# setup step, not drafting a real credit memo.
# ---------------------------------------------------------------------------

def test_resolve_calibrate_model_defaults_when_no_config_file(project_root):
    assert _resolve_calibrate_model() == calibrate._DEFAULT_MODEL


def test_resolve_calibrate_model_falls_back_to_maker_model(project_root):
    (project_root / "config" / "settings.json").write_text(
        json.dumps({"maker_model": "claude-maker-model"}), encoding="utf-8",
    )
    assert _resolve_calibrate_model() == "claude-maker-model"


def test_resolve_calibrate_model_uses_explicit_override_over_maker_model(project_root):
    (project_root / "config" / "settings.json").write_text(json.dumps({
        "maker_model": "claude-maker-model",
        "calibrate_model": "claude-calibrate-model",
    }), encoding="utf-8")
    assert _resolve_calibrate_model() == "claude-calibrate-model"


def test_resolve_calibrate_model_defaults_on_malformed_settings(project_root):
    """Unlike orchestrator.py's _resolve_maker_checker_config(), this must
    NOT raise -- a friendlier default, since calibration isn't drafting a
    real credit memo."""
    (project_root / "config" / "settings.json").write_text("{not valid json", encoding="utf-8")
    assert _resolve_calibrate_model() == calibrate._DEFAULT_MODEL


# ---------------------------------------------------------------------------
# run_calibration(): end-to-end proof that the resolved model actually
# reaches both client.messages.create() calls, matching test_orchestrator.py's
# own test_run_pipeline_uses_a_different_model_for_maker_and_checker_calls
# pattern -- not just that the resolver function returns the right string.
# ---------------------------------------------------------------------------

def test_run_calibration_uses_the_configured_model_in_both_api_calls(project_root, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    (project_root / "config" / "settings.json").write_text(
        json.dumps({"maker_model": "claude-configured-model"}), encoding="utf-8",
    )
    monkeypatch.setattr(calibrate, "_read_sample_text", lambda: "Sample CAM text content.")
    mock_client = MockClient(["Extracted style guide text.", "# Derived Template"])
    monkeypatch.setattr(calibrate, "client", mock_client)

    run_calibration("corporate_credit")

    assert len(mock_client.calls) == 2
    assert mock_client.calls[0]["model"] == "claude-configured-model"
    assert mock_client.calls[1]["model"] == "claude-configured-model"
    assert os.path.isfile(project_root / "config" / "style_guide.md")
