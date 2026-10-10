"""Chart evidence: confirmation timing, labels, pattern lifecycle, isolation.

The central property tested here is temporal: at every candle boundary ``T``,
the evidence returned for ``as_of = T`` must contain only items whose
``known_at <= T``, and an item must keep the same identity and label once it
has become known. A chart can therefore never show a swing as confirmed before
its confirmation instant, and never shows future structure in earlier views.
"""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL
from web_fixtures import insert_candles, migrated_engine, make_settings

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.swings import SwingKind, SwingPoint, SwingTiePolicy
from trading_assistant.pattern_liquidity.analysis import analyze_patterns
from trading_assistant.web.chart_evidence import (
    CHART_EVIDENCE_RULES_VERSION,
    build_chart_evidence,
    swing_labels,
)
from trading_assistant.web.dashboard_service import DashboardService
from trading_assistant.web.state import AppState

#: Synthetic closes: a clean double top (peaks 111.0 / 111.2, neckline 99),
#: which confirms when price closes below the neckline.
DOUBLE_TOP_CLOSES = (
    100, 102, 104, 106, 108, 110, 108, 105, 102, 100, 101, 103, 105, 107, 109,
    110.2, 108, 105, 102, 99, 96, 94, 95, 96, 95, 94, 92, 90, 88, 86,
)
#: The same shape, then a close above the invalidation level (111.2).
INVALIDATED_CLOSES = DOUBLE_TOP_CLOSES[:19] + (101, 104, 108, 113, 115)


def series(closes, *, exchange=EXCHANGE):
    candles = []
    for index, value in enumerate(closes):
        close = D(str(value))
        open_ = close - D("0.5") if index % 2 else close - D("0.2")
        candles.append(
            Candle(
                exchange=exchange,
                symbol=SYMBOL,
                timeframe="1h",
                timestamp=EPOCH + INTERVAL * index,
                open=open_,
                high=close + D("1.0"),
                low=close - D("1.0"),
                close=close,
                volume=D("10"),
            )
        )
    return tuple(candles)


def evidence_at(candles, boundary_index):
    as_of = EPOCH + INTERVAL * boundary_index
    closed = tuple(c for c in candles if c.timestamp + INTERVAL <= as_of)
    snapshot = analyze_patterns(
        closed, exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h", as_of=as_of
    )
    return build_chart_evidence(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe="1h",
        as_of=as_of,
        snapshot=snapshot,
        candles=closed,
        interval=INTERVAL,
    )


def swing(kind, index, price):
    return SwingPoint(
        kind=kind,
        timestamp=EPOCH + INTERVAL * index,
        price=D(str(price)),
        confirmed_at=EPOCH + INTERVAL * (index + 3),
        confirmed_by_timestamp=EPOCH + INTERVAL * (index + 2),
        left_window=2,
        right_window=2,
        tie_policy=SwingTiePolicy.STRICT,
    )


# ---------------------------------------------------------------- labels ----


def test_swing_labels_compare_only_with_the_previous_swing_of_the_same_kind():
    swings = [
        swing(SwingKind.HIGH, 2, 110),
        swing(SwingKind.LOW, 4, 100),
        swing(SwingKind.HIGH, 6, 112),  # higher high
        swing(SwingKind.LOW, 8, 101),  # higher low
        swing(SwingKind.HIGH, 10, 111),  # lower high
        swing(SwingKind.LOW, 12, 99),  # lower low
        swing(SwingKind.HIGH, 14, 111),  # equal high
    ]
    labels = swing_labels(swings)
    assert labels[(SwingKind.HIGH, swings[0].timestamp)] == "SH"
    assert labels[(SwingKind.LOW, swings[1].timestamp)] == "SL"
    assert labels[(SwingKind.HIGH, swings[2].timestamp)] == "HH"
    assert labels[(SwingKind.LOW, swings[3].timestamp)] == "HL"
    assert labels[(SwingKind.HIGH, swings[4].timestamp)] == "LH"
    assert labels[(SwingKind.LOW, swings[5].timestamp)] == "LL"
    assert labels[(SwingKind.HIGH, swings[6].timestamp)] == "EH"


# --------------------------------------------------------------- timing -----


