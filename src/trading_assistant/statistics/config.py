"""Validated, versioned configuration for deterministic journal statistics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from trading_assistant.market_structure.snapshot import to_jsonable

STATISTICS_RULES_VERSION = "journal-statistics-v1"
DEFAULT_MINIMUM_SAMPLE_SIZE = 30
DEFAULT_DECIMAL_PLACES = 8
DEFAULT_DECIMAL_PRECISION = 50
DEFAULT_QUANTILES = (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"))


def _canonical_decimal(value: Decimal | str, *, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{name} must be Decimal or a decimal string")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{name} must be a decimal number") from exc
    else:
        raise TypeError(f"{name} must be Decimal or a decimal string")
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    text = format(parsed, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
        parsed = Decimal(text) if text not in ("", "-") else Decimal(0)
    return parsed


@dataclass(frozen=True, slots=True)
class StatisticsConfig:
    """The exact reporting rules; every field participates in report identity.

    ``minimum_sample_size`` is a fixed reporting floor, not a confidence
    guarantee or a learned threshold. Counts remain available below it; rates
    and distribution summaries are marked ``INSUFFICIENT_DATA`` and their
    percentage/summary conclusions are withheld.
    """

    rules_version: str = STATISTICS_RULES_VERSION
    minimum_sample_size: int = DEFAULT_MINIMUM_SAMPLE_SIZE
    decimal_places: int = DEFAULT_DECIMAL_PLACES
    decimal_precision: int = DEFAULT_DECIMAL_PRECISION
    quantiles: tuple[Decimal, ...] = DEFAULT_QUANTILES

    def __post_init__(self) -> None:
        if not isinstance(self.rules_version, str) or not self.rules_version.strip():
            raise ValueError("rules_version must be a non-empty string")
        object.__setattr__(self, "rules_version", self.rules_version.strip())
        if (
            isinstance(self.minimum_sample_size, bool)
            or not isinstance(self.minimum_sample_size, int)
            or self.minimum_sample_size < 1
        ):
            raise ValueError("minimum_sample_size must be an integer >= 1")
        if isinstance(self.decimal_places, bool) or not isinstance(
            self.decimal_places, int
        ):
            raise TypeError("decimal_places must be an integer")
        if not 0 <= self.decimal_places <= 18:
            raise ValueError("decimal_places must be between 0 and 18")
        if isinstance(self.decimal_precision, bool) or not isinstance(
            self.decimal_precision, int
        ):
            raise TypeError("decimal_precision must be an integer")
        if self.decimal_precision < self.decimal_places + 16:
            raise ValueError("decimal_precision must be at least decimal_places + 16")
        if not isinstance(self.quantiles, tuple):
            raise TypeError("quantiles must be an immutable tuple")
        normalized = tuple(
            sorted(
                _canonical_decimal(value, name=f"quantiles[{index}]")
                for index, value in enumerate(self.quantiles)
            )
        )
        if not normalized:
            raise ValueError("quantiles must contain at least one probability")
        if any(not Decimal(0) < value < Decimal(1) for value in normalized):
            raise ValueError("each quantile probability must be between 0 and 1")
        if len(set(normalized)) != len(normalized):
            raise ValueError("quantile probabilities must be unique")
        object.__setattr__(self, "quantiles", normalized)

    def fingerprint(self) -> str:
        """SHA-256 identity of the exact statistics rules and thresholds."""

        payload = json.dumps(to_jsonable(self), sort_keys=True, separators=(",", ":"))
        return sha256((self.rules_version + ":" + payload).encode()).hexdigest()


def canonical_json(value: object) -> str:
    """Canonical JSON shared by report identities and serialized output."""

    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"))
