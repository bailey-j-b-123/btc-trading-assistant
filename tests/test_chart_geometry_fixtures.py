"""Offline synthetic candle fixtures for the chart-geometry detector tests.

**These fixtures are synthetic and offline.** They are built from explicit
numbers written in this file; nothing here contacts an exchange, reads
``data/``, ``backups/``, or any database, or generates a display shape. They are
not real market history and must never be described as such, and no test uses
them to fabricate a trading decision, a plan, or an outcome.

Construction, stated exactly so every expected number in the tests can be
checked by hand
-------------------------------------------------------------------------------
``synthetic_series(pivots, leg=4)`` places pivot ``k`` at candle index
``k * leg`` and interpolates closes linearly between consecutive pivots:

* ``open[i]  = close[i - 1]`` (the first candle opens at the first pivot price);
* ``close[i]`` is the linear interpolation of the pivot prices;
* ordinary candles carry a wick of ``WICK`` on both sides:
  ``high = max(open, close) + WICK``, ``low = min(open, close) - WICK``;
* a candle that *is* a pivot carries ``WICK + PIVOT_BUMP`` on both sides.

The pivot bump is what makes every intended pivot a **strict** local extreme
under the Step 3 strict tie policy: without it, the candle immediately after a
peak would share the peak's high (``max(open, close)`` is the peak price on both
candles) and Step 3 would reject both. With it, the swing prices are exactly

    swing high at a pivot = pivot price + (WICK + PIVOT_BUMP)
    swing low  at a pivot = pivot price - (WICK + PIVOT_BUMP)

which is ``pivot +/- SWING_OFFSET`` — a single documented constant, so a test can
state the exact expected boundary anchor prices in plain arithmetic.

Pivot ``0`` sits at candle index ``0`` and therefore has no left window: it can
never be a confirmed swing. Fixtures use it as a lead-in that creates the first
real pivot, and it is documented as such wherever it appears.

``tail`` rows are appended verbatim as explicit ``(open, high, low, close)``
candles after the pivot series, so a test can place an exact breakout, an exact
invalidation, or an exact do-nothing candle at a known index.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_assistant.chart_geometry import (
    ChartGeometryFamily,
    ChartGeometryParameters,
    ChartGeometrySnapshot,
    detect_chart_geometry,
)
from trading_assistant.market_data.types import Candle

EPOCH = datetime(2024, 1, 1, tzinfo=UTC)
INTERVAL = timedelta(hours=1)
EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

#: Ordinary-candle wick, in price units.
WICK = Decimal("0.05")
#: Extra wick on a pivot candle, so the pivot is a strict local extreme.
PIVOT_BUMP = Decimal("0.05")
#: Exact distance between a pivot price and its confirmed swing price.
SWING_OFFSET = WICK + PIVOT_BUMP

#: Default leg length in candles between two pivots. Four candles keep the
#: Step 3 default 2/2 swing windows of neighbouring pivots disjoint.
LEG = 4

#: Default swing windows used by the detectors under test (Step 3 defaults).
SWING_LEFT = 2
SWING_RIGHT = 2


# --- Fixture definitions, with the arithmetic each test relies on ------------
#
# SWING_OFFSET = 0.10, so a pivot priced P contributes a swing high of P + 0.10
# and a swing low of P - 0.10.

# ascending_triangle: pivots (lead 100), high 110 @4, low 104 @8, high 110.1 @12,
# low 108 @16.
#   upper 110.10 @4 -> 110.20 @12 : slope 0.10/8 = 0.0125
#                                   travel 0.10/110.15*100 = 0.09078529%  (flat)
#   lower 103.90 @8 -> 107.90 @16 : slope 4.00/8 = 0.5
#                                   travel 4/105.90*100 = 3.77714825%    (rising)
#   gap @4 = 110.10 - (103.90 - 0.5*4) = 8.20
#   gap @16 = (110.20 + 0.0125*4) - 107.90 = 2.35
#   contraction = (8.20 - 2.35)/8.20*100 = 71.34146341%
#   apex = (103.90 - 110.10 - 0.5*4)/(0.0125 - 0.5) = 16.82051282 candles after @4
ASCENDING_PIVOTS = [100, 110, 104, "110.1", 108]
ASCENDING_TAIL = [
    ("108", "109.2", "108.0", "109.0"),  # index 17, inside both boundaries
    ("109.0", "109.7", "109.0", "109.5"),  # index 18, confirms the last component
    ("109.6", "110.0", "109.6", "109.8"),  # index 19, first eligible candle, inside
    ("109.8", "111.7", "109.7", "111.5"),  # index 20, closes above the upper line
]

# descending_triangle: pivots (lead 110), low 100 @4, high 106 @8, low 99.9 @12,
# high 102 @16.
#   upper 106.10 @8 -> 102.10 @16 : slope -4/8 = -0.5
#                                   travel -4/104.10*100 = -3.84245917% (falling)
#   lower 99.90 @4 -> 99.80 @12   : slope -0.10/8 = -0.0125
#                                   travel -0.10/99.85*100 = -0.10015023% (flat)
#   gap @4 = (106.10 + 0.5*4) - 99.90 = 8.20 ; gap @16 = 102.10 - 99.75 = 2.35
#   apex = (99.90 - 106.10 - (-0.0125 * -4))/(-0.5 + 0.0125) = 12.82051282 after @8
DESCENDING_PIVOTS = [110, 100, 106, "99.9", 102]
DESCENDING_TAIL = [
    ("102", "102.05", "100.4", "100.5"),  # index 17
    ("100.5", "100.6", "99.95", "100.0"),  # index 18
    ("100.0", "100.1", "98.4", "98.5"),  # index 19, closes below the lower line
]

# symmetrical_triangle: pivots (lead 100), high 110 @4, low 100 @8, high 107 @12,
# low 103 @16.
#   upper 110.10 @4 -> 107.10 @12 : slope -3/8 = -0.375
#                                   travel -3/108.60*100 = -2.76243094%
#   lower 99.90 @8 -> 102.90 @16  : slope 3/8 = 0.375
#                                   travel 3/101.40*100 = 2.95857988%
#   gap @4 = 110.10 - (99.90 - 0.375*4) = 11.70 ; gap @16 = 105.60 - 102.90 = 2.70
#   apex = (99.90 - 110.10 - 0.375*4)/(-0.375 - 0.375) = 15.6 candles after @4,
#   i.e. at index 19.6: index 19 is still inside, index 20 is at/after the apex.
SYMMETRICAL_PIVOTS = [100, 110, 100, 107, 103]
SYMMETRICAL_CONFIRM_TAIL = [
    ("103.2", "104.4", "103.2", "104.2"),  # index 17
    ("104.2", "104.3", "103.7", "104.0"),  # index 18
    ("104.0", "105.6", "103.9", "105.5"),  # index 19, closes above the upper line
]
SYMMETRICAL_APEX_TAIL = [
    ("103.2", "104.4", "103.2", "104.2"),  # index 17
    ("104.2", "104.3", "103.7", "104.0"),  # index 18
    ("104.0", "104.4", "103.9", "104.2"),  # index 19, still inside (104.025..104.475)
    ("104.2", "104.5", "104.0", "104.3"),  # index 20, past the apex: lines crossed
    ("104.3", "108.0", "104.2", "107.5"),  # index 21, far above the upper line
    ("107.5", "111.0", "107.4", "110.5"),  # index 22, further above again
]

# rising_wedge: pivots (lead 100), high 108 @4, low 102 @8, high 110 @12, low 106 @16.
#   upper 108.10 @4 -> 110.10 @12 : slope 2/8 = 0.25
#                                   travel 2/109.10*100 = 1.83318057%   (rising)
#   lower 101.90 @8 -> 105.90 @16 : slope 4/8 = 0.5
#                                   travel 4/103.90*100 = 3.84985563%   (rising)
#   upper rises more slowly than the lower line, so the gap narrows:
#   gap @4 = 108.10 - (101.90 - 0.5*4) = 8.20 ; gap @16 = 111.10 - 105.90 = 5.20
#   apex = (101.90 - 108.10 - 0.5*4)/(0.25 - 0.5) = 32.8 candles after @4
RISING_PIVOTS = [100, 108, 102, 110, 106]
RISING_TAIL = [
    ("106", "107.2", "106.0", "107.0"),  # index 17
    ("107.0", "107.7", "106.8", "107.5"),  # index 18
    ("107.5", "107.6", "106.4", "106.5"),  # index 19, closes below the lower line
]

# falling_wedge: pivots (lead 100), high 108 @4, low 102 @8, high 104 @12, low 100 @16.
#   upper 108.10 @4 -> 104.10 @12 : slope -4/8 = -0.5
#                                   travel -4/106.10*100 = -3.77002828% (falling)
#   lower 101.90 @8 -> 99.90 @16  : slope -2/8 = -0.25
#                                   travel -2/100.90*100 = -1.98216056% (falling)
#   the upper line falls faster, so the gap narrows:
#   gap @4 = 108.10 - (101.90 + 0.25*4) = 5.20 ; gap @16 = 102.10 - 99.90 = 2.20
#   apex = (101.90 - 108.10 + 0.25*4)/(-0.5 + 0.25) = 20.8 candles after @4
FALLING_PIVOTS = [100, 108, 102, 104, 100]
FALLING_TAIL = [
    ("100", "100.7", "100.0", "100.5"),  # index 17
    ("100.5", "100.7", "100.1", "100.2"),  # index 18
    ("100.2", "101.6", "100.1", "101.5"),  # index 19, closes above the upper line
]

# bull_flag: pivots (lead 104), low 100 @4, high 110 @8, low 106 @12, high 108 @16,
# low 106.1 @20.
#   pole 99.90 @4 -> 110.10 @8 : move 10.20, 10.20/99.90*100 = 10.21021021%, span 4
#   body pivots 105.90 @12, 108.10 @16, 106.00 @20 all inside (99.90, 110.10]
#   deepest body low 105.90 -> retrace (110.10-105.90)/10.20*100 = 41.17647059%
#   upper 110.10 @8 -> 108.10 @16 : slope -0.25, travel -2/109.10*100 = -1.83318057%
#   lower 105.90 @12 -> 106.00 @20: slope 0.0125, travel 0.10/105.95*100 = 0.09438414%
#   body gaps @12/@16/@20 = 3.20 / 2.15 / 1.10 (non-expanding)
BULL_FLAG_PIVOTS = [104, 100, 110, 106, 108, "106.1"]
BULL_FLAG_TAIL = [
    ("106.2", "106.6", "106.2", "106.4"),  # index 21
    ("106.4", "106.6", "106.3", "106.5"),  # index 22, confirms the last component
    ("106.5", "106.9", "106.4", "106.7"),  # index 23, closes above the upper line
    ("106.7", "107.6", "106.6", "107.5"),  # index 24, further above
]

# bear_flag: pivots (lead 100), high 104 @4, low 94 @8, high 98 @12, low 96 @16,
# high 97.9 @20.
#   pole 104.10 @4 -> 93.90 @8 : move 10.20, 10.20/104.10*100 = 9.79827090%, span 4
#   highest body high 98.10 -> retrace (98.10-93.90)/10.20*100 = 41.17647059%
#   upper 98.10 @12 -> 98.00 @20 : slope -0.0125, travel -0.10/98.05*100 = -0.10198878%
#   lower 93.90 @8 -> 95.90 @16  : slope 0.25, travel 2/94.90*100 = 2.10748156%
#   body gaps @12/@16/@20 = 3.20 / 2.15 / 1.10 (non-expanding)
BEAR_FLAG_PIVOTS = [100, 104, 94, 98, 96, "97.9"]
BEAR_FLAG_TAIL = [
    ("97.9", "97.95", "97.5", "97.6"),  # index 21
    ("97.6", "97.9", "97.55", "97.7"),  # index 22, confirms the last component
    ("97.7", "97.85", "97.6", "97.75"),  # index 23, still inside
    ("97.75", "97.8", "96.4", "96.5"),  # index 24, closes below the lower line
]

# cup_and_handle: pivots (lead 100), left rim 110 @4, trough low 100 @8, interior
# high 104 @12, trough low 100.5 @16, right rim 109.5 @20, handle low 106 @24,
# handle high 108 @28, handle low 106.5 @32.
#   rims 110.10 @4 and 109.60 @20 : difference 0.50/110.10*100 = 0.45413261%
#   rim line slope -0.50/16 = -0.03125, travel -0.50/109.85*100 = -0.45516614%
#   deepest trough low 99.90 @8, rim there = 110.10 - 0.03125*4 = 109.975
#       depth 10.075, 10.075/109.975*100 = 9.16117299%
#   trough base difference 0.50/99.90*100 = 0.50050050%
#   interior prominence (104.10-100.40)/10.075*100 = 36.72456576%
#   descent leg 4 candles, ascent leg 12 candles
#   recovery (109.60-99.90)/(110.10-99.90)*100 = 95.09803922%
#   handle lowest 105.90, handle retrace (109.60-105.90)/9.70*100 = 38.14432990%
#   handle lower line 105.90 @24 -> 106.40 @32 : slope 0.0625,
#       travel 0.50/106.15*100 = 0.47103156%
#   handle gaps @24/@28/@32 = 3.575 / 3.20 / 2.825 (non-expanding)
CUP_PIVOTS = [100, 110, 100, 104, "100.5", "109.5", 106, 108, "106.5"]
CUP_TAIL = [
    ("106.6", "107.2", "106.6", "107.0"),  # index 33
    ("107.0", "107.7", "106.9", "107.5"),  # index 34, confirms the last component
    ("107.5", "108.2", "107.4", "108.0"),  # index 35, first eligible candle, inside
    ("108.0", "110.2", "107.9", "110.0"),  # index 36, closes above the rim line
]



def open_time(index: int, *, start: datetime = EPOCH, interval: timedelta = INTERVAL) -> datetime:
    """Return the candle open time at a fixture index."""

    return start + interval * index


def close_time(index: int, *, start: datetime = EPOCH, interval: timedelta = INTERVAL) -> datetime:
    """Return the instant the candle at a fixture index has fully closed."""

    return start + interval * (index + 1)


def _candle(
    index: int,
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    *,
    start: datetime = EPOCH,
    interval: timedelta = INTERVAL,
    volume: str = "10",
    timeframe: str = TIMEFRAME,
) -> Candle:
    return Candle(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=timeframe,
        timestamp=start + interval * index,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=Decimal(volume),
    )


def synthetic_series(
    pivots: Sequence[str | Decimal],
    *,
    leg: int = LEG,
    wick: Decimal = WICK,
    pivot_bump: Decimal = PIVOT_BUMP,
    tail: Sequence[Sequence[str | Decimal]] = (),
    start: datetime = EPOCH,
    interval: timedelta = INTERVAL,
    volume: str = "10",
    timeframe: str = TIMEFRAME,
) -> tuple[Candle, ...]:
    """Build an explicit synthetic OHLCV series from pivot prices.

    ``tail`` rows are ``(open, high, low, close)`` candles appended immediately
    after the last pivot candle, verbatim.
    """

    if len(pivots) < 2:
        raise ValueError("synthetic_series needs at least two pivots")
    prices = [Decimal(str(pivots[0]))]
    pivot_indices = {0}
    for pivot_number in range(1, len(pivots)):
        pivot_indices.add(pivot_number * leg)
        begin = Decimal(str(pivots[pivot_number - 1]))
        end = Decimal(str(pivots[pivot_number]))
        step = (end - begin) / Decimal(leg)
        prices.extend(begin + step * Decimal(offset) for offset in range(1, leg + 1))

    candles: list[Candle] = []
    previous_close = prices[0]
    for index, close in enumerate(prices):
        body_wick = wick + pivot_bump if index in pivot_indices else wick
        candles.append(
            _candle(
                index,
                previous_close,
                max(previous_close, close) + body_wick,
                min(previous_close, close) - body_wick,
                close,
                start=start,
                interval=interval,
                volume=volume,
                timeframe=timeframe,
            )
        )
        previous_close = close

    for offset, row in enumerate(tail):
        if len(row) != 4:
            raise ValueError("each tail row must be exactly (open, high, low, close)")
        index = len(prices) + offset
        open_, high, low, close = (Decimal(str(value)) for value in row)
        candles.append(
            _candle(
                index,
                open_,
                high,
                low,
                close,
                start=start,
                interval=interval,
                volume=volume,
                timeframe=timeframe,
            )
        )
    return tuple(candles)


def last_close_time(candles: Sequence[Candle], interval: timedelta = INTERVAL) -> datetime:
    """Return the instant the final candle in a series has closed."""

    return candles[-1].timestamp + interval


def detect(
    candles: Sequence[Candle],
    *,
    as_of: datetime | None = None,
    parameters: ChartGeometryParameters | None = None,
    exchange: str = EXCHANGE,
    symbol: str = SYMBOL,
    timeframe: str = TIMEFRAME,
) -> ChartGeometrySnapshot:
    """Run the detector over one synthetic series with the fixture instrument."""

    return detect_chart_geometry(
        candles,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        as_of=as_of if as_of is not None else last_close_time(candles, INTERVAL),
        parameters=parameters,
    )


def families_of(snapshot: ChartGeometrySnapshot) -> tuple[ChartGeometryFamily, ...]:
    """Return the distinct families a snapshot describes."""

    return tuple(sorted({formation.family for formation in snapshot.formations}, key=str))


def with_gap(
    candles: Sequence[Candle],
    *,
    drop_index: int,
    interval: timedelta = INTERVAL,
) -> tuple[Candle, ...]:
    """Return a copy of a series with one candle removed, creating a real gap.

    The remaining candles keep their original open times, so the hole is genuine
    missing history rather than a re-spaced series.
    """

    return tuple(candle for index, candle in enumerate(candles) if index != drop_index)


def replacing(
    candles: Sequence[Candle], index: int, *, close: str, high: str | None = None, low: str | None = None
) -> tuple[Candle, ...]:
    """Return a copy of a series with one candle's close (and optionally high/low) replaced."""

    from dataclasses import replace

    result = list(candles)
    target = result[index]
    new_close = Decimal(close)
    result[index] = replace(
        target,
        close=new_close,
        high=Decimal(high) if high is not None else max(target.high, new_close),
        low=Decimal(low) if low is not None else min(target.low, new_close),
    )
    return tuple(result)
