import json
import os

import docx
import openpyxl
import pytest

from deal_export import (
    _downside_financial_data_from_state,
    _financial_data_from_state,
    _supplied_row_values,
    export_deal,
    main,
)


def _write(path, content="content"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_a_bare_call_without_date_str_finds_the_state_of_an_earlier_dated_folder(tmp_path):
    """The state is read BEFORE today's output folder is created. Before, makedirs ran first and the auto-discovery
    (latest dated folder wins) then found the new, empty folder, so the workbook came out blank (#171 review)."""
    base = str(tmp_path)
    earlier = os.path.join(base, "deals", "Acme Corp", "Fleet Loan_2026-01-01")
    os.makedirs(earlier)
    with open(os.path.join(earlier, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"financials": {"FY-Current": {"raw": {"revenue": 4321}}}}, f)
    _write(os.path.join(base, "templates", "cam", "asset_finance_cam.md"))

    output_dir = export_deal("Acme Corp", "Fleet Loan", "asset_finance", "# Draft", base_dir=base)   # no date_str

    assert os.path.basename(output_dir) != "Fleet Loan_2026-01-01"           # a new dated folder for today...
    sheet = openpyxl.load_workbook(os.path.join(output_dir, "Acme Corp_Fleet Loan_Spreading.xlsx"))["Financial Spreading"]
    revenue_row = next(r for r in sheet.iter_rows(values_only=True) if r[0] == "Revenue")
    assert 4321 in revenue_row                                              # ...but the earlier folder's figures


def test_creates_dated_output_folder(tmp_path):
    base = str(tmp_path)
    _write(os.path.join(base, "templates", "cam", "asset_finance_cam.md"))

    output_dir = export_deal(
        "Acme Corp", "Fleet Loan", "asset_finance", "# Draft",
        date_str="2026-01-15", base_dir=base,
    )

    assert output_dir == os.path.join(base, "deals", "Acme Corp", "Fleet Loan_2026-01-15")
    assert os.path.isdir(output_dir)


def test_exports_docx_and_xlsx(tmp_path):
    base = str(tmp_path)
    _write(os.path.join(base, "templates", "cam", "asset_finance_cam.md"))

    output_dir = export_deal(
        "Acme Corp", "Fleet Loan", "asset_finance",
        "# Acme Corp CAM\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n",
        date_str="2026-01-15", base_dir=base,
    )

    docx_path = os.path.join(output_dir, "Acme Corp_Fleet Loan_CAM.docx")
    xlsx_path = os.path.join(output_dir, "Acme Corp_Fleet Loan_Spreading.xlsx")
    assert os.path.isfile(docx_path)
    assert os.path.isfile(xlsx_path)

    doc = docx.Document(docx_path)
    assert doc.paragraphs[0].text == "Acme Corp CAM"
    assert len(doc.tables) == 1

    wb = openpyxl.load_workbook(xlsx_path)
    assert "Financial Spreading" in wb.sheetnames


def test_auto_saves_new_template_when_none_exists(tmp_path):
    base = str(tmp_path)
    # No templates/cam/ or templates/local/cam/ entry for this type at all.

    export_deal(
        "Acme Corp", "Fleet Loan", "brand_new_type", "# Brand New Draft",
        date_str="2026-01-15", base_dir=base,
    )

    new_template_path = os.path.join(base, "templates", "local", "cam", "brand_new_type_cam.md")
    assert os.path.isfile(new_template_path)
    with open(new_template_path, encoding="utf-8") as f:
        assert f.read() == "# Brand New Draft"


def test_does_not_overwrite_an_existing_local_override(tmp_path):
    base = str(tmp_path)
    override_path = os.path.join(base, "templates", "local", "cam", "asset_finance_cam.md")
    _write(override_path, "# Existing override -- must not change")

    export_deal(
        "Acme Corp", "Fleet Loan", "asset_finance", "# A totally different draft",
        date_str="2026-01-15", base_dir=base,
    )

    with open(override_path, encoding="utf-8") as f:
        assert f.read() == "# Existing override -- must not change"


def test_does_not_touch_local_override_when_shipped_default_exists(tmp_path):
    base = str(tmp_path)
    _write(os.path.join(base, "templates", "cam", "asset_finance_cam.md"))

    export_deal(
        "Acme Corp", "Fleet Loan", "asset_finance", "# Draft",
        date_str="2026-01-15", base_dir=base,
    )

    local_override_path = os.path.join(base, "templates", "local", "cam", "asset_finance_cam.md")
    assert not os.path.exists(local_override_path)


def test_export_deal_rejects_unsafe_company(tmp_path):
    base = str(tmp_path)
    with pytest.raises(ValueError):
        export_deal("C:\\Windows\\Temp\\evil", "Fleet Loan", "asset_finance", "# Draft", base_dir=base)


def test_export_deal_rejects_unsafe_deal_type(tmp_path):
    """deal_type flows into template_resolver's path building
    (templates/local/cam/<deal_type>_cam.md) the same way company/proposal
    flow into the deals/ path -- it needs the same protection."""
    base = str(tmp_path)
    with pytest.raises(ValueError):
        export_deal("Acme Corp", "Fleet Loan", "../../evil", "# Draft", base_dir=base)


def test_export_deal_rejects_unsafe_date_str(tmp_path):
    base = str(tmp_path)
    with pytest.raises(ValueError):
        export_deal("Acme Corp", "Fleet Loan", "asset_finance", "# Draft",
                     date_str="../../evil", base_dir=base)


def test_financial_data_from_state_ignores_non_dict_period_value():
    """A period value that isn't a dict (e.g. malformed/legacy state.json)
    must not crash -- it degrades to an empty raw-figures dict for that
    period instead."""
    state = {"financials": {"FY-Current": "not a dict", "FY-1": {"raw": {"revenue": 100}}}}
    result = _financial_data_from_state(state)
    assert result == {"FY-Current": {}, "FY-1": {"revenue": 100}}


# ---------------------------------------------------------------------------
# _financial_data_from_state()'s financials_source branch (issue #121): an
# analyst-supplied deal's raw figures live in their own separate
# analyst_supplied_financials store, never blended with the
# framework-computed path's financials[period]["raw"].
# ---------------------------------------------------------------------------

def test_financial_data_from_state_reads_analyst_supplied_store_when_flagged():
    state = {
        "financials_source": "analyst-supplied",
        "analyst_supplied_financials": {"FY-Current": {"revenue": 500}},
        # Deliberately also present, to prove it's ignored for this deal --
        # an analyst-supplied deal's financials[period] never has a "raw"
        # sub-key populated in practice, but this guards against a future
        # bug that blends the two if it somehow were.
        "financials": {"FY-Current": {"raw": {"revenue": 999}}},
    }
    assert _financial_data_from_state(state) == {"FY-Current": {"revenue": 500}}


def test_financial_data_from_state_reads_framework_computed_store_by_default():
    """financials_source absent, or anything other than "analyst-supplied",
    must keep reading the pre-existing financials[period]["raw"] path --
    the framework-computed behavior this function already had before #121,
    unchanged."""
    state = {"financials": {"FY-Current": {"raw": {"revenue": 500}}}}
    assert _financial_data_from_state(state) == {"FY-Current": {"revenue": 500}}

    state["financials_source"] = "framework-computed"
    assert _financial_data_from_state(state) == {"FY-Current": {"revenue": 500}}


def test_financial_data_from_state_analyst_supplied_with_no_raw_breakdown_is_empty():
    """An analyst-supplied deal where no raw breakdown was ever given (the
    common case -- it's optional) must export blank raw-input cells rather
    than falling back to financials[period]["raw"], which this mode never
    populates anyway."""
    state = {"financials_source": "analyst-supplied", "financials": {"FY-Current": {}}}
    assert _financial_data_from_state(state) == {}


# ---------------------------------------------------------------------------
# _downside_financial_data_from_state(): the exported workbook's FY+1/FY+2/
# FY+3 "(Downside)" columns (see issue #38 / spreading_builder.py's
# COL_TO_PERIOD_KEY) are populated from this, mirroring how
# _financial_data_from_state() feeds the historical/forward base-case
# columns.
# ---------------------------------------------------------------------------

def test_downside_financial_data_from_state_reduces_downside_case_financials():
    state = {
        "downside_case": {
            "financials": {"FY+1": {"raw": {"revenue": 950}}, "FY+2": {"raw": {"revenue": 1000}}},
            "ratios": {"FY+1": {"dscr": 1.02}},
        },
    }
    result = _downside_financial_data_from_state(state)
    assert result == {"FY+1": {"revenue": 950}, "FY+2": {"revenue": 1000}}


def test_downside_financial_data_from_state_empty_when_no_downside_case():
    """No stress_assumptions were ever supplied for this deal -- state.json
    has no "downside_case" key at all (or it's {}) -- must degrade to an
    empty dict, not raise."""
    assert _downside_financial_data_from_state({}) == {}
    assert _downside_financial_data_from_state({"downside_case": {}}) == {}


def test_downside_financial_data_from_state_ignores_non_dict_downside_case():
    """Malformed/legacy state.json where "downside_case" itself isn't a dict
    must not crash."""
    assert _downside_financial_data_from_state({"downside_case": "not a dict"}) == {}


def test_export_deal_populates_downside_columns_in_the_exported_workbook(tmp_path):
    """End-to-end: a deal whose state.json carries a downside_case actually
    gets its FY+1 (Downside) column populated in the exported .xlsx --
    proves the full state.json -> deal_export.py -> spreading_builder.py
    wiring, not just the extraction function in isolation."""
    from state_manager import write_state

    base = str(tmp_path)
    _write(os.path.join(base, "templates", "cam", "asset_finance_cam.md"))
    write_state(
        "Acme Corp", "Fleet Loan", date_str="2026-01-15", base_dir=base,
        financials={"FY-Current": {"raw": {"revenue": 1000}}},
        downside_case={"financials": {"FY+1": {"raw": {"revenue": 950}}}},
    )

    output_dir = export_deal(
        "Acme Corp", "Fleet Loan", "asset_finance", "# Draft",
        date_str="2026-01-15", base_dir=base,
    )

    wb = openpyxl.load_workbook(os.path.join(output_dir, "Acme Corp_Fleet Loan_Spreading.xlsx"))
    ws = wb["Financial Spreading"]
    label_to_row = {ws.cell(row=r, column=1).value: r for r in range(1, ws.max_row + 1)}
    assert ws.cell(row=label_to_row["Revenue"], column=8).value == 950  # H = FY+1 (Downside)


# ---------------------------------------------------------------------------
# CLI (/assemble's Bash step): which dated folder does it export into? Issue
# #97 -- a multi-day deal's output must land next to its own state.json, not
# in a new folder dated whenever the export happened to run.
# ---------------------------------------------------------------------------

def _cli_draft(tmp_path):
    draft = tmp_path / "draft.md"
    draft.write_text("# Draft", encoding="utf-8")
    return str(draft)


def test_cli_exports_into_the_deals_existing_dated_folder(tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "deals" / "Acme Corp" / "Fleet Loan_2026-01-10"
    existing.mkdir(parents=True)
    (existing / "state.json").write_text("{}", encoding="utf-8")

    main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--type", "asset_finance",
          "--draft", _cli_draft(tmp_path)])

    assert (existing / "Acme Corp_Fleet Loan_CAM.docx").exists()
    assert (existing / "Acme Corp_Fleet Loan_Spreading.xlsx").exists()
    today = datetime.now().strftime("%Y-%m-%d")
    assert not (tmp_path / "deals" / "Acme Corp" / f"Fleet Loan_{today}").exists()


