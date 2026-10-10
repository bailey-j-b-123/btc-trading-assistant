"""Hierarchy ledger: a boundary is recorded once; incomplete data is held, not frozen.

The hierarchy ledger has one row per decision boundary (unique on the decision
time per hierarchy version), so a recorded boundary is never re-run. An
incomplete evaluation whose candles may still arrive is HELD, not recorded, and
the runner stops at it to keep boundaries chronological. A gap that can never be
filled (a candle missing mid-history) is a final verdict and is recorded once.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from multi_timeframe_fixtures import (
    DECISION_TIME,
    EXCHANGE,
    SYMBOL,
    hierarchy_candles,
    insert_hierarchy,
    make_service,
)
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import latest_closed_candle_open_time
from trading_assistant.multi_timeframe.service import RunnerStatus
from web_fixtures import migrated_engine


def _engine(tmp_path, *, drop=()):
    """Migrated database with every hierarchy timeframe, minus the ``drop`` candles."""

    engine, _url = migrated_engine(tmp_path, "hold.sqlite3")
    candles = hierarchy_candles(aligned=True)
    kept = {
        timeframe: tuple(
            candle
            for candle in rows
            if (timeframe, candle.timestamp) not in drop
        )
        for timeframe, rows in candles.items()
    }
    insert_hierarchy(engine, kept)
    return engine


def _recorded(service) -> int:
    return service.ledger.counts(exchange=EXCHANGE, symbol=SYMBOL)["observations"]


def test_tail_candle_still_arriving_is_held_then_recorded_exactly_once(tmp_path):
    # The 4h candle that closed by the decision time has not been stored yet.
    expected_4h = latest_closed_candle_open_time(DECISION_TIME, "4h")
    later_4h = tuple(
        candle
        for candle in hierarchy_candles(aligned=True)["4h"]
        if candle.timestamp >= expected_4h
    )
    engine = _engine(tmp_path, drop={("4h", candle.timestamp) for candle in later_4h})
    service = make_service(engine)

    held = service.run_once(now=DECISION_TIME, refresh_market_data=False)
    assert held.status is RunnerStatus.IDLE
    assert held.observations_recorded == 0
    assert "held decision boundary" in held.detail
    assert "4h" in held.detail
    assert _recorded(service) == 0  # nothing frozen while the candle may still arrive

    # The candle arrives: the same boundary is now evaluated and recorded once.
    CandleRepository(engine).insert_unchanged_or_new(later_4h)
    recovered = service.run_once(now=DECISION_TIME, refresh_market_data=False)
    assert recovered.status is RunnerStatus.PROCESSED
    assert recovered.processed_boundaries == (DECISION_TIME,)
    assert _recorded(service) == 1

    # Further passes never re-run the recorded boundary (no unique-key collision).
    again = service.run_once(now=DECISION_TIME, refresh_market_data=False)
    assert again.status is RunnerStatus.IDLE
    assert _recorded(service) == 1


def test_permanent_mid_history_gap_is_recorded_once_and_not_held(tmp_path):
    gap_open = DECISION_TIME - timedelta(hours=20)
    engine = _engine(tmp_path, drop={("1h", gap_open)})
    service = make_service(engine)

    result = service.run_once(now=DECISION_TIME, refresh_market_data=False)
    assert result.status is RunnerStatus.PROCESSED
    assert "held" not in result.detail
    assert _recorded(service) == 1
    observation = service.ledger.observations(exchange=EXCHANGE, symbol=SYMBOL)[-1]
    assert observation.status != "evaluated"  # an honest incomplete verdict, kept
    assert observation.decision_time == DECISION_TIME

    # The gap is final: later passes do not re-record it and do not hold.
    later = service.run_once(now=DECISION_TIME, refresh_market_data=False)
    assert later.status is RunnerStatus.IDLE
    assert _recorded(service) == 1


def test_gap_detail_row_count_matches_ledger(tmp_path):
    engine = _engine(tmp_path)
    service = make_service(engine)
    service.run_once(now=DECISION_TIME, refresh_market_data=False)
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT COUNT(*) FROM forward_hierarchy_observations")
        ).scalar_one()
    assert rows == _recorded(service) == 1
