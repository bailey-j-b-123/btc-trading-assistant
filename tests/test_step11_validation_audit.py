"""Component #8 audit: historical validation (Step 11) regression tests.

* ``directional_r`` (shared with Step 12 forward reporting) silently treated
  any non-bullish direction as bearish and any negative risk as valid,
  producing sign-flipped R values; invalid inputs now fail loudly, mirroring
  the Step 8 guards.
* ``ValidationConfig`` type-checked ``friction`` but not ``split``; a
  non-split value now fails at construction instead of later attribute
  access.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D

import pytest
from test_historical_validation import _levels
from test_journaling import candle, observe

from trading_assistant.historical_validation import ValidationConfig
from trading_assistant.historical_validation.metrics import directional_r
from trading_assistant.journaling.types import OutcomeStatus


def _stopped_long():
    plan = _levels(direction="bullish")
    entry_bar = candle(1, open_="100", high="101", low="99", close="100")
    stop_bar = candle(2, open_="94", high="95", low="89", close="94")
    observation = observe(plan, [entry_bar, stop_bar], 2)
    assert observation.status is OutcomeStatus.STOPPED
    return observation


def test_coherent_directional_r_is_unchanged() -> None:
    observation = _stopped_long()
    assert directional_r(observation, observation.stop_level) == D("-1")


def test_invalid_direction_is_refused_not_treated_as_bearish() -> None:
    observation = replace(_stopped_long(), direction="sideways")
    with pytest.raises(ValueError, match="invalid direction"):
        directional_r(observation, observation.stop_level)


def test_non_positive_risk_is_refused_not_sign_flipped() -> None:
    observation = replace(_stopped_long(), risk_per_unit=D("-10"))
    with pytest.raises(ValueError, match="non-positive risk_per_unit"):
        directional_r(observation, observation.stop_level)


def test_non_split_config_value_is_rejected_at_construction() -> None:
    with pytest.raises(TypeError, match="ChronologicalSplit or None"):
        ValidationConfig(split="not-a-split")