def test_cli_picks_the_most_recent_of_several_existing_dated_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for date in ("2025-01-10", "2026-01-10"):
        (tmp_path / "deals" / "Acme Corp" / f"Fleet Loan_{date}").mkdir(parents=True)

    main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--type", "asset_finance",
          "--draft", _cli_draft(tmp_path)])

    assert (tmp_path / "deals" / "Acme Corp" / "Fleet Loan_2026-01-10" / "Acme Corp_Fleet Loan_CAM.docx").exists()
    assert not (tmp_path / "deals" / "Acme Corp" / "Fleet Loan_2025-01-10" / "Acme Corp_Fleet Loan_CAM.docx").exists()


def test_cli_date_str_flag_overrides_discovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deals" / "Acme Corp" / "Fleet Loan_2026-01-10").mkdir(parents=True)

    main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--type", "asset_finance",
          "--draft", _cli_draft(tmp_path), "--date-str", "2026-03-01"])

    assert (tmp_path / "deals" / "Acme Corp" / "Fleet Loan_2026-03-01" / "Acme Corp_Fleet Loan_CAM.docx").exists()


def test_cli_brand_new_deal_gets_a_folder_dated_today(tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.chdir(tmp_path)

    main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--type", "asset_finance",
          "--draft", _cli_draft(tmp_path)])

    today = datetime.now().strftime("%Y-%m-%d")
    assert (tmp_path / "deals" / "Acme Corp" / f"Fleet Loan_{today}" / "Acme Corp_Fleet Loan_CAM.docx").exists()


