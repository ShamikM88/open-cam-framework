import math
import sys


class FinancialInputError(ValueError):
    """A framework-computed financial input cannot be safely evaluated."""


class NonFiniteNumberError(FinancialInputError):
    """A financial input or computed value contains NaN or infinity."""


class StressAssumptionsError(ValueError):
    """A downside stress assumption violates the agreed unit/range rules."""


STRESS_KEYS = ("revenue_haircut_pct", "opex_increase_pct", "interest_rate_bump_bps")
STRESS_PERCENT_KEYS = ("revenue_haircut_pct", "opex_increase_pct")


def validate_stress_assumptions(value):
    """Refuse downside shocks that invert the stress or make revenue negative.

    Present values must be finite JSON numbers (booleans, strings, nulls and
    containers are refused). This shared domain validator adds no upper bounds
    beyond the revenue-haircut ceiling. Warnings are emitted only after all
    domain errors have been checked, so a refused input produces one error
    line rather than a warning followed by an error.
    """
    if not isinstance(value, dict):
        return value

    for key in STRESS_KEYS:
        if key not in value:
            continue
        item = value[key]
        if (isinstance(item, bool) or not isinstance(item, (int, float))
                or (isinstance(item, float) and not math.isfinite(item))):
            raise StressAssumptionsError(
                f"stress assumptions: {key} must be a finite JSON number; found {item!r}"
            )
        if item < 0:
            raise StressAssumptionsError(
                f"stress assumptions: {key} must be non-negative; found {item!r}"
            )
        if key == "revenue_haircut_pct" and item >= 100:
            raise StressAssumptionsError(
                f"stress assumptions: revenue_haircut_pct must be less than 100; found {item!r}"
            )

    for key in STRESS_PERCENT_KEYS:
        item = value.get(key)
        if (isinstance(item, (int, float)) and not isinstance(item, bool)
                and (not isinstance(item, float) or math.isfinite(item)) and 0 < item < 1):
            print(
                f"warning: stress assumptions: {key}={item!r} is between 0 and 1 percent; "
                "it will be applied as written, not treated as a whole-percent value",
                file=sys.stderr,
            )
    return value


class InvalidFinancialInputError(FinancialInputError):
    """A recognised raw line item is not a finite JSON number or null."""


def validate_raw_line_items(value, field_names, path="financial inputs"):
    """Reject non-JSON-numeric values for known raw line-item fields.

    Null remains the established spelling of an unavailable input. Unknown
    keys are deliberately ignored here because callers may carry metadata
    alongside the framework's recognised raw fields.
    """
    if not isinstance(value, dict):
        return value
    known_fields = set(field_names)
    for period, raw in value.items():
        if not isinstance(raw, dict):
            continue
        for field, item in raw.items():
            if field not in known_fields or item is None:
                continue
            is_json_number = (
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and (not isinstance(item, float) or math.isfinite(item))
            )
            if not is_json_number:
                raise InvalidFinancialInputError(
                    f"{path}: period {period!r}, field {field!r} must be a finite JSON number or null; "
                    f"found {item!r}"
                )
    return value


def validate_period_objects(value, path="financial inputs"):
    """Require every supplied period to be an object of raw line items.

    A period with a string/list/scalar previously reached a dict update or
    arithmetic, producing a traceback or misleading lower-level error.
    Validate the shape before any merge or state mutation.
    """
    if value is None:
        return value
    if not isinstance(value, dict):
        raise InvalidFinancialInputError(
            f"{path} must be an object keyed by period; found {type(value).__name__}"
        )
    for period, line_items in value.items():
        if not isinstance(line_items, dict):
            raise InvalidFinancialInputError(
                f"{path}: period {period!r} must be an object of line items; "
                f"found {type(line_items).__name__}"
            )
    return value


def validate_period_keys(value, allowed_periods, path="financial inputs"):
    """Refuse a key that is not one of the framework's period labels.

    The evaluator has no entity dimension: an input keyed by entity name
    (`{"HoldCo": {"FY-Current": {...}}}`) used to be accepted, with the entity
    names silently treated as periods and the results keyed by entity. Name the
    first unexpected key and the valid labels in one line instead (issue #48).
    A value that is not a mapping is left for validate_period_objects.
    """
    if not isinstance(value, dict):
        return value
    for period in value:
        if period not in allowed_periods:
            raise InvalidFinancialInputError(
                f"{path}: unexpected period key {period!r}; expected one of {', '.join(allowed_periods)}"
            )
    return value


def validate_finite_numbers(value, path="input"):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NonFiniteNumberError(f"non-finite numeric value at {path}: {value!r}")
    elif isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}[{key!r}]"
            validate_finite_numbers(child, path=child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            validate_finite_numbers(child, path=f"{path}[{index}]")
    return value


COLLATERAL_MONETARY_FIELDS = ("exposure", "cap_value", "non_recovery", "costs", "collateral_value")


class NegativeCollateralInputError(FinancialInputError):
    """A monetary collateral field is negative on a supplied asset (issue #378)."""


def _collateral_asset_label(asset, index):
    """Name an asset in an error line: asset_id, else asset_class, else its position."""
    asset_id = asset.get("asset_id")
    if isinstance(asset_id, str) and asset_id:
        return asset_id
    asset_class = asset.get("asset_class")
    if isinstance(asset_class, str) and asset_class:
        return asset_class
    return f"asset {index + 1}"


def validate_collateral_inputs(value):
    """Refuse a negative on every monetary collateral field (issue #378, decision 5 on #266).

    A negative per-asset exposure or collateral value used to be accepted as
    input and could net into a positive workbook total, so the refusal belongs
    where the input is recorded, not in each consumer. Zero is a valid figure
    and is accepted; null (the established spelling of an unavailable input),
    non-numeric values and unknown keys are left to the other validators.
    Non-finite values are refused by validate_finite_numbers, which callers run
    first. The error names the asset, field and value in one line.
    """
    if not isinstance(value, list):
        return value
    for index, asset in enumerate(value):
        if not isinstance(asset, dict):
            continue
        label = _collateral_asset_label(asset, index)
        for field in COLLATERAL_MONETARY_FIELDS:
            item = asset.get(field)
            if item is None or isinstance(item, bool) or not isinstance(item, (int, float)):
                continue
            if item < 0:
                raise NegativeCollateralInputError(
                    f"collateral[{label}]: {field} must be a non-negative finite number; found {item!r}"
                )
    return value
