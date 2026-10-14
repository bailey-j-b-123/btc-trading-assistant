"""Detector audit: what the chronological zone clustering actually does, and what it does not.

This file exists because the Support & Resistance task must not change a working
detector on the strength of a screenshot. Each test here is a *recorded
characteristic* of ``market_structure.levels.detect_zones`` (and of the swing
confirmation it depends on), measured from explicit fixtures:

* determinism — the same candles always give the same zones, whatever order the
  swings arrive in;
* no look-ahead — at instant T the result contains only swings confirmed by T, and
  a swing confirmed later can never appear in an earlier result;
* explicit configuration — the tolerance is the configured one, and the anchor
  price is the first (oldest) swing of each cluster;
* documented tie-breaking — equal distances go to the earliest-created cluster;
* real source prices — every band edge is one of its members' prices;
* known limitations, stated rather than hidden — clustering is anchored and
  chronological, so a gap-bridging run of swings can pull one cluster wider than
  its anchor tolerance, and the lookback window means an older level drops out of
  the result entirely when it leaves the window.

Nothing here asserts a *desirable* outcome that the detector does not have: the
limitation tests pin the behaviour so a later change is a deliberate, reviewed act.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_assistant.market_structure.levels import (
    LevelParameters,
    ZoneRole,
    detect_zones,
)
from trading_assistant.market_structure.swings import (
    SwingKind,
    SwingParameters,
    SwingPoint,
    SwingTiePolicy,
    detect_swings,
)

INTERVAL = timedelta(hours=1)


def swing(
    hours: int,
    price: str,
    *,
    kind: SwingKind = SwingKind.HIGH,
    confirmed_hours: int | None = None,
) -> SwingPoint:
    """One confirmed swing at ``hours`` (confirmed ``right_window`` candles later by default)."""

    timestamp = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(hours=hours)
    confirmed = timestamp + timedelta(hours=2 if confirmed_hours is None else confirmed_hours)
    return SwingPoint(
        kind=kind,
        timestamp=timestamp,
        price=Decimal(price),
        confirmed_at=confirmed,
        confirmed_by_timestamp=confirmed - INTERVAL,
        left_window=2,
        right_window=2,
        tie_policy=SwingTiePolicy.STRICT,
    )


def candles(prices: list[str], *, high_pad: str = "1", low_pad: str = "1"):
    """Candles whose highs/lows are the given prices padded symmetrically."""

    from trading_assistant.market_data.types import Candle

    start = datetime(2026, 10, 1, tzinfo=UTC)
    rows = []
    for index, price in enumerate(prices):
        value = Decimal(price)
        rows.append(
            Candle(
                exchange="binance",
                symbol="BTC/USDT",
                timeframe="1h",
                timestamp=start + timedelta(hours=index),
                open=value,
                high=value + Decimal(high_pad),
                low=value - Decimal(low_pad),
                close=value,
                volume=Decimal("1"),
            )
        )
    return tuple(rows)


AS_OF = datetime(2026, 10, 20, tzinfo=UTC)


# --- determinism ---------------------------------------------------------------


def test_the_same_swings_always_give_the_same_zones():
    swings = [swing(0, "100"), swing(4, "101"), swing(8, "99.5"), swing(30, "120")]
    first = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("110"))
    second = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("110"))
    assert first.zones == second.zones


def test_input_order_does_not_change_the_result():
    swings = [swing(8, "99.5"), swing(0, "100"), swing(30, "120"), swing(4, "101")]
    ordered = detect_zones(list(reversed(swings)), as_of=AS_OF, latest_close=Decimal("110"))
    shuffled = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("110"))
    assert ordered.zones == shuffled.zones
    assert [zone.band_low for zone in ordered.zones] == [zone.band_low for zone in shuffled.zones]


def test_band_edges_are_real_member_prices():
    swings = [swing(0, "100"), swing(4, "100.42"), swing(8, "100.1")]
    result = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("110"))
    zone = result.zones[0]
    prices = {member.price for member in swings}
    assert zone.band_low in prices and zone.band_high in prices
    assert zone.band_low == Decimal("100") and zone.band_high == Decimal("100.42")
    assert zone.center == Decimal("100.21")


def test_the_anchor_is_the_oldest_swing_and_the_tolerance_is_the_configured_one():
    parameters = LevelParameters(tolerance_pct=Decimal("1"), lookback_swings=40, min_touches=1)
    # 100.9 is inside 1% of 100 (tolerance 1.0); 101.5 is not.
    swings = [swing(0, "100"), swing(2, "100.9"), swing(4, "101.5")]
    result = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("110"), parameters=parameters)
    by_low = {zone.band_low: zone for zone in result.zones}
    assert Decimal("100") in by_low, "the anchor keeps its own cluster"
    assert by_low[Decimal("100")].touch_count == 2
    assert by_low[Decimal("101.5")].touch_count == 1
    assert all(zone.tolerance_pct == Decimal("1") for zone in result.zones)


def test_a_tie_goes_to_the_earliest_created_cluster():
    # Swing A anchors 100 and 102 (both within 1% reached from A). A later swing at 101 is 1 away
    # from both, so the tie must resolve to the cluster created first, deterministically.
    swings = [swing(0, "100"), swing(2, "102"), swing(4, "101")]
    result = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("200"))
    counts = sorted(zone.touch_count for zone in result.zones)
    assert counts == [1, 2], "one cluster took the tied swing, the other kept its own"
    first_cluster = next(zone for zone in result.zones if zone.band_low == Decimal("100"))
    assert first_cluster.touch_count == 2
    assert first_cluster.band_high == Decimal("101")


def test_cluster_order_is_deterministic_and_documented():
    swings = [
        swing(0, "100"), swing(2, "100.4"),
        swing(10, "110"), swing(12, "110.4"), swing(14, "110.1"),
        swing(30, "105"), swing(32, "105.2"),
    ]
    result = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("106"))
    # Most touched first, then most recently tested, then lowest centre.
    assert [zone.touch_count for zone in result.zones] == [3, 2, 2]
    assert [zone.center for zone in result.zones] == [
        Decimal("110.20000000"),
        Decimal("105.10000000"),
        Decimal("100.20000000"),
    ]
    assert len(result.zones) == 3
    assert result.cluster_count == 3


# --- as-of / no look-ahead -----------------------------------------------------


def test_a_swing_confirmed_after_as_of_is_excluded_and_counted():
    early = swing(0, "100")                                  # confirmed 2026-10-01 02:00
    late = swing(1, "100.4", confirmed_hours=100)            # confirmed 2026-10-05 05:00
    result = detect_zones(
        [early, late],
        as_of=datetime(2026, 10, 2, tzinfo=UTC),
        latest_close=Decimal("110"),
    )
    assert result.swing_count == 1, "the later swing is not knowable at this instant"
    assert result.zones[0].touch_count == 1


def test_a_zone_result_never_gains_members_as_as_of_moves_backwards():
    swings = [swing(0, "100"), swing(2, "100.4"), swing(100, "100.2", confirmed_hours=2)]
    now = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("110"))
    earlier = detect_zones(
        swings,
        as_of=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
        latest_close=Decimal("110"),
    )
    assert now.zones[0].touch_count == 3
    assert earlier.zones[0].touch_count == 2, "the later swing is invisible before it is confirmed"


def test_the_role_is_the_centre_rule_and_the_band_edges_are_not_used_for_it():
    """The detector's own role is by centre; the display layer uses the edges. Both are kept."""

    # Centre 100.25 sits above the 100 close -> resistance by the detector's rule, even though the
    # band spans the close. This is exactly the disagreement the presentation layer explains.
    swings = [swing(0, "99"), swing(2, "101.5")]
    result = detect_zones(
        swings,
        as_of=AS_OF,
        latest_close=Decimal("100"),
        parameters=LevelParameters(tolerance_pct=Decimal("3")),
    )
    zone = result.zones[0]
    assert zone.band_low == Decimal("99") and zone.band_high == Decimal("101.5")
    assert zone.center == Decimal("100.25")
    assert zone.role is ZoneRole.RESISTANCE
    assert zone.band_low <= Decimal("100") <= zone.band_high, "the band does contain the close"


