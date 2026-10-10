from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from trading_assistant.config import Settings
from trading_assistant.database import create_database_engine
from trading_assistant.market_data import (
    CandleValidationError,
    ExchangeDataError,
    HistoricalCandleConflict,
    MarketDataService,
    parse_ohlcv_row,
    validate_ohlcv_rows,
)
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    is_timeframe_aligned,
    latest_closed_candle_open_time,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FIVE_MINUTES_MS = 5 * 60 * 1_000

def candle_row(timestamp_ms: int, *, open_value="10.1", high="11.2", low="9.8", close="10.7", volume="3.25"):
    return [timestamp_ms, open_value, high, low, close, volume]


class FakeSource:
    exchange_id = "binance"

    def __init__(self, rows, *, fail_on_call: int | None = None):
        self.rows = list(rows)
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.last_http_response = None
        self.requests: list[int] = []
        self.request_limits: list[int] = []

    def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
        self.calls += 1
        self.requests.append(since_ms)
        self.request_limits.append(limit)
        if self.fail_on_call == self.calls:
            raise ConnectionError("simulated offline exchange")
        return [row for row in self.rows if int(row[0]) >= since_ms][:limit]


def configured_settings(
    tmp_path: Path,
    *,
    page_limit: int = 100,
    exchange: str = "binance",
    symbol: str = "ETH/USDT",
    base_asset: str = "ETH",
    quote_asset: str = "USDT",
) -> Settings:
    return Settings(
        _env_file=None,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        exchange=exchange,
        default_timeframe="5m",
        supported_timeframes=("5m", "15m", "1h", "4h"),
        raw_data_dir=tmp_path / "raw",
        market_data_page_limit=page_limit,
        market_data_max_pages=20,
    )


def migrate_database(database_url: str) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def create_service(
    tmp_path: Path,
    source,
    *,
    page_limit: int = 100,
    exchange: str = "binance",
    symbol: str = "ETH/USDT",
    base_asset: str = "ETH",
    quote_asset: str = "USDT",
):
    database_path = tmp_path / "market.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path}")
    migrate_database(f"sqlite:///{database_path}")
    settings = configured_settings(
        tmp_path,
        page_limit=page_limit,
        exchange=exchange,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
    )
    service = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(settings.raw_data_dir),
        clock=lambda: EPOCH + timedelta(days=10),
    )
    return engine, service


def test_parse_ccxt_row_uses_utc_and_decimal_values():
    candle = parse_ohlcv_row(
        [0, 0.1, 0.3, 0.05, 0.2, 0.1],
        exchange="binance",
        symbol="ETH/USDT",
        timeframe="5m",
    )

    assert candle.timestamp == EPOCH
    assert candle.timestamp.tzinfo is UTC
    assert candle.open == Decimal("0.1")
    assert candle.high == Decimal("0.3")
    assert isinstance(candle.volume, Decimal)


def test_datetime_conversion_is_explicitly_utc():
    plus_two = datetime.fromisoformat("2024-01-01T02:00:00+02:00")
    utc = datetime.fromisoformat("2024-01-01T00:00:00+00:00")

    assert datetime_to_milliseconds(plus_two) == datetime_to_milliseconds(utc)
    assert datetime_to_milliseconds(utc) % FIVE_MINUTES_MS == 0


def test_weekly_timeframe_uses_monday_utc_alignment():
    monday_open = EPOCH + timedelta(days=4)

    assert is_timeframe_aligned(datetime_to_milliseconds(monday_open), "1w")
    assert not is_timeframe_aligned(0, "1w")
    assert latest_closed_candle_open_time(monday_open + timedelta(days=7), "1w") == monday_open


def test_closed_candle_filter_excludes_current_forming_candle():
    report = validate_ohlcv_rows(
        [candle_row(0), candle_row(FIVE_MINUTES_MS), candle_row(2 * FIVE_MINUTES_MS)],
        exchange="binance",
        symbol="ETH/USDT",
        timeframe="5m",
        expected_start=EPOCH,
        expected_end=EPOCH + timedelta(minutes=5),
        closed_through=EPOCH + timedelta(minutes=5),
    )

    assert report.is_valid
    assert report.complete
    assert [candle.timestamp for candle in report.candles] == [EPOCH, EPOCH + timedelta(minutes=5)]
    assert report.excluded_open_count == 1


def test_validation_reports_duplicate_out_of_order_and_unaligned_timestamps():
    report = validate_ohlcv_rows(
        [
            candle_row(FIVE_MINUTES_MS),
            candle_row(0),
            candle_row(0),
            candle_row(1),
        ],
        exchange="binance",
        symbol="ETH/USDT",
        timeframe="5m",
    )

    codes = {issue.code for issue in report.issues}
    assert {"duplicate_timestamp", "out_of_order", "unaligned_timestamp"} <= codes
    assert report.rejected_count >= 3


