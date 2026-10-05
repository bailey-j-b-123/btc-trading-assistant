"""Validated, request-scoped Step 4 rules (percent values, not fractions)."""

from dataclasses import dataclass, fields
from decimal import Decimal

from trading_assistant.market_structure.numeric import as_decimal, require_int


@dataclass(frozen=True, slots=True)
class PatternLiquidityParameters:
    breakout_tolerance_pct: Decimal = Decimal("0.1")
    breakout_confirmation_candles: int = 1
    failure_window_candles: int = 10
    failure_reentry_pct: Decimal = Decimal("0.1")
    sweep_penetration_pct: Decimal = Decimal("0.1")
    sweep_reclaim_pct: Decimal = Decimal(0)
    retest_window_candles: int = 10
    retest_tolerance_pct: Decimal = Decimal("0.2")
    retest_hold_pct: Decimal = Decimal("0.1")
    equal_tolerance_pct: Decimal = Decimal("0.2")
    equal_min_members: int = 2
    equal_lookback_swings: int = 40
    pattern_tolerance_pct: Decimal = Decimal("0.5")
    pattern_min_depth_pct: Decimal = Decimal(1)
    head_min_prominence_pct: Decimal = Decimal(1)
    neckline_tolerance_pct: Decimal = Decimal("0.1")
    pattern_invalidation_pct: Decimal = Decimal("0.1")
    pattern_max_span_candles: int = 120

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name.endswith("_pct"):
                value = as_decimal(value, name=field.name)
                if not 0 <= value < 100:
                    raise ValueError(f"{field.name} must be >= 0 and < 100")
                object.__setattr__(self, field.name, value)
            else:
                require_int(value, name=field.name, minimum=1)
        if self.equal_tolerance_pct == 0:
            raise ValueError("equal_tolerance_pct must be positive (Step 3 zone rule)")
        if not 2 <= self.equal_min_members <= self.equal_lookback_swings:
            raise ValueError(
                "equal_min_members must be between 2 and equal_lookback_swings"
            )
        if self.pattern_min_depth_pct == 0 or self.head_min_prominence_pct == 0:
            raise ValueError("pattern depth and head prominence must be positive")