def test_zone_role_boundaries_are_exact():
    swings = [swing(0, "100"), swing(2, "100")]
    above = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("99.99"))
    at = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("100"))
    below = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("100.01"))
    assert above.zones[0].role is ZoneRole.RESISTANCE
    assert at.zones[0].role is ZoneRole.AT_PRICE
    assert below.zones[0].role is ZoneRole.SUPPORT


def test_timestamps_and_counts_are_the_swings_own():
    swings = [swing(0, "100"), swing(6, "100.4"), swing(12, "100.1")]
    zone = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("110")).zones[0]
    assert zone.first_observed_timestamp == swings[0].timestamp
    assert zone.last_tested_timestamp == swings[2].timestamp
    assert zone.source_swing_timestamps == tuple(point.timestamp for point in swings)
    assert zone.touch_count == len(zone.source_swing_timestamps)
    assert zone.high_source_count == 3 and zone.low_source_count == 0


def test_distance_is_measured_from_the_centre_to_the_supplied_close():
    swings = [swing(0, "100"), swing(2, "100.8")]
    zone = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("102")).zones[0]
    assert zone.center == Decimal("100.40000000")
    assert zone.latest_close == Decimal("102")
    # (100.4 - 102) / 102 * 100, at the documented 8-decimal derived scale.
    assert zone.distance_pct_from_latest_close == Decimal("-1.56862745")
    assert zone.band_width == Decimal("0.80000000")
    # (high - low) / high * 100, as the detector records it.
    assert zone.band_width_pct == Decimal("0.79365079")


