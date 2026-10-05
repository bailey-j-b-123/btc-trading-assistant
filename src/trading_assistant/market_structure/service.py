"""Read-only orchestration: build market-structure snapshots from stored candles.

The service only reads Step 2 OHLCV history. It never inserts, updates, or
deletes candles, never runs a migration, and never contacts an exchange. Every
request takes an explicit UTC ``as_of`` instant (defaulting to the injected
clock) and fetches candles only up to the latest candle that had fully closed by
that instant, which is what makes historical recalculation reproducible.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from trading_assistant.config import Settings, get_settings
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    require_utc_datetime,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import Candle, CandleGap
from trading_assistant.market_structure.analysis import (
    TimeframeStructureAnalysis,
    analyze_candles,
)
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.completeness import (
    DataCompleteness,
    describe_completeness,
)
from trading_assistant.market_structure.higher_timeframe import (
    HigherTimeframeContext,
    build_higher_timeframe_context,
    higher_timeframes_for,
)
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.snapshot import MarketStructureSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _LoadedCandles:
    """One timeframe's stored candle window as of a request instant."""

    interval: timedelta
    expected_latest_closed_open_time: datetime
    candles: tuple[Candle, ...]
    gaps: tuple[CandleGap, ...]
    completeness: DataCompleteness


class MarketStructureService:
    """Derive deterministic market structure from stored closed candles."""

    def __init__(
        self,
        engine: Engine,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings if settings is not None else get_settings()
        self.repository = CandleRepository(engine)
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def snapshot(
        self,
        *,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        as_of: datetime | None = None,
        parameters: MarketStructureParameters | None = None,
    ) -> MarketStructureSnapshot:
        """Build one complete market-structure snapshot as of an explicit UTC instant.

        The requested exchange, symbol, timeframe, and parameters are all
        optional and fall back to the configured defaults. Higher timeframes
        come from ``parameters.higher_timeframes`` or, when that is ``None``,
        from every longer timeframe in the configured supported set.
        """

        resolved_parameters = parameters if parameters is not None else MarketStructureParameters()
        resolved_exchange, resolved_symbol, resolved_timeframe = self._resolve_target(
            exchange, symbol, timeframe
        )
        as_of_utc = require_utc_datetime(
            as_of if as_of is not None else self._clock(), field_name="as_of"
        )

        primary = self._load_candles(
            exchange=resolved_exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=as_of_utc,
        )
        primary_analysis = analyze_candles(
            primary.candles,
            interval=primary.interval,
            as_of=as_of_utc,
            parameters=resolved_parameters,
        )
        contexts = tuple(
            self._higher_timeframe_context(
                exchange=resolved_exchange,
                symbol=resolved_symbol,
                timeframe=higher_timeframe,
                as_of=as_of_utc,
                parameters=resolved_parameters,
            )
            for higher_timeframe in self._resolve_higher_timeframes(
                resolved_timeframe, resolved_parameters
            )
        )

        snapshot = MarketStructureSnapshot(
            exchange=resolved_exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=as_of_utc,
            latest_closed_candle=primary.candles[-1] if primary.candles else None,
            analysis=primary_analysis,
            completeness=primary.completeness,
            higher_timeframes=contexts,
        )
        logger.info(
            "Market-structure snapshot completed",
            extra={
                "fields": {
                    "exchange": resolved_exchange,
                    "symbol": resolved_symbol,
                    "timeframe": resolved_timeframe,
                    "as_of": as_of_utc.isoformat(),
                    "candle_count": primary.completeness.candle_count,
                    "complete": primary.completeness.complete,
                    "missing_candle_count": primary.completeness.missing_candle_count,
                    "confirmed_swing_count": len(snapshot.swings),
                    "trend": snapshot.trend.direction.value,
                    "trend_reason": snapshot.trend.reason.value,
                    "range_detected": snapshot.detected_range is not None,
                    "zone_count": len(snapshot.zones),
                    "higher_timeframes": [context.timeframe for context in contexts],
                }
            },
        )
        if not primary.completeness.complete:
            logger.warning(
                "Market-structure snapshot window is incomplete",
                extra={
                    "fields": {
                        "exchange": resolved_exchange,
                        "symbol": resolved_symbol,
                        "timeframe": resolved_timeframe,
                        "as_of": as_of_utc.isoformat(),
                        "gap_count": len(primary.completeness.gaps),
                        "missing_candle_count": primary.completeness.missing_candle_count,
                        "missing_candles_after_latest_stored": (
                            primary.completeness.missing_candles_after_latest_stored
                        ),
                    }
                },
            )
        return snapshot

    def timeframe_analysis(
        self,
        *,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        as_of: datetime | None = None,
        parameters: MarketStructureParameters | None = None,
    ) -> TimeframeStructureAnalysis:
        """Return only the single-timeframe structural analysis for an instant."""

        resolved_parameters = parameters if parameters is not None else MarketStructureParameters()
        resolved_exchange, resolved_symbol, resolved_timeframe = self._resolve_target(
            exchange, symbol, timeframe
        )
        as_of_utc = require_utc_datetime(
            as_of if as_of is not None else self._clock(), field_name="as_of"
        )
        loaded = self._load_candles(
            exchange=resolved_exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=as_of_utc,
        )
        return analyze_candles(
            loaded.candles,
            interval=loaded.interval,
            as_of=as_of_utc,
            parameters=resolved_parameters,
        )

    def _resolve_target(
        self,
        exchange: str | None,
        symbol: str | None,
        timeframe: str | None,
    ) -> tuple[str, str, str]:
        resolved_exchange = self.settings.exchange if exchange is None else exchange
        resolved_symbol = self.settings.symbol if symbol is None else symbol
        resolved_timeframe = self.settings.default_timeframe if timeframe is None else timeframe
        if not resolved_exchange.strip():
            raise ValueError("exchange must not be empty")
        if not resolved_symbol.strip():
            raise ValueError("symbol must not be empty")
        self._require_supported_timeframe(resolved_timeframe)
        return resolved_exchange, resolved_symbol, resolved_timeframe

    def _require_supported_timeframe(self, timeframe: str) -> None:
        if timeframe not in self.settings.supported_timeframes:
            raise ValueError(f"timeframe {timeframe!r} is not in configured supported_timeframes")
        timeframe_to_milliseconds(timeframe)

    def _resolve_higher_timeframes(
        self,
        timeframe: str,
        parameters: MarketStructureParameters,
    ) -> tuple[str, ...]:
        configured = parameters.higher_timeframes
        resolved = (
            higher_timeframes_for(timeframe, self.settings.supported_timeframes)
            if configured is None
            else configured
        )
        base_milliseconds = timeframe_to_milliseconds(timeframe)
        for candidate in resolved:
            if candidate not in self.settings.supported_timeframes:
                raise ValueError(
                    f"higher timeframe {candidate!r} is not in configured supported_timeframes"
                )
            if timeframe_to_milliseconds(candidate) <= base_milliseconds:
                raise ValueError(
                    f"higher timeframe {candidate!r} must be longer than the requested timeframe "
                    f"{timeframe!r}"
                )
        return resolved

    def _load_candles(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
    ) -> _LoadedCandles:
        interval = interval_for_timeframe(timeframe)
        expected_latest_closed = latest_closed_candle_open_time(as_of, timeframe)
        result = self.repository.get_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_time=expected_latest_closed,
        )
        return _LoadedCandles(
            interval=interval,
            expected_latest_closed_open_time=expected_latest_closed,
            candles=result.candles,
            gaps=result.gaps,
            completeness=describe_completeness(
                result.candles,
                interval=interval,
                expected_latest_closed_open_time=expected_latest_closed,
                gaps=result.gaps,
            ),
        )

    def _higher_timeframe_context(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        parameters: MarketStructureParameters,
    ) -> HigherTimeframeContext:
        loaded = self._load_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
        )
        return build_higher_timeframe_context(
            timeframe,
            loaded.candles,
            interval=loaded.interval,
            as_of=as_of,
            expected_latest_closed_open_time=loaded.expected_latest_closed_open_time,
            parameters=parameters,
            gaps=loaded.gaps,
        )


def create_market_structure_service(
    engine: Engine,
    *,
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> MarketStructureService:
    """Construct the read-only market-structure service without side effects."""

    return MarketStructureService(engine, settings=settings, clock=clock)