def test_every_returned_item_is_known_at_or_before_the_as_of_instant():
    candles = series(DOUBLE_TOP_CLOSES)
    for boundary in range(6, len(candles) + 1):
        payload = evidence_at(candles, boundary)
        as_of = EPOCH + INTERVAL * boundary
        assert payload["excluded_future_count"] >= 0
        for group in ("swings", "patterns", "breakouts", "sweeps", "retests", "failed_breakouts", "candle_shapes"):
            for item in payload[group]:
                assert item["known_at"] is not None
                known = _parse(item["known_at"])
                assert known <= as_of, (group, item["id"], known, as_of)


def test_a_swing_is_absent_until_its_confirmation_instant_and_then_keeps_its_label():
    candles = series(DOUBLE_TOP_CLOSES)
    history = {}
    for boundary in range(6, len(candles) + 1):
        payload = evidence_at(candles, boundary)
        for item in payload["swings"]:
            history.setdefault(item["id"], []).append((boundary, item["label"], item["known_at"]))
    assert history, "the double-top fixture must produce confirmed swings"
    for swing_id, sightings in history.items():
        first_boundary, first_label, known_at = sightings[0]
        # The first time a swing is shown must be at or after its confirmation.
        assert _parse(known_at) <= EPOCH + INTERVAL * first_boundary
        # Once visible, neither label nor identity may change in later views.
        assert {label for _, label, _ in sightings} == {first_label}, swing_id
        # It is shown contiguously from its first appearance onward (no re-labelling
        # back and forth as more candles arrive).
        boundaries = [b for b, _, _ in sightings]
        assert boundaries == list(range(first_boundary, first_boundary + len(boundaries)))


def test_pattern_states_progress_formed_to_confirmed_with_their_own_times():
    candles = series(DOUBLE_TOP_CLOSES)
    before = evidence_at(candles, 19)
    assert [p["state"] for p in before["patterns"] if p["is_latest_state"]] == ["formed"]
    after = evidence_at(candles, 22)
    latest = [p for p in after["patterns"] if p["is_latest_state"]]
    assert [p["state"] for p in latest] == ["confirmed"]
    formed_record = next(p for p in after["patterns"] if p["state"] == "formed")
    assert formed_record["is_latest_state"] is False
    assert _parse(latest[0]["known_at"]) > _parse(formed_record["known_at"])
    assert latest[0]["confirmation_time"] == latest[0]["known_at"]
    assert latest[0]["type"] == "double_top"
    assert latest[0]["label"] == "Double top"
    assert latest[0]["side"] == "top"


def test_pattern_invalidation_is_reported_only_when_it_happens():
    """Rally through the invalidation level (111.2) with no neckline break first."""

    candles = series(INVALIDATED_CLOSES)
    # Candle index 21 (close 108) is inside the peaks but below invalidation.
    before = evidence_at(candles, 22)
    assert [p["state"] for p in before["patterns"] if p["is_latest_state"]] == ["formed"]
    # Candle index 22 closes at 113, above 111.2: known at its close (boundary 23).
    after = evidence_at(candles, 23)
    latest = [p for p in after["patterns"] if p["is_latest_state"]]
    assert {p["state"] for p in latest} == {"invalidated"}
    assert all(p["confirmation_time"] is None for p in latest)
    assert _parse(latest[0]["known_at"]) == EPOCH + INTERVAL * 23


def test_shapes_and_events_never_appear_before_their_candle_close():
    candles = series(DOUBLE_TOP_CLOSES)
    payload = evidence_at(candles, 10)
    for shape in payload["candle_shapes"]:
        assert _parse(shape["known_at"]) <= EPOCH + INTERVAL * 10
    assert payload["evidence_window"]["last_candle"] == _iso(EPOCH + INTERVAL * 9)


def test_zone_and_range_projections_carry_their_source_and_timestamps():
    payload = evidence_at(series(DOUBLE_TOP_CLOSES), len(DOUBLE_TOP_CLOSES))
    for zone in payload["zones"]:
        assert zone["role"] in {"support", "resistance", "at_price"}
        assert zone["band_low"] <= zone["band_high"] or float(zone["band_low"]) <= float(zone["band_high"])
        assert zone["source_swing_times"]
    assert payload["rules_version"] == CHART_EVIDENCE_RULES_VERSION


_EVIDENCE_KIND_BY_GROUP = {
    "swings": "swing",
    "patterns": "pattern",
    "breakouts": "breakout",
    "failed_breakouts": "failed_breakout",
    "sweeps": "sweep",
    "retests": "retest",
    "equal_levels": "equal_level",
    "zones": "zone",
    "candle_shapes": "candle_shape",
}


