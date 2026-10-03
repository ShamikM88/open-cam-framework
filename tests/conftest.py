import os
import sys
from pathlib import Path

import builtins

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# Hypothesis profiles (tests/test_properties.py, issue #143). CI and a plain `pytest` use "ci": a bounded number
# of examples with a fixed seed (derandomize) and no stored example database, so a run is the same run every time.
# `HYPOTHESIS_PROFILE=explore pytest tests/test_properties.py` searches harder with random seeds (and keeps a
# .hypothesis/ database of failures, which is git-ignored) -- use it when changing the code those tests guard.
from hypothesis import HealthCheck, settings  # noqa: E402  (after the sys.path line above on purpose)

settings.register_profile("ci", max_examples=60, derandomize=True, deadline=None, database=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("explore", max_examples=1000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


def pytest_addoption(parser):
    parser.addoption("--update-snapshots", action="store_true", default=False,
                     help="rewrite the golden files in tests/snapshots/ from the current output (issue #145); "
                          "review the resulting diff before committing. CI never passes this.")


@pytest.fixture
def update_snapshots(request):
    return request.config.getoption("--update-snapshots")


@pytest.fixture
def cp1252_default_open(monkeypatch):
    """Make a text-mode open() with no explicit `encoding` behave as it does
    on a Windows machine (cp1252), whatever platform the tests run on.

    Linux CI defaults to UTF-8, so an encoding bug (issue #137) is invisible
    there; with this fixture a regression -- a reader or writer that forgets
    `encoding=` -- mojibakes or raises on any platform. Binary opens and opens
    that pass an encoding are untouched.

    Limit: this patches `builtins.open` only. `pathlib`'s `Path.read_text()` /
    `write_text()` / `Path.open()` (which call `io.open`), `io.open` and
    `os.fdopen` are not affected, so a regression through those is only caught
    by review, and ruff's PLW1514 only partly (it resolves `open()` and some
    obvious `Path(...)` calls, not arbitrary Path-typed expressions).
    """
    real_open = builtins.open

    def open_with_cp1252_default(file, mode="r", *args, **kwargs):
        has_positional_encoding = len(args) >= 2  # open(file, mode, buffering, encoding)
        if "b" not in mode and kwargs.get("encoding") is None and not has_positional_encoding:
            kwargs["encoding"] = "cp1252"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_with_cp1252_default)
