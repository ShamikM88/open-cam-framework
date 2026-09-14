import os

import docx
import openpyxl
import pytest

from deal_export import _downside_financial_data_from_state, _financial_data_from_state, export_deal


def _write(path, content="content"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


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
    import openpyxl
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
