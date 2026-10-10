"""Bounded, resumable historical backfill of Binance Spot candles.

This is a thin, explicit layer over ``MarketDataService.download_history``: it splits a
requested range into aligned chunks, skips chunks that are already fully stored, downloads
the rest one chunk at a time, and stops after a configurable number of chunks so a run is
bounded. Re-running the same command resumes where the previous run stopped.

Guarantees (all enforced here or by the existing download path):

* Real exchange candles only. Nothing is interpolated or synthesised; a missing candle is
  reported as a gap, never filled.
* Existing rows are never rewritten or deleted. Storage is idempotent: a re-download
  reports ``already_present`` rather than duplicating or altering a candle.
* Binance Spot only. A source that is not Binance is refused before any request is made,
  so legacy Kraken rows are never read or written by this path.
* The end of the range is capped at the newest closed candle as of ``as_of``. Forming
  candles are never stored as history.
* ``dry_run`` performs database reads only: no exchange request and no write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trading_assistant.market_data.service import MarketDataService
from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    is_timeframe_aligned,
    latest_closed_candle_open_time,
    milliseconds_to_datetime,
    require_utc_datetime,
    timeframe_anchor_milliseconds,
    timeframe_to_milliseconds,
)

REQUIRED_EXCHANGE = "binance"
DEFAULT_CHUNK_CANDLES = 1000


@dataclass(frozen=True, slots=True)
class BackfillChunk:
    """One inclusive range of candle open times, aligned to its timeframe."""

    timeframe: str
    start: datetime
    end: datetime
    expected_count: int


@dataclass(slots=True)
class TimeframeBackfill:
    timeframe: str
    requested_start: datetime
    requested_end: datetime
    chunks_planned: int = 0
    chunks_already_complete: int = 0
    chunks_downloaded: int = 0
    chunks_remaining: int = 0
    inserted: int = 0
    already_present: int = 0
    received: int = 0
    gaps: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    stopped_at_chunk_limit: bool = False
    earliest_stored_before: datetime | None = None
    earliest_stored_after: datetime | None = None
    latest_stored_after: datetime | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "chunks_planned": self.chunks_planned,
            "chunks_already_complete": self.chunks_already_complete,
            "chunks_downloaded": self.chunks_downloaded,
            "chunks_remaining": self.chunks_remaining,
            "inserted": self.inserted,
            "already_present": self.already_present,
            "received": self.received,
            "gaps": self.gaps,
            "error": self.error,
            "stopped_at_chunk_limit": self.stopped_at_chunk_limit,
            "earliest_stored_before": _iso(self.earliest_stored_before),
            "earliest_stored_after": _iso(self.earliest_stored_after),
            "latest_stored_after": _iso(self.latest_stored_after),
        }


@dataclass(slots=True)
class BackfillReport:
    exchange: str
    symbol: str
    dry_run: bool
    as_of: datetime
    timeframes: list[TimeframeBackfill] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.error is None for item in self.timeframes)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "dry_run": self.dry_run,
            "as_of": self.as_of.isoformat(),
            "ok": self.ok,
            "timeframes": [item.to_json_dict() for item in self.timeframes],
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def plan_chunks(
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    chunk_candles: int = DEFAULT_CHUNK_CANDLES,
) -> tuple[BackfillChunk, ...]:
    """Split an inclusive open-time range into contiguous aligned chunks.

    The chunks cover ``start..end`` exactly: no overlap, no gap, each at most
    ``chunk_candles`` candles long. Both bounds must be aligned to the timeframe.
    """

    if chunk_candles < 1:
        raise ValueError("chunk_candles must be at least 1")
    start_utc = require_utc_datetime(start, field_name="start")
    end_utc = require_utc_datetime(end, field_name="end")
    if start_utc.microsecond or end_utc.microsecond:
        raise ValueError("start and end must have whole-second precision")
    start_ms = datetime_to_milliseconds(start_utc, field_name="start")
    end_ms = datetime_to_milliseconds(end_utc, field_name="end")
    if not is_timeframe_aligned(start_ms, timeframe) or not is_timeframe_aligned(end_ms, timeframe):
        raise ValueError(f"start and end must align to the {timeframe} candle boundary")
    if start_ms > end_ms:
        raise ValueError("start must not be after end")
    interval_ms = timeframe_to_milliseconds(timeframe)
    chunks: list[BackfillChunk] = []
    cursor_ms = start_ms
    while cursor_ms <= end_ms:
        last_ms = min(end_ms, cursor_ms + (chunk_candles - 1) * interval_ms)
        count = (last_ms - cursor_ms) // interval_ms + 1
        chunks.append(
            BackfillChunk(
                timeframe=timeframe,
                start=milliseconds_to_datetime(cursor_ms),
                end=milliseconds_to_datetime(last_ms),
                expected_count=int(count),
            )
        )
        cursor_ms = last_ms + interval_ms
    return tuple(chunks)


def floor_to_timeframe(value: datetime, timeframe: str) -> datetime:
    """Floor a UTC instant to the timeframe's candle boundary (so 2025-10-01 is valid for 4h too)."""

    value_utc = require_utc_datetime(value, field_name="value")
    interval_ms = timeframe_to_milliseconds(timeframe)
    anchor_ms = timeframe_anchor_milliseconds(timeframe)
    ms = datetime_to_milliseconds(value_utc, field_name="value")
    return milliseconds_to_datetime(anchor_ms + ((ms - anchor_ms) // interval_ms) * interval_ms)


def _chunk_complete(service: MarketDataService, *, symbol: str, chunk: BackfillChunk) -> bool:
    """A chunk is complete when every expected open time is stored (database read only)."""

    stored = service.repository.get_candles(
        exchange=REQUIRED_EXCHANGE,
        symbol=symbol,
        timeframe=chunk.timeframe,
        start_time=chunk.start,
        end_time=chunk.end,
    )
    return len(stored.candles) == chunk.expected_count and not stored.gaps


def run_backfill(
    service: MarketDataService,
    *,
    symbol: str,
    timeframes: tuple[str, ...],
    start: datetime,
    as_of: datetime,
    end: datetime | None = None,
    chunk_candles: int = DEFAULT_CHUNK_CANDLES,
    max_chunks: int | None = None,
    dry_run: bool = False,
) -> BackfillReport:
    """Backfill ``timeframes`` for ``symbol`` from ``start`` up to the newest closed candle.

    ``max_chunks`` bounds the number of downloads in this run across all timeframes (None means
    unbounded). A failure stops only the affected timeframe; the error is reported and the next
    run resumes from the first incomplete chunk.
    """

    if service.source.exchange_id != REQUIRED_EXCHANGE:
        raise ValueError(
            f"backfill is Binance-only; the configured source is {service.source.exchange_id!r}"
        )
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    report = BackfillReport(
        exchange=REQUIRED_EXCHANGE, symbol=symbol, dry_run=dry_run, as_of=as_of_utc
    )
    budget = max_chunks
    for timeframe in timeframes:
        service._validate_instrument_and_timeframe(symbol, timeframe)  # noqa: SLF001 - same validation as downloads
        newest_closed = latest_closed_candle_open_time(as_of_utc, timeframe)
        floored_end = floor_to_timeframe(end, timeframe) if end is not None else None
        requested_end = min(floored_end, newest_closed) if floored_end is not None else newest_closed
        item = TimeframeBackfill(
            timeframe=timeframe,
            requested_start=floor_to_timeframe(start, timeframe),
            requested_end=requested_end,
        )
        item.earliest_stored_before = service.repository.earliest_timestamp(
            exchange=REQUIRED_EXCHANGE, symbol=symbol, timeframe=timeframe
        )
        if item.requested_start > requested_end:
            report.timeframes.append(item)
            continue
        chunks = plan_chunks(
            timeframe=timeframe,
            start=item.requested_start,
            end=requested_end,
            chunk_candles=chunk_candles,
        )
        item.chunks_planned = len(chunks)
        pending: list[BackfillChunk] = []
        for chunk in chunks:
            if _chunk_complete(service, symbol=symbol, chunk=chunk):
                item.chunks_already_complete += 1
            else:
                pending.append(chunk)
        item.chunks_remaining = len(pending)
        for chunk in pending:
            if dry_run:
                continue
            if budget is not None and budget <= 0:
                item.stopped_at_chunk_limit = True
                break
            try:
                update = service.download_history(
                    start_time=chunk.start,
                    end_time=chunk.end,
                    symbol=symbol,
                    timeframe=timeframe,
                    as_of=as_of_utc,
                )
            except Exception as exc:  # noqa: BLE001 - reported; the next run resumes
                item.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                break
            if budget is not None:
                budget -= 1
            item.chunks_downloaded += 1
            item.chunks_remaining -= 1
            item.inserted += update.inserted_count
            item.already_present += update.already_present_count
            item.received += update.received_count
            item.gaps.extend(
                {"start": gap.start.isoformat(), "end": gap.end.isoformat(), "missing": gap.missing_count}
                for gap in update.gaps
            )
        if not dry_run:
            item.earliest_stored_after = service.repository.earliest_timestamp(
                exchange=REQUIRED_EXCHANGE, symbol=symbol, timeframe=timeframe
            )
            item.latest_stored_after = service.repository.latest_timestamp(
                exchange=REQUIRED_EXCHANGE, symbol=symbol, timeframe=timeframe
            )
        report.timeframes.append(item)
    return report