# --- limits that are reported, not hidden ---------------------------------------


def test_lookback_swings_bounds_the_window_and_old_levels_do_drop_out():
    parameters = LevelParameters(lookback_swings=3, tolerance_pct=Decimal("1"), min_touches=1)
    swings = [swing(0, "100"), swing(2, "100.2"), swing(4, "110"), swing(6, "120"), swing(8, "130")]
    result = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("140"), parameters=parameters)
    assert result.swing_count == 3, "only the newest swings in the window are clustered"
    assert all(zone.band_high <= Decimal("130") for zone in result.zones)
    assert Decimal("100") not in {zone.band_low for zone in result.zones}


def test_the_anchor_never_drifts_so_a_band_cannot_be_widened_by_bridging():
    """The measured strength of anchored chronological clustering.

    100 anchors the cluster and 100.9 joins it (within 1% of the anchor). 101.7 is more than 1%
    from the ANCHOR, and the anchor is never replaced by a member, so 101.7 cannot ride in on the
    back of 100.9: it starts its own cluster. A band can therefore never grow beyond twice the
    anchor's tolerance, and the anchor stays the oldest swing of the cluster.
    """

    parameters = LevelParameters(tolerance_pct=Decimal("1"), lookback_swings=40, min_touches=1)
    swings = [swing(0, "100"), swing(2, "100.9"), swing(4, "101.7")]
    result = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("200"), parameters=parameters)
    by_low = {zone.band_low: zone for zone in result.zones}
    assert set(by_low) == {Decimal("100"), Decimal("101.7")}, "no bridging cluster was formed"
    anchored = by_low[Decimal("100")]
    assert anchored.touch_count == 2
    assert anchored.band_low == Decimal("100") and anchored.band_high == Decimal("100.9")
    anchor_tolerance = Decimal("100") * Decimal("1") / Decimal("100")
    assert anchored.band_high - anchored.band_low <= anchor_tolerance
    # The configured tolerance is recorded on the zone: the number shown is never guessed.
    assert anchored.tolerance_pct == Decimal("1")


def test_a_swing_at_the_anchor_tolerance_boundary_still_joins():
    parameters = LevelParameters(tolerance_pct=Decimal("1"), lookback_swings=40, min_touches=1)
    swings = [swing(0, "100"), swing(2, "101")]
    zone = detect_zones(
        swings, as_of=AS_OF, latest_close=Decimal("200"), parameters=parameters
    ).zones[0]
    assert zone.touch_count == 2, "1% of 100 is exactly 1.0, and the comparison is inclusive"
    assert zone.band_high == Decimal("101")


def test_max_zones_truncates_and_counts_what_it_dropped():
    parameters = LevelParameters(tolerance_pct=Decimal("1"), max_zones=2, min_touches=1)
    swings = [swing(index * 2, str(100 + index * 10)) for index in range(5)]
    result = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("1"), parameters=parameters)
    assert len(result.zones) == 2
    assert result.discarded_beyond_max_zones == 3


