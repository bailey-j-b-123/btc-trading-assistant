"""Read-only repository adapter. No schema, persistence, exchange or execution I/O."""

from datetime import datetime

from sqlalchemy.engine import Engine

from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    require_utc_datetime,
)
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.pattern_liquidity.analysis import analyze_patterns
from trading_assistant.pattern_liquidity.events import Event
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.pattern_liquidity.snapshot import PatternLiquiditySnapshot


class PatternLiquidityService:
    def __init__(self, engine: Engine) -> None:
        self.repository = CandleRepository(engine)

    def snapshot(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        parameters: PatternLiquidityParameters | None = None,
        structure_parameters: MarketStructureParameters | None = None,
    ) -> PatternLiquiditySnapshot:
        as_of = require_utc_datetime(as_of, field_name="as_of")
        stored = self.repository.get_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_time=latest_closed_candle_open_time(as_of, timeframe),
        )
        return analyze_patterns(
            stored.candles,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            parameters=parameters,
            structure_parameters=structure_parameters,
        )

    def enumerate_events(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        known_since: datetime | None = None,
        parameters: PatternLiquidityParameters | None = None,
        structure_parameters: MarketStructureParameters | None = None,
    ) -> tuple[Event, ...]:
        """Replay full stored history; filter occurrences only AFTER warming up.

        known_since is inclusive. State transitions are distinct occurrences;
        the original event is never retrospectively labeled with its future state.
        """
        if known_since is not None:
            known_since = require_utc_datetime(known_since, field_name="known_since")
            if known_since > require_utc_datetime(as_of, field_name="as_of"):
                raise ValueError("known_since must not be after as_of")
        events = self.snapshot(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            parameters=parameters,
            structure_parameters=structure_parameters,
        ).events()
        return tuple(
            e for e in events if known_since is None or e.known_at >= known_since
        )
