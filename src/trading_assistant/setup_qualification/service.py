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


def bounded_replay_start(
    *, as_of: datetime, timeframe: str, parameters: QualificationParameters
) -> datetime:
    """Earliest frame whose seeds can still be live at ``as_of``.

    A candidate older than its family's ``max_bars`` is terminally expired in
    every state (WATCH and QUALIFIED alike), so any setup that is live at
    ``as_of`` was seeded within ``max_bars`` closes of it. Starting the replay
    one extra close earlier is the documented engine warm-up contract (see
    :func:`enumerate_qualifications`): the final snapshot reproduces every
    live candidate exactly, while ancient terminal history — already recorded
    in the forward ledger or journal where it mattered — is not carried in the
    snapshot envelope. This keeps dashboard and runner replays bounded no
    matter how much history is stored.

    Scope note: within one bounded history only snapshots at ``B >= start +
    max_bars`` are live-complete (for the history returned here, the final
    snapshot and the one before it); earlier frames are warm-up scaffolding
    that can miss seeds which were still live at those earlier closes. Every
    caller therefore roots the window at its FIRST consumed boundary — the
    dashboard consumes one boundary, the forward runner consumes ``B >=
    first_as_of >= replay_start + max_bars + 1`` — so every consumed snapshot
    is exactly what a genesis replay would produce for its live setups.
    """

    as_of = require_utc_datetime(as_of, field_name="as_of")
    interval = interval_for_timeframe(timeframe)
    max_bars = max(
        parameters.continuation_max_bars,
        parameters.reversal_max_bars,
        parameters.range_max_bars,
    )
    return as_of - (max_bars + 1) * interval


class QualificationService:
    """Correctness-first replay; bounded warm-up keeps callers independent of history depth."""

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
        start_at: datetime | None = None,
    ) -> tuple[QualificationSnapshot, ...]:
        as_of = require_utc_datetime(as_of, field_name="as_of")
        interval = interval_for_timeframe(timeframe)
        last_open = latest_closed_candle_open_time(as_of, timeframe)
        if last_open + interval != as_of:
            raise ValueError("as_of must be a base candle-close boundary")
        p = parameters or QualificationParameters()
        sp = structure_parameters or MarketStructureParameters()
        return enumerate_qualifications(
            self.build_frames(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                as_of=as_of,
                parameters=p,
                pattern_parameters=pattern_parameters,
                structure_parameters=sp,
                start_at=start_at,
            ),
            as_of=as_of,
            parameters=p,
            known_since=known_since,
        )

    def build_frames(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        parameters: QualificationParameters | None = None,
        pattern_parameters: PatternLiquidityParameters | None = None,
        structure_parameters: MarketStructureParameters | None = None,
        start_at: datetime | None = None,
    ) -> tuple[QualificationFrame, ...]:
        """Build one exact Step 3/4 frame per base candle-close boundary up to ``as_of``.

        A read-only refactor of the frame construction Steps 5 and 10 already
        perform: each frame is a real Step 4 snapshot at its own boundary plus
        same-as-of higher-timeframe Step 3 contexts. Nothing is resampled,
        sorted, repaired, or synthesized and no rule or threshold is applied
        here. Callers that need both the frames and the resulting snapshots
        (Step 11 replay, the Step 12 forward runner) can build frames once and
        hand them to :func:`enumerate_qualifications`.

        ``start_at`` bounds the replay to recent boundaries (see
        :func:`bounded_replay_start`); ``None`` replays from the first stored
        candle, preserving the original full-history behaviour. Frames are
        always consecutive closes ending exactly at ``as_of``. Only snapshots
        at ``B >= start_at + max_bars`` are live-complete; earlier frames are
        warm-up scaffolding, so callers must root ``start_at`` at least
        ``max_bars + 1`` closes before their first consumed boundary.
        """

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
        frames: list[QualificationFrame] = []
        at = stored.candles[0].timestamp + interval if stored.candles else as_of
        if start_at is not None:
            start = require_utc_datetime(start_at, field_name="start_at")
            start_open = latest_closed_candle_open_time(start, timeframe)
            if start_open + interval != start:
                raise ValueError("start_at must be a base candle-close boundary")
            if start > as_of:
                raise ValueError("start_at must not be after as_of")
            at = max(at, start)
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
            for higher_timeframe in p.higher_timeframes:
                expected = latest_closed_candle_open_time(at, higher_timeframe)
                candles = self.patterns.repository.get_candles(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=higher_timeframe,
                    end_time=expected,
                )
                higher.append(
                    build_higher_timeframe_context(
                        higher_timeframe,
                        candles.candles,
                        interval=interval_for_timeframe(higher_timeframe),
                        as_of=at,
                        expected_latest_closed_open_time=expected,
                        parameters=sp,
                        gaps=candles.gaps,
                    )
                )
            frames.append(QualificationFrame(source, tuple(higher)))
            at += interval
        return tuple(frames)

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
        start_at: datetime | None = None,
    ) -> QualificationSnapshot:
        return self.enumerate_snapshots(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            parameters=parameters,
            pattern_parameters=pattern_parameters,
            structure_parameters=structure_parameters,
            start_at=start_at,
        )[-1]
