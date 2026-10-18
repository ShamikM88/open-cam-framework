import math


class FinancialInputError(ValueError):
    """A framework-computed financial input cannot be safely evaluated."""


class NonFiniteNumberError(FinancialInputError):
    """A financial input or computed value contains NaN or infinity."""


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
