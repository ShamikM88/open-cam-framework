"""Shared unit conventions for ratios whose display unit is percent.

The stored and compared unit for these ratios is a fraction (0.87 means 87%).
Input warnings are deliberately advisory: a bare value outside the plausible
fraction range is retained unchanged so an analyst can correct the source.
"""
import sys
from decimal import Decimal

PERCENT_TYPE_RATIOS = frozenset({"gearing", "fcf_conversion_pct"})


def warn_percent_type_values(period_ratios):
    """Print one aggregated stderr warning for percent-type values above 10 in magnitude.

    period_ratios is an iterable of (period, {metric: value}) pairs.
    Values are not coerced or changed here; the caller records exactly what
    was supplied except where transcription explicitly includes a percent
    suffix for a percent-type ratio.
    """
    suspicious = []
    for period, ratios in period_ratios:
        if not isinstance(ratios, dict):
            continue
        for metric, value in ratios.items():
            if metric not in PERCENT_TYPE_RATIOS or value is None or isinstance(value, bool):
                continue
            if isinstance(value, (int, float, Decimal)) and abs(value) > 10:
                suspicious.append(f"{period}.{metric}={value!r}")
    if suspicious:
        items = ", ".join(suspicious)
        print(
            "warning: percent-type ratios (gearing, fcf_conversion_pct) and their covenant thresholds "
            "are stored as fractions (0.87 means 87%). Values over 10 in absolute magnitude are recorded "
            f"as supplied, not converted; check the unit: {items}",
            file=sys.stderr,
        )
