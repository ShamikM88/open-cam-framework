import math


class NonFiniteNumberError(ValueError):
    """A financial input or computed value contains NaN or infinity."""


def is_finite_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


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
