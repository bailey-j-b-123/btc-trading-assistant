"""Chart history pagination and bounded chart reads.

All candles here are SYNTHETIC test fixtures (deterministic prices, labelled as such). They
exercise the paging cursor, gap reporting and the bounded read path. They say nothing about real
exchange history.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from web_fixtures import insert_candles, make_client, make_settings, migrated_engine

from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.types import Candle

SYMBOL = "BTC/USDT"
EXCHANGE = "binance"
START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def synthetic_hours(count: int, *, skip: range = range(0)) -> tuple[Candle, ...]:
    """SYNTHETIC 1h closed candles START + k hours for k in range(count), minus skipped indexes."""

    rows = []
    for index in range(count):
        if index in skip:
            continue
        price = D("60000") + D(index)
        rows.append(
            Candle(
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe="1h",
                timestamp=START + index * HOUR,
                open=price,
                high=price + D("5"),
                low=price - D("5"),
                close=price + D("1"),
                volume=D("3"),
            )
        )
    return tuple(rows)


def build(tmp_path, candles, *, boundary_hours: int):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, candles)
    settings = make_settings(url)
    client = make_client(engine, settings, clock=START + boundary_hours * HOUR)
    return engine, client


def fetch(client, **params):
    response = client.get("/api/market/candles", params={"symbol": SYMBOL, "timeframe": "1h", **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_pages_are_contiguous_non_overlapping_and_cover_all_history(tmp_path):
    # SYNTHETIC: 1200 hourly candles; the clock is one interval after the newest candle.
    engine, client = build(tmp_path, synthetic_hours(1200), boundary_hours=1200)
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = fetch(client, limit=500, **({"before": cursor} if cursor else {}))
        pages += 1
        opens = [row[0] for row in page["candles"]]
        seen = opens + seen  # pages are newest-first; prepend older ones
        assert page["returned_count"] == len(page["candles"])
        if page["earliest_reached"]:
            break
        assert page["next_before"] is not None
        cursor = page["next_before"]
        assert pages < 10, "paging must terminate"
    assert pages == 3
    assert len(seen) == 1200
    assert len(set(seen)) == len(seen), "no duplicate boundary candles across pages"
    assert seen == sorted(seen)


def test_first_page_is_latest_and_earliest_reached_is_flagged_on_last_page(tmp_path):
    engine, client = build(tmp_path, synthetic_hours(1200), boundary_hours=1200)
    first = fetch(client, limit=500)
    assert first["candles"][-1][0] == int((START + 1199 * HOUR).timestamp() * 1000)
    assert first["has_older"] is True and first["earliest_reached"] is False
    assert first["earliest_stored_open"] == "2026-01-01T00:00:00Z"
    assert first["latest_stored_open"] == "2026-02-19T23:00:00Z"  # 1199 hours after START
    last = fetch(client, limit=500, before=first["next_before"])
    last = fetch(client, limit=500, before=last["next_before"])
    assert last["returned_count"] == 200
    assert last["earliest_reached"] is True and last["has_older"] is False
    assert last["next_before"] is None


def test_before_the_earliest_candle_returns_empty_and_reached(tmp_path):
    engine, client = build(tmp_path, synthetic_hours(50), boundary_hours=50)
    page = fetch(client, limit=500, before="2026-01-01T00:00:00Z")
    assert page["candles"] == []
    assert page["earliest_reached"] is True
    assert page["next_before"] is None


def test_gap_inside_the_page_is_reported_and_outside_it_is_not(tmp_path):
    # SYNTHETIC: hour 600 is missing from the stored series.
    engine, client = build(tmp_path, synthetic_hours(1200, skip=range(600, 601)), boundary_hours=1200)
    inside = fetch(client, limit=200)  # covers hours 1000..1199: no hole there
    assert inside["complete"] is True and inside["gaps"] == []
    covering = fetch(client, limit=700)  # covers hours 500..1199: includes the hole at 600
    assert covering["complete"] is False
    assert len(covering["gaps"]) == 1
    assert covering["gaps"][0]["missing_count"] == 1
    # The whole-series completeness signal is still exact, computed by COUNT.
    assert covering["series_missing_count"] == 1
    assert inside["series_missing_count"] == 1


def test_history_before_the_first_stored_candle_is_not_reported_as_a_gap(tmp_path):
    engine, client = build(tmp_path, synthetic_hours(120), boundary_hours=120)
    page = fetch(client, limit=500)  # asks for far more than is stored
    assert page["returned_count"] == 120
    assert page["gaps"] == []
    assert page["complete"] is True
    assert page["earliest_reached"] is True


def test_a_window_of_only_missing_candles_still_moves_the_cursor_back(tmp_path):
    # SYNTHETIC: hours 100..399 are missing, so a 100-candle page can be entirely a hole.
    engine, client = build(tmp_path, synthetic_hours(500, skip=range(100, 400)), boundary_hours=500)
    seen: list[int] = []
    cursor = None
    for _ in range(20):
        page = fetch(client, limit=100, **({"before": cursor} if cursor else {}))
        seen = [row[0] for row in page["candles"]] + seen
        if page["earliest_reached"]:
            break
        assert page["next_before"] < (cursor or "9999")
        cursor = page["next_before"]
    else:
        pytest.fail("paging through a stored hole did not terminate")
    assert len(seen) == 200  # hours 0..99 and 400..499 only; the hole is never filled


def test_chart_read_loads_only_the_requested_window(tmp_path, monkeypatch):
    # SYNTHETIC: 3000 candles; a 50-candle page must read 50 rows, not the whole series.
    engine, client = build(tmp_path, synthetic_hours(3000), boundary_hours=3000)
    loaded: list[int] = []
    original = CandleRepository.get_candles

    def counting(self, **kwargs):
        result = original(self, **kwargs)
        loaded.append(len(result.candles))
        return result

    monkeypatch.setattr(CandleRepository, "get_candles", counting)
    page = fetch(client, limit=50)
    assert page["returned_count"] == 50
    assert loaded == [50], f"expected one bounded read of 50 rows, got {loaded}"