def test_cli_rejects_an_unsafe_company_before_touching_the_deals_tree(tmp_path, monkeypatch):
    import deal_export
    monkeypatch.chdir(tmp_path)

    def must_not_resolve(*args, **kwargs):
        pytest.fail("resolve_date_str() (the deals/ glob) ran before sanitization")

    monkeypatch.setattr(deal_export, "resolve_date_str", must_not_resolve)
    with pytest.raises(ValueError):
        main(["--company", "../evil", "--proposal", "Fleet Loan", "--type", "asset_finance",
              "--draft", _cli_draft(tmp_path)])
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("bad", ["foo", "2026-1-5", "2026-01-05-extra", ""])
def test_cli_rejects_a_malformed_date_str(tmp_path, monkeypatch, bad):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--type", "asset_finance",
              "--draft", _cli_draft(tmp_path), "--date-str", bad])
    assert not (tmp_path / "deals").exists()


def test_cli_does_not_export_into_another_deals_prefix_sharing_folder(tmp_path, monkeypatch):
    """Proposal "Fleet" must not claim "Fleet_Q2"'s folder (issue #97 review)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deals" / "Acme" / "Fleet_2026-01-10").mkdir(parents=True)
    (tmp_path / "deals" / "Acme" / "Fleet_Q2_2026-05-01").mkdir(parents=True)

    main(["--company", "Acme", "--proposal", "Fleet", "--type", "asset_finance",
          "--draft", _cli_draft(tmp_path)])

    assert (tmp_path / "deals" / "Acme" / "Fleet_2026-01-10" / "Acme_Fleet_CAM.docx").exists()
    assert not (tmp_path / "deals" / "Acme" / "Fleet_Q2_2026-05-01" / "Acme_Fleet_CAM.docx").exists()


# ---------------------------------------------------------------------------
# _supplied_row_values: what an analyst-supplied period writes into the workbook (issue #219)
# ---------------------------------------------------------------------------

def test_supplied_row_values_keeps_numbers_writes_na_for_a_null_ratio_and_leaves_everything_else_blank():
    """The docstring's contract, row by row. A number (zero included) is written; a ratio recorded as null is N/A, as the
    workbook writes it; a null subtotal, text, a boolean and a key with no workbook row are left out, so the cell stays
    blank. A defect here is a wrong number or a stray "N/A" in the exported workbook."""
    values = _supplied_row_values({
        "ebitda": 1000, "total_assets": 0, "dscr": 2.5,
        "gross_leverage": None,                 # a ratio recorded as null: N/A
        "net_profit": None,                     # a subtotal recorded as null: blank, not "N/A"
        "current_ratio": "n/m",                 # text: blank
        "gearing": True,                        # a boolean is not a number: blank
        "not_a_workbook_row": 5,                # no row for it: ignored
    })
    assert values == {"EBITDA": 1000, "Total Assets": 0, "DSCR": 2.5, "Gross Leverage": "N/A"}


def test_supplied_row_values_takes_the_later_period_dict_and_ignores_a_malformed_one():
    """The forecast columns pass the subtotals and then the ratios (or a base and an override): a later value wins for
    the same row, and anything that is not a dict is skipped rather than failing the export."""
    assert _supplied_row_values({"ebitda": 1}, {"ebitda": 2}) == {"EBITDA": 2}
    assert _supplied_row_values(None, "text", ["ebitda"], {"dscr": 1.5}) == {"DSCR": 1.5}
    assert _supplied_row_values() == {}


# ---------------------------------------------------------------------------
# main(): what the command line prints, defaults to and refuses, in-process (issue #182)
# ---------------------------------------------------------------------------

def _docx_text(path):
    return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)


def test_cli_prints_where_the_files_went_and_they_are_there(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "deals" / "Acme Corp" / "Fleet Loan_2026-01-10"
    existing.mkdir(parents=True)

    main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--type", "asset_finance", "--draft", _cli_draft(tmp_path)])

    out = capsys.readouterr()
    assert out.err == ""
    *notices, line = out.out.splitlines()
    assert len(notices) == 1 and notices[0].startswith("[Auto-Template] No template found for 'asset_finance'")
    assert line.startswith("Done! Files generated in ") and line.endswith("Fleet Loan_2026-01-10")
    assert os.path.samefile(line.removeprefix("Done! Files generated in "), existing)
    assert sorted(p.name for p in existing.iterdir()) == ["Acme Corp_Fleet Loan_CAM.docx", "Acme Corp_Fleet Loan_Spreading.xlsx"]


def test_cli_type_defaults_to_corporate_credit(tmp_path, monkeypatch):
    """With no --type the deal type is corporate_credit: with no template for a type, export_deal auto-saves one named for it."""
    monkeypatch.chdir(tmp_path)
    main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--draft", _cli_draft(tmp_path)])
    saved = sorted(p.name for p in (tmp_path / "templates").rglob("*.md"))
    assert saved == ["corporate_credit_cam.md"]


def test_cli_exports_the_draft_it_was_given_read_as_utf8(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("# Credit memo\n\nFacility of £1m \u2014 caf\u00e9 terms.\n", encoding="utf-8")

    main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--type", "asset_finance", "--draft", str(draft),
          "--date-str", "2026-03-01"])

    folder = tmp_path / "deals" / "Acme Corp" / "Fleet Loan_2026-03-01"
    text = _docx_text(folder / "Acme Corp_Fleet Loan_CAM.docx")
    assert "Credit memo" in text and "Facility of £1m \u2014 caf\u00e9 terms." in text
    assert capsys.readouterr().out.strip().endswith("Fleet Loan_2026-03-01")


def test_cli_with_a_missing_draft_fails_before_creating_a_deal_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--draft", str(tmp_path / "nope.md")])
    assert not (tmp_path / "deals").exists() and not (tmp_path / "templates").exists()


def test_cli_turns_a_state_error_into_one_error_line_and_exports_nothing(tmp_path, monkeypatch, capsys):
    """A state the export cannot use is one `error:` exit message, and nothing is created in the deal folder."""
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "deals" / "Acme Corp" / "Fleet Loan_2026-01-10"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({"financials": [1, 2]}), encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--type", "asset_finance", "--draft", _cli_draft(tmp_path)])

    message = str(caught.value.code)
    assert message.startswith("error: ") and '"financials" must be an object' in message and "\n" not in message
    assert capsys.readouterr().out == ""
    assert sorted(p.name for p in folder.iterdir()) == ["state.json"]
    assert not (tmp_path / "templates").exists()


def test_cli_names_the_bad_date_and_exits_with_the_usage_status(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(["--company", "Acme Corp", "--proposal", "Fleet Loan", "--draft", _cli_draft(tmp_path), "--date-str", "2026-1-5"])
    assert caught.value.code == 2
    assert "--date-str '2026-1-5' must be a YYYY-MM-DD date" in capsys.readouterr().err


def test_cli_rejects_an_unsafe_proposal_before_the_deals_tree_is_searched_as_it_does_a_company(tmp_path, monkeypatch):
    import deal_export
    monkeypatch.chdir(tmp_path)

    def must_not_resolve(*args, **kwargs):
        pytest.fail("resolve_date_str() (the deals/ glob) ran before the proposal was sanitized")

    monkeypatch.setattr(deal_export, "resolve_date_str", must_not_resolve)
    with pytest.raises(ValueError):
        main(["--company", "Acme Corp", "--proposal", "../evil", "--draft", _cli_draft(tmp_path)])
    assert not (tmp_path / "deals").exists()


@pytest.mark.parametrize("argv, missing", [
    ([], "--company"),
    (["--company", "A"], "--proposal"),
    (["--company", "A", "--proposal", "P"], "--draft"),
])
def test_cli_requires_company_proposal_and_draft(capsys, argv, missing):
    with pytest.raises(SystemExit) as caught:
        main(argv)
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "usage:" in err and missing in err


def test_cli_help_lists_every_flag_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as helped:
        main(["--help"])
    assert helped.value.code == 0
    text = capsys.readouterr().out
    for flag in ("--company", "--proposal", "--type", "--draft", "--date-str"):
        assert flag in text, flag
