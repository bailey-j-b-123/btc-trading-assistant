"""Regression tests for the read-only links between zone bands and stored events.

These pin the rule that matters for trust: a band is linked to an event only when
the event's OWN stored reference is a zone reference with exactly the band's
bounds. Numeric similarity is not a link, a swing or range reference is not a
zone reference, and an event that was not yet knowable at the analysed instant is
never presented as having referenced anything.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

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


# --- a retest projected from REAL detector objects must stay linkable (BRAIN-AUDIT-0005) ---
# The linkage advertises breakouts, failed breakouts, sweeps and retests, but the retest branch of
# the shared projection used to omit the reference it borrows from its breakout, so a retest could
# never be linked whatever its bounds were. These cases build real pattern_liquidity objects.

def _projected_retest(reference_type: str, *, low: str = "61000", high: str = "61500"):
    from datetime import timedelta as _timedelta

    from trading_assistant.market_data.types import Candle
    from trading_assistant.pattern_liquidity.events import Breakout, Reference, Retest
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
        id=f"ref-{reference_type}",
        type=reference_type,
        band_low=Decimal(low),
        band_high=Decimal(high),
        known_at=AS_OF,
        swings=(),
    )
    breakout = Breakout(
        id="bo-retest",
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
    retest = Retest(
        id="rt-1",
        breakout=breakout,
        state="held",
        candle=candle,
        known_at=AS_OF,
        distance_from_band=Decimal("1"),
        elapsed_candles=2,
        evidence_candles=(),
        parameters=None,
    )
    return project_event(retest, "retest", _timedelta(hours=1))


def test_a_real_retest_projection_carries_the_reference_of_the_breakout_it_follows():
    projected = _projected_retest("zone")
    assert projected["reference_type"] == "zone"
    assert projected["reference_id"] == "ref-zone"
    assert projected["band_low"] == "61000"
    assert projected["band_high"] == "61500"


def test_a_zone_referenced_retest_is_linked_and_counted_as_a_retest():
    linkage = link_events_to_bands([band()], events=[_projected_retest("zone")], as_of=AS_OF)["1h:61000:61500"]
    assert linkage["linked"] is True
    assert linkage["counts"]["retest"] == 1
    assert linkage["events"][0]["id"] == "rt-1"
    assert linkage["reference_ids"] == ["ref-zone"]


@pytest.mark.parametrize("reference_type", ["swing_high", "swing_low", "range_high", "range_low"])
def test_a_retest_on_a_non_zone_reference_is_never_relabelled_as_a_zone_link(reference_type):
    linkage = link_events_to_bands([band()], events=[_projected_retest(reference_type)], as_of=AS_OF)[
        "1h:61000:61500"
    ]
    assert linkage["linked"] is False
    assert linkage["counts"]["retest"] == 0
    assert linkage["overlapping"] is False, "a non-zone reference is not an overlapping zone reference"


# --- overlapping, non-identical references are measured, never claimed as a link ------------
# A band's bounds are re-derived on every analysis frame while an event keeps the bounds it was
# detected with, so the exact-identity rule above reports no link for a historical reference to the
# same level. That is correct, but reporting it as "nothing referenced this band" is not
# (BRAIN-AUDIT-0006): the measured overlap is stated instead, and it never becomes a link.

def test_a_historical_reference_with_shifted_bounds_overlaps_without_linking():
    from trading_assistant.web.zone_evidence import OVERLAP_NOTE

    linkage = link_events_to_bands(
        [band()], events=[event(band_low="61000", band_high="61300")], as_of=AS_OF
    )["1h:61000:61500"]
    assert linkage["linked"] is False, "the identity rule is unchanged"
    assert linkage["note"] == EMPTY_LINKAGE["note"]
    assert linkage["overlapping"] is True
    assert linkage["overlapping_count"] == 1
    assert linkage["overlapping_counts"]["breakout"] == 1
    assert linkage["overlap_note"] == OVERLAP_NOTE
    record = linkage["overlapping_events"][0]
    assert record["band_low"] == "61000" and record["band_high"] == "61300"
    assert record["overlap_relation"] == "contained_by"


def test_a_reference_that_contains_the_band_reports_that_relation():
    linkage = link_events_to_bands(
        [band()], events=[event(band_low="60000", band_high="62000")], as_of=AS_OF
    )["1h:61000:61500"]
    assert linkage["linked"] is False
    assert linkage["overlapping_events"][0]["overlap_relation"] == "contains"


def test_a_partially_overlapping_reference_reports_a_plain_overlap():
    linkage = link_events_to_bands(
        [band()], events=[event(band_low="61400", band_high="62000")], as_of=AS_OF
    )["1h:61000:61500"]
    assert linkage["overlapping_events"][0]["overlap_relation"] == "overlaps"


def test_a_reference_that_does_not_overlap_is_not_reported_as_an_overlap():
    linkage = link_events_to_bands(
        [band()], events=[event(band_low="70000", band_high="70100")], as_of=AS_OF
    )["1h:61000:61500"]
    assert linkage["linked"] is False
    assert linkage["overlapping"] is False
    assert linkage["overlapping_count"] == 0
    assert linkage["overlap_note"] is None


def test_an_identical_reference_is_a_link_and_never_also_an_overlap():
    linkage = link_events_to_bands([band()], events=[event()], as_of=AS_OF)["1h:61000:61500"]
    assert linkage["linked"] is True
    assert linkage["overlapping"] is False
    assert linkage["overlapping_count"] == 0


def test_an_overlap_is_still_bounded_by_the_analysed_instant():
    later = event(band_low="61000", band_high="61300", known_at=AS_OF + timedelta(hours=1))
    linkage = link_events_to_bands([band()], events=[later], as_of=AS_OF)["1h:61000:61500"]
    assert linkage["linked"] is False
    assert linkage["overlapping"] is False, "an event recorded later is not evidence at this instant"


def test_a_non_zone_reference_is_never_reported_as_an_overlapping_zone_reference():
    linkage = link_events_to_bands(
        [band()], events=[event(reference_type="swing_high", band_low="61000", band_high="61300")],
        as_of=AS_OF,
    )["1h:61000:61500"]
    assert linkage["linked"] is False
    assert linkage["overlapping"] is False


def test_a_merged_bands_overlap_is_measured_through_its_member_zones():
    linkage = link_events_to_bands(
        [band(members=[{"band_low": "61000", "band_high": "61200"}, {"band_low": "61400", "band_high": "61500"}])],
        events=[event(band_low="61000", band_high="61300")],
        as_of=AS_OF,
    )["1h:61000:61500"]
    assert linkage["linked"] is False
    assert linkage["overlapping_count"] == 1
    # 61000–61300 contains the first member zone 61000–61200 and spills past it, and shares only an
    # edge with the second, so the relation is decided against the member it actually covers.
    assert linkage["overlapping_events"][0]["overlap_relation"] == "contains"


def test_every_kind_counts_its_own_overlaps_separately():
    events = [
        event(kind="breakout", event_id="b1", band_low="61000", band_high="61300"),
        event(kind="retest", event_id="r1", band_low="61000", band_high="61200"),
        event(kind="retest", event_id="r2", band_low="61050", band_high="61250"),
    ]
    linkage = link_events_to_bands([band()], events=events, as_of=AS_OF)["1h:61000:61500"]
    assert linkage["overlapping_counts"] == {
        "breakout": 1,
        "failed_breakout": 0,
        "sweep": 0,
        "retest": 2,
    }
