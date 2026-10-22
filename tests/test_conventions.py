"""Tests for scripts/conventions.py: persisted analyst-confirmed spreading
conventions, borrower-specific (deals/<Company>/_conventions.json) and
enterprise-wide (config/spreading_conventions.json).

Mirrors test_state_manager.py's tmp_path + base_dir isolation pattern
(rather than monkeypatch.chdir) since this module reuses state_manager's
own _FileLock/sanitize_path_component.
"""
import json
import os
import threading

import pytest

from conventions import (
    company_convention_path,
    enterprise_convention_path,
    read_company_convention,
    read_enterprise_convention,
    write_company_convention,
    write_enterprise_convention,
)


# ---------------------------------------------------------------------------
# Borrower-specific scope
# ---------------------------------------------------------------------------

def test_read_company_convention_returns_none_when_no_file_exists(tmp_path):
    assert read_company_convention("Acme Corp", base_dir=str(tmp_path)) is None


def test_write_company_convention_creates_the_file_with_expected_fields(tmp_path):
    base = str(tmp_path)
    record = write_company_convention(
        "Acme Corp", financials_source="analyst-supplied",
        note="Depreciation embedded in Cost of Goods Sold",
        confirmed_date="2026-01-15", proposal="Fleet Loan", base_dir=base,
    )

    assert record["financials_source_default"] == "analyst-supplied"
    assert record["financials_source_note"] == "Depreciation embedded in Cost of Goods Sold"
    assert record["confirmed_date"] == "2026-01-15"
    assert record["history"] == [{
        "financials_source": "analyst-supplied",
        "note": "Depreciation embedded in Cost of Goods Sold",
        "confirmed_date": "2026-01-15",
        "proposal": "Fleet Loan",
    }]

    path = company_convention_path("Acme Corp", base_dir=base)
    assert os.path.isfile(path)
    assert path == os.path.join(base, "deals", "Acme Corp", "_conventions.json")

    assert read_company_convention("Acme Corp", base_dir=base) == record


def test_write_company_convention_leaves_no_stray_temp_file_behind(tmp_path):
    base = str(tmp_path)
    write_company_convention("Acme Corp", financials_source="analyst-supplied", note="A note",
                              confirmed_date="2026-01-15", base_dir=base)

    company_dir = os.path.dirname(company_convention_path("Acme Corp", base_dir=base))
    assert os.listdir(company_dir) == ["_conventions.json"]


def test_write_company_convention_overwrites_current_and_appends_history(tmp_path):
    base = str(tmp_path)
    write_company_convention("Acme Corp", financials_source="analyst-supplied", note="First note",
                              confirmed_date="2026-01-15", base_dir=base)
    second = write_company_convention("Acme Corp", financials_source="analyst-supplied", note="Corrected note",
                                       confirmed_date="2026-02-01", base_dir=base)

    assert second["financials_source_note"] == "Corrected note"
    assert second["confirmed_date"] == "2026-02-01"
    assert len(second["history"]) == 2
    assert [e["note"] for e in second["history"]] == ["First note", "Corrected note"]


def test_read_company_convention_raises_valueerror_on_corrupted_json(tmp_path):
    base = str(tmp_path)
    path = company_convention_path("Acme Corp", base_dir=base)
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    with pytest.raises(ValueError, match="corrupted"):
        read_company_convention("Acme Corp", base_dir=base)


def test_company_convention_path_sanitizes_company(tmp_path):
    with pytest.raises(ValueError):
        company_convention_path("../escape", base_dir=str(tmp_path))


def test_write_company_convention_defaults_confirmed_date_to_today_when_omitted(tmp_path):
    from datetime import date
    record = write_company_convention("Acme Corp", financials_source="analyst-supplied",
                                       note="A note", base_dir=str(tmp_path))
    assert record["confirmed_date"] == date.today().isoformat()


