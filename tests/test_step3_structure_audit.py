"""Component #2 audit: market-structure engine (Step 3) regression tests.

Step 3 was audited for determinism, closed-candle-only anti-lookahead
behaviour, gap honesty, and fail-closed handling of valid-but-degenerate
input. These tests pin the two repairs:

* an all-zero reference window is valid Step 2 data (zero volume is
  accepted), so ``calculate_volume`` reports it explicitly instead of
  raising ``ValueError`` from a division by zero;
* ``wilder_average`` validates its period with ``require_int`` (bools and
  non-integers rejected; the ``period >= 1`` message is unchanged).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from market_structure_fixtures import (
    INTERVAL,
    analytic_candles,
)

from trading_assistant.market_structure import (
    VolumeParameters,
    calculate_volume,
    wilder_average,
)
from trading_assistant.market_structure.volume import ZERO_REFERENCE_VOLUME


def _as_of_after(candles):
    return candles[-1].timestamp + INTERVAL


def test_all_zero_reference_volume_is_reported_not_raised() -> None:
    candles = analytic_candles(
        [("100", "101", "99", "100", "0")] * 6,
        # Five zero-volume closes form the reference window; the newest
        # closed candle also has zero volume. All of it is valid data.
    )
    context = calculate_volume(
        candles,
        interval=INTERVAL,
        as_of=_as_of_after(candles),
        parameters=VolumeParameters(period=5),
    )
    assert context.sufficient is True
    assert context.reason == ZERO_REFERENCE_VOLUME == "zero_reference_volume"
    assert context.reference_average_volume == Decimal(0)
    assert context.relative_volume is None
    assert context.current_volume == Decimal(0)


def test_zero_reference_with_nonzero_current_stays_explicit() -> None:
    rows = [("100", "101", "99", "100", "0")] * 5 + [
        ("100", "101", "99", "100", "7")
    ]
    context = calculate_volume(
        analytic_candles(rows),
        interval=INTERVAL,
        as_of=_as_of_after(analytic_candles(rows)),
        parameters=VolumeParameters(period=5),
    )
    assert context.sufficient is True
    assert context.reason == "zero_reference_volume"
    assert context.reference_average_volume == Decimal(0)
    assert context.relative_volume is None
    assert context.current_volume == Decimal(7)


def test_wilder_average_rejects_non_integer_periods() -> None:
    values = (Decimal(10), Decimal(8), Decimal(10), Decimal(12))
    with pytest.raises(TypeError, match="period must be an integer"):
        wilder_average(values, period=True)
    with pytest.raises(TypeError, match="period must be an integer"):
        wilder_average(values, period=3.0)
    with pytest.raises(ValueError, match=r"period must be an integer >= 1"):
        wilder_average(values, period=0)
    # Valid behaviour is unchanged (Wilder RMA of 10, 8, 10, 12 over 3).
    assert wilder_average(values, period=3) == Decimal("10.22222222")
