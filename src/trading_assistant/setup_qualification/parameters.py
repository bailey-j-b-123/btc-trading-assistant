"""Request-scoped qualification thresholds and canonical versioned identities."""

import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from trading_assistant.market_data.timeframes import timeframe_to_milliseconds
from trading_assistant.market_structure.numeric import as_decimal, require_int
from trading_assistant.market_structure.snapshot import to_jsonable

RULES_VERSION = "setup-qualification-v1"


def fingerprint(*parts: object) -> str:
    payload = json.dumps(to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    return sha256((RULES_VERSION + ":" + payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class QualificationParameters:
    continuation_max_bars: int = 10
    reversal_max_bars: int = 10
    range_max_bars: int = 10
    min_relative_volume: Decimal = Decimal(1)
    max_atr_percent: Decimal = Decimal(10)
    higher_timeframes: tuple[str, ...] = ()
    require_higher_timeframe_alignment: bool = False

    def __post_init__(self) -> None:
        for name in ("continuation_max_bars", "reversal_max_bars", "range_max_bars"):
            require_int(getattr(self, name), name=name, minimum=1)
        for name in ("min_relative_volume", "max_atr_percent"):
            value = as_decimal(getattr(self, name), name=name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            # Normalize equivalent decimal spellings without ambient-context rounding.
            value = (
                Decimal(format(value, "f").rstrip("0").rstrip("."))
                if "." in format(value, "f")
                else value
            )
            object.__setattr__(self, name, value)
        if type(self.require_higher_timeframe_alignment) is not bool:
            raise TypeError("require_higher_timeframe_alignment must be boolean")
        if not isinstance(self.higher_timeframes, tuple):
            raise TypeError("higher_timeframes must be an immutable tuple")
        if len(set(self.higher_timeframes)) != len(self.higher_timeframes):
            raise ValueError("higher_timeframes must be unique")
        for timeframe in self.higher_timeframes:
            timeframe_to_milliseconds(timeframe)
        object.__setattr__(
            self,
            "higher_timeframes",
            tuple(
                sorted(
                    self.higher_timeframes,
                    key=lambda t: (timeframe_to_milliseconds(t), t),
                )
            ),
        )
        if self.require_higher_timeframe_alignment and not self.higher_timeframes:
            raise ValueError(
                "required higher-timeframe alignment needs configured timeframes"
            )