def test_every_item_carries_an_explicit_evidence_kind_for_the_frontend():
    """The explainer dispatches on evidence_kind; swings and patterns have no other type tag."""
    candles = series(DOUBLE_TOP_CLOSES)
    seen: dict[str, int] = {}
    for boundary in range(6, len(candles) + 1):
        payload = evidence_at(candles, boundary)
        for group, expected in _EVIDENCE_KIND_BY_GROUP.items():
            for item in payload.get(group, []):
                assert item["evidence_kind"] == expected, (group, item.get("id"))
                seen[group] = seen.get(group, 0) + 1
    # Swings and patterns are exercised by the double-top fixture; the check must not pass vacuously.
    assert seen.get("swings", 0) > 0
    assert seen.get("patterns", 0) > 0
    assert seen.get("candle_shapes", 0) > 0


def test_candle_shape_window_is_limited_to_the_chart_window():
    closes = tuple(100 + (i % 7) for i in range(600))
    payload = evidence_at(series(closes), 600)
    assert payload["evidence_window"]["candle_count"] == 500
    assert payload["evidence_window"]["candle_limit"] == 500


# ------------------------------------------------------ service / isolation --


@pytest.fixture
def service_state(tmp_path):
    engine, url = migrated_engine(tmp_path, name="evidence.sqlite3")
    insert_candles(engine, series(DOUBLE_TOP_CLOSES))
    settings = make_settings(url)
    clock_time = EPOCH + INTERVAL * len(DOUBLE_TOP_CLOSES)
    state = AppState(engine, settings=settings, clock=lambda: clock_time)
    yield state
    engine.dispose()


def test_service_returns_binance_evidence_and_ignores_other_exchanges(service_state):
    service = DashboardService(service_state)
    payload = service.chart_evidence(symbol=SYMBOL, timeframe="1h")
    assert payload["available"] is True
    assert payload["exchange"] == "binance"
    assert payload["evidence_window"]["candle_count"] == len(DOUBLE_TOP_CLOSES)


def test_kraken_historical_candles_never_enter_binance_chart_evidence(tmp_path):
    engine, url = migrated_engine(tmp_path, name="isolation.sqlite3")
    insert_candles(engine, series(DOUBLE_TOP_CLOSES))
    insert_candles(engine, series(INVALIDATED_CLOSES, exchange="kraken"))
    settings = make_settings(url)
    state = AppState(engine, settings=settings, clock=lambda: EPOCH + INTERVAL * 40)
    service = DashboardService(state)
    payload = service.chart_evidence(symbol=SYMBOL, timeframe="1h", as_of=EPOCH + INTERVAL * len(DOUBLE_TOP_CLOSES))
    assert payload["exchange"] == "binance"
    assert payload["evidence_window"]["candle_count"] == len(DOUBLE_TOP_CLOSES)
    engine.dispose()


def test_empty_history_is_an_explicit_unavailable_state_not_fabricated_evidence(tmp_path):
    engine, url = migrated_engine(tmp_path, name="empty.sqlite3")
    settings = make_settings(url)
    state = AppState(engine, settings=settings, clock=lambda: EPOCH + INTERVAL * 5)
    payload = DashboardService(state).chart_evidence(symbol=SYMBOL, timeframe="1h")
    assert payload["available"] is False
    assert payload["reason"]
    assert payload["swings"] == [] and payload["patterns"] == [] and payload["candle_shapes"] == []
    engine.dispose()


