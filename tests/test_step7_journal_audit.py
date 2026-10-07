"""Component #6 audit: journaling/statistics (Steps 7-8) regression tests.

``observe_outcome`` strictly validates candle identity, alignment, window,
and duplicates, but never checked the OHLC range its touch semantics read:
an inverted (``high < low``) or NaN range silently produced a terminal state
instead of being refused. Stored candles are Step 2-validated so production
observations are unchanged; incoherent direct inputs now fail loudly.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D

import pytest
from test_journaling import candle, levels, observe

from trading_assistant.journaling import OutcomeStatus


def test_inverted_candle_range_is_refused() -> None:
    plan = levels()
    bad = candle(0, open_="100", high="95", low="105", close="100")
    with pytest.raises(ValueError, match="below candle low"):
        observe(plan, [bad], 0)


def test_non_finite_candle_range_is_refused() -> None:
    plan = levels()
    bad = replace(
        candle(0, open_="100", high="101", low="99", close="100"),
        low=D("NaN"),
    )
    with pytest.raises(ValueError, match="must be finite"):
        observe(plan, [bad], 0)


def test_non_decimal_candle_range_is_refused() -> None:
    plan = levels()
    bad = replace(
        candle(0, open_="100", high="101", low="99", close="100"),
        high=101.5,
    )
    with pytest.raises(TypeError, match="must be a Decimal"):
        observe(plan, [bad], 0)


def test_coherent_observation_is_unchanged() -> None:
    plan = levels()
    quiet = candle(0, open_="100", high="101", low="99", close="100")
    observation = observe(plan, [quiet], 0)
    assert observation.status is OutcomeStatus.OPEN_AT_CUTOFF
    assert observation.entry_reached and observation.entry_ordered
