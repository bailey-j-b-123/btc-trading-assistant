"""Offline endurance support: real runner/decisions, synthetic public candles only.

This module is not collected as a test and never runs on import. All database
paths are created inside a fresh TemporaryDirectory. No exchange factory or
environment-selected database is used. Strategy and runner parameters retain
their production defaults.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import resource
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import event, select, text

from forward_fixtures import FakeExchange, ohlcv_row
from test_decision_invariance import EPOCH, synthetic_series
from web_fixtures import migrated_engine

from trading_assistant.config import Settings
from trading_assistant.database.engine import create_database_engine
from trading_assistant.forward_testing import ForwardParameters, ForwardRunner, ForwardTestService, RunnerSettings
from trading_assistant.forward_testing.models import paper_trade_occupies_slot
from trading_assistant.forward_testing.parameters import canonical_json
from trading_assistant.forward_testing.repository import _CYCLE_INFORMATIONAL_FIELDS
from trading_assistant.forward_testing.tables import ForwardCycleRow, ForwardObservationRow, ForwardPaperOutcomeRow, ForwardPaperPlanRow
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.service import MarketDataService
from trading_assistant.market_data.timeframes import datetime_to_milliseconds
import trading_assistant.market_structure.analysis as structure_analysis
import trading_assistant.pattern_liquidity.analysis as pattern_analysis


SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
INTERVAL = timedelta(hours=1)
TABLES = (ForwardCycleRow, ForwardObservationRow, ForwardPaperPlanRow, ForwardPaperOutcomeRow)


class InjectedInterruption(RuntimeError):
    """An offline fault after a real SQL statement, before its transaction commits."""


def _digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def cache_counts() -> dict[str, int]:
    return {"structure": len(structure_analysis._ANALYSIS_MEMO), "detectors": len(pattern_analysis._DETECTOR_MEMO)}


def own_peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1024)


def disk_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def sqlite_storage(engine, root: Path) -> dict:
    """Read logical allocation and actual scratch DB/WAL sizes, without writes."""
    with engine.connect() as connection:
        pages = int(connection.exec_driver_sql("PRAGMA page_count").scalar())
        page_size = int(connection.exec_driver_sql("PRAGMA page_size").scalar())
        free_pages = int(connection.exec_driver_sql("PRAGMA freelist_count").scalar())
        journal_mode = str(connection.exec_driver_sql("PRAGMA journal_mode").scalar())
    files = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = root / ("endurance.sqlite3" + suffix)
        files["database" if not suffix else suffix[1:]] = path.stat().st_size if path.exists() else 0
    return {"page_count": pages, "page_size": page_size, "freelist_pages": free_pages,
            "logical_allocated_bytes": pages * page_size,
            "logical_used_bytes": (pages - free_pages) * page_size,
            "journal_mode": journal_mode, "file_bytes": files,
            "file_total_bytes": sum(files.values())}


def raw_rows(engine) -> dict[str, dict[str, dict]]:
    result = {}
    with engine.connect() as connection:
        for model in TABLES:
            primary = next(iter(model.__table__.primary_key.columns)).name
            rows = connection.execute(select(model.__table__)).mappings()
            result[model.__tablename__] = {row[primary]: dict(row) for row in rows}
    return result


def _frozen_hashes(engine):
    return {table: {key: _digest(row) for key, row in rows.items()} for table, rows in raw_rows(engine).items()}


def _assert_frozen(engine, frozen):
    current = _frozen_hashes(engine)
    for table, rows in frozen.items():
        for key, digest in rows.items():
            assert current[table].get(key) == digest, f"stored row changed or disappeared: {table}/{key}"


def canonical_state(engine, service) -> dict:
    """Hash decision identity fields, with a precise operational-field exclusion.

    Outcome version chains are checked independently. Latest outcome payloads
    remain exact; no decision, level, policy, fingerprint or outcome is removed.
    """
    rows = raw_rows(engine)
    ignored = {
        "forward_cycles": _CYCLE_INFORMATIONAL_FIELDS,
        "forward_observations": {"recorded_at", "data_health"},
        "forward_paper_plans": {"recorded_at", "data_health"},
    }
    hashes = {}
    for table, fields in ignored.items():
        selected = rows[table]
        if table == "forward_cycles":
            # Failed attempts are preserved, but compare the finally established
            # conclusion at each close, not its earlier unresolved placeholder.
            selected = {key: row for key, row in selected.items() if row["status"] in {"COMPLETE", "INSUFFICIENT_HISTORY"}}
            closes = [row["as_of"] for row in selected.values()]
            assert len(closes) == len(set(closes)), "more than one determinate conclusion at a close"
        material = {key: {name: value for name, value in row.items() if name not in fields} for key, row in selected.items()}
        hashes[table] = _digest(material)
    latest = service.ledger.latest_outcomes(exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME)
    hashes["latest_outcome_payloads"] = _digest({item.paper_plan_id: item.payload_json for item in latest})
    full_chains = {key: {name: value for name, value in row.items() if name != "recorded_at"} for key, row in rows["forward_paper_outcomes"].items()}
    hashes["full_outcome_chains"] = _digest(full_chains)
    chain_material = {}
    for row in rows["forward_paper_outcomes"].values():
        material = {name: value for name, value in row.items() if name not in {"outcome_id", "supersedes_outcome_id", "recorded_at"}}
        # Keep observation links and every projected field/rule/config value.
        # Only assigned chain IDs and write time are excluded from audit-only
        # classification; the full-chain hash above still retains all links.
        chain_material.setdefault(row["paper_plan_id"], []).append(json.loads(canonical_json(material)))
    for items in chain_material.values():
        items.sort(key=lambda row: row["sequence"])
    unresolved = [{"cycle_id": row["cycle_id"], "as_of": str(row["as_of"]), "status": row["status"]} for row in rows["forward_cycles"].values() if row["status"] not in {"COMPLETE", "INSUFFICIENT_HISTORY"}]
    return {"hashes": hashes, "row_counts": {table: len(items) for table, items in rows.items()}, "resolved_closes": len(rows["forward_cycles"]) - len(unresolved), "unresolved_audit_versions": unresolved, "outcome_chain_material": chain_material, "cycle_selection": "one determinate COMPLETE or immutable INSUFFICIENT_HISTORY conclusion per close; unfinished siblings retained and reported", "excluded_fields": {table: sorted(fields) for table, fields in ignored.items()}}


def assert_invariants(engine, service, boundary, expected_candles=None) -> dict:
    rows = raw_rows(engine)
    cycles, observations, plans, outcomes = (rows[model.__tablename__] for model in TABLES)
    for cycle_id, cycle in cycles.items():
        linked = [item for item in observations.values() if item["cycle_id"] == cycle_id]
        assert len(linked) == cycle["observation_count"], "cycle lost candidate rows"
        # This column advertises linked paper observations, including reused plans,
        # not the number of newly created plan rows at this boundary.
        assert sum(item["paper_plan_id"] is not None for item in linked) == cycle["paper_plan_count"]
    for item in plans.values():
        observation = observations[item["observation_id"]]
        assert observation["paper_plan_id"] == item["paper_plan_id"]
        assert observation["cycle_id"] == item["cycle_id"]
    setup_keys = [(p["exchange"], p["symbol"], p["timeframe"], p["setup_id"]) for p in plans.values()]
    assert len(setup_keys) == len(set(setup_keys)), "duplicate frozen plan for a setup"
    by_plan = {}
    for item in outcomes.values():
        by_plan.setdefault(item["paper_plan_id"], []).append(item)
    for chain in by_plan.values():
        chain.sort(key=lambda item: item["sequence"])
        assert [item["sequence"] for item in chain] == list(range(1, len(chain) + 1))
        assert chain[0]["supersedes_outcome_id"] is None
        for previous, current in zip(chain, chain[1:]):
            assert current["supersedes_outcome_id"] == previous["outcome_id"]
    complete_closes = [item["as_of"] for item in cycles.values() if item["status"] == "COMPLETE"]
    assert len(complete_closes) == len(set(complete_closes)), "complete boundary was recorded twice"
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    ledger_plans = service.ledger.paper_plans(exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME)
    latest = {item.paper_plan_id: item for item in service.ledger.latest_outcomes(exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME)}
    occupied = sum(paper_trade_occupies_slot(latest.get(plan.paper_plan_id), plan, now=boundary) for plan in ledger_plans)
    assert occupied <= 1, "more than one paper plan occupies the instrument"
    if expected_candles is not None:
        stored = CandleRepository(engine).get_candles(exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME).candles
        assert stored == expected_candles, "stored prefix changed or contains a future/forming candle"
    return {"integrity": "ok", "foreign_key_errors": 0, "occupied_slots": occupied, "outcome_chains": len(by_plan)}


class ReplaySource(FakeExchange):
    """Available genesis history plus one forming bar; optional exact duplicates."""

    def __init__(self, series, *, duplicates=False):
        super().__init__()
        self.series = series
        self.available = 1
        self.duplicates = duplicates
        self.offline_requests = 0
        self.redispatched_closed_rows = 0
        self.failed_fetches = 0
        self.fetch_requests = 0

    def expose(self, closed_count):
        self.available = closed_count
        self.set_candles(self.series[: closed_count + 1])

    def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
        self.fetch_requests += 1
        if timeframe == TIMEFRAME and self.offline_requests:
            self.offline_requests -= 1
            self.failed_fetches += 1
            raise ConnectionError("offline endurance fault: temporary public-source outage")
        rows = super().fetch_ohlcv(symbol, timeframe=timeframe, since_ms=since_ms, limit=limit)
        if self.duplicates:
            older = [item for item in self.candles if item.timeframe == timeframe and item.symbol == symbol and datetime_to_milliseconds(item.timestamp) < since_ms]
            if older:
                # Repeat a previously delivered completed candle across pages.
                # Never make a malformed page with duplicate timestamps within it.
                self.redispatched_closed_rows += 1
                return [ohlcv_row(older[-1]), *rows][:limit]
        return rows


@dataclass
class Driver:
    total: int
    stress: bool
    source: ReplaySource
    progress: Callable[[dict], None]
    closed: int = 1
    clock: dict = field(default_factory=dict)
    timings: list = field(default_factory=list)
    waits: list = field(default_factory=list)
    faults: dict = field(default_factory=dict)
    last_error: Exception | None = None
    last_result: object | None = None
    restart: bool = False
    done: bool = False
    passes: int = 0
    forming_checks: int = 0
    peak_disk: int = 0
    service: object | None = None
    root: Path | None = None

    def set_closed(self, count):
        self.closed = count
        self.clock["now"] = EPOCH + count * INTERVAL + timedelta(seconds=30)
        self.source.expose(count)

    def observe(self, result, error, elapsed):
        self.passes += 1
        self.last_result, self.last_error = result, error
        self.timings.append(elapsed)
        latest = self.service.candles.latest_timestamp(exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME)
        assert latest is None or latest <= self.source.series[self.closed - 1].timestamp, "future/forming candle entered storage"
        self.forming_checks += 1
        if error is None and result.market_data_error is None:
            if self.closed % 25 == 0 or self.closed == self.total:
                assert_invariants(self.service.engine, self.service, EPOCH + self.closed * INTERVAL, self.source.series[: self.closed])
                self.peak_disk = max(self.peak_disk, disk_bytes(self.root))
                self.progress({"phase": "running", "case": "stress" if self.stress else "baseline", "closed_boundary": self.closed, "target": self.total, "passes": self.passes, "last_pass_seconds": elapsed, "peak_rss_bytes": own_peak_rss_bytes(), "scratch_disk_bytes": self.peak_disk})

    def wait(self, timeout):
        self.waits.append(float(timeout))
        if self.last_error is not None or self.last_result.market_data_error is not None:
            return False  # Retry at the identical decision clock; no missing-candle policy is hidden.
        if self.last_result.pending_boundaries:
            return False
        if self.closed >= self.total:
            self.done = True
            return True
        if self.stress:
            restart_at = max(2, self.total // 3)
            outage_at = max(2, self.total // 4)
            repeat_at = max(2, self.total // 5)
            jump_at = max(2, self.total // 2)
            if self.closed == restart_at and "restart" not in self.faults:
                self.faults["restart"] = self.closed
                self.restart = True
                return True
            if self.closed == outage_at and "outage" not in self.faults:
                self.faults["outage"] = self.closed
                self.source.offline_requests = 2 * self.service.runner_settings.fetch_max_attempts
                return False
            if self.closed == repeat_at and "repeat_poll" not in self.faults:
                self.faults["repeat_poll"] = self.closed
                return False
            if self.closed == jump_at and "downtime_catch_up" not in self.faults:
                jump = min(3, self.total - self.closed)
                self.faults["downtime_catch_up"] = {"from": self.closed, "to": self.closed + jump}
                self.set_closed(self.closed + jump)
                return False
        self.set_closed(self.closed + 1)
        return False


class SimulatedEvent:
    def __init__(self, driver):
        self.driver, self.stopped = driver, False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout=0):
        self.stopped = self.driver.wait(timeout)
        return self.stopped


class ObservedService(ForwardTestService):
    def __init__(self, *args, driver, **kwargs):
        super().__init__(*args, **kwargs)
        self.driver = driver

    def run_once(self, *args, **kwargs):
        began = time.perf_counter()
        result, error = None, None
        try:
            result = super().run_once(*args, **kwargs)
            return result
        except Exception as exc:
            error = exc
            raise
        finally:
            self.driver.observe(result, error, time.perf_counter() - began)


def run_case(case: str, boundaries: int, *, progress=lambda _: None) -> dict:
    if case not in {"baseline", "stress"} or boundaries < 1:
        raise ValueError("case must be baseline/stress and boundaries must be positive")
    series = synthetic_series(length=boundaries + 1, seed=7)
    started = time.perf_counter()
    cache_before = cache_counts()
    rss_before = own_peak_rss_bytes()
    with tempfile.TemporaryDirectory(prefix="brain-offline-endurance-") as directory:
        root = Path(directory)
        engine, url = migrated_engine(root, "endurance.sqlite3", sqlite_busy_timeout_ms=5000)
        sqlite_initial = sqlite_storage(engine, root)
        defaults = Settings.model_construct().model_dump(mode="python")
        defaults.update(database_url=url, raw_data_dir=root / "raw")
        settings = Settings(_env_file=None, **defaults)
        source = ReplaySource(series, duplicates=case == "stress")
        driver = Driver(boundaries, case == "stress", source, progress, root=root)
        driver.set_closed(1)

        def after_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
            upper = statement.lstrip().upper()
            if upper.startswith(("UPDATE ", "DELETE ")) and "FORWARD_" in upper:
                raise AssertionError("endurance run attempted to rewrite a forward row")
            if not driver.stress or not upper.startswith("INSERT INTO "):
                return
            if "INSERT INTO FORWARD_CYCLES" in upper and driver.closed >= max(3, boundaries * 2 // 5) and "cycle_sql_interruption" not in driver.faults:
                assert _connection.in_transaction()
                driver.faults["cycle_sql_interruption"] = {"boundary": driver.closed, "real_insert_executed": True, "transaction_open": True, "fault_type": "InjectedInterruption after SQL INSERT"}
                raise InjectedInterruption("after real cycle INSERT, before atomic bundle commit")
            if "INSERT INTO FORWARD_PAPER_OUTCOMES" in upper and driver.closed >= max(3, boundaries * 3 // 5) and "outcome_sql_interruption" not in driver.faults:
                assert _connection.in_transaction()
                driver.faults["outcome_sql_interruption"] = {"boundary": driver.closed, "real_insert_executed": True, "transaction_open": True, "fault_type": "InjectedInterruption after SQL INSERT"}
                raise InjectedInterruption("after real outcome INSERT, before its transaction commits")

        event.listen(engine, "after_cursor_execute", after_sql)
        frozen = {}
        sessions = 0
        try:
            while not driver.done:
                sessions += 1
                market = MarketDataService(engine, source, settings=settings, raw_store=RawResponseStore(root / "raw"), clock=lambda: driver.clock["now"])
                service = ObservedService(engine, driver=driver, settings=settings, clock=lambda: driver.clock["now"], parameters=ForwardParameters(), runner_settings=RunnerSettings(), market_data_service=market, ledger_start=EPOCH + INTERVAL, sleep=lambda delay: driver.waits.append(float(delay)))
                driver.service = service
                if frozen:
                    _assert_frozen(engine, frozen)
                runner = ForwardRunner(service, stop_event=SimulatedEvent(driver), runner_id=f"offline-{case}-{sessions}")
                runner.run(refresh_market_data=True, max_passes=boundaries * 8 + 100)
                if not driver.done and not driver.restart:
                    raise AssertionError("runner ended before completing the requested source")
                _assert_frozen(engine, frozen)
                frozen = _frozen_hashes(engine)
                if driver.restart and not driver.done:
                    # Reopen persisted SQLite through a fresh Engine, not only
                    # a new service borrowing the old connection pool.
                    event.remove(engine, "after_cursor_execute", after_sql)
                    engine.dispose()
                    engine = create_database_engine(url, sqlite_busy_timeout_ms=5000)
                    event.listen(engine, "after_cursor_execute", after_sql)
                driver.restart = False
            # Final same-boundary repeat: real service must be a ledger no-op.
            counts_before = service.ledger.counts(exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME)
            replay = service.run_once(refresh_market_data=True)
            assert replay.cycles_recorded == replay.observations_recorded == replay.paper_plans_created == replay.outcomes_recorded == 0
            assert service.ledger.counts(exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME) == counts_before
            _assert_frozen(engine, frozen)
            checks = assert_invariants(engine, service, EPOCH + boundaries * INTERVAL, series[:boundaries])
            canonical = canonical_state(engine, service)
            assert canonical["resolved_closes"] == boundaries, {"case": case, "expected": boundaries, "canonical": canonical}
            driver.peak_disk = max(driver.peak_disk, disk_bytes(root))
            sorted_times = sorted(driver.timings)
            coverage = {"restart_executed": sessions > 1, "failed_source_fetches": source.failed_fetches, "source_fetch_requests": source.fetch_requests, "duplicate_closed_deliveries": source.redispatched_closed_rows, "forming_exclusion_checked_passes": driver.forming_checks, "cycle_sql_interruption_exercised": "cycle_sql_interruption" in driver.faults, "outcome_sql_interruption_exercised": "outcome_sql_interruption" in driver.faults, "paper_plans_created": counts_before["paper_plans"], "outcome_versions_created": counts_before["outcome_versions"]}
            input_signature = {"candles": _digest(series[:boundaries]), "forward_parameters": service.parameters.fingerprint(), "planning_parameters": service.planning_parameters.fingerprint(), "qualification_parameters": _digest(service.qualification_parameters), "pattern_parameters": _digest(service.pattern_parameters), "structure_parameters": _digest(service.structure_parameters), "runner_parameters": _digest(service.runner_settings)}
            sqlite_final = sqlite_storage(engine, root)
            return {"case": case, "boundaries": boundaries, "passes": driver.passes, "runner_sessions": sessions, "faults_observed": driver.faults, "actual_coverage": coverage, "checks": checks, "canonical": canonical, "input_signature": input_signature, "row_counts": canonical["row_counts"], "latency_seconds": {"total_wall": time.perf_counter() - started, "pass_median": statistics.median(driver.timings), "pass_p95": sorted_times[math.ceil(.95 * len(sorted_times)) - 1], "pass_max": max(driver.timings)}, "process_lifetime_peak_rss_before_bytes": rss_before, "process_lifetime_peak_rss_after_bytes": own_peak_rss_bytes(), "scratch_disk_sampled_peak_bytes": driver.peak_disk, "sqlite_storage_initial": sqlite_initial, "sqlite_storage_final": sqlite_final, "sqlite_logical_growth_bytes": sqlite_final["logical_allocated_bytes"] - sqlite_initial["logical_allocated_bytes"], "sqlite_file_growth_bytes": sqlite_final["file_total_bytes"] - sqlite_initial["file_total_bytes"], "cache_before": cache_before, "cache_after": cache_counts(), "synthetic_wait_count": len(driver.waits), "strategy_parameters": "unchanged production defaults", "runner_parameters": "unchanged production defaults", "source": "existing seed-7 Decimal walk, full genesis prefix plus one excluded forming candle"}
        finally:
            event.remove(engine, "after_cursor_execute", after_sql)
            engine.dispose()


def _compare_cases(cases):
    comparison = None
    chain_classification = None
    checks_passed = True
    canonical_agreement = None
    if len(cases) == 2:
        left, right = (item["canonical"]["hashes"] for item in cases)
        comparison = {name: left[name] == right[name] for name in left}
        required = {name: matches for name, matches in comparison.items() if name != "full_outcome_chains"}
        checks_passed = all(required.values())
        canonical_agreement = all(comparison.values())
        if not comparison["full_outcome_chains"]:
            baseline = cases[0]["canonical"]["outcome_chain_material"]
            stress = cases[1]["canonical"]["outcome_chain_material"]
            def material(items, skip_incomplete=False):
                return [{key: value for key, value in row.items() if key != "sequence"} for row in items if not (skip_incomplete and row["status"] == "INCOMPLETE_DATA")]
            # Different assigned IDs alone do not prove there was a recovery
            # audit. Require actually present additional incomplete versions
            # before applying this narrowly described classification.
            baseline_incomplete = sum(row["status"] == "INCOMPLETE_DATA" for items in baseline.values() for row in items)
            stress_incomplete = sum(row["status"] == "INCOMPLETE_DATA" for items in stress.values() for row in items)
            audit_only = stress_incomplete > baseline_incomplete and set(baseline) == set(stress) and all(material(baseline[key]) == material(stress[key], True) for key in baseline)
            if audit_only:
                chain_classification = "full chain IDs differ; ordered non-INCOMPLETE_DATA links/projections/payload/config/rules material matches exactly after extra incomplete audit versions are removed for classification only; every raw version is retained"
            else:
                checks_passed = False
                chain_classification = "UNCLASSIFIED outcome-history difference beyond extra INCOMPLETE_DATA audit versions"
        else:
            chain_classification = "full ordered outcome chains match excluding only recorded_at"
    return {"checks_passed": checks_passed, "canonical_agreement": canonical_agreement, "canonical_comparison": comparison, "outcome_chain_classification": chain_classification}


def compare_reports(baseline_report, stress_report) -> dict:
    """Compare saved real-run reports without another replay or a database read."""
    reports = (baseline_report, stress_report)
    cases = []
    for report, expected in zip(reports, ("baseline", "stress")):
        if report.get("format") != "brain-offline-endurance-v1" or report.get("phase") != "complete":
            raise ValueError("comparison requires completed endurance JSON reports")
        selected = [item for item in report.get("cases", ()) if item.get("case") == expected]
        if len(selected) != 1 or selected[0].get("boundaries") != report.get("boundaries"):
            raise ValueError("report case/boundary identity is missing or inconsistent")
        cases.append(selected[0])
    left, right = cases
    for name in ("boundaries", "source", "strategy_parameters", "runner_parameters"):
        if name not in left or left[name] != right.get(name):
            raise ValueError(f"reports describe different or unknown {name}")
    exact_signature = "input_signature" in left and "input_signature" in right
    if exact_signature and left["input_signature"] != right["input_signature"]:
        raise ValueError("reports carry different candle/configuration fingerprints")
    comparison = _compare_cases(cases)
    return {"format": "brain-offline-endurance-comparison-v1", "phase": "complete", "boundaries": left["boundaries"], "cases": cases, **comparison, "exact_input_signature_verified": exact_signature, "input_validation": "exact candle/configuration fingerprints" if exact_signature else "LEGACY report: only equal seed/source/default-parameter declarations are available; original run provenance must be independently checked", "limitations": ["Saved-report comparison performs no replay and cannot certify missing legacy metadata", "Literal canonical differences remain failures; no terminal setup facts or assigned decision identities are dropped", "Every raw recovery audit version remains in its source report"]}


def run_endurance(boundaries: int = 1000, *, case="both", progress=lambda _: None) -> dict:
    selected = ("baseline", "stress") if case == "both" else (case,)
    cases = [run_case(name, boundaries, progress=progress) for name in selected]
    return {"format": "brain-offline-endurance-v1", "label": "SYNTHETIC PAPER VALIDATION — NOT PERFORMANCE", "boundaries": boundaries, "simulation_completed": True, "cases": cases, **_compare_cases(cases), "limitations": ["No real market, profitability, calibrated costs, private exchange, live database, orders or deployment", "Injected SQL exceptions verify rollback/retry; this does not prove process-kill or power-loss durability", "Source outages retry at the same closed boundary; unresolved audit cycles/outcomes are preserved and reported separately", "Downtime catch-up retains every candle; missing/delayed-candle scheduling with irreversible completed decisions remains a separate scenario", "Disk peak is sampled, RSS is the process-lifetime peak including imports/caches, and both cases in one invocation share memo caches"]}
