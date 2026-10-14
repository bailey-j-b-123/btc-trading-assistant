"""Regression tests for the read-only links between zone bands and stored events.

These pin the rules that matter for trust. A band is **linked** to an event only
when the event's OWN stored reference is a zone reference with exactly the band's
bounds: numeric similarity is never identity, a swing or range reference is not a
zone reference, and an event that was not yet knowable at the analysed instant is
never presented as having referenced anything.

A second, separate fact is pinned beside it: a smaller recorded reference lying
**inside** the band's own price area is reported as a *contained reference*
(``contained_*``), never as a link, and it stops the identity rule from reading as
an absolute "no stored event used this zone" while a stored event's band really
does sit inside the displayed band. Zones re-derive their bounds every frame, so
this is the normal case for a level that has since been extended by further
confirmed swings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_assistant.web.zone_evidence import (
    EMPTY_LINKAGE,
    NOT_EVALUATED_LINKAGE,
    link_events_to_bands,
)

AS_OF = datetime(2026, 10, 10, 17, 0, tzinfo=UTC)


def band(band_id="1h:61000:61500", low="61000", high="61500", members=None):
    row = {"id": band_id, "band_low": low, "band_high": high}
    if members is not None:
        row["member_zones"] = members
    return row


def event(
    *,
    kind="breakout",
    reference_type="zone",
    band_low="61000",
    band_high="61500",
    known_at=AS_OF - timedelta(hours=1),
    event_id="evt-1",
    **extra,
):
    row = {
        "id": event_id,
        "evidence_kind": kind,
        "kind": kind,
        "reference_type": reference_type,
        "reference_id": f"ref-{event_id}",
        "band_low": band_low,
        "band_high": band_high,
        "known_at": known_at.isoformat().replace("+00:00", "Z"),
        "time": (known_at - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "time_ms": int(known_at.timestamp() * 1000),
    }
    row.update(extra)
    return row


# --- exact identity ------------------------------------------------------------


def test_a_zone_reference_with_the_bands_own_bounds_links():
    result = link_events_to_bands(
        [band()],
        events=[event(direction="bullish", breakout_close="61700", penetration_pct="0.4")],
        as_of=AS_OF,
    )
    linkage = result["1h:61000:61500"]
    assert linkage["linked"] is True
    assert linkage["event_count"] == 1
    assert linkage["counts"]["breakout"] == 1
    assert linkage["events"][0]["direction"] == "bullish"
    # The breakout close 61700 leaves the 61000-61500 band BELOW price at that instant: the
    # position is the band's, read from the event's own recorded close.
    assert linkage["events"][0]["position_at_event"] == "below_price"
    assert linkage["matched_band_bounds"] == [{"band_low": "61000", "band_high": "61500"}]


def test_a_numerically_similar_band_is_not_a_link():
    # One tick away is a different level, and it is reported as no link.
    result = link_events_to_bands([band()], events=[event(band_low="61000.1")], as_of=AS_OF)
    assert result["1h:61000:61500"]["linked"] is False
    assert result["1h:61000:61500"]["events"] == []


def test_a_swing_or_range_reference_at_the_same_price_is_not_a_zone_link():
    events = [
        event(reference_type="swing_high"),
        event(reference_type="range_high", event_id="evt-2"),
        event(reference_type="swing_low", event_id="evt-3"),
    ]
    result = link_events_to_bands([band()], events=events, as_of=AS_OF)
    assert result["1h:61000:61500"]["linked"] is False


def test_a_zone_reference_that_belongs_to_another_band_does_not_link():
    result = link_events_to_bands(
        [band(), band(band_id="1h:64000:64500", low="64000", high="64500")],
        events=[event(band_low="64000", band_high="64500")],
        as_of=AS_OF,
    )
    assert result["1h:61000:61500"]["linked"] is False
    assert result["1h:64000:64500"]["linked"] is True


# --- merged bands --------------------------------------------------------------


def test_a_merged_band_links_through_the_member_zone_the_event_actually_used():
    members = [
        {"id": "1h:61000:61200", "band_low": "61000", "band_high": "61200"},
        {"id": "1h:61400:61500", "band_low": "61400", "band_high": "61500"},
    ]
    merged = band(members=members)
    result = link_events_to_bands(
        [merged],
        events=[event(band_low="61400", band_high="61500", kind="retest", state="held")],
        as_of=AS_OF,
    )
    linkage = result[merged["id"]]
    assert linkage["linked"] is True
    assert linkage["matched_band_bounds"] == [{"band_low": "61400", "band_high": "61500"}]
    assert linkage["events"][0]["state"] == "held"


def test_a_merged_band_with_no_member_match_stays_unlinked():
    members = [
        {"id": "1h:61000:61200", "band_low": "61000", "band_high": "61200"},
        {"id": "1h:61400:61500", "band_low": "61400", "band_high": "61500"},
    ]
    result = link_events_to_bands(
        [band(members=members)],
        events=[event(band_low="63000", band_high="63500")],
        as_of=AS_OF,
    )
    assert result["1h:61000:61500"]["linked"] is False


# --- causality -----------------------------------------------------------------


def test_an_event_recorded_after_the_analysed_instant_is_never_linked():
    result = link_events_to_bands(
        [band()],
        events=[event(known_at=AS_OF + timedelta(hours=1))],
        as_of=AS_OF,
    )
    assert result["1h:61000:61500"]["linked"] is False


def test_every_kind_is_counted_separately_and_reports_its_own_stored_facts():
    events = [
        event(kind="breakout", event_id="b1", direction="bullish", breakout_close="61700"),
        event(kind="failed_breakout", event_id="f1", direction="bullish", breakout_id="b1", reentry_distance="120"),
        event(kind="sweep", event_id="s1", direction="below", extreme="60880", reclaim_close="61250"),
        event(kind="retest", event_id="r1", state="observed", breakout_id="b1", distance_from_band="35"),
    ]
    linkage = link_events_to_bands([band()], events=events, as_of=AS_OF)["1h:61000:61500"]
    assert linkage["counts"] == {"breakout": 1, "failed_breakout": 1, "sweep": 1, "retest": 1}
    assert linkage["event_count"] == 4
    by_id = {row["id"]: row for row in linkage["events"]}
    assert by_id["f1"]["breakout_id"] == "b1"
    assert by_id["s1"]["extreme"] == "60880"
    assert by_id["r1"]["distance_from_band"] == "35"
    # The recorded close decides where the band sat at the event, never the current price.
    assert by_id["b1"]["position_at_event"] == "below_price"
    # The sweep's own reclaim close (61250) sits inside the band, so the recorded instant reads
    # "price inside" — taken from the sweep's stored close, not from the current price.
    assert by_id["s1"]["position_at_event"] == "price_inside"


def test_a_band_inside_the_event_close_reports_price_inside_at_that_event():
    linkage = link_events_to_bands(
        [band()],
        events=[event(kind="breakout", breakout_close="61250")],
        as_of=AS_OF,
    )["1h:61000:61500"]
    assert linkage["events"][0]["position_at_event"] == "price_inside"


def test_malformed_events_are_ignored_rather_than_guessed():
    events = [
        {"id": "no-kind", "reference_type": "zone", "band_low": "61000", "band_high": "61500", "known_at": "2026-10-10T10:00:00Z"},
        {"id": "no-bounds", "evidence_kind": "breakout", "reference_type": "zone", "known_at": "2026-10-10T10:00:00Z"},
        event(event_id="good"),
        "not-a-dict",
    ]
    linkage = link_events_to_bands([band()], events=events, as_of=AS_OF)["1h:61000:61500"]
    assert linkage["event_count"] == 1
    assert linkage["events"][0]["id"] == "good"


# --- honest empty states -------------------------------------------------------


def test_bands_with_no_supplied_events_are_unchecked_not_unreferenced():
    linkage = link_events_to_bands([band()], events=None, as_of=AS_OF)["1h:61000:61500"]
    assert linkage["linked"] is False
    assert linkage["event_count"] == 0
    assert linkage["note"] == EMPTY_LINKAGE["note"], "zero events supplied is still a real search"
    assert linkage["event_count"] != NOT_EVALUATED_LINKAGE["event_count"]


def test_an_empty_band_list_yields_no_links():
    assert link_events_to_bands([], events=[event()], as_of=AS_OF) == {}


def test_linked_bands_carry_their_reference_ids_for_traceability():
    linkage = link_events_to_bands(
        [band()],
        events=[event(event_id="evt-9")],
        as_of=AS_OF,
    )["1h:61000:61500"]
    assert linkage["reference_ids"] == ["ref-evt-9"]
    assert linkage["first_known_at"] == linkage["last_known_at"]


def test_present_zones_attaches_the_linkage_it_was_given(monkeypatch):
    """The presentation surface passes stored events straight through to the bands."""

    from trading_assistant.web.zone_presentation import present_zones

    zones = [
        {
            "role": "support",
            "band_low": "61000",
            "band_high": "61500",
            "touch_count": 2,
            "high_source_count": 1,
            "low_source_count": 1,
            "first_observed_timestamp": "2026-10-09T10:00:00Z",
            "last_tested_timestamp": "2026-10-10T10:00:00Z",
            "source_swing_timestamps": ["2026-10-09T10:00:00Z", "2026-10-10T10:00:00Z"],
        }
    ]
    with_events = present_zones(
        zones, timeframe="1h", as_of=AS_OF, latest_close="60000", events=[event(kind="sweep", direction="below")]
    )
    assert with_events["bands"][0]["linked_events"]["counts"]["sweep"] == 1

    without_events = present_zones(zones, timeframe="1h", as_of=AS_OF, latest_close="60000")
    assert without_events["bands"][0]["linked_events"]["event_count"] is None
    assert without_events["bands"][0]["linked_events"]["note"].startswith("stored events were not available")


def test_event_projection_is_the_same_shape_the_chart_evidence_uses():
    """The linkage reads the stored events through the one shared projection."""

    from datetime import timedelta as _timedelta

    from trading_assistant.market_data.types import Candle
    from trading_assistant.pattern_liquidity.events import Breakout, Reference
    from trading_assistant.web.chart_evidence import project_event

    candle = Candle(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        timestamp=datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("1"),
    )
    reference = Reference(
        id="ref-1",
        type="zone",
        band_low=Decimal("61000"),
        band_high=Decimal("61500"),
        known_at=AS_OF,
        swings=(),
    )
    breakout = Breakout(
        id="bo-1",
        direction="bullish",
        reference=reference,
        previous_candle=candle,
        candle=candle,
        known_at=AS_OF,
        confirmation_candles=(),
        breakout_close=Decimal("61700"),
        penetration=Decimal("200"),
        penetration_pct=Decimal("0.33"),
        penetration_atr=None,
        volatility=None,
        volume=None,
        parameters=None,
    )
    projected = project_event(breakout, "breakout", _timedelta(hours=1))
    assert projected["reference_type"] == "zone"
    assert projected["band_low"] == "61000"
    assert projected["breakout_close"] == "61700"
    linkage = link_events_to_bands([band()], events=[projected], as_of=AS_OF)["1h:61000:61500"]
    assert linkage["linked"] is True
    assert linkage["events"][0]["id"] == "bo-1"


# --- contained references: a smaller earlier band inside the displayed band ------
#
# Zones are re-derived for every analysis frame, so the band on screen today can be
# a wider version of the level an older event was detected against. The event keeps
# the bounds it actually used; that fact is reported separately from identity.


def test_a_smaller_reference_inside_the_band_is_contained_not_identical():
    result = link_events_to_bands(
        [band()],
        events=[event(band_low="61050", band_high="61400", event_id="evt-small")],
        as_of=AS_OF,
    )
    linkage = result["1h:61000:61500"]
    # Identity is untouched: the event's own band is not this band.
    assert linkage["linked"] is False
    assert linkage["event_count"] == 0
    assert linkage["events"] == []
    assert linkage["note"] == "no stored event used this zone as its own reference level"
    # The earlier, smaller reference is reported as its own fact.
    assert linkage["contained_count"] == 1
    (record,) = linkage["contained_events"]
    assert record["match"] == "contained"
    assert record["id"] == "evt-small"
    # The record keeps the event's OWN recorded bounds, and says which band contained them.
    assert (record["band_low"], record["band_high"]) == ("61050", "61400")
    assert record["linked_via_bounds"] == {"band_low": "61000", "band_high": "61500"}
    assert linkage["contained_band_bounds"] == [{"band_low": "61000", "band_high": "61500"}]
    assert linkage["contained_note"] is not None
    assert "smaller recorded references" in linkage["contained_note"]


def test_a_reference_touching_a_band_edge_counts_as_contained():
    # Closed intervals: a single-price reference sitting on the band's low is inside it.
    result = link_events_to_bands([band()], events=[event(band_low="61000", band_high="61000")], as_of=AS_OF)
    assert result["1h:61000:61500"]["contained_count"] == 1
    assert result["1h:61000:61500"]["contained_events"][0]["match"] == "contained"


def test_a_reference_outside_or_wider_than_the_band_is_neither_linked_nor_contained():
    outside = link_events_to_bands([band()], events=[event(band_low="63000", band_high="63500")], as_of=AS_OF)
    assert outside["1h:61000:61500"]["linked"] is False
    assert outside["1h:61000:61500"]["contained_count"] == 0
    assert outside["1h:61000:61500"]["contained_note"] is None

    wider = link_events_to_bands(
        [band()], events=[event(band_low="60000", band_high="62000")], as_of=AS_OF
    )
    # Overlapping but not contained: the displayed band is not shown as its descendant.
    assert wider["1h:61000:61500"]["linked"] is False
    assert wider["1h:61000:61500"]["contained_count"] == 0


def test_a_non_zone_reference_is_never_reported_even_inside_the_band():
    events = [
        event(reference_type="swing_high", band_low="61050", band_high="61400"),
        event(reference_type="range_high", event_id="evt-2", band_low="61050", band_high="61400"),
    ]
    result = link_events_to_bands([band()], events=events, as_of=AS_OF)
    assert result["1h:61000:61500"]["linked"] is False
    assert result["1h:61000:61500"]["contained_count"] == 0


def test_a_contained_reference_after_the_analysed_instant_is_never_reported():
    result = link_events_to_bands(
        [band()],
        events=[event(band_low="61050", band_high="61400", known_at=AS_OF + timedelta(hours=1))],
        as_of=AS_OF,
    )
    assert result["1h:61000:61500"]["contained_count"] == 0


def test_exact_links_and_contained_references_are_counted_separately():
    result = link_events_to_bands(
        [band()],
        events=[
            event(kind="breakout", event_id="evt-exact"),
            event(kind="retest", event_id="evt-contained", band_low="61050", band_high="61400"),
        ],
        as_of=AS_OF,
    )
    linkage = result["1h:61000:61500"]
    assert linkage["linked"] is True
    assert linkage["event_count"] == 1
    assert linkage["counts"] == {"breakout": 1, "failed_breakout": 0, "sweep": 0, "retest": 0}
    assert linkage["contained_count"] == 1
    assert linkage["contained_counts"] == {"breakout": 0, "failed_breakout": 0, "sweep": 0, "retest": 1}
    assert linkage["events"][0]["match"] == "exact"
    assert linkage["contained_events"][0]["match"] == "contained"
    # With identity present the identity note stands, and both classes are still listed.
    assert "own band as their reference level" in linkage["note"]
    assert linkage["contained_note"] is None


def test_the_near_miss_is_reported_as_contained_but_never_as_identity():
    """One tick away is a different level: never a link, but never a bare negative either."""

    result = link_events_to_bands([band()], events=[event(band_low="61000.1")], as_of=AS_OF)
    linkage = result["1h:61000:61500"]
    assert linkage["linked"] is False
    assert linkage["events"] == []
    assert linkage["counts"]["breakout"] == 0
    assert linkage["contained_count"] == 1
    assert linkage["contained_events"][0]["match"] == "contained"
    assert linkage["contained_events"][0]["band_low"] == "61000.1"


def test_a_merged_band_reports_a_contained_reference_through_its_member_zone():
    members = [
        {"id": "1h:61000:61200", "band_low": "61000", "band_high": "61200"},
        {"id": "1h:61400:61500", "band_low": "61400", "band_high": "61500"},
    ]
    result = link_events_to_bands(
        [band(members=members)],
        events=[event(band_low="61050", band_high="61150", kind="retest", state="held")],
        as_of=AS_OF,
    )
    linkage = result["1h:61000:61500"]
    assert linkage["linked"] is False
    assert linkage["contained_count"] == 1
    record = linkage["contained_events"][0]
    assert record["linked_via_bounds"] == {"band_low": "61000", "band_high": "61200"}
    assert record["state"] == "held"


def test_bands_with_no_contained_reference_say_so_explicitly():
    result = link_events_to_bands([band()], events=[event(band_low="63000", band_high="63500")], as_of=AS_OF)
    linkage = result["1h:61000:61500"]
    assert linkage["contained_count"] == 0
    assert linkage["contained_events"] == []
    assert linkage["contained_counts"] == {"breakout": 0, "failed_breakout": 0, "sweep": 0, "retest": 0}
    assert linkage["contained_note"] is None


# --- the retest projection: a real Retest must carry the band it was measured against ---


def _real_breakout(reference_type="zone", band_low="61000", band_high="61500", event_id="bo-real"):
    from datetime import timedelta as _timedelta

    from trading_assistant.market_data.types import Candle
    from trading_assistant.pattern_liquidity.events import Breakout, Reference

    candle = Candle(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        timestamp=datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("1"),
    )
    reference = Reference(
        id=f"ref-{event_id}",
        type=reference_type,
        band_low=Decimal(band_low),
        band_high=Decimal(band_high),
        known_at=AS_OF,
        swings=(),
    )
    assert _timedelta(hours=1)  # imports stay intentional and explicit
    return Breakout(
        id=event_id,
        direction="bullish",
        reference=reference,
        previous_candle=candle,
        candle=candle,
        known_at=AS_OF,
        confirmation_candles=(),
        breakout_close=Decimal("61700"),
        penetration=Decimal("200"),
        penetration_pct=Decimal("0.33"),
        penetration_atr=None,
        volatility=None,
        volume=None,
        parameters=None,
    )


def _real_retest(breakout, *, state="held", event_id="rt-real"):
    from datetime import timedelta as _timedelta

    from trading_assistant.market_data.types import Candle
    from trading_assistant.pattern_liquidity.events import Retest

    candle = Candle(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        timestamp=datetime(2026, 10, 8, 13, 0, tzinfo=UTC),
        open=Decimal("105"),
        high=Decimal("108"),
        low=Decimal("100"),
        close=Decimal("106"),
        volume=Decimal("1"),
    )
    assert _timedelta(hours=1)
    return Retest(
        id=event_id,
        breakout=breakout,
        state=state,
        candle=candle,
        known_at=AS_OF,
        distance_from_band=Decimal("0"),
        elapsed_candles=1,
        evidence_candles=(candle,),
        parameters=None,
    )


def test_a_real_retest_projects_the_reference_it_was_measured_against():
    """BRAIN-AUDIT-0005: a retest must expose its breakout's own reference band.

    Without the reference type and id on the retest projection, the linkage's
    'zone-reference only' rule silently drops every retest, whatever its bounds are.
    """

    from datetime import timedelta as _timedelta

    from trading_assistant.web.chart_evidence import project_event

    retest = _real_retest(_real_breakout())
    projected = project_event(retest, "retest", _timedelta(hours=1))
    assert projected["reference_type"] == "zone"
    assert projected["reference_id"] == "ref-bo-real"
    assert projected["band_low"] == "61000"
    assert projected["band_high"] == "61500"
    assert projected["state"] == "held"
    assert projected["breakout_id"] == "bo-real"
    # The retest's own recorded close travels with it, so the reported position is its own fact.
    assert projected["close"] == "106"

    linkage = link_events_to_bands([band()], events=[projected], as_of=AS_OF)["1h:61000:61500"]
    assert linkage["linked"] is True
    assert linkage["event_count"] == 1
    assert linkage["counts"] == {"breakout": 0, "failed_breakout": 0, "sweep": 0, "retest": 1}
    assert linkage["events"][0]["id"] == "rt-real"
    assert linkage["events"][0]["reference_id"] == "ref-bo-real"
    # Close 106 sits under the 61000-61500 band, so the band was above price at that instant.
    assert linkage["events"][0]["close_at_event"] == "106"
    assert linkage["events"][0]["position_at_event"] == "above_price"


def test_a_real_retest_measured_against_a_swing_stays_unlinked():
    from datetime import timedelta as _timedelta

    from trading_assistant.web.chart_evidence import project_event

    retest = _real_retest(_real_breakout(reference_type="swing_high", band_low="61000", band_high="61000"))
    projected = project_event(retest, "retest", _timedelta(hours=1))
    assert projected["reference_type"] == "swing_high"
    linkage = link_events_to_bands([band()], events=[projected], as_of=AS_OF)["1h:61000:61500"]
    assert linkage["linked"] is False
    assert linkage["contained_count"] == 0


def test_a_real_failed_breakout_projects_its_reference_id():
    from datetime import timedelta as _timedelta

    from trading_assistant.pattern_liquidity.events import FailedBreakout
    from trading_assistant.web.chart_evidence import project_event

    breakout = _real_breakout()
    candle = breakout.candle
    failed = FailedBreakout(
        id="fb-real",
        breakout=breakout,
        candle=candle,
        known_at=AS_OF,
        reentry_distance=Decimal("150"),
        elapsed_candles=2,
        elapsed_seconds=7200,
        evidence_candles=(candle,),
        parameters=None,
    )
    projected = project_event(failed, "failed_breakout", _timedelta(hours=1))
    assert projected["reference_type"] == "zone"
    assert projected["reference_id"] == "ref-bo-real"
    assert projected["breakout_id"] == "bo-real"
