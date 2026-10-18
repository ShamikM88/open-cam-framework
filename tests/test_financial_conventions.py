from financial_conventions import PERCENT_TYPE_RATIOS, warn_percent_type_values


def test_percent_type_ratio_constant_names_only_fraction_stored_ratios():
    assert PERCENT_TYPE_RATIOS == frozenset({"gearing", "fcf_conversion_pct"})


def test_oversized_percent_type_values_emit_one_aggregated_warning(capsys):
    warn_percent_type_values([
        ("FY+1", {"gearing": 87, "fcf_conversion_pct": 35, "dscr": 87}),
        ("FY+2", {"gearing": None, "fcf_conversion_pct": 10}),
        ("FY+3", "not a ratio mapping"),
        ("FY+4", {"gearing": True}),
    ])

    warning = capsys.readouterr().err
    assert warning.count("warning:") == 1
    assert "FY+1.gearing=87" in warning and "FY+1.fcf_conversion_pct=35" in warning
    assert "FY+1.dscr" not in warning and "FY+2" not in warning
    assert "FY+3" not in warning and "FY+4" not in warning


def test_percent_type_values_at_or_below_ten_do_not_warn(capsys):
    warn_percent_type_values([
        ("FY+1", {"gearing": 10, "fcf_conversion_pct": 0.87}),
    ])

    assert capsys.readouterr().err == ""
