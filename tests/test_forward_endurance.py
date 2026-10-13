"""Reusable endurance checks use the real engine, offline fixtures and temporary SQL."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from forward_endurance_harness import run_endurance
from forward_fixtures import (
    EXCHANGE, INTERVAL, QUALIFYING_BOUNDARY, SYMBOL, TIMEFRAME,
    bar, labelled_series, make_harness,
)
from trading_assistant.forward_testing.models import paper_trade_occupies_slot
from trading_assistant.journaling.types import OutcomeStatus
from trading_assistant.market_data.repository import CandleRepository

SCOPE = {"exchange": EXCHANGE, "symbol": SYMBOL, "timeframe": TIMEFRAME}


def test_small_reusable_endurance_runs_the_actual_loop_and_preserves_recovery_records():
    report = run_endurance(30)
    assert report["canonical_comparison"]
    assert report["simulation_completed"] is True
    baseline, stress = report["cases"]
    assert baseline["boundaries"] == stress["boundaries"] == 30
    assert baseline["canonical"]["resolved_closes"] == stress["canonical"]["resolved_closes"] == 30
    assert stress["canonical"]["unresolved_audit_versions"]
    assert stress["runner_sessions"] == 2
    assert {"restart", "repeat_poll", "outage", "downtime_catch_up", "cycle_sql_interruption"} <= set(stress["faults_observed"])
    assert all(case["checks"]["integrity"] == "ok" for case in report["cases"])


def test_catch_up_preserves_canonical_decisions_for_identical_confirmed_input():
    report = run_endurance(30)
    assert report["checks_passed"] is True
    assert report["canonical_agreement"] is True


def test_missing_then_delayed_candle_recovers_without_erasing_the_gap(request):
    baseline = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    subject = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    for harness in (baseline, subject):
        request.addfinalizer(harness.engine.dispose)
        harness.advance_to(QUALIFYING_BOUNDARY)
        assert harness.run(refresh_market_data=True).paper_plans_created == 1
    plan = subject.service.ledger.paper_plans(**SCOPE)[0]
    entry = bar(21, 126, low=123)
    target = bar(22, 140, high=141, low=124)
    boundary = entry.timestamp + INTERVAL
    baseline.source.set_candles(labelled_series() + (entry, target))
    baseline.advance_to(boundary)
    baseline.run(refresh_market_data=True)
    subject.advance_to(boundary)  # Its closed entry candle is temporarily absent.
    subject.run(refresh_market_data=True)
    before = subject.service.ledger_snapshot(**SCOPE)
    gaps = tuple(item for item in before.cycles if item.status.value == "MISSING_CANDLE")
    assert gaps
    incomplete = subject.service.ledger.latest_outcomes(**SCOPE)[0]
    assert incomplete.observation.status is OutcomeStatus.INCOMPLETE_DATA
    assert paper_trade_occupies_slot(incomplete, plan, now=boundary)

    subject.source.set_candles(labelled_series() + (entry, target))
    repaired = subject.run(refresh_market_data=True)  # Same boundary; delayed input only.
    assert repaired.paper_plans_created == 0
    after = subject.service.ledger_snapshot(**SCOPE)
    assert set(before.cycles).issubset(after.cycles)
    assert set(before.latest_outcomes).issubset(subject.service.ledger.outcome_versions(plan.paper_plan_id))
    assert subject.service.ledger.paper_plans(**SCOPE)[0] == plan
    assert subject.service.ledger.latest_outcomes(**SCOPE)[0].payload_json == baseline.service.ledger.latest_outcomes(**SCOPE)[0].payload_json
    # The source also offers the target candle, which has not closed yet.
    stored = CandleRepository(subject.engine).get_candles(**SCOPE).candles
    assert stored == labelled_series() + (entry,)
    frozen = subject.service.ledger_snapshot(**SCOPE)
    repeat = subject.run(refresh_market_data=True)
    assert repeat.cycles_recorded == repeat.observations_recorded == repeat.paper_plans_created == repeat.outcomes_recorded == 0
    assert subject.service.ledger_snapshot(**SCOPE) == frozen

    for harness in (baseline, subject):
        harness.advance_to(target.timestamp + INTERVAL)
        harness.run(refresh_market_data=True)
    settled = subject.service.ledger.latest_outcomes(**SCOPE)[0]
    assert settled.observation.status is OutcomeStatus.TARGETS_REACHED
    assert not paper_trade_occupies_slot(settled, plan, now=target.timestamp + INTERVAL)
    assert settled.payload_json == baseline.service.ledger.latest_outcomes(**SCOPE)[0].payload_json
    assert gaps[0] in subject.service.ledger.cycles(**SCOPE)
    print("ENDURANCE_DELAYED_INPUT=" + json.dumps({
        "missing_cycles_preserved": len(gaps), "same_boundary_recovered": True,
        "latest_outcome_matches": True, "final_status": settled.observation.status.value,
        "former_slot_freed": True, "forming_candle_excluded": True,
    }, sort_keys=True))


def _cli_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "forward_endurance.py"
    specification = importlib.util.spec_from_file_location("endurance_cli", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_cli_saves_actual_results_and_ignores_inherited_database_settings(tmp_path, monkeypatch):
    saved = dict(os.environ)
    monkeypatch.chdir(tmp_path)
    # An inert test value, never a production path or credential.
    forbidden = tmp_path / "do-not-open.sqlite3"
    os.environ["TRADING_ASSISTANT_DATABASE_URL"] = f"sqlite:///{forbidden}"
    try:
        result = _cli_module().main(["--boundaries", "12", "--case", "baseline", "--output", "evidence.json"])
    finally:
        os.environ.clear()
        os.environ.update(saved)
    assert not forbidden.exists()
    artifact = json.loads((tmp_path / "evidence.json").read_text())
    assert artifact == result
    assert artifact["phase"] == "complete"
    assert artifact["cases"][0]["row_counts"]["forward_cycles"] == 12


@pytest.mark.parametrize("arguments", [
    ["--boundaries", "0"], ["--output", "not-a-report.sqlite3"],
])
def test_cli_rejects_invalid_runs_before_opening_any_database(tmp_path, monkeypatch, arguments):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _cli_module().main(arguments)
    assert list(tmp_path.iterdir()) == []
