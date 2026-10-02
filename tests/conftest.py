import sys
from pathlib import Path

import builtins

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def cp1252_default_open(monkeypatch):
    """Make a text-mode open() with no explicit `encoding` behave as it does
    on a Windows machine (cp1252), whatever platform the tests run on.

    Linux CI defaults to UTF-8, so an encoding bug (issue #137) is invisible
    there; with this fixture a regression -- a reader or writer that forgets
    `encoding=` -- mojibakes or raises on any platform. Binary opens and opens
    that pass an encoding are untouched.
    """
    real_open = builtins.open

    def open_with_cp1252_default(file, mode="r", *args, **kwargs):
        has_positional_encoding = len(args) >= 2  # open(file, mode, buffering, encoding)
        if "b" not in mode and kwargs.get("encoding") is None and not has_positional_encoding:
            kwargs["encoding"] = "cp1252"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_with_cp1252_default)
