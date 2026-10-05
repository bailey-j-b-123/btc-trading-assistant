"""Application state container: the deterministic services behind the API.

The web layer deliberately constructs no exchange client and no market-data
download service: candle reads go through the read-only ``CandleRepository``
and all analysis flows through the existing Step 3–9 services. A ``clock`` is
injectable so freshness and default ``as_of`` handling stay testable.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from trading_assistant.ai_explanation import ExplanationService
from trading_assistant.config import Settings, get_settings
from trading_assistant.database import create_database_engine
from trading_assistant.journaling import JournalService
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    timeframe_to_milliseconds,
)
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.pattern_liquidity import PatternLiquidityService
from trading_assistant.setup_qualification import QualificationService
from trading_assistant.statistics import JournalStatisticsService
from trading_assistant.statistics.config import StatisticsConfig


class AppState:
    """Holds the engine, settings, clock, and lazily created Step 2–9 services."""

    def __init__(
        self,
        engine: Engine,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        statistics_config: StatisticsConfig | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings if settings is not None else get_settings()
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self.statistics_config = statistics_config

        self.candles = CandleRepository(engine)
        self.qualification = QualificationService(engine)
        self.patterns = PatternLiquidityService(engine)
        self.journal = JournalService(engine)
        self.statistics = JournalStatisticsService(engine, config=statistics_config)
        self.explanations = ExplanationService()

    def now(self) -> datetime:
        instant = self._clock()
        if instant.tzinfo is None:
            raise ValueError("the web clock must return timezone-aware UTC datetimes")
        return instant.astimezone(UTC)

    def current_boundary(self, timeframe: str) -> datetime:
        """The most recent candle-close boundary at or before the clock instant."""

        return latest_closed_candle_open_time(
            self.now(), timeframe
        ) + interval_for_timeframe(timeframe)

    def boundary_for(self, timeframe: str, instant: datetime) -> datetime:
        return latest_closed_candle_open_time(
            instant, timeframe
        ) + interval_for_timeframe(timeframe)

    def timeframe_milliseconds(self, timeframe: str) -> int:
        return timeframe_to_milliseconds(timeframe)

    def require_supported_timeframe(self, timeframe: str) -> None:
        if timeframe not in self.settings.supported_timeframes:
            raise ValueError(
                f"timeframe {timeframe!r} is not in configured supported_timeframes "
                f"{list(self.settings.supported_timeframes)}"
            )

    def require_symbol(self, symbol: str | None) -> str:
        resolved = self.settings.symbol if symbol is None else symbol
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError("symbol must be a non-empty string")
        return resolved


def create_default_state(
    *,
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    statistics_config: StatisticsConfig | None = None,
) -> AppState:
    """Build state over the configured database URL without migrating it."""

    resolved = settings if settings is not None else get_settings()
    engine = create_database_engine(resolved.database_url)
    return AppState(
        engine,
        settings=resolved,
        clock=clock,
        statistics_config=statistics_config,
    )