def _parse(text):
    from datetime import datetime

    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def test_repeat_chart_evidence_reads_reuse_the_analysis_without_changing_it(tmp_path, monkeypatch):
    """Same instant and same candle window: one analysis, identical payload. Changed window: recomputed."""
    import trading_assistant.pattern_liquidity.analysis as analysis_module
    from trading_assistant.web import dashboard_service as ds

    engine, url = migrated_engine(tmp_path, "cache.sqlite3")
    insert_candles(engine, series(DOUBLE_TOP_CLOSES))
    settings = make_settings(url)
    state = AppState(engine=engine, settings=settings, clock=lambda: EPOCH + INTERVAL * 40)
    service = DashboardService(state)
    as_of = EPOCH + INTERVAL * len(DOUBLE_TOP_CLOSES)

    ds._CHART_EVIDENCE_CACHE.clear()
    calls = []
    real = analysis_module.analyze_patterns

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(analysis_module, "analyze_patterns", counting)
    first = service.chart_evidence(symbol=SYMBOL, timeframe="1h", as_of=as_of)
    second = service.chart_evidence(symbol=SYMBOL, timeframe="1h", as_of=as_of)
    assert first == second
    assert len(calls) == 1
    # Mutating a returned payload must never poison the cache.
    second["swings"].clear()
    assert service.chart_evidence(symbol=SYMBOL, timeframe="1h", as_of=as_of)["swings"]
    assert len(calls) == 1
    # A new as-of instant is a different analysis and is not served from the cache.
    service.chart_evidence(symbol=SYMBOL, timeframe="1h", as_of=as_of + INTERVAL)
    assert len(calls) == 2


# --- Lifecycle anchors: where each pattern event is drawn (audit P2) -------------

def _is_candle_open(value):
    return (value - EPOCH) % INTERVAL == timedelta(0)


def _latest(payload):
    return [p for p in payload["patterns"] if p["is_latest_state"]]


def test_formed_pattern_is_anchored_on_its_last_swing_candle_not_its_close_boundary():
    candles = series(DOUBLE_TOP_CLOSES)
    formed = next(p for p in evidence_at(candles, 19)["patterns"] if p["state"] == "formed")
    anchor = _parse(formed["anchor_time"])
    assert _is_candle_open(anchor), "anchor must be a candle open, never a close boundary"
    assert anchor in {c.timestamp for c in candles}
    # known_at is the close boundary of the last swing's confirmation candle: a different instant.
    assert _parse(formed["known_at"]) != anchor
    assert formed["confirmed_at"] is None and formed["invalidated_at"] is None


def test_confirmed_pattern_is_anchored_on_the_candle_that_closed_through_the_neckline():
    candles = series(DOUBLE_TOP_CLOSES)
    confirmed = _latest(evidence_at(candles, 22))[0]
    assert confirmed["state"] == "confirmed"
    anchor = _parse(confirmed["anchor_time"])
    assert _is_candle_open(anchor)
    assert anchor == EPOCH + INTERVAL * 20, "index 20 (close 96, below the 99 neckline) is the breaking candle"
    assert _parse(confirmed["confirmed_at"]) == EPOCH + INTERVAL * 21  # its close boundary
    assert confirmed["invalidated_at"] is None


def test_confirmation_on_the_latest_stored_candle_keeps_a_visible_anchor():
    """Audit edge case: the neckline break is the latest stored candle.

    Its known_at (close boundary) equals as_of and lies beyond that candle's open
    timestamp. The projection must still place the event on that stored candle.
    """

    candles = series(DOUBLE_TOP_CLOSES[:21])  # last stored candle = index 20 (the break, close 96)
    payload = evidence_at(candles, 21)
    latest = _latest(payload)
    assert [p["state"] for p in latest] == ["confirmed"]
    confirmed = latest[0]
    assert _parse(confirmed["known_at"]) == EPOCH + INTERVAL * 21 == _parse(payload["as_of"])
    stored_opens = {c.timestamp for c in candles}
    assert _parse(confirmed["anchor_time"]) in stored_opens
    assert _parse(confirmed["anchor_time"]) == candles[-1].timestamp


def test_invalidation_is_anchored_on_the_invalidating_candle_not_on_formation():
    candles = series(INVALIDATED_CLOSES)
    latest = _latest(evidence_at(candles, 23))
    assert {p["state"] for p in latest} == {"invalidated"}
    record = latest[0]
    anchor = _parse(record["anchor_time"])
    assert anchor == EPOCH + INTERVAL * 22, "index 22 closed above the invalidation level"
    assert anchor != _parse(record["formed_at"]), "invalidation must not be placed at formation"
    assert _parse(record["invalidated_at"]) == EPOCH + INTERVAL * 23
    assert record["confirmed_at"] is None


def test_invalidation_on_the_latest_stored_candle_is_visible():
    candles = series(INVALIDATED_CLOSES[:23])
    payload = evidence_at(candles, 23)
    record = _latest(payload)[0]
    assert record["state"] == "invalidated"
    assert _parse(record["anchor_time"]) == candles[-1].timestamp
    assert _parse(record["invalidated_at"]) == _parse(payload["as_of"])
