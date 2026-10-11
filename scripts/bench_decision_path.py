"""Time one cold decision (frames + qualification) on SYNTHETIC history.

Usage (from the repository root, after `pip install -e ".[dev]"`):

    .venv/bin/python scripts/bench_decision_path.py 1000

Builds a deterministic synthetic 1h series of N closed candles in a temporary
SQLite database, then times ``DashboardService._evaluate`` at the newest boundary
in this process. Peak RSS is reported from ``getrusage``. Run one N per process
for a clean peak. Nothing here reads an exchange or writes to project data.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_decision_invariance import EPOCH, synthetic_series  # noqa: E402
from web_fixtures import insert_candles, make_settings, migrated_engine  # noqa: E402

from trading_assistant.web.dashboard_service import DashboardService  # noqa: E402
from trading_assistant.web.state import AppState  # noqa: E402


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("candles", type=int, help="number of closed 1h candles (SYNTHETIC)")
    args = parser.parse_args()
    if args.candles < 2:
        raise SystemExit("candles must be at least 2")

    with tempfile.TemporaryDirectory() as tmp:
        engine, url = migrated_engine(Path(tmp), name="bench.sqlite3")
        insert_candles(engine, synthetic_series(args.candles))
        state = AppState(
            engine,
            settings=make_settings(url),
            clock=lambda: EPOCH + timedelta(hours=args.candles),
        )
        service = DashboardService(state)
        as_of = EPOCH + timedelta(hours=args.candles)
        rss_before = _peak_rss_mb()
        started = time.perf_counter()
        snapshot, _frame, frames = service._evaluate(
            exchange="binance", symbol="BTC/USDT", timeframe="1h", as_of=as_of
        )
        elapsed = time.perf_counter() - started
        print(
            json.dumps(
                {
                    "candles": args.candles,
                    "seconds": round(elapsed, 2),
                    "frames": len(frames),
                    "setups": len(snapshot.setups),
                    "rss_before_mb": round(rss_before, 1),
                    "peak_rss_mb": round(_peak_rss_mb(), 1),
                    "data": "SYNTHETIC",
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
