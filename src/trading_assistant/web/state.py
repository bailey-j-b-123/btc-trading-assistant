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
from trading_assistant.forward_testing import ForwardTestService
from trading_assistant.historical_validation import HistoricalValidationService
from trading_assistant.journaling import JournalService
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    timeframe_to_milliseconds,
)
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.multi_timeframe import MultiTimeframeService
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
        # Step 11 is derived read-only validation; it does not use JournalService
        # and has no persistence/migration side effects.
        self.validation = HistoricalValidationService(engine)
        self.explanations = ExplanationService()
        # Step 12 read-only view over the immutable forward ledger. It is built
        # without a market-data source on purpose: the web layer never downloads
        # candles and never runs the forward runner. Only the runner records
        # forward cycles; the dashboard only reads what was already recorded.
        self.forward = ForwardTestService(
            engine,
            settings=self.settings,
            clock=self._clock,
            explanation_service=self.explanations,
        )
        # Step 13 read-only hierarchy evaluation over stored closed candles.
        # Like the forward service above, it is built without a market-data
        # source on purpose: the web layer never downloads candles and never
        # runs the hierarchy runner. Only the runner records hierarchy
        # observations; the dashboard only reads what was already recorded and
        # evaluates the ladder live at its own decision instant. When the
        # configured supported_timeframes do not cover the default 4H/1H/15M/5M
        # hierarchy, the service is unavailable (never an error): the dashboard
        # ladder reports itself unavailable with the exact reason, and the rest
        # of the dashboard is unaffected.
        self.multi_timeframe: MultiTimeframeService | None
        self.multi_timeframe_unavailable_reason: str | None = None
        try:
            self.multi_timeframe = MultiTimeframeService(
                engine,
                settings=self.settings,
                clock=self._clock,
            )
        except ValueError as exc:
            self.multi_timeframe = None
            self.multi_timeframe_unavailable_reason = str(exc)

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
