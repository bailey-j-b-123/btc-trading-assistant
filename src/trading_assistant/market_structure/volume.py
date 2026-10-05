"""Deterministic volume context from closed candles.

Only ordinary OHLCV volume is used. The module reports the latest closed candle
volume, a rolling average volume, and a relative-volume ratio, together with an
explicit sufficient/insufficient-data status. No buyer/seller intent, order-flow
or participation claim is derived from this data.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import (
    candles_closed_by,
    require_positive_interval,
)
from trading_assistant.market_structure.numeric import (
    divide,
    mean,
    quantize_derived,
    require_int,
)

#: Reason reported when the requested rolling window is not fully available.
INSUFFICIENT_CANDLES = "insufficient_candles"

#: Documented definition of the relative-volume ratio.
RELATIVE_VOLUME_BASIS = "latest_volume_over_prior_period_average"


@dataclass(frozen=True, slots=True)
class VolumeParameters:
    """Lookback used for the rolling average volume and relative volume."""

    period: int = 20

    def __post_init__(self) -> None:
        require_int(self.period, name="period", minimum=1)


@dataclass(frozen=True, slots=True)
class VolumeContext:
    """Volume context with explicit lookback requirements and availability."""

    sufficient: bool
    reason: str | None
    period: int
    relative_volume_basis: str
    candle_count: int
    required_candle_count: int
    latest_candle_timestamp: datetime | None
    current_volume: Decimal | None
    rolling_average_volume: Decimal | None
    reference_average_volume: Decimal | None
    relative_volume: Decimal | None

    @property
    def available(self) -> bool:
        """Alias for :attr:`sufficient`."""

        return self.sufficient


def calculate_volume(
    candles: Iterable[Candle],
    *,
    interval: timedelta,
    as_of: datetime,
    parameters: VolumeParameters | None = None,
) -> VolumeContext:
    """Calculate volume context from candles closed by ``as_of``.

    ``rolling_average_volume`` is the simple mean of the last ``period`` closed
    candles including the latest one. ``reference_average_volume`` and
    ``relative_volume`` exclude the latest candle, so the ratio compares the
    newest closed volume against the preceding window. ``sufficient`` is only
    true when ``period + 1`` closed candles exist; shorter history yields
    ``None`` for the fields whose window is unavailable rather than a silently
    shortened average.
    """

    resolved = parameters if parameters is not None else VolumeParameters()
    resolved_interval = require_positive_interval(interval)
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    ordered = candles_closed_by(candles, interval=resolved_interval, as_of=as_of_utc)
    required = resolved.period + 1

    if not ordered:
        return VolumeContext(
            sufficient=False,
            reason=INSUFFICIENT_CANDLES,
            period=resolved.period,
            relative_volume_basis=RELATIVE_VOLUME_BASIS,
            candle_count=0,
            required_candle_count=required,
            latest_candle_timestamp=None,
            current_volume=None,
            rolling_average_volume=None,
            reference_average_volume=None,
            relative_volume=None,
        )

    volumes = [candle.volume for candle in ordered]
    current_volume = volumes[-1]
    rolling_average = mean(volumes[-resolved.period :]) if len(volumes) >= resolved.period else None
    reference_average: Decimal | None = None
    relative: Decimal | None = None
    if len(volumes) >= required:
        reference_average = mean(volumes[-required:-1])
        relative = quantize_derived(divide(current_volume, reference_average))

    sufficient = len(ordered) >= required
    return VolumeContext(
        sufficient=sufficient,
        reason=None if sufficient else INSUFFICIENT_CANDLES,
        period=resolved.period,
        relative_volume_basis=RELATIVE_VOLUME_BASIS,
        candle_count=len(ordered),
        required_candle_count=required,
        latest_candle_timestamp=ordered[-1].timestamp,
        current_volume=current_volume,
        rolling_average_volume=rolling_average,
        reference_average_volume=reference_average,
        relative_volume=relative,
    )
