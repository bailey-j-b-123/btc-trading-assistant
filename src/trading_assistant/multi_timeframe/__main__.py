"""Command-line entry point for the Step 13 multi-timeframe hierarchy runner.

Examples::

    # one pass over closed 5M boundaries (safe, explicit, easy to inspect)
    python -m trading_assistant.multi_timeframe run --once

    # continuous closed-candle polling until Ctrl-C / SIGTERM
    python -m trading_assistant.multi_timeframe run

    # print recorded hierarchy state and per-timeframe market-data health
    python -m trading_assistant.multi_timeframe status

    # replay the hierarchy over a historical range (no lookahead; audit only)
    python -m trading_assistant.multi_timeframe replay --start 2024-01-01T00:00:00Z \
        --end 2024-01-02T00:00:00Z

This process only reads public market data and writes the append-only
hierarchy ledger. It cannot place an order, authenticate to an exchange, or
hold a balance. The hierarchy is decision support: PLANNABLE is not an order.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from trading_assistant.config import get_settings
from trading_assistant.database import create_database_engine
from trading_assistant.forward_testing.parameters import RunnerSettings
from trading_assistant.logging_config import configure_logging
from trading_assistant.market_data.service import create_market_data_service
from trading_assistant.multi_timeframe.hierarchy import default_hierarchy
from trading_assistant.multi_timeframe.replay import replay_hierarchy
from trading_assistant.multi_timeframe.runner import (
    MultiTimeframeRunner,
    run_single_pass,
)
from trading_assistant.multi_timeframe.service import MultiTimeframeService
from trading_assistant.web.schemas import parse_utc_iso


def _runner_settings(args: argparse.Namespace) -> RunnerSettings:
    defaults = RunnerSettings()
    return RunnerSettings(
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
    )


def _build_service(args: argparse.Namespace) -> MultiTimeframeService:
    settings = get_settings()
    hierarchy = default_hierarchy()
    for step in hierarchy.steps:
        if step.timeframe not in settings.supported_timeframes:
            raise SystemExit(
                f"hierarchy timeframe {step.timeframe!r} is not in configured "
                f"supported_timeframes {list(settings.supported_timeframes)}"
            )
    engine = create_database_engine(settings.database_url)
    ledger_start = (
        None
        if getattr(args, "start_at", None) is None
        else parse_utc_iso(args.start_at, field_name="start_at")
    )
    market_data = create_market_data_service(engine, settings=settings)
    return MultiTimeframeService(
        engine,
        settings=settings,
        hierarchy=hierarchy,
        market_data_service=market_data,
        ledger_start=ledger_start,
        runner_settings=_runner_settings(args),
    )


def _add_runner_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--interval-seconds", default=None)
    parser.add_argument("--fetch-max-attempts", type=int, default=None)
    parser.add_argument("--retry-backoff-seconds", default=None)
    parser.add_argument("--stop-after-errors", type=int, default=None)
    parser.add_argument("--bootstrap-candles", type=int, default=None)
    parser.add_argument("--start-at", default=None)
    parser.add_argument("--symbol", default=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trading_assistant.multi_timeframe",
        description=(
            "Evaluate the deterministic multi-timeframe hierarchy "
            "(4H context, 1H setup, 15M confirmation, 5M execution) over "
            "closed candles. Decision support only; never an order."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="poll for newly closed execution candles and evaluate"
    )
    run_parser.add_argument(
        "--once", action="store_true", help="run exactly one pass and exit"
    )
    run_parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="do not refresh public market data (stored candles only)",
    )
    _add_runner_arguments(run_parser)

    status_parser = subparsers.add_parser(
        "status", help="print recorded hierarchy state and data health"
    )
    status_parser.add_argument("--symbol", default=None)

    replay_parser = subparsers.add_parser(
        "replay", help="replay the hierarchy over a historical range"
    )
    replay_parser.add_argument("--start", required=True)
    replay_parser.add_argument("--end", required=True)
    replay_parser.add_argument("--symbol", default=None)
    replay_parser.add_argument(
        "--record",
        action="store_true",
        help="append each replayed evaluation to the immutable ledger",
    )
    _add_runner_arguments(replay_parser)

    args = parser.parse_args(argv)
    configure_logging()
    service = _build_service(args)
    try:
        if args.command == "status":
            payload = service.status(symbol=getattr(args, "symbol", None))
            print(json.dumps(payload, indent=2, default=str))
            return 0
        if args.command == "replay":
            start = parse_utc_iso(args.start, field_name="start")
            end = parse_utc_iso(args.end, field_name="end")
            snapshots = replay_hierarchy(
                service,
                symbol=getattr(args, "symbol", None),
                start=start,
                end=end,
                record=args.record,
            )
            for snapshot in snapshots:
                print(
                    json.dumps(
                        {
                            "decision_time": snapshot.decision_time.isoformat(),
                            "decision": snapshot.decision.value,
                            "alignment": snapshot.alignment.value,
                            "context": snapshot.context.regime.value,
                            "setup": snapshot.setup.setup_state,
                            "confirmation": snapshot.confirmation.state.value,
                            "execution": snapshot.execution.state.value,
                        },
                        default=str,
                    )
                )
            return 0
        # run
        refresh = not getattr(args, "no_refresh", False)
        if getattr(args, "once", False):
            result = run_single_pass(
                service,
                symbol=getattr(args, "symbol", None),
                refresh_market_data=refresh,
            )
            print(json.dumps(result.to_json_dict(), indent=2, default=str))
            return 0
        runner = MultiTimeframeRunner(service, runner_id="multi-timeframe")
        runner.install_signal_handlers()
        result = runner.run(
            symbol=getattr(args, "symbol", None),
            once=False,
            refresh_market_data=refresh,
        )
        if result is None:
            return 0
        print(json.dumps(result.to_json_dict(), indent=2, default=str))
        return 0
    finally:
        service.release_database_connections()
        market_data = getattr(service, "_market_data", None)
        if market_data is not None:
            market_data.close()


if __name__ == "__main__":
    sys.exit(main())
