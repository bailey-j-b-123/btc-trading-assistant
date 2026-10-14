"""BRAIN-AUDIT-0005 / 0006 end-to-end regressions over STORED detector output.

The unit cases in ``test_zone_evidence.py`` use hand-built payloads. These drive the real
detectors, the real shared projection and the real linkage over candles actually stored in a
migrated database, through the dashboard payload the operator's screen is built from, so the
identity rule and the overlap fact are pinned against the pipeline's own time model.

The series is the Support & Resistance demo generator, which is explicitly SYNTHETIC demonstration
data, not market history. It is anchored at a fixed date here so the test does not depend on the
wall clock; only the price path, which is deterministic, is taken from the generator.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from datetime import UTC, datetime, timedelta

import pytest

from scripts import seed_sr_demo
from trading_assistant.web.dashboard_service import DashboardService
from trading_assistant.web.state import AppState
from trading_assistant.web.zone_evidence import OVERLAP_NOTE
from web_fixtures import insert_candles, make_settings, migrated_engine

ANCHOR = datetime(2026, 6, 1, tzinfo=UTC)
SPAN_DAYS = 60
#: The instant at which a band's bounds still equal the reference an event was detected with.
LINKED_INSTANT = ANCHOR + timedelta(days=11, hours=8)
#: The end of the series, where the same levels have been re-derived several times since.
END_INSTANT = ANCHOR + timedelta(days=SPAN_DAYS)
RELATIONS = {"contains", "contained_by", "overlaps"}


@pytest.fixture(scope="module")
def service(tmp_path_factory) -> DashboardService:
    """The real dashboard service over a migrated database holding the synthetic series."""

    directory = tmp_path_factory.mktemp("sr-linkage")
    engine, url = migrated_engine(directory, name="sr-linkage.sqlite3")
    minutes = seed_sr_demo.build_minute_path(SPAN_DAYS * 24 * 60, wobble=12.0)
    for timeframe, minutes_per_candle in (("1h", 60), ("4h", 240)):
        rows = tuple(
            replace(row, timeframe=timeframe)
            for row in seed_sr_demo.aggregate(minutes, minutes_per_candle, ANCHOR)
        )
        assert rows, f"the generator produced no {timeframe} candles"
        insert_candles(engine, rows)
    state = AppState(engine, settings=make_settings(url), clock=lambda: END_INSTANT)
    return DashboardService(state)


def bands_at(service: DashboardService, as_of: datetime) -> list[dict]:
    payload = service.dashboard(symbol="BTC/USDT", timeframe="4h", as_of=as_of)
    bands = payload["overlays"]["zone_bands"]["bands"]
    assert bands, "the synthetic series must present bands at this instant"
    return bands


def band_targets(band: dict) -> set[tuple[str, str]]:
    """Every stored bound pair this band may be linked through (its own, or a member zone's)."""

    targets = {(str(band["band_low"]), str(band["band_high"]))}
    for member in band.get("member_zones") or ():
        targets.add((str(member["band_low"]), str(member["band_high"])))
    return targets


def test_a_stored_exact_reference_links_while_unreferenced_bands_stay_unlinked_at_one_instant(service):
    """One fixed as_of must show both a true positive and a true negative of the identity rule."""

    bands = bands_at(service, LINKED_INSTANT)
    linked = [band for band in bands if band["linked_events"]["linked"]]
    unlinked = [band for band in bands if not band["linked_events"]["linked"]]
    assert linked, "an event detected at this instant holds the band's own bounds"
    assert unlinked, "a band nothing referenced must stay unlinked at the same instant"

    for band in linked:
        linkage = band["linked_events"]
        assert linkage["event_count"] == sum(linkage["counts"].values())
        assert linkage["events"], "a linked band carries its stored events"
        # Exact identity, measured against the payload's own bounds: no tolerance, no rounding.
        for bounds in linkage["matched_band_bounds"]:
            assert (bounds["band_low"], bounds["band_high"]) in band_targets(band)
        assert linkage["overlap_note"] in (None, OVERLAP_NOTE)

    for band in unlinked:
        linkage = band["linked_events"]
        assert linkage["counts"]["breakout"] == 0
        assert linkage["event_count"] == 0


def test_a_historical_reference_to_the_same_level_is_reported_as_an_overlap_not_a_link(service):
    """The end state: every stored reference to these levels differs at one edge from the band."""

    bands = bands_at(service, END_INSTANT)
    overlapping = [band for band in bands if band["linked_events"]["overlapping"]]
    assert overlapping, "the demo series stores references to these levels at earlier bounds"

    for band in overlapping:
        linkage = band["linked_events"]
        assert linkage["linked"] is False, "an overlapping reference is not a link"
        assert linkage["overlapping_count"] == len(linkage["overlapping_events"])
        assert linkage["overlapping_count"] == sum(linkage["overlapping_counts"].values())
        assert linkage["overlap_note"] == OVERLAP_NOTE
        targets = [(Decimal(low), Decimal(high)) for low, high in band_targets(band)]
        for record in linkage["overlapping_events"]:
            relation = record["overlap_relation"]
            lower, upper = Decimal(str(record["band_low"])), Decimal(str(record["band_high"]))
            assert relation in RELATIONS
            assert (str(record["band_low"]), str(record["band_high"])) not in band_targets(band), (
                "an identical reference would be a link, not an overlap"
            )
            overlapping_targets = [
                (low, high) for low, high in targets if lower < high and upper > low
            ]
            assert overlapping_targets, "an 'overlap' that does not overlap would be a fabrication"
            # The stated relation must be true of at least one bound pair it actually overlaps.
            assert any(
                (lower >= low and upper <= high) if relation == "contained_by"
                else (lower <= low and upper >= high) if relation == "contains"
                else True
                for low, high in overlapping_targets
            ), f"{relation} does not describe {lower}-{upper} against {overlapping_targets}"

    no_reference = [
        band
        for band in bands
        if not band["linked_events"]["linked"] and not band["linked_events"]["overlapping"]
    ]
    assert no_reference, "the series also presents a band nothing references at all"


def test_retests_reach_the_linkage_with_the_reference_of_their_own_breakout(service):
    """BRAIN-AUDIT-0005: retests were invisible to the linkage because their reference was dropped."""

    bands = bands_at(service, END_INSTANT)
    records = [
        record
        for band in bands
        for record in band["linked_events"]["overlapping_events"]
    ]
    retests = [record for record in records if record["kind"] == "retest"]
    assert retests, "stored retests must reach the linkage, not vanish in the projection"
    for record in retests:
        assert record["reference_type"] == "zone"
        assert record["reference_id"], "a retest carries the reference id of the breakout it follows"
        assert record["breakout_id"], "a retest keeps the id of the breakout it follows"

    breakout_bounds = {
        (str(record["band_low"]), str(record["band_high"])) for record in records if record["kind"] == "breakout"
    }
    for record in retests:
        assert (str(record["band_low"]), str(record["band_high"])) in breakout_bounds, (
            "a retest's reference is the breakout's reference, not its own candle"
        )