def test_validation_reports_missing_candle_gaps_without_fabricating_data():
    report = validate_ohlcv_rows(
        [candle_row(0), candle_row(2 * FIVE_MINUTES_MS)],
        exchange="binance",
        symbol="ETH/USDT",
        timeframe="5m",
        expected_start=EPOCH,
        expected_end=EPOCH + timedelta(minutes=10),
    )

    assert len(report.candles) == 2
    assert report.missing_candle_count == 1
    assert report.gaps[0].start == EPOCH + timedelta(minutes=5)
    assert report.gaps[0].end == EPOCH + timedelta(minutes=5)
    assert not report.complete


def test_missing_and_non_numeric_values_are_reported():
    report = validate_ohlcv_rows(
        [candle_row(0, close=None), candle_row(FIVE_MINUTES_MS, volume="not-a-number")],
        exchange="binance",
        symbol="ETH/USDT",
        timeframe="5m",
    )

    assert report.rejected_count == 2
    assert all(issue.code == "malformed_value" for issue in report.issues)


def test_invalid_ohlc_and_negative_volume_are_rejected():
    report = validate_ohlcv_rows(
        [
            candle_row(0, open_value="12", high="11", low="10", close="10.5"),
            candle_row(FIVE_MINUTES_MS, volume="-0.01"),
        ],
        exchange="binance",
        symbol="ETH/USDT",
        timeframe="5m",
    )

    assert report.rejected_count == 2
    assert {issue.code for issue in report.issues} >= {"invalid_ohlc", "negative_volume"}
    with pytest.raises(CandleValidationError):
        parse_ohlcv_row(
            candle_row(0, open_value="12", high="11", low="10", close="10.5"),
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="5m",
        )


def test_download_is_closed_decimal_idempotent_and_retrieval_is_chronological(tmp_path):
    rows = [candle_row(0), candle_row(FIVE_MINUTES_MS), candle_row(2 * FIVE_MINUTES_MS)]
    source = FakeSource(rows)
    engine, service = create_service(tmp_path, source)
    as_of = EPOCH + timedelta(minutes=10)

    try:
        first = service.download_history(start_time=EPOCH, as_of=as_of)
        assert first.received_count == 3
        assert first.accepted_count == 2
        assert first.excluded_open_count == 1
        assert first.inserted_count == 2
        assert first.already_present_count == 0
        assert first.complete
        assert len(first.raw_files) == 1
        assert json.loads(first.raw_files[0].read_text(encoding="utf-8"))["exchange"] == "binance"
        with engine.connect() as connection:
            stored_numeric_type, stored_numeric_text = connection.execute(
                text("SELECT typeof(open), open FROM ohlcv_candles LIMIT 1")
            ).one()
        assert stored_numeric_type == "text"
        assert stored_numeric_text == "10.1"

        repeated = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=5),
            as_of=as_of,
        )
        assert repeated.inserted_count == 0
        assert repeated.already_present_count == 2

        rows.extend(
            [
                candle_row(3 * FIVE_MINUTES_MS),
                candle_row(4 * FIVE_MINUTES_MS),
                candle_row(5 * FIVE_MINUTES_MS),
                candle_row(6 * FIVE_MINUTES_MS),
            ]
        )
        source.rows = list(rows)
        updated = service.update_history(as_of=EPOCH + timedelta(minutes=30))
        assert updated.inserted_count == 4
        assert updated.excluded_open_count == 1
        assert updated.complete

        candles = service.get_candles(
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="5m",
        )
        assert [candle.timestamp for candle in candles.candles] == [
            EPOCH + timedelta(minutes=5 * index) for index in range(6)
        ]
        assert candles.complete
        assert all(candle.timestamp.tzinfo is UTC for candle in candles.candles)
        assert candles.candles[0].open == Decimal("10.1")

        no_op = service.update_history(as_of=EPOCH + timedelta(minutes=30))
        assert no_op.inserted_count == 0
        assert no_op.received_count == 0
    finally:
        engine.dispose()


def test_retrieval_reports_gaps_in_stored_candle_range(tmp_path):
    source = FakeSource([candle_row(0), candle_row(2 * FIVE_MINUTES_MS)])
    engine, service = create_service(tmp_path, source)
    try:
        updated = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=10),
            as_of=EPOCH + timedelta(minutes=15),
        )
        retrieved = service.get_candles(
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="5m",
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=10),
        )

        assert not updated.complete
        assert updated.missing_candle_count == 1
        assert not retrieved.complete
        assert retrieved.missing_candle_count == 1
        assert len(retrieved.candles) == 2
    finally:
        engine.dispose()


def test_download_paginates_and_advances_cursor_by_one_candle(tmp_path):
    rows = [candle_row(index * FIVE_MINUTES_MS) for index in range(4)]
    source = FakeSource(rows)
    engine, service = create_service(tmp_path, source, page_limit=2)
    try:
        result = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=15),
            as_of=EPOCH + timedelta(minutes=20),
        )

        assert result.received_count == 4
        assert result.inserted_count == 4
        assert result.complete
        assert source.requests == [0, 2 * FIVE_MINUTES_MS]
        assert len(result.raw_files) == 2
    finally:
        engine.dispose()