def test_min_touches_rejects_and_counts_isolated_clusters():
    parameters = LevelParameters(tolerance_pct=Decimal("1"), min_touches=2, lookback_swings=40)
    swings = [swing(0, "100"), swing(2, "100.2"), swing(10, "130")]
    result = detect_zones(swings, as_of=AS_OF, latest_close=Decimal("1"), parameters=parameters)
    assert result.discarded_below_min_touches == 1
    assert [zone.band_low for zone in result.zones] == [Decimal("100")]


def test_no_swings_gives_no_zones_and_says_it_is_insufficient():
    result = detect_zones([], as_of=AS_OF, latest_close=Decimal("100"))
    assert result.zones == ()
    assert result.sufficient is False
    assert result.swing_count == 0


def test_missing_close_makes_every_zone_at_price_rather_than_guessing_a_side():
    swings = [swing(0, "100"), swing(2, "100.2")]
    result = detect_zones(swings, as_of=AS_OF, latest_close=None)
    assert result.zones[0].role is ZoneRole.AT_PRICE
    assert result.zones[0].distance_pct_from_latest_close is None


# --- the swing confirmation the zones depend on ---------------------------------


def test_swings_are_confirmed_only_once_their_right_window_has_closed():
    rows = candles(["100", "101", "105", "102", "101", "103", "104"])
    peak = detect_swings(
        rows,
        interval=INTERVAL,
        as_of=rows[4].timestamp + INTERVAL,  # the peak's right window has just closed
        parameters=SwingParameters(left_window=2, right_window=2),
    )
    assert [point.price for point in peak.swings] == [Decimal("106")]
    assert peak.swings[0].confirmed_at == rows[4].timestamp + INTERVAL
    assert peak.swings[0].confirmed_by_timestamp == rows[4].timestamp


def test_a_wider_candle_can_be_both_a_swing_high_and_a_swing_low():
    """Why one instant can carry two swing evidences — the reason for the distinct count.

    A candle whose range exceeds every neighbour's is the strict maximum high AND the strict
    minimum low of its window, so the detector reports two swings at one timestamp. Zone touch
    counts count those two evidences; the presentation reports the single instant as one test.
    """

    from trading_assistant.market_data.types import Candle

    start = datetime(2026, 10, 1, tzinfo=UTC)
    rows = []
    for index, (high, low) in enumerate(
        [("101", "99"), ("101", "99"), ("120", "80"), ("101", "99"), ("101", "99")]
    ):
        rows.append(
            Candle(
                exchange="binance",
                symbol="BTC/USDT",
                timeframe="1h",
                timestamp=start + timedelta(hours=index),
                open=Decimal("100"),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal("100"),
                volume=Decimal("1"),
            )
        )
    result = detect_swings(
        tuple(rows),
        interval=INTERVAL,
        as_of=start + timedelta(hours=10),
        parameters=SwingParameters(left_window=2, right_window=2),
    )
    kinds = sorted(point.kind.value for point in result.swings)
    assert kinds == ["high", "low"], "one candle, two swing evidences"
    assert result.swings[0].timestamp == result.swings[1].timestamp == start + timedelta(hours=2)


def test_gaps_stop_structure_being_inferred_across_missing_candles():
    rows = list(candles(["100", "101", "105", "102", "101"]))
    from dataclasses import replace

    gapped = tuple(
        replace(row, timestamp=row.timestamp + (INTERVAL * 5 if index >= 3 else timedelta(0)))
        for index, row in enumerate(rows)
    )
    result = detect_swings(
        gapped,
        interval=INTERVAL,
        as_of=gapped[-1].timestamp + INTERVAL,
        parameters=SwingParameters(left_window=2, right_window=2),
    )
    assert result.swings == ()
    assert result.gap_window_count >= 1


@pytest.mark.parametrize("tolerance", ["0", "100", "-1"])
def test_invalid_tolerance_is_rejected(tolerance):
    with pytest.raises(ValueError):
        LevelParameters(tolerance_pct=Decimal(tolerance))
