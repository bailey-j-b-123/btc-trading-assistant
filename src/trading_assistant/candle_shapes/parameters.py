"""Versioned, validated thresholds for descriptive candle shapes.

All ratios are dimensionless and computed with exact Decimal arithmetic:

* ``body ratio``  = |close - open| / (high - low)
* ``wick ratio``  = wick length / (high - low)

A candle with zero range (high == low) has no shape and is never classified.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

CANDLE_SHAPE_RULES_VERSION = "candle-shapes-v1"


def _ratio(name: str, value: Decimal, *, low: Decimal, high: Decimal) -> None:
    if not isinstance(value, Decimal):
        raise ValueError(f"{name} must be a Decimal")
    if not (low < value <= high):
        raise ValueError(f"{name} must satisfy {low} < value <= {high}")


@dataclass(frozen=True, slots=True)
class CandleShapeParameters:
    """Thresholds. Defaults are the documented v1 definitions.

    ``strong_body_min_ratio``
        A bullish (close > open) or bearish (close < open) candle whose body is
        at least this share of its full range is a *strong body*.
    ``rejection_wick_min_ratio``
        A candle whose lower (or upper) wick is at least this share of its full
        range, AND at least ``rejection_wick_to_body_min`` times its body, shows
        a *long-wick rejection* of that extreme.
    ``rejection_wick_to_body_min``
        Minimum wick-to-body multiple for a long-wick rejection.
    ``indecision_max_body_ratio``
        A candle whose body is at most this share of its range is *indecision*
        (small body relative to range; the standard doji family).
    """

    strong_body_min_ratio: Decimal = Decimal("0.70")
    rejection_wick_min_ratio: Decimal = Decimal("0.60")
    rejection_wick_to_body_min: Decimal = Decimal("2")
    indecision_max_body_ratio: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        _ratio("strong_body_min_ratio", self.strong_body_min_ratio, low=Decimal(0), high=Decimal(1))
        _ratio("rejection_wick_min_ratio", self.rejection_wick_min_ratio, low=Decimal(0), high=Decimal(1))
        _ratio("rejection_wick_to_body_min", self.rejection_wick_to_body_min, low=Decimal(0), high=Decimal(1000))
        _ratio("indecision_max_body_ratio", self.indecision_max_body_ratio, low=Decimal(0), high=Decimal(1))
        if self.indecision_max_body_ratio >= self.strong_body_min_ratio:
            raise ValueError("indecision_max_body_ratio must be below strong_body_min_ratio")

    def fingerprint_payload(self) -> dict[str, str]:
        return {
            "rules_version": CANDLE_SHAPE_RULES_VERSION,
            "strong_body_min_ratio": format(self.strong_body_min_ratio, "f"),
            "rejection_wick_min_ratio": format(self.rejection_wick_min_ratio, "f"),
            "rejection_wick_to_body_min": format(self.rejection_wick_to_body_min, "f"),
            "indecision_max_body_ratio": format(self.indecision_max_body_ratio, "f"),
        }
