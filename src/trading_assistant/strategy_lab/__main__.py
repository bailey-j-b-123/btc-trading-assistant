"""Commands run without ChatGPT/Codex/Arena. Separate public-data-only state."""

import argparse
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import signal
import json
import os
from .data import acquire, dataset
from .models import Execution, Strategy, canonical
from .runner import run_experiment
from .store import Store
from .campaign import run_campaign, ResearchPlan
from .paper import poll_paper, execution_from_json
from .overnight import overnight
from .report import report
from .validation import ValidationRules
from .brain import compare_brain


def load_plan(path):
    if path is None:
        return ResearchPlan()
    from pathlib import Path

    file = Path(path)
    if file.is_symlink() or file.name == ".env" or file.stat().st_size > 65536:
        raise ValueError("only bounded ordinary lab JSON configuration supported")
    raw = json.loads(file.read_text())
    if "execution" in raw:
        raw["execution"] = execution_from_json(raw["execution"])
    if "rules" in raw:
        values = raw["rules"]
        for name in ("max_drawdown", "maximum_ambiguity_fraction", "alpha"):
            if name in values:
                values[name] = Decimal(values[name])
        raw["rules"] = ValidationRules(**values)
    for name in ("development", "holdout"):
        if name in raw:
            raw[name] = tuple(raw[name])
    if "folds" in raw:
        raw["folds"] = tuple(tuple(p) for p in raw["folds"])
    return ResearchPlan(**raw)


def instant(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise ValueError("use explicit UTC timestamps")
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description="Isolated paper-only BRAIN Strategy Lab")
    p.add_argument("--root", default=".strategy-lab")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("acquire")
    a.add_argument("--months", nargs="+", required=True)
    a.add_argument("--timeframe", default="1h")
    sub.add_parser("status")
    sub.add_parser("report")
    brain = sub.add_parser("brain-compare")
    brain.add_argument("--start", required=True)
    brain.add_argument("--end", required=True)
    brain.add_argument("--max-boundaries", type=int, default=24)
    brain.add_argument("--max-seconds", type=int, default=60)
    brain.add_argument("--no-acquire", action="store_true")
    paper = sub.add_parser("paper")
    paper.add_argument("--verify-feed", choices=("1h", "4h"))
    night = sub.add_parser("overnight")
    night.add_argument("--minutes", type=float, default=480)
    night.add_argument("--max-jobs", type=int, default=2)
    night.add_argument("--burst-seconds", type=int, default=60)
    night.add_argument("--idle-seconds", type=int, default=60)
    night.add_argument("--duty-cycle", type=float, default=0.5)
    night.add_argument("--nice", type=int, choices=range(0, 20), default=10)
    night.add_argument("--config")
    camp = sub.add_parser("campaign")
    camp.add_argument("--max-jobs", type=int, default=4)
    camp.add_argument("--max-seconds", type=int, default=300)
    camp.add_argument("--no-acquire", action="store_true")
    camp.add_argument("--config")
    r = sub.add_parser("run")
    r.add_argument("--start", required=True)
    r.add_argument("--end", required=True)
    r.add_argument("--family", default="sma_cross")
    r.add_argument("--timeframe", default="1h")
    r.add_argument("--max-boundaries", type=int)
    r.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    r.add_argument("--slippage-bps", type=Decimal, default=Decimal("3"))
    args = p.parse_args(argv)
    store = Store(args.root)
    try:
        if args.command in {"status", "report"}:
            result = store.status() if args.command == "status" else report(store)
        else:
            stopping = False

            def request_stop(*_):
                nonlocal stopping
                stopping = True

            signal.signal(signal.SIGINT, request_stop)
            signal.signal(signal.SIGTERM, request_stop)
            with store.writer():
                store.heartbeat(args.command)
                if args.command == "acquire":
                    result = acquire(
                        store, args.months, args.timeframe, stop=lambda: stopping
                    )
                elif args.command == "campaign":
                    result = run_campaign(
                        store,
                        load_plan(args.config),
                        max_jobs=args.max_jobs,
                        max_seconds=args.max_seconds,
                        stop=lambda: stopping,
                        prepare=not args.no_acquire,
                    )
                elif args.command == "paper":
                    result = poll_paper(
                        store,
                        timeframes=[args.verify_feed] if args.verify_feed else None,
                    )
                elif args.command == "brain-compare":
                    result = compare_brain(
                        store,
                        start=instant(args.start),
                        end=instant(args.end),
                        max_boundaries=args.max_boundaries,
                        max_seconds=args.max_seconds,
                        prepare=not args.no_acquire,
                        stop=lambda: stopping,
                    )
                elif args.command == "overnight":
                    os.nice(args.nice)
                    result = overnight(
                        store,
                        load_plan(args.config),
                        minutes=args.minutes,
                        max_jobs=args.max_jobs,
                        burst_seconds=args.burst_seconds,
                        idle_seconds=args.idle_seconds,
                        duty_cycle=args.duty_cycle,
                        stop=lambda: stopping,
                    )
                else:
                    c, m = dataset(
                        store, instant(args.start), instant(args.end), args.timeframe
                    )
                    result = run_experiment(
                        store,
                        c,
                        m,
                        Strategy(family=args.family, timeframe=args.timeframe),
                        replace(
                            Execution(),
                            fee_bps=args.fee_bps,
                            slippage_bps=args.slippage_bps,
                        ),
                        max_boundaries=args.max_boundaries,
                        stop=lambda: stopping,
                    )
                if (
                    not isinstance(result, dict)
                    or result.get("stage") != "blocked_error"
                ):
                    store.heartbeat("stopped" if stopping else "idle")
        print(canonical(result))
        return 0
    except Exception as exc:
        # An admission refusal must not write a competing worker's health.
        if "another lab worker" not in str(exc):
            with store.writer():
                store.error(args.command, exc)
                store.heartbeat("blocked_error", detail=str(exc)[:500])
        print(
            canonical(
                {
                    "error": type(exc).__name__,
                    "detail": str(exc),
                    "progress_preserved": True,
                }
            )
        )
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
