"""Component #9 audit: multi-timeframe hierarchy (Step 13) regression tests.

* The final gate assumed any confirmation state past WAITING/CONTRADICTING/
  INVALIDATED was CONFIRMING, so NOT_APPLICABLE (no evaluation happened)
  could proceed toward PLANNABLE; only explicit CONFIRMING now proceeds.
* The confirmation/execution layers silently evaluated any non-bullish setup
  direction as bearish and tolerated a missing setup-creation instant; both
  are now refused loudly, and alignment reports UNKNOWN for them.
* The service assumed the four-layer contract deep inside the runner path
  (``AttributeError`` on partial hierarchies); construction now requires it.
* A SQLite lock during the market-data refresh was swallowed into
  ``market_data_error`` while Step 12 stops immediately (PR #22); locks now
  propagate. Completed passes with a non-transient market-data failure now
  count toward ``stop_after_errors`` like Step 12 instead of spinning
  forever; transient failures never count.
* Re-recording a hierarchy observation at the same instant misreported
  ``created``; it now comes from the write itself.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from test_multi_timeframe import (
    EPOCH,
    HOUR,
    _confirming_layer,
    _confirmation_setup,
    boundary,
    context_layer,
    setup_layer,
    window_candles,
)
from test_multi_timeframe_service import make_service
from web_fixtures import migrated_engine

from trading_assistant.forward_testing.parameters import RunnerSettings
from trading_assistant.multi_timeframe.alignment import evaluate_alignment
from trading_assistant.multi_timeframe.confirmation import evaluate_confirmation
from trading_assistant.multi_timeframe.decision import gate_decision
from trading_assistant.multi_timeframe.errors import MultiTimeframeError
from trading_assistant.multi_timeframe.execution import evaluate_execution
from trading_assistant.multi_timeframe.hierarchy import (
    TimeframeHierarchy,
    TimeframeRole,
    TimeframeStep,
)
from trading_assistant.multi_timeframe.models import (
    ConfirmationState,
    ContextRegime,
    ExecutionLayerSnapshot,
    ExecutionState,
    HierarchyAlignment,
    HierarchyDecision,
)
from trading_assistant.multi_timeframe.runner import MultiTimeframeRunner


class _FailingMarketData:
    """A market-data source that always fails the same way."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def download_history(self, **kwargs):
        raise self._error

    def update_history(self, **kwargs):
        raise self._error


def _armed_execution(decision_time):
    return ExecutionLayerSnapshot(
        timeframe="5m",
        decision_time=decision_time,
        boundary_open=decision_time - timedelta(minutes=5),
        boundary_close=decision_time,
        state=ExecutionState.ARMED,
        reason="armed",
        window_start=EPOCH,
        window_end=decision_time - timedelta(minutes=5),
        candle_count=12,
        missing_candle_count=0,
        stale=False,
        armed_at=EPOCH,
        trigger_at=None,
        entry_zone_low=Decimal("100"),
        entry_zone_high=Decimal("102"),
        latest_close=Decimal("101"),
        evidence=(),
    )


def test_not_applicable_confirmation_cannot_proceed_toward_plannable() -> None:
    decision_time = EPOCH + 6 * HOUR
    setup = setup_layer(decision_time=decision_time)
    context = context_layer(
        regime=ContextRegime.BULLISH_STRUCTURE, decision_time=decision_time
    )
    confirmation = replace(
        _confirming_layer(decision_time),
        state=ConfirmationState.NOT_APPLICABLE,
        reason="nothing evaluated",
    )
    decision, reasons, status, _, _, _ = gate_decision(
        context=context,
        setup=setup,
        confirmation=confirmation,
        execution=_armed_execution(decision_time),
        alignment=evaluate_alignment(context, setup),
    )
    assert decision is HierarchyDecision.AWAITING_CONFIRMATION
    assert status == "evaluated"
    assert "confirmation_not_confirming" in reasons


def test_confirmation_refuses_an_unrecognized_setup_direction() -> None:
    setup = _confirmation_setup(direction="sideways")
    candles = window_candles("15m", EPOCH, ("100.5", "101"), timedelta(minutes=15))
    with pytest.raises(ValueError, match="must state bullish or bearish"):
        evaluate_confirmation(
            setup,
            candles,
            boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR),
        )


