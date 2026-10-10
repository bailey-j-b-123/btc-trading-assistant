"""Regression tests for the bounded, resumable Binance historical backfill.

Every exchange call goes to a deterministic in-memory fake source. These are synthetic checks of
the backfill logic only; they do not prove that Binance returns any particular data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from trading_assistant.config import Settings
from trading_assistant.database import create_database_engine
from trading_assistant.market_data.backfill import (
    floor_to_timeframe,
    plan_chunks,
    run_backfill,
)
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.service import create_market_data_service

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EPOCH = datetime(2024, 1, 1, tzinfo=UTC)
HOUR_MS = 3_600_000
SYMBOL = "BTC/USDT"


def _row(timestamp_ms: int) -> list:
    return [timestamp_ms, "10.1", "11.2", "9.8", "10.7", "3.25"]


def _rows(count: int, *, skip: set[int] | None = None) -> list[list]:
    skipped = skip or set()
    start_ms = int(EPOCH.timestamp() * 1000)
    return [_row(start_ms + i * HOUR_MS) for i in range(count) if i not in skipped]


class FakeSource:
    def __init__(self, rows, *, exchange_id: str = "binance", fail_on_call: int | None = None):
        self.exchange_id = exchange_id
        self.rows = list(rows)
        self.fail_on_call = fail_on_call
        self.calls = 0

    def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise ConnectionError("simulated exchange outage")
        return [row for row in self.rows if int(row[0]) >= since_ms][:limit]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        symbol=SYMBOL,
        base_asset="BTC",
        quote_asset="USDT",
        exchange="binance",
        default_timeframe="1h",
        supported_timeframes=("5m", "15m", "1h", "4h"),
        raw_data_dir=tmp_path / "raw",
        market_data_page_limit=1000,
        market_data_max_pages=20,
    )


def _database(tmp_path: Path):
    path = tmp_path / "market.sqlite3"
    url = f"sqlite:///{path}"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return create_database_engine(url)


def _service(engine, tmp_path: Path, source):
    return create_market_data_service(
        engine,
        settings=_settings(tmp_path),
        source=source,
        clock=lambda: EPOCH + timedelta(days=30),
    )


def _stored_count(engine, timeframe: str = "1h") -> int:
    repo = CandleRepository(engine)
    return len(repo.get_candles(exchange="binance", symbol=SYMBOL, timeframe=timeframe).candles)


AS_OF = EPOCH + timedelta(hours=300)  # newest closed 1h candle opens at 299:00, the last fixture row


# --- chunk planning (pure) ------------------------------------------------------------------------


def test_plan_chunks_covers_the_range_exactly_without_overlap_or_gaps():
    chunks = plan_chunks(timeframe="1h", start=EPOCH, end=EPOCH + timedelta(hours=249), chunk_candles=100)
    assert [chunk.expected_count for chunk in chunks] == [100, 100, 50]
    assert chunks[0].start == EPOCH
    assert chunks[-1].end == EPOCH + timedelta(hours=249)
    for previous, current in zip(chunks, chunks[1:]):
        assert current.start == previous.end + timedelta(hours=1), "chunks are contiguous, no overlap"


def test_plan_chunks_rejects_misaligned_or_inverted_or_empty_chunk_requests():
    with pytest.raises(ValueError, match="align"):
        plan_chunks(timeframe="1h", start=EPOCH + timedelta(minutes=30), end=EPOCH + timedelta(hours=5))
    with pytest.raises(ValueError, match="not be after"):
        plan_chunks(timeframe="1h", start=EPOCH + timedelta(hours=5), end=EPOCH)
    with pytest.raises(ValueError, match="at least 1"):
        plan_chunks(timeframe="1h", start=EPOCH, end=EPOCH, chunk_candles=0)


def test_floor_to_timeframe_lets_a_plain_date_serve_every_timeframe():
    value = datetime(2024, 1, 2, 7, 33, 10, tzinfo=UTC)
    assert floor_to_timeframe(value, "4h") == datetime(2024, 1, 2, 4, tzinfo=UTC)
    assert floor_to_timeframe(value, "1h") == datetime(2024, 1, 2, 7, tzinfo=UTC)
    assert floor_to_timeframe(value, "5m") == datetime(2024, 1, 2, 7, 30, tzinfo=UTC)


# --- the run itself ----------------------------------------------------------------------------


def test_dry_run_reports_the_plan_with_no_exchange_request_and_no_writes(tmp_path):
    engine = _database(tmp_path)
    source = FakeSource(_rows(300))
    service = _service(engine, tmp_path, source)
    report = run_backfill(
        service, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF,
        chunk_candles=100, dry_run=True,
    )
    item = report.timeframes[0]
    assert report.dry_run is True and report.ok
    assert item.chunks_planned == 3 and item.chunks_remaining == 3 and item.chunks_downloaded == 0
    assert source.calls == 0
    assert _stored_count(engine) == 0


def test_full_backfill_stores_real_candles_chunk_by_chunk(tmp_path):
    engine = _database(tmp_path)
    source = FakeSource(_rows(300))
    service = _service(engine, tmp_path, source)
    report = run_backfill(
        service, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF, chunk_candles=100,
    )
    item = report.timeframes[0]
    assert report.ok and item.error is None
    assert item.chunks_downloaded == 3 and item.inserted == 300
    assert _stored_count(engine) == 300
    assert item.earliest_stored_after == EPOCH
    assert item.latest_stored_after == EPOCH + timedelta(hours=299)


def test_rerunning_a_complete_backfill_is_a_no_op_and_never_duplicates(tmp_path):
    engine = _database(tmp_path)
    source = FakeSource(_rows(300))
    service = _service(engine, tmp_path, source)
    run_backfill(service, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF, chunk_candles=100)
    calls_after_first = source.calls
    report = run_backfill(
        service, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF, chunk_candles=100,
    )
    item = report.timeframes[0]
    assert item.chunks_already_complete == 3 and item.chunks_downloaded == 0 and item.inserted == 0
    assert source.calls == calls_after_first, "complete chunks are skipped without a network request"
    assert _stored_count(engine) == 300


def test_a_failed_chunk_is_reported_and_the_next_run_resumes_from_it(tmp_path):
    engine = _database(tmp_path)
    flaky = _service(engine, tmp_path, FakeSource(_rows(300), fail_on_call=2))
    first = run_backfill(
        flaky, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF, chunk_candles=100,
    )
    failed = first.timeframes[0]
    assert first.ok is False
    assert failed.error and "ConnectionError" in failed.error
    assert _stored_count(engine) == 100, "only the completed chunk is stored; nothing partial is invented"

    healthy_source = FakeSource(_rows(300))
    healthy = _service(engine, tmp_path, healthy_source)
    second = run_backfill(
        healthy, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF, chunk_candles=100,
    )
    resumed = second.timeframes[0]
    assert second.ok
    assert resumed.chunks_already_complete == 1, "the chunk stored before the failure is not re-downloaded"
    assert resumed.chunks_downloaded == 2
    assert _stored_count(engine) == 300


def test_max_chunks_bounds_a_run_and_a_second_run_continues(tmp_path):
    engine = _database(tmp_path)
    service = _service(engine, tmp_path, FakeSource(_rows(300)))
    first = run_backfill(
        service, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF,
        chunk_candles=100, max_chunks=1,
    )
    assert first.timeframes[0].chunks_downloaded == 1
    assert first.timeframes[0].stopped_at_chunk_limit is True
    assert _stored_count(engine) == 100
    second = run_backfill(
        service, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF, chunk_candles=100,
    )
    assert second.timeframes[0].stopped_at_chunk_limit is False
    assert _stored_count(engine) == 300


def test_backfill_never_stores_candles_beyond_as_of(tmp_path):
    """Look-ahead guard: the newest closed candle as of the instant is the last one stored."""

    engine = _database(tmp_path)
    service = _service(engine, tmp_path, FakeSource(_rows(300)))
    as_of = EPOCH + timedelta(hours=100)  # newest closed 1h candle opens at 99:00
    report = run_backfill(service, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=as_of, chunk_candles=100)
    assert report.timeframes[0].latest_stored_after == EPOCH + timedelta(hours=99)
    assert _stored_count(engine) == 100


def test_a_missing_exchange_candle_is_reported_as_a_gap_and_never_filled(tmp_path):
    engine = _database(tmp_path)
    service = _service(engine, tmp_path, FakeSource(_rows(300, skip={50})))
    report = run_backfill(service, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF, chunk_candles=100)
    item = report.timeframes[0]
    assert _stored_count(engine) == 299, "no synthetic candle is inserted for the missing hour"
    assert any(gap["missing"] == 1 for gap in item.gaps), "the missing open time is reported as a gap"


def test_non_binance_source_is_refused_before_any_request(tmp_path):
    """Kraken records stay isolated: the backfill never talks to or writes for another exchange."""

    engine = _database(tmp_path)
    source = FakeSource(_rows(300), exchange_id="kraken")
    service = _service(engine, tmp_path, source)
    with pytest.raises(ValueError, match="Binance-only"):
        run_backfill(service, symbol=SYMBOL, timeframes=("1h",), start=EPOCH, as_of=AS_OF)
    assert source.calls == 0
    assert _stored_count(engine) == 0

def test_cli_refuses_missing_database_without_creating_it(tmp_path: Path, monkeypatch, capsys) -> None:
    import trading_assistant.ingestion.__main__ as cli

    missing = tmp_path / "missing.sqlite3"
    settings = Settings(_env_file=None, database_url=f"sqlite:///{missing}")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    exit_code = cli.main(
        ["backfill", "--start", "2024-01-01", "--dry-run", "--lock-file", str(tmp_path / "x.lock")]
    )

    assert exit_code == 1
    assert not missing.exists()
    assert "database file not found" in capsys.readouterr().err
