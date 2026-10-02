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

# ---------------------------------------------------------------------------
# Sample-length overflow (issue #109): samples beyond the per-call limit are
# either explicitly ignored or split into parts, processed, and merged --
# decided before any API call or write, never silently truncated.
# ---------------------------------------------------------------------------

class FakeStdin:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


class RecordingClient:
    """Returns "RESULT-<n>" for the n-th call; optionally raises on call
    `fail_on` (1-based) to simulate an API failure partway through a run.
    Records every call's prompt text."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_on == len(self.calls):
            raise RuntimeError("simulated API failure")
        return SimpleNamespace(content=[SimpleNamespace(text=f"RESULT-{len(self.calls)}")])

    def prompts(self):
        return [c["messages"][0]["content"] for c in self.calls]


def _long_text(paragraphs=120):
    # ~ 30k+ characters of paragraph-structured text, several chunks' worth.
    return "".join(f"Paragraph {i}: " + "sentence about credit risk. " * 8 + "\n\n" for i in range(paragraphs))


@pytest.fixture
def overflow_env(project_root, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    text = _long_text()
    assert len(text) > 2 * calibrate.CHUNK_CHAR_LIMIT
    monkeypatch.setattr(calibrate, "_read_sample_text", lambda: text)
    client = RecordingClient()
    monkeypatch.setattr(calibrate, "client", client)
    return SimpleNamespace(root=project_root, text=text, client=client, monkeypatch=monkeypatch)


def _answers(monkeypatch, *answers):
    queue = list(answers)
    asked = []

    def fake_input(prompt=""):
        asked.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def test_split_into_chunks_is_lossless_and_within_the_limit():
    text = _long_text()
    chunks = calibrate._split_into_chunks(text)
    assert "".join(chunks) == text
    assert len(chunks) > 1
    assert all(len(c) <= calibrate.CHUNK_CHAR_LIMIT for c in chunks)


def test_split_into_chunks_prefers_paragraph_boundaries():
    chunks = calibrate._split_into_chunks(_long_text())
    assert all(c.endswith("\n\n") for c in chunks[:-1])


def test_split_into_chunks_hard_cuts_text_with_no_whitespace_without_losing_any():
    text = "x" * 30_000
    chunks = calibrate._split_into_chunks(text)
    assert "".join(chunks) == text
    assert all(len(c) <= calibrate.CHUNK_CHAR_LIMIT for c in chunks)


def test_split_into_chunks_short_and_empty_text():
    assert calibrate._split_into_chunks("short") == ["short"]
    assert calibrate._split_into_chunks("") == []


def test_samples_within_the_limit_never_prompt(project_root, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(calibrate, "_read_sample_text", lambda: "x" * calibrate.CHUNK_CHAR_LIMIT)
    client = RecordingClient()
    monkeypatch.setattr(calibrate, "client", client)
    monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(True))
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not prompt"))

    run_calibration("corporate_credit")

    assert len(client.calls) == 2


def test_prompt_is_asked_before_any_api_call_or_file_write(overflow_env):
    mp, client = overflow_env.monkeypatch, overflow_env.client
    mp.setattr(calibrate.sys, "stdin", FakeStdin(True))
    asked = []

    def checking_input(prompt=""):
        asked.append(prompt)
        assert client.calls == []
        assert not (overflow_env.root / "config" / "style_guide.md").exists()
        return "i"

    mp.setattr("builtins.input", checking_input)
    run_calibration("corporate_credit")

    assert asked  # the prompt really was shown (and the assertions above ran)


def test_ignore_uses_only_the_first_limit_characters(overflow_env):
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(True))
    _answers(overflow_env.monkeypatch, "i")

    run_calibration("corporate_credit")

    prompts = overflow_env.client.prompts()
    assert len(prompts) == 2
    assert overflow_env.text[:calibrate.CHUNK_CHAR_LIMIT] in prompts[0]
    assert overflow_env.text[:calibrate.CHUNK_CHAR_LIMIT + 200] not in prompts[0]


def test_split_processes_every_part_then_merges_and_loses_no_text(overflow_env):
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(True))
    _answers(overflow_env.monkeypatch, "s")
    chunks = calibrate._split_into_chunks(overflow_env.text)

    run_calibration("corporate_credit")

    client = overflow_env.client
    k = len(chunks)
    assert len(client.calls) == 2 * (k + 1)  # k parts + 1 merge, for style and template
    style_part_prompts = client.prompts()[:k]
    assert all(chunk in prompt for chunk, prompt in zip(chunks, style_part_prompts))
    # The merge call receives every part's result.
    merge_prompt = client.prompts()[k]
    assert all(f"RESULT-{i}" in merge_prompt for i in range(1, k + 1))
    # Written results are the merge calls' output.
    style = (overflow_env.root / "config" / "style_guide.md").read_text(encoding="utf-8")
    assert style.endswith(f"RESULT-{k + 1}")
    template = overflow_env.root / "templates" / "local" / "cam" / "corporate_credit_cam.md"
    assert template.read_text(encoding="utf-8") == f"RESULT-{2 * k + 2}"


def test_invalid_answer_reprompts(overflow_env):
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(True))
    asked = _answers(overflow_env.monkeypatch, "maybe", "", "i")

    run_calibration("corporate_credit")

    assert len(asked) == 3
    assert len(overflow_env.client.calls) == 2


def test_no_terminal_falls_back_to_split_without_prompting(overflow_env):
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(False))
    overflow_env.monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not prompt"))
    k = len(calibrate._split_into_chunks(overflow_env.text))

    run_calibration("corporate_credit")

    assert len(overflow_env.client.calls) == 2 * (k + 1)


def test_closed_stdin_at_the_prompt_falls_back_to_split(overflow_env):
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(True))

    def eof(*_):
        raise EOFError

    overflow_env.monkeypatch.setattr("builtins.input", eof)
    k = len(calibrate._split_into_chunks(overflow_env.text))

    run_calibration("corporate_credit")

    assert len(overflow_env.client.calls) == 2 * (k + 1)


def test_on_overflow_flag_skips_the_prompt(overflow_env):
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(True))
    overflow_env.monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not prompt"))

    run_calibration("corporate_credit", on_overflow="ignore")
    assert len(overflow_env.client.calls) == 2

    k = len(calibrate._split_into_chunks(overflow_env.text))
    overflow_env.client.calls.clear()
    run_calibration("corporate_credit", on_overflow="split")
    assert len(overflow_env.client.calls) == 2 * (k + 1)


def test_a_failure_partway_through_leaves_existing_files_untouched(overflow_env):
    style = overflow_env.root / "config" / "style_guide.md"
    style.write_text("EXISTING STYLE GUIDE", encoding="utf-8")
    template = overflow_env.root / "templates" / "local" / "cam" / "corporate_credit_cam.md"
    template.parent.mkdir(parents=True)
    template.write_text("EXISTING TEMPLATE", encoding="utf-8")
    k = len(calibrate._split_into_chunks(overflow_env.text))
    # Style fully succeeds; the template pass fails on its second part.
    overflow_env.client.fail_on = (k + 1) + 2
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(False))

    with pytest.raises(RuntimeError, match="simulated API failure"):
        run_calibration("corporate_credit")

    assert style.read_text(encoding="utf-8") == "EXISTING STYLE GUIDE"
    assert template.read_text(encoding="utf-8") == "EXISTING TEMPLATE"


def test_large_sample_sets_are_merged_hierarchically(overflow_env):
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(False))
    overflow_env.monkeypatch.setattr(calibrate, "MERGE_CHAR_BUDGET", 10)  # force multi-round merging
    k = len(calibrate._split_into_chunks(overflow_env.text))

    run_calibration("corporate_credit")

    # 3 parts with a tiny budget: round 1 merges parts 1+2 (part 3 is a lone
    # leftover and passes through without a call), round 2 merges that result
    # with part 3 -- two merge calls per artefact, every one over >= 2 parts.
    assert k == 3
    merge_prompts = [p for p in overflow_env.client.prompts() if "Merge them into ONE" in p]
    assert len(merge_prompts) == 4
    assert all("Part 2:" in p for p in merge_prompts)
    assert len(overflow_env.client.calls) == 2 * (k + 2)
    assert (overflow_env.root / "config" / "style_guide.md").exists()


def test_the_merge_prompts_carry_the_grounding_and_no_real_data_rules():
    assert "ZERO real data" in calibrate.TEMPLATE_MERGE_PROMPT
    assert "[placeholder]" in calibrate.TEMPLATE_MERGE_PROMPT
    assert "editorial" in calibrate.STYLE_MERGE_PROMPT


def test_original_sample_files_are_never_modified(overflow_env):
    samples = overflow_env.root / "inputs" / "calibration_samples"
    samples.mkdir(parents=True)
    originals = {}
    for name in ("a.pdf", "b.pdf"):
        (samples / name).write_bytes(b"%PDF-1.4 original bytes of " + name.encode())
        originals[name] = (samples / name).read_bytes()
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(False))

    for mode in ("split", "ignore"):
        run_calibration("corporate_credit", on_overflow=mode)

    assert sorted(p.name for p in samples.iterdir()) == ["a.pdf", "b.pdf"]
    assert {p.name: p.read_bytes() for p in samples.iterdir()} == originals


def test_mock_mode_reports_the_part_count_without_prompting_or_calling_the_api(overflow_env, capsys):
    overflow_env.monkeypatch.setattr(calibrate.sys, "stdin", FakeStdin(True))
    overflow_env.monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not prompt"))
    k = len(calibrate._split_into_chunks(overflow_env.text))

    run_calibration("corporate_credit", mock=True)

    assert overflow_env.client.calls == []
    assert f"split them into {k} parts" in capsys.readouterr().out
