"""Download real OHLCV history from the configured exchange into SQLite.

The dashboard only ever reads stored closed candles; it never fetches from
an exchange itself. Run this script first (it needs internet access) so the
dashboard has real data to display:

    alembic upgrade head
    python scripts/download_history.py --all-timeframes
    python -m trading_assistant.web

Examples:
    python scripts/download_history.py                    # 1h, last 180 days
    python scripts/download_history.py --timeframe 15m --days 30
    python scripts/download_history.py --all-timeframes --days 90
    python scripts/download_history.py --start 2024-01-01 --end 2024-06-01

Re-running is safe and idempotent: candles already stored are skipped, so the
same command both backfills history and tops up recent closes. Only fully
closed candles are ever stored; the still-open candle is always excluded.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import ccxt  # noqa: E402

from trading_assistant.config import Settings, get_settings  # noqa: E402
from trading_assistant.database import create_database_engine  # noqa: E402
from trading_assistant.logging_config import configure_logging  # noqa: E402
from trading_assistant.market_data.errors import MarketDataError  # noqa: E402
from trading_assistant.market_data.exchange import CCXTMarketDataSource  # noqa: E402
from trading_assistant.market_data.service import MarketDataService  # noqa: E402
from trading_assistant.market_data.timeframes import (  # noqa: E402
    datetime_to_milliseconds,
    milliseconds_to_datetime,
    timeframe_anchor_milliseconds,
    timeframe_to_milliseconds,
)

DEFAULT_DAYS = 180


@dataclass(frozen=True)
class DownloadRequest:
    timeframe: str
    start_time: datetime
    end_time: datetime | None


def floor_to_timeframe(instant: datetime, timeframe: str) -> datetime:
    """Floor an aware datetime down to its timeframe boundary (UTC)."""

    if instant.tzinfo is None:
        raise ValueError("instant must be a timezone-aware datetime")
    interval_ms = timeframe_to_milliseconds(timeframe)
    anchor_ms = timeframe_anchor_milliseconds(timeframe)
    instant_ms = datetime_to_milliseconds(instant)
    floored_ms = ((instant_ms - anchor_ms) // interval_ms) * interval_ms + anchor_ms
    return milliseconds_to_datetime(floored_ms)


def parse_day(value: str, *, field_name: str) -> datetime:
    """Parse a ``YYYY-MM-DD`` calendar day as UTC midnight."""

    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        raise ValueError(f"{field_name} must be YYYY-MM-DD, got {value!r}") from None
    return parsed


def build_download_plan(
    *,
    settings: Settings,
    timeframe: str | None,
    all_timeframes: bool,
    days: int,
    start: str | None,
    end: str | None,
    now: datetime,
) -> tuple[DownloadRequest, ...]:
    """Resolve CLI options into explicit per-timeframe download requests."""

    if all_timeframes and timeframe is not None:
        raise ValueError("--timeframe and --all-timeframes are mutually exclusive")
    if days <= 0:
        raise ValueError("--days must be a positive integer")
    if start is not None and days != DEFAULT_DAYS:
        raise ValueError("--start and --days are mutually exclusive")

    intervals = (
        tuple(settings.supported_timeframes)
        if all_timeframes
        else (timeframe or settings.default_timeframe,)
    )
    for interval in intervals:
        if interval not in settings.supported_timeframes:
            raise ValueError(
                f"timeframe {interval!r} is not in configured "
                f"supported_timeframes {list(settings.supported_timeframes)}"
            )

    end_time = parse_day(end, field_name="--end") if end is not None else None
    requests: list[DownloadRequest] = []
    for interval in intervals:
        if start is not None:
            start_time = parse_day(start, field_name="--start")
        else:
            start_time = floor_to_timeframe(now - timedelta(days=days), interval)
        # Day-midnight bounds are already aligned for day multiples, but floor
        # unconditionally so every request satisfies the service contract.
        start_time = floor_to_timeframe(start_time, interval)
        floored_end = floor_to_timeframe(end_time, interval) if end_time else None
        if floored_end is not None and start_time > floored_end:
            raise ValueError(
                f"start {start_time.isoformat()} is after end "
                f"{floored_end.isoformat()} for timeframe {interval!r}"
            )
        requests.append(
            DownloadRequest(
                timeframe=interval, start_time=start_time, end_time=floored_end
            )
        )
    return tuple(requests)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download real OHLCV history into the local database"
    )
    parser.add_argument(
        "--timeframe",
        default=None,
        help="single timeframe to download (default: configured default)",
    )
    parser.add_argument(
        "--all-timeframes",
        action="store_true",
        help="download every configured supported timeframe",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"how far back to start when --start is omitted (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="explicit start day, YYYY-MM-DD (UTC)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="explicit end day, YYYY-MM-DD (UTC); default: latest closed candle",
    )
    return parser.parse_args(argv)


def download_once(
    settings: Settings, request: DownloadRequest
) -> tuple[int, int, int, bool]:
    """Download one timeframe range; return (inserted, present, missing, ok)."""

    engine = create_database_engine(settings.database_url)
    source = CCXTMarketDataSource(settings.exchange)
    try:
        with MarketDataService(engine, source, settings=settings) as service:
            result = service.download_history(
                start_time=request.start_time,
                end_time=request.end_time,
                timeframe=request.timeframe,
            )
    finally:
        engine.dispose()
    return (
        result.inserted_count,
        result.already_present_count,
        result.missing_candle_count,
        result.complete,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()
    settings = get_settings()
    try:
        plan = build_download_plan(
            settings=settings,
            timeframe=args.timeframe,
            all_timeframes=args.all_timeframes,
            days=args.days,
            start=args.start,
            end=args.end,
            now=datetime.now(UTC),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"downloading {settings.exchange} {settings.symbol} "
        f"for {', '.join(r.timeframe for r in plan)}"
    )
    failed = False
    for request in plan:
        end_label = request.end_time.date().isoformat() if request.end_time else "latest close"
        print(
            f"[{request.timeframe}] {request.start_time.date().isoformat()} "
            f"-> {end_label} ...",
            flush=True,
        )
        try:
            inserted, present, missing, complete = download_once(settings, request)
        except MarketDataError as exc:
            # The service wraps the raw ccxt failure; recover the cause so a
            # plain connectivity outage gets a tailored hint, not a traceback.
            cause = exc.__cause__
            if isinstance(cause, ccxt.NetworkError):
                print(
                    f"[{request.timeframe}] network error reaching "
                    f"{settings.exchange}: {cause}. Check your internet "
                    "connection and try again; already-downloaded candles "
                    "are kept.",
                    file=sys.stderr,
                )
            else:
                print(f"[{request.timeframe}] download failed: {exc}", file=sys.stderr)
            failed = True
            continue
        except ValueError as exc:
            print(f"[{request.timeframe}] error: {exc}", file=sys.stderr)
            failed = True
            continue
        status = "complete" if complete else f"INCOMPLETE ({missing} missing)"
        print(
            f"[{request.timeframe}] done: inserted={inserted} "
            f"already_present={present} status={status}"
        )
    if failed:
        print("one or more downloads failed; see errors above.", file=sys.stderr)
        return 1
    print("done. Start the dashboard with: python -m trading_assistant.web")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
