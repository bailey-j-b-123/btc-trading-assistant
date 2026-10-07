"""Deterministic multi-timeframe fixtures for the Step 13 hierarchy tests.

The fixtures build ONE coherent synthetic market across four timeframes so the
REAL engines (Step 3 structure, Step 4 patterns, Step 5 qualification) can be
exercised end to end without any exchange:

* ``1h``  — the proven ``QUALIFYING_ROWS`` series from ``web_fixtures`` (the
  real Step 5 engine reaches QUALIFIED at ``DECISION_TIME``), stored exactly as
  that fixture stores it;
* ``5m``  — twelve sub-candles per 1H candle, walking linearly from the
  previous 1H close to that 1H close (the 5M aggregate close equals the stored
  1H close);
* ``15m`` — aggregates of the 5M sub-candles (three per 15M candle);
* ``4h``  — a deliberately INDEPENDENT zigzag series (history before the 1H
  window) constructed to produce a controlled context regime: a rising zigzag
  (ALIGNED with the bullish 1H setup) or a falling zigzag (COUNTER_TREND).

Scenario candles for one extra hour (``SCENARIO_HOUR``) are crafted relative to
the evaluated setup's reference band, so confirmation/execution states can be
driven deterministically. Nothing here contacts an exchange; every number is
explicit so the fixtures stay reproducible and reviewable.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import latest_closed_candle_open_time
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.multi_timeframe.hierarchy import default_hierarchy
from trading_assistant.multi_timeframe.service import MultiTimeframeService

from web_fixtures import EXCHANGE, QUALIFYING_ROWS, SYMBOL, bar

EPOCH = datetime(2024, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
FIVE_MINUTES = timedelta(minutes=5)

#: The 1H series is exactly the proven qualifying series.
TAIL_HOURS = len(QUALIFYING_ROWS)  # 21 hourly closes
DECISION_TIME = EPOCH + TAIL_HOURS * HOUR  # 2024-01-02T21:00:00Z (QUALIFIED)

#: One extra hour of 5M/15M scenario candles (closed by SCENARIO_DECISION_TIME).
SCENARIO_HOUR = TAIL_HOURS  # hour index 21 (opens 21:00, closes 22:00)
SCENARIO_DECISION_TIME = EPOCH + (SCENARIO_HOUR + 1) * HOUR  # 2024-01-02T22:00:00Z

#: The 4H series starts this many hours before EPOCH so the context timeframe
#: has enough history for a confirmed swing trend.
CONTEXT_HISTORY_HOURS = 96

WICK = Decimal("0.25")
FLAT_PRICE = Decimal("100")


# ---------------------------------------------------------------------------
# 1H series (the proven qualifying tail)
# ---------------------------------------------------------------------------


def one_hour_candles(symbol: str = SYMBOL, *, extra_hour: bool = False) -> tuple[Candle, ...]:
    """The exact ``QUALIFYING_ROWS`` 1H series (QUALIFIED at DECISION_TIME).

    With ``extra_hour`` the series gains one more 1H candle (hour 21, closing
    at 22:00 UTC) so the setup boundary is still known at
    ``SCENARIO_DECISION_TIME``; the setup remains QUALIFIED there.
    """

    rows = list(QUALIFYING_ROWS)
    if extra_hour:
        rows.append(QUALIFYING_ROWS[-1])  # hour 21 closes at the same 124
    candles = tuple(bar(index, price) for index, price in enumerate(rows))
    if symbol == SYMBOL:
        return candles
    return tuple(replace(candle, symbol=symbol) for candle in candles)


# ---------------------------------------------------------------------------
# 5M / 15M derivation from the 1H closes
# ---------------------------------------------------------------------------


def _five_minute_children(
    hour_index: int, previous_close: Decimal, target_close: Decimal, symbol: str
) -> tuple[Candle, ...]:
    """Twelve 5M candles walking linearly from ``previous_close`` to ``target_close``."""

    candles: list[Candle] = []
    open_price = previous_close
    for step in range(1, 13):
        close_price = previous_close + (target_close - previous_close) * Decimal(step) / Decimal(12)
        high = max(open_price, close_price) + WICK
        low = min(open_price, close_price) - WICK
        candles.append(
            Candle(
                exchange=EXCHANGE,
                symbol=symbol,
                timeframe="5m",
                timestamp=EPOCH + hour_index * HOUR + (step - 1) * FIVE_MINUTES,
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=Decimal("10"),
            )
        )
        open_price = close_price
    return tuple(candles)


def five_minute_candles(
    hours: int = TAIL_HOURS, symbol: str = SYMBOL, *, extra: Iterable[Candle] = ()
) -> tuple[Candle, ...]:
    """5M sub-candles for the first ``hours`` 1H candles, plus ``extra`` scenario candles."""

    rows = [Decimal(str(price)) for price in QUALIFYING_ROWS[:hours]]
    candles: list[Candle] = []
    previous = rows[0] if rows else FLAT_PRICE
    for hour_index, target in enumerate(rows):
        candles.extend(_five_minute_children(hour_index, previous, target, symbol))
        previous = target
    return tuple(candles) + tuple(extra)


def aggregate_candles(candles: tuple[Candle, ...], factor: int, timeframe: str) -> tuple[Candle, ...]:
    """Aggregate ``factor`` consecutive candles into one (open/high/low/close/volume)."""

    aggregated: list[Candle] = []
    for start in range(0, len(candles) - factor + 1, factor):
        group = candles[start : start + factor]
        aggregated.append(
            Candle(
                exchange=group[0].exchange,
                symbol=group[0].symbol,
                timeframe=timeframe,
                timestamp=group[0].timestamp,
                open=group[0].open,
                high=max(candle.high for candle in group),
                low=min(candle.low for candle in group),
                close=group[-1].close,
                volume=sum((candle.volume for candle in group), Decimal("0")),
            )
        )
    return tuple(aggregated)


def fifteen_minute_candles(
    hours: int = TAIL_HOURS, symbol: str = SYMBOL, *, extra: Iterable[Candle] = ()
) -> tuple[Candle, ...]:
    """15M candles aggregated from the derived 5M sub-candles (plus ``extra``)."""

    base = aggregate_candles(five_minute_candles(hours, symbol), 3, "15m")
    return base + tuple(extra)


# ---------------------------------------------------------------------------
# 4H context series (independent, controlled regime)
# ---------------------------------------------------------------------------

#: Candles per zigzag leg (pivot spacing; >> the 2+2 swing window).
ZIGZAG_LEG = 5

#: Rising zigzag pivots (bullish 4H structure) aligned with the bullish 1H setup.
ALIGNED_CONTEXT_PIVOTS = ("100", "112", "104", "116", "108", "120", "112", "124")

#: Falling zigzag pivots (bearish 4H structure) against the bullish 1H setup.
COUNTER_CONTEXT_PIVOTS = ("124", "112", "120", "108", "116", "104", "112", "100")


def _interpolated_zigzag(
    pivots: tuple[str, ...], *, leg: int, step: timedelta, end_open: datetime, symbol: str
) -> tuple[Candle, ...]:
    """Zigzag candles whose LAST candle opens at ``end_open`` (strict local extremes)."""

    prices = [Decimal(pivots[0])]
    for leg_start, leg_end in zip(pivots, pivots[1:]):
        start_value = Decimal(leg_start)
        end_value = Decimal(leg_end)
        step_size = (end_value - start_value) / Decimal(leg)
        prices.extend(start_value + step_size * Decimal(index) for index in range(1, leg + 1))
    candles: list[Candle] = []
    start = end_open - (len(prices) - 1) * step
    previous_close = prices[0]
    for index, price in enumerate(prices):
        wick = WICK if index % 2 == 0 else WICK / Decimal("2")
        candles.append(
            Candle(
                exchange=EXCHANGE,
                symbol=symbol,
                timeframe="4h",
                timestamp=start + index * step,
                open=previous_close,
                high=max(previous_close, price) + wick,
                low=min(previous_close, price) - wick,
                close=price,
                volume=Decimal("40"),
            )
        )
        previous_close = price
    return tuple(candles)


def four_hour_candles(*, aligned: bool = True, symbol: str = SYMBOL) -> tuple[Candle, ...]:
    """The controlled 4H context series, ending closed by DECISION_TIME.

    The last candle opens at the latest 4H boundary whose close is at or before
    ``DECISION_TIME`` (16:00→20:00 UTC), so the context layer is complete and
    current at the decision instant. The series is deliberately independent of
    the 1H series: its only job is a controlled context regime.
    """

    pivots = ALIGNED_CONTEXT_PIVOTS if aligned else COUNTER_CONTEXT_PIVOTS
    interval = interval_for_timeframe("4h")
    # The latest 4H-aligned candle open whose full interval closed by the
    # decision time (16:00→20:00 UTC for DECISION_TIME = 21:00 UTC).
    end_open = latest_closed_candle_open_time(DECISION_TIME, "4h")
    return _interpolated_zigzag(
        pivots, leg=ZIGZAG_LEG, step=interval, end_open=end_open, symbol=symbol
    )


# ---------------------------------------------------------------------------
# Scenario candles (one extra hour, crafted relative to a reference band)
# ---------------------------------------------------------------------------


def scenario_candles(
    *,
    band_low: Decimal,
    band_high: Decimal,
    direction: str = "bullish",
    closes_15m: tuple[str, ...] | None = None,
    closes_5m: tuple[str, ...] | None = None,
    symbol: str = SYMBOL,
) -> dict[str, tuple[Candle, ...]]:
    """One hour of 15M/5M scenario candles crafted relative to the setup band.

    ``closes_15m`` / ``closes_5m`` are explicit close prices (strings); the
    opens walk linearly from the last stored close (the final 1H close of the
    qualifying tail). When omitted, the candles simply continue the walk.
    """

    last_close = Decimal(str(QUALIFYING_ROWS[-1]))
    candles_15m: list[Candle] = []
    candles_5m: list[Candle] = []
    if closes_15m is not None:
        open_price = last_close
        for index, close_text in enumerate(closes_15m):
            close = Decimal(close_text)
            candles_15m.append(
                Candle(
                    exchange=EXCHANGE,
                    symbol=symbol,
                    timeframe="15m",
                    timestamp=EPOCH + SCENARIO_HOUR * HOUR + index * timedelta(minutes=15),
                    open=open_price,
                    high=max(open_price, close) + WICK,
                    low=min(open_price, close) - WICK,
                    close=close,
                    volume=Decimal("30"),
                )
            )
            open_price = close
    if closes_5m is not None:
        open_price = last_close
        for index, close_text in enumerate(closes_5m):
            close = Decimal(close_text)
            candles_5m.append(
                Candle(
                    exchange=EXCHANGE,
                    symbol=symbol,
                    timeframe="5m",
                    timestamp=EPOCH + SCENARIO_HOUR * HOUR + index * FIVE_MINUTES,
                    open=open_price,
                    high=max(open_price, close) + WICK,
                    low=min(open_price, close) - WICK,
                    close=close,
                    volume=Decimal("10"),
                )
            )
            open_price = close
    return {"15m": tuple(candles_15m), "5m": tuple(candles_5m)}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def hierarchy_candles(
    *, aligned: bool = True, symbol: str = SYMBOL, scenario: dict[str, tuple[Candle, ...]] | None = None
) -> dict[str, tuple[Candle, ...]]:
    """All four timeframes for one fixture variant (plus optional scenario candles)."""

    scenario = scenario or {}
    has_scenario = bool(scenario.get("5m") or scenario.get("15m"))
    return {
        "1h": one_hour_candles(symbol, extra_hour=has_scenario),
        "5m": five_minute_candles(symbol=symbol, extra=scenario.get("5m", ())),
        "15m": fifteen_minute_candles(symbol=symbol, extra=scenario.get("15m", ())),
        "4h": four_hour_candles(aligned=aligned, symbol=symbol),
    }


def insert_hierarchy(engine, candles_by_timeframe: dict[str, tuple[Candle, ...]]) -> None:
    """Store every timeframe through the idempotent candle repository."""

    repository = CandleRepository(engine)
    for timeframe in ("5m", "15m", "1h", "4h"):
        repository.insert_unchanged_or_new(candles_by_timeframe[timeframe])


def make_service(
    engine,
    *,
    clock=None,
    aligned: bool = True,
    hierarchy=None,
    ledger_start=None,
    market_data_service=None,
    settings=None,
    **kwargs,
) -> MultiTimeframeService:
    """A MultiTimeframeService over a migrated engine with the fixture clock."""

    from web_fixtures import make_settings

    resolved_settings = settings
    if resolved_settings is None:

        url = str(engine.url)
        resolved_settings = make_settings(url)
    return MultiTimeframeService(
        engine,
        settings=resolved_settings,
        clock=clock if clock is not None else (lambda: DECISION_TIME),
        hierarchy=hierarchy if hierarchy is not None else default_hierarchy(),
        ledger_start=ledger_start,
        market_data_service=market_data_service,
        **kwargs,
    )


def evaluate_band(service: MultiTimeframeService, *, decision_time: datetime = DECISION_TIME):
    """Evaluate once and return the active setup's reference band + created_at."""

    snapshot = service.evaluate(decision_time=decision_time)
    return snapshot


__all__ = [
    "ALIGNED_CONTEXT_PIVOTS",
    "COUNTER_CONTEXT_PIVOTS",
    "DECISION_TIME",
    "EPOCH",
    "EXCHANGE",
    "SCENARIO_DECISION_TIME",
    "SCENARIO_HOUR",
    "SYMBOL",
    "TAIL_HOURS",
    "aggregate_candles",
    "evaluate_band",
    "fifteen_minute_candles",
    "five_minute_candles",
    "four_hour_candles",
    "hierarchy_candles",
    "insert_hierarchy",
    "make_service",
    "one_hour_candles",
    "scenario_candles",
]
