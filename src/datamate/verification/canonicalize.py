from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any


def canonicalize(value: Any) -> Any:
    """Diagnostics only; never reorder requested lists or collapse NULL to zero."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return (type(value).__name__, value)
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            return ("invalid_number", str(number))
        with localcontext() as context:
            context.prec = max(80, len(number.as_tuple().digits))
            return ("number", str(number.normalize()) if number else "0")
    if isinstance(value, dict):
        return tuple((key, canonicalize(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(canonicalize(item) for item in value)
    raise ValueError("Unsupported canonical value")
