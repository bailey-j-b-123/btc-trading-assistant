"""Deterministic decimal helpers used by every market-structure calculation.

All market-structure arithmetic is performed on :class:`decimal.Decimal`
values. Division runs inside an explicitly configured decimal context and every
derived mean, ratio, or percentage is rounded with a documented quantum, so
identical inputs always produce identical output on any machine.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext

#: Decimal context precision used for intermediate division.
DIVISION_PRECISION = 28

#: Rounding rule applied to derived means, ratios, and percentages.
DERIVED_ROUNDING = ROUND_HALF_EVEN

#: Fixed output scale (8 decimal places) for derived means, ratios, percentages.
DERIVED_QUANTUM = Decimal("0.00000001")


def as_decimal(value: Decimal | str | float, *, name: str) -> Decimal:
    """Coerce a configuration value to a finite Decimal without binary noise.

    A wrong type raises ``TypeError``; a non-numeric, non-finite, or otherwise
    unusable value raises ``ValueError``. ``bool`` is rejected explicitly even
    though Python treats it as an ``int``.
    """

    if isinstance(value, bool):
        raise TypeError(f"{name} must be a decimal number, not a boolean")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{name} must be a decimal number") from exc
    elif isinstance(value, float):
        parsed = Decimal(str(value))
    else:
        raise TypeError(f"{name} must be a decimal number")
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    return parsed


def require_int(
    value: int,
    *,
    name: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    """Validate a configuration integer and return it unchanged.

    A non-integer (including ``bool``) raises ``TypeError``; an out-of-range
    integer raises ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be an integer <= {maximum}")
    return value


def divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Divide inside an explicit decimal context for reproducible results."""

    if denominator == 0:
        raise ValueError("cannot divide by zero in a market-structure calculation")
    with localcontext() as context:
        context.prec = DIVISION_PRECISION
        context.rounding = DERIVED_ROUNDING
        return Decimal(numerator) / Decimal(denominator)


def quantize_derived(value: Decimal) -> Decimal:
    """Round a derived value to the documented fixed output scale."""

    return value.quantize(DERIVED_QUANTUM, rounding=DERIVED_ROUNDING)


def mean(values: Sequence[Decimal]) -> Decimal:
    """Return the rounded arithmetic mean of at least one value."""

    if not values:
        raise ValueError("mean requires at least one value")
    total = sum(values, Decimal(0))
    return quantize_derived(divide(total, Decimal(len(values))))


def percentage_of(value: Decimal, base: Decimal) -> Decimal:
    """Return ``value / base * 100`` as a rounded percentage value."""

    return quantize_derived(divide(Decimal(value) * 100, Decimal(base)))


def percentage_width(high: Decimal, low: Decimal) -> Decimal:
    """Return ``(high - low) / high * 100`` for a band or range."""

    return percentage_of(Decimal(high) - Decimal(low), high)


def tolerance_band(price: Decimal, tolerance_pct: Decimal) -> Decimal:
    """Return the absolute tolerance implied by a percentage of a price."""

    return quantize_derived(Decimal(price) * Decimal(tolerance_pct) / Decimal(100))


def is_within_tolerance(value: Decimal, reference: Decimal, tolerance_pct: Decimal) -> bool:
    """Return whether ``value`` is within ``tolerance_pct`` percent of ``reference``."""

    return abs(Decimal(value) - Decimal(reference)) <= tolerance_band(reference, tolerance_pct)
