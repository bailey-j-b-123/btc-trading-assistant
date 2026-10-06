"""Market-data orchestration: pagination, raw archival, validation, and safe storage."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self

from sqlalchemy.engine import Engine

from trading_assistant.config import Settings, get_settings
from trading_assistant.market_data.errors import (
    ExchangeDataError,
    PaginationError,
    RawDataWriteError,
)
from trading_assistant.market_data.exchange import CCXTMarketDataSource
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    is_timeframe_aligned,
    latest_closed_candle_open_time,
    milliseconds_to_datetime,
    require_utc_datetime,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import (
    CandleQueryResult,
    MarketDataUpdateResult,
)
from trading_assistant.market_data.validation import (
    CandleValidationError,
    parse_timestamp_milliseconds,
    validate_ohlcv_rows,
)

logger = logging.getLogger(__name__)


class OHLCVSource(Protocol):
    """Minimal source interface implemented by CCXT and deterministic test doubles."""

    exchange_id: str

    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str,
        since_ms: int,
        limit: int,
    ) -> Any: ...


def _row_timestamp_ms(row: Any) -> int | None:
    if not isinstance(row, (list, tuple)) or not row:
        return None
    try:
        return parse_timestamp_milliseconds(row[0])
    except (ValueError, OverflowError):
        return None


def _row_count(page: Any) -> int:
    if isinstance(page, Sequence) and not isinstance(page, (str, bytes)):
        return len(page)
    return 0


class MarketDataService:
    """Fetch and retain closed OHLCV candles without rewriting historical records."""

    def __init__(
        self,
        engine: Engine,
        source: OHLCVSource,
        *,
        settings: Settings | None = None,
        raw_store: RawResponseStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings if settings is not None else get_settings()
        self.source = source
        self.repository = CandleRepository(engine)
        self.raw_store = raw_store if raw_store is not None else RawResponseStore(self.settings.raw_data_dir)
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def close(self) -> None:
        """Close the configured source when it offers a close method."""

        close = getattr(self.source, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def download_history(
        self,
        *,
        start_time: datetime,
        end_time: datetime | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        as_of: datetime | None = None,
    ) -> MarketDataUpdateResult:
        """Download a requested historical range; bounds are inclusive candle open times.

        ``start_time`` is mandatory for an initial download so history depth is
        explicit. The end is capped at the latest fully closed candle. Network or
        validation failures leave database contents unchanged; raw pages already
        fetched remain archived for inspection.
        """

        instrument = self.settings.symbol if symbol is None else symbol
        interval = self.settings.default_timeframe if timeframe is None else timeframe
        self._validate_instrument_and_timeframe(instrument, interval)
        interval_ms = timeframe_to_milliseconds(interval)
        start_utc = require_utc_datetime(start_time, field_name="start_time")
        if start_utc.microsecond:
            raise ValueError("start_time must have whole-second precision")
        requested_start_ms = datetime_to_milliseconds(start_utc, field_name="start_time")
        if not is_timeframe_aligned(requested_start_ms, interval):
            raise ValueError("start_time must align to the requested timeframe")

        requested_end = (
            require_utc_datetime(end_time, field_name="end_time") if end_time is not None else None
        )
        if requested_end is not None and requested_end.microsecond:
            raise ValueError("end_time must have whole-second precision")
        requested_end_ms = (
            datetime_to_milliseconds(requested_end, field_name="end_time")
            if requested_end is not None
            else None
        )
        if requested_end_ms is not None and not is_timeframe_aligned(requested_end_ms, interval):
            raise ValueError("end_time must align to the requested timeframe")
        if requested_end_ms is not None and requested_start_ms > requested_end_ms:
            raise ValueError("start_time must not be after end_time")

        as_of_utc = require_utc_datetime(as_of if as_of is not None else self._clock(), field_name="as_of")
        latest_closed = latest_closed_candle_open_time(as_of_utc, interval)
        latest_closed_ms = datetime_to_milliseconds(latest_closed)
        effective_end_ms = min(requested_end_ms, latest_closed_ms) if requested_end_ms is not None else latest_closed_ms

        if requested_start_ms > effective_end_ms:
            return MarketDataUpdateResult(
                exchange=self.source.exchange_id,
                symbol=instrument,
                timeframe=interval,
                range_start=start_utc,
                range_end=latest_closed,
                received_count=0,
                accepted_count=0,
                rejected_count=0,
                inserted_count=0,
                already_present_count=0,
                excluded_open_count=0,
                gaps=(),
                raw_files=(),
            )

        effective_end = milliseconds_to_datetime(effective_end_ms)
        logger.info(
            "Market-data download started",
            extra={
                "fields": {
                    "exchange": self.source.exchange_id,
                    "symbol": instrument,
                    "timeframe": interval,
                    "start_time_utc": start_utc.isoformat(),
                    "end_time_utc": effective_end.isoformat(),
                }
            },
        )

        raw_pages: list[Any] = []
        raw_files: list[Path] = []
        received_count = 0
        cursor_ms = requested_start_ms
        pages_fetched = 0
        last_http_response: Any = None
        # Apply a known source cap before each request; legacy/test sources that
        # do not advertise one retain the configured page size.
        page_limit = self.settings.market_data_page_limit
        source_max_limit = getattr(self.source, "max_ohlcv_limit", None)
        if source_max_limit is not None:
            page_limit = min(page_limit, source_max_limit)

        while cursor_ms <= effective_end_ms and pages_fetched < self.settings.market_data_max_pages:
            page_index = pages_fetched
            try:
                page = self.source.fetch_ohlcv(
                    instrument,
                    timeframe=interval,
                    since_ms=cursor_ms,
                    limit=page_limit,
                )
            except Exception as exc:
                logger.error(
                    "Market-data exchange request failed",
                    extra={
                        "fields": {
                            "exchange": self.source.exchange_id,
                            "symbol": instrument,
                            "timeframe": interval,
                            "since_ms": cursor_ms,
                            "error_type": type(exc).__name__,
                        }
                    },
                )
                raise ExchangeDataError(
                    f"OHLCV fetch failed for {self.source.exchange_id} {instrument} {interval} "
                    f"at {cursor_ms}ms (underlying error type: {type(exc).__name__})"
                ) from exc

            pages_fetched += 1
            page_count = _row_count(page)
            received_count += page_count
            last_http_response = getattr(self.source, "last_http_response", None)
            try:
                raw_path = self.raw_store.write_page(
                    exchange=self.source.exchange_id,
                    symbol=instrument,
                    timeframe=interval,
                    since_ms=cursor_ms,
                    limit=page_limit,
                    response=page,
                    http_response=last_http_response,
                    retrieved_at=require_utc_datetime(self._clock(), field_name="clock"),
                )
            except RawDataWriteError as exc:
                logger.error(
                    "Market-data raw response could not be preserved",
                    extra={
                        "fields": {
                            "exchange": self.source.exchange_id,
                            "symbol": instrument,
                            "timeframe": interval,
                            "error_type": type(exc).__name__,
                        }
                    },
                )
                raise
            raw_files.append(raw_path)
            logger.info(
                "Market-data response page received",
                extra={
                    "fields": {
                        "exchange": self.source.exchange_id,
                        "symbol": instrument,
                        "timeframe": interval,
                        "page": page_index,
                        "since_ms": cursor_ms,
                        "received_count": page_count,
                        "raw_file": str(raw_path),
                    }
                },
            )
            raw_pages.append(page)

            # Kraken's public OHLC endpoint is a rolling window: it returns at
            # most the latest 720 candles, regardless of how old ``since`` is.
            # It is not a normal date-range endpoint, so asking for another
            # page with a locally-derived timestamp can repeat the same window
            # rather than advance through history.  Keep the returned page for
            # validation/storage, where any unavailable older range remains an
            # explicit gap, but never fabricate pagination for Kraken.
            if getattr(self.source, "ohlcv_is_rolling_window", False):
                break

            if not isinstance(page, Sequence) or isinstance(page, (str, bytes)):
                break
            if not page:
                break
            page_timestamps = [timestamp for row in page if (timestamp := _row_timestamp_ms(row)) is not None]
            if not page_timestamps:
                break
            last_page_timestamp = max(page_timestamps)
            if last_page_timestamp < cursor_ms:
                logger.error(
                    "Market-data pagination returned no progress",
                    extra={
                        "fields": {
                            "exchange": self.source.exchange_id,
                            "symbol": instrument,
                            "timeframe": interval,
                            "since_ms": cursor_ms,
                        }
                    },
                )
                raise PaginationError(
                    f"{self.source.exchange_id} returned no page progress for "
                    f"{instrument} {interval} at {cursor_ms}ms"
                )
            if last_page_timestamp >= effective_end_ms:
                break
            next_cursor_ms = last_page_timestamp + interval_ms
            if next_cursor_ms <= cursor_ms:
                logger.error(
                    "Market-data pagination cursor did not advance",
                    extra={
                        "fields": {
                            "exchange": self.source.exchange_id,
                            "symbol": instrument,
                            "timeframe": interval,
                            "since_ms": cursor_ms,
                        }
                    },
                )
                raise PaginationError(
                    f"Pagination did not advance for {self.source.exchange_id} "
                    f"{instrument} {interval} at {cursor_ms}ms"
                )
            cursor_ms = next_cursor_ms

        if cursor_ms <= effective_end_ms and pages_fetched >= self.settings.market_data_max_pages:
            logger.error(
                "Market-data pagination page limit reached",
                extra={
                    "fields": {
                        "exchange": self.source.exchange_id,
                        "symbol": instrument,
                        "timeframe": interval,
                        "page_limit": self.settings.market_data_max_pages,
                        "next_since_ms": cursor_ms,
                    }
                },
            )
            raise PaginationError(
                f"Pagination page limit ({self.settings.market_data_max_pages}) reached before "
                f"{self.source.exchange_id} {instrument} {interval} range completed"
            )

        rows: list[Any] = []
        for page in raw_pages:
            if isinstance(page, Sequence) and not isinstance(page, (str, bytes)):
                rows.extend(page)
            else:
                rows.append(page)

        closed_through = latest_closed
        report = validate_ohlcv_rows(
            rows,
            exchange=self.source.exchange_id,
            symbol=instrument,
            timeframe=interval,
            expected_start=start_utc,
            expected_end=effective_end,
            closed_through=closed_through,
        )
        logger.info(
            "Market-data validation completed",
            extra={
                "fields": {
                    "exchange": self.source.exchange_id,
                    "symbol": instrument,
                    "timeframe": interval,
                    "received_count": received_count,
                    "accepted_count": len(report.candles),
                    "rejected_count": report.rejected_count,
                    "excluded_open_count": report.excluded_open_count,
                    "gap_count": len(report.gaps),
                    "missing_candle_count": report.missing_candle_count,
                }
            },
        )
        if report.gaps:
            logger.warning(
                "Market-data range contains missing candles",
                extra={
                    "fields": {
                        "exchange": self.source.exchange_id,
                        "symbol": instrument,
                        "timeframe": interval,
                        "gap_count": len(report.gaps),
                        "missing_candle_count": report.missing_candle_count,
                    }
                },
            )
        if report.issues:
            logger.error(
                "Market-data validation rejected response rows",
                extra={
                    "fields": {
                        "exchange": self.source.exchange_id,
                        "symbol": instrument,
                        "timeframe": interval,
                        "issue_codes": sorted({issue.code for issue in report.issues}),
                        "rejected_count": report.rejected_count,
                    }
                },
            )
            raise CandleValidationError(report)

        try:
            inserted_count, already_present_count = self.repository.insert_unchanged_or_new(report.candles)
        except Exception as exc:
            logger.error(
                "Market-data database write failed",
                extra={
                    "fields": {
                        "exchange": self.source.exchange_id,
                        "symbol": instrument,
                        "timeframe": interval,
                        "error_type": type(exc).__name__,
                    }
                },
            )
            raise

        logger.info(
            "Market-data database write completed",
            extra={
                "fields": {
                    "exchange": self.source.exchange_id,
                    "symbol": instrument,
                    "timeframe": interval,
                    "inserted_count": inserted_count,
                    "already_present_count": already_present_count,
                }
            },
        )
        logger.info(
            "Market-data download completed",
            extra={
                "fields": {
                    "exchange": self.source.exchange_id,
                    "symbol": instrument,
                    "timeframe": interval,
                    "complete": report.complete,
                    "received_count": received_count,
                    "accepted_count": len(report.candles),
                    "rejected_count": report.rejected_count,
                }
            },
        )
        return MarketDataUpdateResult(
            exchange=self.source.exchange_id,
            symbol=instrument,
            timeframe=interval,
            range_start=start_utc,
            range_end=effective_end,
            received_count=received_count,
            accepted_count=len(report.candles),
            rejected_count=report.rejected_count,
            inserted_count=inserted_count,
            already_present_count=already_present_count,
            excluded_open_count=report.excluded_open_count,
            gaps=report.gaps,
            raw_files=tuple(raw_files),
        )

    def update_history(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        as_of: datetime | None = None,
    ) -> MarketDataUpdateResult:
        """Download initial history from an explicit start, or continue after the latest row."""

        instrument = self.settings.symbol if symbol is None else symbol
        interval = self.settings.default_timeframe if timeframe is None else timeframe
        self._validate_instrument_and_timeframe(instrument, interval)
        if start_time is not None:
            return self.download_history(
                start_time=start_time,
                end_time=end_time,
                symbol=instrument,
                timeframe=interval,
                as_of=as_of,
            )

        latest = self.repository.latest_timestamp(
            exchange=self.source.exchange_id,
            symbol=instrument,
            timeframe=interval,
        )
        if latest is None:
            raise ValueError("no stored history exists; provide start_time for the initial download")

        interval_ms = timeframe_to_milliseconds(interval)
        next_start_ms = datetime_to_milliseconds(latest) + interval_ms
        now = require_utc_datetime(as_of if as_of is not None else self._clock(), field_name="as_of")
        latest_closed = latest_closed_candle_open_time(now, interval)
        if next_start_ms > datetime_to_milliseconds(latest_closed):
            return MarketDataUpdateResult(
                exchange=self.source.exchange_id,
                symbol=instrument,
                timeframe=interval,
                range_start=milliseconds_to_datetime(next_start_ms),
                range_end=latest_closed,
                received_count=0,
                accepted_count=0,
                rejected_count=0,
                inserted_count=0,
                already_present_count=0,
                excluded_open_count=0,
                gaps=(),
                raw_files=(),
            )
        return self.download_history(
            start_time=milliseconds_to_datetime(next_start_ms),
            end_time=end_time,
            symbol=instrument,
            timeframe=interval,
            as_of=now,
        )

    def get_candles(
        self,
        *,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> CandleQueryResult:
        """Retrieve ordered candles and explicit gap information for inclusive bounds."""

        resolved_exchange = self.source.exchange_id if exchange is None else exchange
        resolved_symbol = self.settings.symbol if symbol is None else symbol
        resolved_timeframe = self.settings.default_timeframe if timeframe is None else timeframe
        try:
            result = self.repository.get_candles(
                exchange=resolved_exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                start_time=start_time,
                end_time=end_time,
            )
        except CandleValidationError as exc:
            logger.error(
                "Stored market-data validation failed",
                extra={
                    "fields": {
                        "exchange": resolved_exchange,
                        "symbol": resolved_symbol,
                        "timeframe": resolved_timeframe,
                        "issue_codes": sorted({issue.code for issue in exc.report.issues}),
                        "rejected_count": exc.report.rejected_count,
                        "gap_count": len(exc.report.gaps),
                        "missing_candle_count": exc.report.missing_candle_count,
                    }
                },
            )
            raise

        if result.gaps:
            logger.warning(
                "Stored market-data range contains missing candles",
                extra={
                    "fields": {
                        "exchange": resolved_exchange,
                        "symbol": resolved_symbol,
                        "timeframe": resolved_timeframe,
                        "gap_count": len(result.gaps),
                        "missing_candle_count": result.missing_candle_count,
                    }
                },
            )
        return result

    def _validate_instrument_and_timeframe(self, symbol: str, timeframe: str) -> None:
        if not symbol.strip():
            raise ValueError("symbol must not be empty")
        if timeframe not in self.settings.supported_timeframes:
            raise ValueError(
                f"timeframe {timeframe!r} is not in configured supported_timeframes"
            )
        timeframe_to_milliseconds(timeframe)


def create_market_data_service(
    engine: Engine,
    *,
    settings: Settings | None = None,
    source: OHLCVSource | None = None,
    raw_store: RawResponseStore | None = None,
    clock: Callable[[], datetime] | None = None,
) -> MarketDataService:
    """Construct the configured CCXT-backed service without opening a network connection."""

    resolved_settings = settings if settings is not None else get_settings()
    resolved_source = source if source is not None else CCXTMarketDataSource(resolved_settings.exchange)
    return MarketDataService(
        engine,
        resolved_source,
        settings=resolved_settings,
        raw_store=raw_store,
        clock=clock,
    )