def test_concurrent_write_company_convention_calls_do_not_clobber_each_other(tmp_path):
    base = str(tmp_path)

    def writer(note):
        write_company_convention("Acme Corp", financials_source="analyst-supplied", note=note,
                                  confirmed_date="2026-01-15", base_dir=base)

    t1 = threading.Thread(target=writer, args=("Note A",))
    t2 = threading.Thread(target=writer, args=("Note B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    record = read_company_convention("Acme Corp", base_dir=base)
    assert len(record["history"]) == 2  # neither call's entry was lost
    assert {"Note A", "Note B"} == {e["note"] for e in record["history"]}


# ---------------------------------------------------------------------------
# Enterprise-wide scope
# ---------------------------------------------------------------------------

def test_read_enterprise_convention_returns_none_when_no_file_exists(tmp_path):
    assert read_enterprise_convention(base_dir=str(tmp_path)) is None


def test_write_enterprise_convention_creates_the_file_with_expected_fields(tmp_path):
    base = str(tmp_path)
    record = write_enterprise_convention(
        financials_source="analyst-supplied",
        note="This institution always supplies its own pre-spread figures",
        confirmed_date="2026-01-15", base_dir=base,
    )

    assert record["financials_source_default"] == "analyst-supplied"
    assert record["history"] == [{
        "financials_source": "analyst-supplied",
        "note": "This institution always supplies its own pre-spread figures",
        "confirmed_date": "2026-01-15",
    }]
    assert "proposal" not in record["history"][0]  # no company/proposal concept at this scope

    path = enterprise_convention_path(base_dir=base)
    assert os.path.isfile(path)
    assert path == os.path.join(base, "config", "spreading_conventions.json")

    assert read_enterprise_convention(base_dir=base) == record


def test_write_enterprise_convention_leaves_no_stray_temp_file_behind(tmp_path):
    base = str(tmp_path)
    write_enterprise_convention(financials_source="analyst-supplied", note="A note",
                                 confirmed_date="2026-01-15", base_dir=base)

    config_dir = os.path.dirname(enterprise_convention_path(base_dir=base))
    assert os.listdir(config_dir) == ["spreading_conventions.json"]


def test_write_enterprise_convention_overwrites_current_and_appends_history(tmp_path):
    base = str(tmp_path)
    write_enterprise_convention(financials_source="analyst-supplied", note="First",
                                 confirmed_date="2026-01-15", base_dir=base)
    second = write_enterprise_convention(financials_source="analyst-supplied", note="Second",
                                          confirmed_date="2026-02-01", base_dir=base)

    assert second["financials_source_note"] == "Second"
    assert len(second["history"]) == 2


def test_read_enterprise_convention_raises_valueerror_on_corrupted_json(tmp_path):
    base = str(tmp_path)
    path = enterprise_convention_path(base_dir=base)
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    with pytest.raises(ValueError, match="corrupted"):
        read_enterprise_convention(base_dir=base)


def test_concurrent_write_enterprise_convention_calls_do_not_clobber_each_other(tmp_path):
    base = str(tmp_path)

    def writer(note):
        write_enterprise_convention(financials_source="analyst-supplied", note=note,
                                     confirmed_date="2026-01-15", base_dir=base)

    t1 = threading.Thread(target=writer, args=("Note A",))
    t2 = threading.Thread(target=writer, args=("Note B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    record = read_enterprise_convention(base_dir=base)
    assert len(record["history"]) == 2
    assert {"Note A", "Note B"} == {e["note"] for e in record["history"]}


# ---------------------------------------------------------------------------
# The two scopes never interfere with each other
# ---------------------------------------------------------------------------

def test_company_and_enterprise_scopes_do_not_collide(tmp_path):
    base = str(tmp_path)
    write_company_convention("Acme Corp", financials_source="analyst-supplied", note="Borrower note",
                              confirmed_date="2026-01-15", base_dir=base)

    assert read_enterprise_convention(base_dir=base) is None  # untouched by the company-scope write

    write_enterprise_convention(financials_source="framework-computed", note="Enterprise note",
                                 confirmed_date="2026-01-16", base_dir=base)

    company_record = read_company_convention("Acme Corp", base_dir=base)
    enterprise_record = read_enterprise_convention(base_dir=base)
    assert company_record["financials_source_note"] == "Borrower note"
    assert enterprise_record["financials_source_note"] == "Enterprise note"


# ---------------------------------------------------------------------------
# CLI (__main__) smoke tests
# ---------------------------------------------------------------------------

def test_cli_write_then_read_round_trips_for_company_scope(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    import sys
    import runpy

    argv = ["conventions.py", "--company", "Acme Corp", "--write",
            "--financials-source", "analyst-supplied", "--note", "A note",
            "--confirmed-date", "2026-01-15"]
    monkeypatch.setattr(sys, "argv", argv)
    runpy.run_module("conventions", run_name="__main__")
    write_output = json.loads(capsys.readouterr().out)
    assert write_output["financials_source_note"] == "A note"

    argv = ["conventions.py", "--company", "Acme Corp", "--read"]
    monkeypatch.setattr(sys, "argv", argv)
    runpy.run_module("conventions", run_name="__main__")
    read_output = json.loads(capsys.readouterr().out)
    assert read_output["found"] is True
    assert read_output["convention"]["financials_source_note"] == "A note"


def test_cli_read_on_empty_enterprise_scope_reports_not_found(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    import sys
    import runpy

    argv = ["conventions.py", "--enterprise", "--read"]
    monkeypatch.setattr(sys, "argv", argv)
    runpy.run_module("conventions", run_name="__main__")
    output = json.loads(capsys.readouterr().out)
    assert output == {"found": False, "convention": None}


def test_cli_rejects_write_without_financials_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    import runpy

    argv = ["conventions.py", "--enterprise", "--write"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        runpy.run_module("conventions", run_name="__main__")


def test_cli_rejects_proposal_without_company(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    import runpy

    argv = ["conventions.py", "--enterprise", "--write", "--financials-source", "analyst-supplied",
            "--proposal", "Fleet Loan"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        runpy.run_module("conventions", run_name="__main__")