def test_exchange_page_cap_does_not_truncate_required_history(tmp_path):
    rows = [candle_row(index * FIVE_MINUTES_MS) for index in range(4)]
    source = FakeSource(rows)
    # Simulate a source with a small hard per-request cap. The configured limit
    # is intentionally oversized; the service must request multiple pages.
    source.max_ohlcv_limit = 2
    engine, service = create_service(tmp_path, source, page_limit=5_000)
    try:
        result = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=15),
            as_of=EPOCH + timedelta(minutes=20),
        )

        assert result.received_count == result.inserted_count == 4
        assert result.complete
        assert source.request_limits == [2, 2]
        assert source.requests == [0, 2 * FIVE_MINUTES_MS]
        archived_limits = [
            json.loads(path.read_text(encoding="utf-8"))["request"]["limit"]
            for path in result.raw_files
        ]
        assert archived_limits == [2, 2]
    finally:
        engine.dispose()


def test_incremental_exchange_failure_preserves_existing_history(tmp_path):
    source = FakeSource([candle_row(0), candle_row(FIVE_MINUTES_MS)])
    engine, service = create_service(tmp_path, source, page_limit=2)
    try:
        initial = service.download_history(start_time=EPOCH, as_of=EPOCH + timedelta(minutes=10))
        assert initial.inserted_count == 2

        source.rows = [
            candle_row(2 * FIVE_MINUTES_MS),
            candle_row(3 * FIVE_MINUTES_MS),
            candle_row(4 * FIVE_MINUTES_MS),
            candle_row(5 * FIVE_MINUTES_MS),
        ]
        source.calls = 0
        source.requests.clear()
        source.fail_on_call = 2

        with pytest.raises(ExchangeDataError):
            service.update_history(as_of=EPOCH + timedelta(minutes=30))

        candles = service.get_candles(
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="5m",
        )
        assert [candle.timestamp for candle in candles.candles] == [EPOCH, EPOCH + timedelta(minutes=5)]
        assert source.requests == [2 * FIVE_MINUTES_MS, 4 * FIVE_MINUTES_MS]
        assert len(list((tmp_path / "raw").rglob("*.json"))) == 2
    finally:
        engine.dispose()


def test_exchange_error_message_survives_wrapping_and_logging(tmp_path, caplog):
    source = FakeSource([], fail_on_call=1)
    engine, service = create_service(tmp_path, source)
    try:
        with caplog.at_level("ERROR", logger="trading_assistant.market_data.service"):
            with pytest.raises(ExchangeDataError) as captured:
                service.download_history(
                    start_time=EPOCH,
                    end_time=EPOCH,
                    as_of=EPOCH + timedelta(minutes=5),
                )

        assert "underlying ConnectionError: simulated offline exchange" in str(
            captured.value
        )
        assert isinstance(captured.value.__cause__, ConnectionError)
        assert str(captured.value.__cause__) == "simulated offline exchange"
        failure_record = next(
            record
            for record in caplog.records
            if record.getMessage() == "Market-data exchange request failed"
        )
        assert failure_record.fields["error_type"] == "ConnectionError"
        assert failure_record.fields["error"] == "simulated offline exchange"
    finally:
        engine.dispose()


def test_invalid_download_is_rejected_before_database_insert(tmp_path):
    invalid = candle_row(0, open_value="12", high="11", low="10", close="10.5")
    source = FakeSource([invalid])
    engine, service = create_service(tmp_path, source)
    try:
        with pytest.raises(CandleValidationError):
            service.download_history(
                start_time=EPOCH,
                end_time=EPOCH,
                as_of=EPOCH + timedelta(minutes=5),
            )

        assert service.get_candles(
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="5m",
        ).candles == ()
    finally:
        engine.dispose()


def test_conflicting_exchange_values_do_not_overwrite_existing_candle(tmp_path):
    source = FakeSource([candle_row(0)])
    engine, service = create_service(tmp_path, source)
    try:
        service.download_history(start_time=EPOCH, end_time=EPOCH, as_of=EPOCH + timedelta(minutes=5))
        source.rows = [candle_row(0, close="10.8")]

        with pytest.raises(HistoricalCandleConflict):
            service.download_history(start_time=EPOCH, end_time=EPOCH, as_of=EPOCH + timedelta(minutes=5))

        candle = service.get_candles(
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="5m",
        ).candles[0]
        assert candle.close == Decimal("10.7")
    finally:
        engine.dispose()


def test_empty_initial_download_requires_explicit_start_for_incremental_api(tmp_path):
    source = FakeSource([])
    engine, service = create_service(tmp_path, source)
    try:
        with pytest.raises(ValueError, match="provide start_time"):
            service.update_history()
    finally:
        engine.dispose()
