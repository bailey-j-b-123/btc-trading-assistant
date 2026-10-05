"""Optional read-only adapter over the existing Step 2–4 APIs, with no writes."""

from datetime import datetime

from sqlalchemy.engine import Engine

from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    require_utc_datetime,
)
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.higher_timeframe import (
    build_higher_timeframe_context,
)
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.pattern_liquidity.service import PatternLiquidityService
from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
)
from trading_assistant.setup_qualification.parameters import QualificationParameters


class QualificationService:
    """Correctness-first full replay; cache source frames externally for large histories."""

    def __init__(self, engine: Engine) -> None:
        self.patterns = PatternLiquidityService(engine)

    def enumerate_snapshots(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        parameters: QualificationParameters | None = None,
        pattern_parameters: PatternLiquidityParameters | None = None,
        structure_parameters: MarketStructureParameters | None = None,
        known_since: datetime | None = None,
    ) -> tuple[QualificationSnapshot, ...]:
        as_of = require_utc_datetime(as_of, field_name="as_of")
        interval = interval_for_timeframe(timeframe)
        last_open = latest_closed_candle_open_time(as_of, timeframe)
        if last_open + interval != as_of:
            raise ValueError("as_of must be a base candle-close boundary")
        p = parameters or QualificationParameters()
        sp = structure_parameters or MarketStructureParameters()
        stored = self.patterns.repository.get_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_time=last_open,
        )

        def frames():
            at = stored.candles[0].timestamp + interval if stored.candles else as_of
            while at <= as_of:
                source = self.patterns.snapshot(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    as_of=at,
                    parameters=pattern_parameters,
                    structure_parameters=sp,
                )
                higher = []
                for tf in p.higher_timeframes:
                    expected = latest_closed_candle_open_time(at, tf)
                    candles = self.patterns.repository.get_candles(
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=tf,
                        end_time=expected,
                    )
                    higher.append(
                        build_higher_timeframe_context(
                            tf,
                            candles.candles,
                            interval=interval_for_timeframe(tf),
                            as_of=at,
                            expected_latest_closed_open_time=expected,
                            parameters=sp,
                            gaps=candles.gaps,
                        )
                    )
                yield QualificationFrame(source, tuple(higher))
                at += interval

        return enumerate_qualifications(
            frames(), as_of=as_of, parameters=p, known_since=known_since
        )

    def snapshot(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        parameters: QualificationParameters | None = None,
        pattern_parameters: PatternLiquidityParameters | None = None,
        structure_parameters: MarketStructureParameters | None = None,
    ) -> QualificationSnapshot:
        return self.enumerate_snapshots(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            parameters=parameters,
            pattern_parameters=pattern_parameters,
            structure_parameters=structure_parameters,
        )[-1]
