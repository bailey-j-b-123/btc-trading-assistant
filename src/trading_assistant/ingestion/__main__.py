"""Command-line entry point for the Binance Spot market-data ingestion process.

Examples (run from the repository root, with the virtualenv active)::

    # one refresh of every required timeframe, then print coverage
    python -m trading_assistant.ingestion run --once

    # keep refreshing every N seconds (the ONLY ingestion process to run)
    python -m trading_assistant.ingestion run --interval-seconds 60

    # coverage report only (no network)
    python -m trading_assistant.ingestion status

Public market data only: no API key, no account, no order endpoint.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from trading_assistant.config import get_settings
from trading_assistant.database import create_database_engine
from trading_assistant.forward_testing.parameters import RunnerSettings
from trading_assistant.ingestion.lock import IngestionLockError, SingleInstanceLock
from trading_assistant.ingestion.service import coverage_for, ingest_once
from trading_assistant.logging_config import configure_logging
from trading_assistant.market_data.service import create_market_data_service
from trading_assistant.multi_timeframe.hierarchy import default_hierarchy
from trading_assistant.multi_timeframe.service import MultiTimeframeService

logger = logging.getLogger(__name__)

DEFAULT_LOCK_FILE = Path("data") / "ingestion.lock"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m trading_assistant.ingestion")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="refresh every required timeframe")
    run.add_argument(
        "--lock-file",
        default=str(DEFAULT_LOCK_FILE),
        help="single-instance lock file (default: data/ingestion.lock)",
    )
    run.add_argument("--once", action="store_true", help="one pass then exit")
    run.add_argument(
        "--interval-seconds",
        type=float,
        default=60.0,
        help="pause between passes when running continuously (default 60)",
    )
    run.add_argument(
        "--max-passes",
        type=int,
        default=None,
        help="stop after this many passes (testing / bounded runs)",
    )
    subparsers.add_parser("status", help="print per-timeframe coverage (no network)")
    return parser


def _service(settings) -> MultiTimeframeService:
    hierarchy = default_hierarchy()
    for timeframe in hierarchy.timeframes:
        if timeframe not in settings.supported_timeframes:
            raise SystemExit(
                f"hierarchy timeframe {timeframe!r} is not in supported_timeframes "
                f"{list(settings.supported_timeframes)}"
            )
    engine = create_database_engine(settings.database_url)
    market_data = create_market_data_service(engine, settings=settings)
    return MultiTimeframeService(
        engine,
        settings=settings,
        hierarchy=hierarchy,
        market_data_service=market_data,
        ledger_start=None,
        runner_settings=RunnerSettings(),
    )


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    settings = get_settings()
    service = _service(settings)
    symbol = settings.symbol
    if args.command == "status":
        as_of = datetime.now(UTC)
        coverage = [
            coverage_for(service, symbol=symbol, timeframe=timeframe, as_of=as_of)
            for timeframe in service.hierarchy.timeframes
        ]
        _print(
            {
                "exchange": settings.exchange,
                "symbol": symbol,
                "as_of": as_of.isoformat(),
                "all_complete": all(item.complete for item in coverage),
                "timeframes": [item.describe() for item in coverage],
            }
        )
        return 0

    passes = 0
    try:
        with SingleInstanceLock(args.lock_file):
            while True:
                as_of = datetime.now(UTC)
                report = ingest_once(service, symbol=symbol, as_of=as_of)
                _print(report.to_json_dict())
                passes += 1
                if args.once or (args.max_passes is not None and passes >= args.max_passes):
                    return 0 if report.error_count == 0 else 1
                time.sleep(max(args.interval_seconds, 1.0))
    except IngestionLockError as exc:
        print(f"ingestion not started: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
