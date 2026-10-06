"""Command-line entry point for the Step 12 forward tester.

Examples::

    # one pass over closed candles (safe, explicit, easy to inspect)
    python -m trading_assistant.forward_testing run --once

    # continuous closed-candle polling until Ctrl-C / SIGTERM
    python -m trading_assistant.forward_testing run

    # print market-data health, runner status and recorded sample sizes
    python -m trading_assistant.forward_testing status

This process only reads public market data and writes the append-only forward
ledger. It cannot place an order, authenticate to an exchange, or hold a balance.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from trading_assistant.config import get_settings
from trading_assistant.database import create_database_engine
from trading_assistant.forward_testing.parameters import (
    ForwardParameters,
    RunnerSettings,
)
from trading_assistant.forward_testing.runner import (
    ForwardRunner,
    format_status,
    run_single_pass,
)
from trading_assistant.forward_testing.service import ForwardTestService
from trading_assistant.historical_validation.parameters import FrictionAssumptions
from trading_assistant.logging_config import configure_logging
from trading_assistant.market_data.service import create_market_data_service
from trading_assistant.web.schemas import parse_utc_iso


def _parameters(args: argparse.Namespace) -> ForwardParameters:
    friction = FrictionAssumptions(
        fee_bps=Decimal(str(args.fee_bps)),
        entry_slippage_bps=Decimal(str(args.entry_slippage_bps)),
        exit_slippage_bps=Decimal(str(args.exit_slippage_bps)),
    )
    return ForwardParameters(
        observation_horizon_candles=args.horizon_candles,
        minimum_sample_size=args.minimum_sample_size,
        minimum_history_candles=args.minimum_history_candles,
        max_catch_up_candles=args.max_catch_up_candles,
        friction=friction,
    )


def _build_service(args: argparse.Namespace) -> ForwardTestService:
    settings = get_settings()
    if args.timeframe is not None and args.timeframe not in settings.supported_timeframes:
        raise SystemExit(
            f"timeframe {args.timeframe!r} is not in configured supported_timeframes "
            f"{list(settings.supported_timeframes)}"
        )
    engine = create_database_engine(settings.database_url)
    ledger_start = (
        None
        if args.start_at is None
        else parse_utc_iso(args.start_at, field_name="start_at")
    )
    backfill_start = (
        None
        if args.backfill_start is None
        else parse_utc_iso(args.backfill_start, field_name="backfill_start")
    )
    # ``status`` reuses this builder but does not define the runner-loop flags
    # (they belong to ``run``), so fall back to the documented defaults.
    defaults = RunnerSettings()
    return ForwardTestService(
        engine,
        settings=settings,
        parameters=_parameters(args),
        runner_settings=RunnerSettings(
            interval_seconds=Decimal(
                str(getattr(args, "interval_seconds", defaults.interval_seconds))
            ),
            fetch_max_attempts=getattr(
                args, "fetch_max_attempts", defaults.fetch_max_attempts
            ),
            fetch_retry_backoff_seconds=Decimal(
                str(
                    getattr(
                        args, "retry_backoff_seconds", defaults.fetch_retry_backoff_seconds
                    )
                )
            ),
            stop_after_errors=getattr(
                args, "stop_after_errors", defaults.stop_after_errors
            ),
            bootstrap_candles=getattr(
                args, "bootstrap_candles", defaults.bootstrap_candles
            ),
        ),
        market_data_factory=lambda: create_market_data_service(engine, settings=settings),
        ledger_start=ledger_start,
        backfill_start=backfill_start,
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", default=None, help="instrument symbol override")
    parser.add_argument("--timeframe", default=None, help="base timeframe override")
    parser.add_argument(
        "--horizon-candles",
        type=int,
        default=20,
        help="closed candles a paper observation may look forward (evaluation only)",
    )
    parser.add_argument(
        "--minimum-sample-size",
        type=int,
        default=20,
        help="reporting floor for percentages and descriptive summaries",
    )
    parser.add_argument(
        "--minimum-history-candles",
        type=int,
        default=10,
        help="runner precondition: stored candles required before recording a close",
    )
    parser.add_argument(
        "--max-catch-up-candles",
        type=int,
        default=720,
        help="maximum missed closes one pass may record (remainder is reported)",
    )
    parser.add_argument("--fee-bps", default="0", help="hypothetical two-sided fee basis points")
    parser.add_argument(
        "--entry-slippage-bps", default="0", help="hypothetical adverse entry slippage"
    )
    parser.add_argument(
        "--exit-slippage-bps", default="0", help="hypothetical adverse exit slippage"
    )
    parser.add_argument(
        "--start-at",
        default=None,
        help=(
            "ISO-8601 UTC boundary at which forward recording begins; omit to start "
            "at the first boundary the runner sees"
        ),
    )
    parser.add_argument(
        "--backfill-start",
        default=None,
        help=(
            "ISO-8601 UTC instant at which the initial public OHLCV download starts "
            "when no stored history exists; an instant that is not a candle open is "
            "moved up to the next candle open (never earlier)"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trading_assistant.forward_testing",
        description=(
            "Step 12 forward tester: record deterministic Step 3-6 decisions at "
            "each closed candle, track paper observations, and compare live "
            "forward results with Step 11 historical validation. No orders, no "
            "accounts, no private exchange API."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run the closed-candle forward tester")
    _add_common(run)
    run.add_argument(
        "--once", action="store_true", help="process pending closed candles once and exit"
    )
    run.add_argument(
        "--no-refresh",
        action="store_true",
        help="do not fetch market data; process already-stored closed candles only",
    )
    run.add_argument(
        "--interval-seconds",
        type=int,
        default=60,
        help="how often to look for a newly closed candle",
    )
    run.add_argument("--fetch-max-attempts", type=int, default=3)
    run.add_argument("--retry-backoff-seconds", type=int, default=5)
    run.add_argument("--stop-after-errors", type=int, default=10)
    run.add_argument(
        "--bootstrap-candles",
        type=int,
        default=RunnerSettings().bootstrap_candles,
        help=(
            "newest closed candles the runner seeds itself with when no history is "
            "stored and no --backfill-start is given"
        ),
    )
    run.add_argument(
        "--max-passes",
        type=int,
        default=None,
        help="stop after this many passes (useful for supervised runs)",
    )

    status = subparsers.add_parser("status", help="print forward-testing status")
    _add_common(status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        service = _build_service(args)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        if args.command == "status":
            payload = service.status(symbol=args.symbol, timeframe=args.timeframe)
            print(format_status(payload))
            return 0
        if args.command == "run":
            if args.once:
                result = run_single_pass(
                    service,
                    symbol=args.symbol,
                    timeframe=args.timeframe,
                    refresh_market_data=not args.no_refresh,
                )
                print(result.detail)
                print(
                    "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE. "
                    "PAPER OBSERVATION — NO REAL ORDER."
                )
                return 0
            runner = ForwardRunner(service)
            runner.install_signal_handlers()
            runner.run(
                symbol=args.symbol,
                timeframe=args.timeframe,
                refresh_market_data=not args.no_refresh,
                max_passes=args.max_passes,
            )
            return 0
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        print("forward runner interrupted; recorded history is intact")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"forward tester failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
