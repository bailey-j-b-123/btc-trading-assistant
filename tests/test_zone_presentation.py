"""Regression tests for display-only support/resistance presentation.

These pin the audit findings: a band that contains the close must never be
labelled support or resistance; overlapping bands must not stack; the chart
shows only the nearest bands; isolated single swings are flagged; and nothing
in the presentation changes the detector's raw zones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_assistant.web.zone_presentation import (
    DISPLAY_INSIDE_MAX,
    DISPLAY_PER_SIDE,
    FADE_AFTER_CANDLES,
    POSITION_ABOVE,
    POSITION_BELOW,
    POSITION_INSIDE,
    position_of,
    present_zones,
)

AS_OF = datetime(2026, 10, 10, 17, 0, tzinfo=UTC)


def zone(low, high, *, touches=2, highs=1, lows=1, last_back_hours=2, role="resistance", first_back_hours=30):
    return {
        "role": role,
        "band_low": str(low),
        "band_high": str(high),
        "touch_count": touches,
        "high_source_count": highs,
        "low_source_count": lows,
        "first_observed_timestamp": (AS_OF - timedelta(hours=first_back_hours)).isoformat().replace("+00:00", "Z"),
        "last_tested_timestamp": (AS_OF - timedelta(hours=last_back_hours)).isoformat().replace("+00:00", "Z"),
        "source_swing_timestamps": [(AS_OF - timedelta(hours=last_back_hours)).isoformat().replace("+00:00", "Z")],
    }


def present(zones, close="60000", timeframe="1h"):
    return present_zones(zones, timeframe=timeframe, as_of=AS_OF, latest_close=close)


# --- position -----------------------------------------------------------------

@pytest.mark.parametrize(
    ("low", "high", "close", "expected"),
    [
        ("100", "110", "120", POSITION_BELOW),
        ("100", "110", "90", POSITION_ABOVE),
        ("100", "110", "105", POSITION_INSIDE),
        ("100", "110", "100", POSITION_INSIDE),  # boundary counts as inside
        ("100", "110", "110", POSITION_INSIDE),
    ],
)
def test_position_is_taken_from_band_edges(low, high, close, expected):
    assert position_of(Decimal(low), Decimal(high), Decimal(close)) == expected


def test_band_containing_price_is_inside_even_when_detector_centre_rule_says_resistance():
    # Audit finding: the detector labels by centre vs close. Centre 62000 > close 61900
    # gives raw role "resistance", yet the band spans the close. Display must say inside.
    band = zone(61500, 62500, role="resistance")
    result = present([band], close="61900")
    shown = result["bands"]
    assert len(shown) == 1
    assert shown[0]["position"] == POSITION_INSIDE
    assert shown[0]["display_role"] == "price_inside"
    assert shown[0]["raw_roles"] == ["resistance"], "the detector's label is kept for transparency"


def test_above_and_below_bands_get_resistance_and_support_display_roles():
    result = present([zone(65000, 65100), zone(58000, 58100, role="resistance")], close="60000")
    roles = {b["band_low"]: b["display_role"] for b in result["bands"]}
    assert roles == {"65000": "resistance", "58000": "support"}
    # The raw detector role on the lower band says resistance; display corrects it to support.
    lower = next(b for b in result["bands"] if b["band_low"] == "58000")
    assert lower["raw_roles"] == ["resistance"]
    assert lower["position"] == POSITION_BELOW


# --- merge ----------------------------------------------------------------------

def test_overlapping_bands_merge_and_sum_touches():
    result = present(
        [
            zone(61000, 61500, touches=2, highs=2, lows=0),
            zone(61400, 61900, touches=3, highs=1, lows=2),
            zone(61880, 62000, touches=1, highs=1, lows=0),
        ],
        close="60000",
    )
    assert result["merged_count"] == 2
    assert len(result["bands"]) == 1
    merged = result["bands"][0]
    assert merged["band_low"] == "61000"
    assert merged["band_high"] == "62000"
    assert merged["touch_count"] == 6
    assert merged["swing_high_count"] == 4
    assert merged["swing_low_count"] == 2
    assert merged["merged_zone_count"] == 3


def test_touching_edges_count_as_overlap():
    result = present([zone(100, 110), zone(110, 120)], close="90")
    assert result["merged_count"] == 1


def test_separate_bands_are_not_merged():
    result = present([zone(100, 110), zone(111, 120)], close="90")
    assert result["merged_count"] == 0
    assert len(result["bands"]) == 2


# --- selection ------------------------------------------------------------------

def test_only_nearest_bands_per_side_are_shown_and_rest_are_counted():
    above = [zone(61000 + i * 500, 61050 + i * 500) for i in range(5)]
    below = [zone(59000 - i * 500, 59050 - i * 500) for i in range(5)]
    result = present(above + below, close="60000")
    assert len([b for b in result["bands"] if b["display_role"] == "resistance"]) == DISPLAY_PER_SIDE
    assert len([b for b in result["bands"] if b["display_role"] == "support"]) == DISPLAY_PER_SIDE
    assert result["hidden_count"] == 6
    # The nearest above is 61000, the nearest below is 59000.
    lows = {b["band_low"] for b in result["bands"]}
    assert {"61000", "59000"} <= lows


def test_bands_containing_price_are_capped():
    inside = [zone(59900 + i * 10, 60200 + i * 10) for i in range(6)]
    result = present(inside, close="60050")
    assert len(result["bands"]) <= DISPLAY_INSIDE_MAX
    assert all(b["position"] == POSITION_INSIDE for b in result["bands"])


# --- isolation, age and fading -----------------------------------------------------

def test_single_swing_zone_is_flagged_isolated_not_tested():
    result = present([zone(61000, 61100, touches=1, highs=1, lows=0)], close="60000")
    band = result["bands"][0]
    assert band["isolated"] is True
    assert band["touch_count"] == 1


def test_multi_touch_zone_is_not_isolated():
    result = present([zone(61000, 61100, touches=3)], close="60000")
    assert result["bands"][0]["isolated"] is False


def test_age_is_counted_in_candles_of_the_viewed_timeframe():
    result = present([zone(61000, 61100, last_back_hours=10)], close="60000", timeframe="1h")
    assert result["bands"][0]["age_candles"] == 10
    result_4h = present([zone(61000, 61100, last_back_hours=10)], close="60000", timeframe="4h")
    assert result_4h["bands"][0]["age_candles"] == 2


def test_old_zone_is_faded_not_removed():
    old = zone(61000, 61100, last_back_hours=FADE_AFTER_CANDLES + 5)
    result = present([old], close="60000")
    band = result["bands"][0]
    assert band["faded"] is True
    assert band["age_candles"] == FADE_AFTER_CANDLES + 5


def test_recent_zone_is_not_faded():
    result = present([zone(61000, 61100, last_back_hours=3)], close="60000")
    assert result["bands"][0]["faded"] is False


# --- honest empty and invalid states -----------------------------------------------

def test_no_zones_reports_reason_without_bands():
    result = present([], close="60000")
    assert result["bands"] == []
    assert result["reason"] == "no_zones"


def test_missing_close_reports_reason_and_draws_nothing():
    result = present([zone(61000, 61100)], close=None)
    assert result["bands"] == []
    assert result["reason"] == "no_close"


def test_inverted_or_undated_zones_are_skipped_not_guessed():
    bad = zone(61100, 61000)  # low above high
    undated = zone(61000, 61100)
    undated["last_tested_timestamp"] = None
    result = present([bad, undated, zone(62000, 62100)], close="60000")
    assert result["skipped_invalid"] == 2
    assert [b["band_low"] for b in result["bands"]] == ["62000"]


def test_each_band_carries_the_close_it_was_classified_against():
    result = present([zone(61000, 61100)], close="60000")
    assert result["bands"][0]["latest_close"] == "60000"


def test_source_timeframe_and_source_swings_are_carried_for_inspection():
    result = present([zone(61000, 61100)], close="60000", timeframe="4h")
    band = result["bands"][0]
    assert band["source_timeframe"] == "4h"
    assert band["source_swing_timestamps"], "the evidence must list the source swings"
    assert band["id"].startswith("4h:")


def test_presentation_does_not_mutate_input_zones():
    original = [zone(61000, 61100)]
    snapshot = repr(original)
    present(original, close="60000")
    assert repr(original) == snapshot