def test_confirmation_refuses_a_setup_without_a_creation_instant() -> None:
    setup = _confirmation_setup(created_at=None)
    with pytest.raises(ValueError, match="without the instant"):
        evaluate_confirmation(
            setup,
            (),
            boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR),
        )


def test_execution_refuses_an_unrecognized_setup_direction() -> None:
    decision_time = EPOCH + 6 * HOUR
    setup = replace(_confirmation_setup(), direction="sideways")
    candles = window_candles(
        "5m", EPOCH, tuple("101" for _ in range(12)), timedelta(minutes=5)
    )
    with pytest.raises(ValueError, match="must state bullish or bearish"):
        evaluate_execution(
            setup,
            _confirming_layer(decision_time),
            candles,
            boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time),
        )


def test_execution_refuses_a_setup_without_a_creation_instant() -> None:
    decision_time = EPOCH + 6 * HOUR
    setup = replace(_confirmation_setup(), created_at=None)
    with pytest.raises(ValueError, match="without the instant"):
        evaluate_execution(
            setup,
            _confirming_layer(decision_time),
            (),
            boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time),
        )


def test_alignment_is_unknown_for_an_unrecognized_setup_direction() -> None:
    assert (
        evaluate_alignment(
            context_layer(regime=ContextRegime.BULLISH_STRUCTURE),
            setup_layer(direction="sideways"),
        )
        is HierarchyAlignment.UNKNOWN
    )


def test_service_construction_requires_the_four_layer_contract(tmp_path: Path) -> None:
    engine, _url = migrated_engine(tmp_path, "partial.sqlite3")
    partial = TimeframeHierarchy(
        steps=(
            TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="4h"),
            TimeframeStep(role=TimeframeRole.SETUP, timeframe="1h"),
        )
    )
    with pytest.raises(ValueError, match="four-layer"):
        make_service(engine, hierarchy=partial)


def test_sqlite_lock_during_refresh_propagates_instead_of_being_recorded(
    tmp_path: Path,
) -> None:
    engine, _url = migrated_engine(tmp_path, "lock.sqlite3")
    lock = OperationalError("SELECT 1", {}, RuntimeError("database is locked"))
    service = make_service(engine, market_data_service=_FailingMarketData(lock))
    with pytest.raises(OperationalError):
        service.run_once(refresh_market_data=True)


def test_runner_stops_after_repeated_non_transient_market_data_failures(
    tmp_path: Path,
) -> None:
    engine, _url = migrated_engine(tmp_path, "mtf-err.sqlite3")
    service = make_service(
        engine,
        market_data_service=_FailingMarketData(ValueError("permanent misconfiguration")),
    )
    runner = MultiTimeframeRunner(
        service,
        settings=RunnerSettings(stop_after_errors=2, interval_seconds=1),
        sleep=lambda seconds: None,
    )
    with pytest.raises(MultiTimeframeError, match="stopping after 2"):
        runner.run(max_passes=10, refresh_market_data=True)


def test_runner_keeps_going_through_transient_market_data_failures(
    tmp_path: Path,
) -> None:
    engine, _url = migrated_engine(tmp_path, "mtf-trans.sqlite3")
    service = make_service(
        engine, market_data_service=_FailingMarketData(ConnectionError("network down"))
    )
    waits: list[float] = []
    runner = MultiTimeframeRunner(
        service,
        settings=RunnerSettings(stop_after_errors=1, interval_seconds=1),
        sleep=waits.append,
    )
    result = runner.run(max_passes=3, refresh_market_data=True)
    assert result.market_data_error_transient is True
    assert result.market_data_error_type == "ConnectionError"
    # Error passes wait the normal cadence instead of spinning hot.
    assert waits


def test_duplicate_hierarchy_observation_reports_not_created(tmp_path: Path) -> None:
    engine, _url = migrated_engine(tmp_path, "mtf-created.sqlite3")
    service = make_service(engine)
    instant = EPOCH + 6 * HOUR
    snapshot = service.evaluate(decision_time=instant)
    _, first = service.record(snapshot, recorded_at=instant)
    _, second = service.record(snapshot, recorded_at=instant)
    assert first is True
    assert second is False
