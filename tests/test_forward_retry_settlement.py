"""A recovered early data gap must not keep an immutable close pending.

Use the real runner, market-data service and default decisions. Every source
candle and database is synthetic; observation hooks delegate to the real engine.
"""
from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import tempfile

import pytest
from sqlalchemy import event

from forward_endurance_harness import (
    InjectedInterruption, ReplaySource, assert_invariants, raw_rows,
)
from test_decision_invariance import EPOCH, synthetic_series
from web_fixtures import migrated_engine
from trading_assistant.config import Settings
from trading_assistant.database import create_database_engine
from trading_assistant.forward_testing import (
    ForwardParameters, ForwardRunner, ForwardTestService, RunnerSettings,
)
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.service import MarketDataService


INTERVAL = timedelta(hours=1)
SCOPE = {"exchange": "binance", "symbol": "BTC/USDT", "timeframe": "1h"}


def _run_schedule(name):
    series = synthetic_series(length=31, seed=7)
    with tempfile.TemporaryDirectory(prefix="forward-retry-settlement-") as directory:
        root = Path(directory)
        engine, url = migrated_engine(root, "replay.sqlite3")
        defaults = Settings.model_construct().model_dump(mode="python")
        defaults.update(database_url=url, raw_data_dir=root / "raw")
        settings = Settings(_env_file=None, **defaults)
        source = ReplaySource(series, duplicates=name == "combined")
        clock = {"now": EPOCH + INTERVAL + timedelta(seconds=30)}
        fault = {"sql_fired": False, "closed": 1}
        replay_calls = []
        trace = []
        frozen = None

        def after_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
            if name != "combined" or fault["sql_fired"]:
                return
            if statement.upper().startswith("INSERT INTO FORWARD_CYCLES") and fault["closed"] >= 12:
                fault["sql_fired"] = True
                raise InjectedInterruption("after actual cycle INSERT, before transaction commit")

        event.listen(engine, "after_cursor_execute", after_sql)

        def new_service():
            market = MarketDataService(
                engine, source, settings=settings,
                raw_store=RawResponseStore(root / "raw"), clock=lambda: clock["now"],
            )
            service = ForwardTestService(
                engine, settings=settings, clock=lambda: clock["now"],
                parameters=ForwardParameters(), runner_settings=RunnerSettings(),
                market_data_service=market, ledger_start=EPOCH + INTERVAL,
                sleep=lambda _delay: None,
            )
            real_build_frames = service.qualification.build_frames

            def observed_build_frames(*args, **kwargs):
                replay_calls.append({"start_at": kwargs.get("start_at"), "as_of": kwargs.get("as_of")})
                return real_build_frames(*args, **kwargs)

            service.qualification.build_frames = observed_build_frames
            return service

        service = new_service()
        steps = [closed for closed in range(1, 31) if not (name == "combined" and closed in (16, 17))]
        try:
            for closed in steps:
                if name == "combined" and closed == 11:
                    event.remove(engine, "after_cursor_execute", after_sql)
                    engine.dispose()
                    engine = create_database_engine(url)
                    event.listen(engine, "after_cursor_execute", after_sql)
                    service = new_service()
                boundary = EPOCH + closed * INTERVAL
                clock["now"] = boundary + timedelta(seconds=30)
                source.expose(closed)
                fault["closed"] = closed
                if name in {"outage_only", "combined"} and closed == 8:
                    source.offline_requests = 6
                for _attempt in range(10):
                    try:
                        result = ForwardRunner(service, runner_id=f"retry-settlement-{name}").run(
                            once=True, refresh_market_data=True,
                        )
                    except InjectedInterruption:
                        continue
                    if result.market_data_error is None:
                        break
                else:
                    raise AssertionError("controlled schedule did not recover")
                pending = service._pending_opens(
                    **SCOPE, latest_cycle=service.ledger.latest_cycle(**SCOPE),
                    target_boundary=boundary, interval=INTERVAL,
                )
                statuses_at_eight = sorted(
                    cycle.status.value for cycle in service.ledger.cycles(**SCOPE)
                    if cycle.as_of == EPOCH + 8 * INTERVAL
                )
                trace.append({
                    "closed": closed, "processed_boundaries": result.processed_boundaries,
                    "pending_boundaries": tuple(item + INTERVAL for item in pending),
                    "statuses_at_eight": statuses_at_eight,
                    "last_replay": None if not replay_calls else replay_calls[-1],
                    "first_stored_open": CandleRepository(engine).get_candles(**SCOPE).candles[0].timestamp,
                })
                if closed == 8:
                    frozen = raw_rows(engine)
            current = raw_rows(engine)
            assert frozen is not None
            for table, rows in frozen.items():
                for key, row in rows.items():
                    assert current[table].get(key) == row, "earlier audit or decision row changed"
            assert_invariants(engine, service, EPOCH + 30 * INTERVAL, expected_candles=series[:30])
            observations = service.ledger.observations(**SCOPE)
            cycles = {}
            for cycle in service.ledger.cycles(**SCOPE):
                if not cycle.complete and cycle.status.value != "INSUFFICIENT_HISTORY":
                    continue  # Retained failed-attempt audits are checked separately.
                assert cycle.as_of not in cycles, "duplicate settled conclusion"
                cycles[cycle.as_of] = {
                    "cycle_id": cycle.cycle_id, "snapshot_id": cycle.snapshot_id,
                    "explanation_id": cycle.explanation_id,
                    "observation_ids": sorted(
                        item.observation_id for item in observations if item.cycle_id == cycle.cycle_id
                    ),
                    "snapshot": None if cycle.snapshot_json is None else json.loads(cycle.snapshot_json),
                }
            assert len(cycles) == 30
            return cycles, trace, {"failed_fetches": source.failed_fetches, "sql_fired": fault["sql_fired"]}
        finally:
            event.remove(engine, "after_cursor_execute", after_sql)
            engine.dispose()


@pytest.mark.parametrize("schedule", ["outage_only", "combined"])
def test_recovered_early_gap_preserves_complete_snapshot_and_link_identities(schedule):
    baseline, _, _ = _run_schedule("baseline")
    recovered, _, coverage = _run_schedule(schedule)
    assert coverage["failed_fetches"] == 6
    assert coverage["sql_fired"] is (schedule == "combined")
    # Keep every snapshot fact, including old terminal candidates and all IDs.
    assert recovered == baseline


def test_missing_sibling_of_settled_insufficient_history_is_not_retried_forever():
    _, trace, coverage = _run_schedule("outage_only")
    at_eight = next(item for item in trace if item["closed"] == 8)
    at_nine = next(item for item in trace if item["closed"] == 9)
    at_twenty_five = next(item for item in trace if item["closed"] == 25)
    assert coverage["failed_fetches"] == 6
    assert at_eight["statuses_at_eight"] == ["INSUFFICIENT_HISTORY", "MISSING_CANDLE"]
    assert all(item["statuses_at_eight"] == at_eight["statuses_at_eight"] for item in trace if item["closed"] >= 8)
    assert all(EPOCH + 8 * INTERVAL not in item["pending_boundaries"] for item in trace if item["closed"] >= 8)
    assert at_nine["processed_boundaries"] == (EPOCH + 9 * INTERVAL,)
    assert at_twenty_five["processed_boundaries"] == (EPOCH + 25 * INTERVAL,)
    assert all(item["first_stored_open"] == EPOCH for item in trace)
    assert at_nine["last_replay"]["start_at"] > at_eight["last_replay"]["start_at"]
    assert at_twenty_five["last_replay"]["start_at"] > at_nine["last_replay"]["start_at"]
