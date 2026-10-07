"""Step 13 pure multi-timeframe hierarchy tests (no database, no exchange).

These tests pin the deterministic contract of the hierarchy itself: the
versioned timeframe roles, closed-candle decision boundaries (including the
10:00 vs 12:00 UTC 4H boundary cases), the rule that lower timeframes can
never create or override a higher-timeframe conclusion, the alignment
classification, every confirmation/execution state, the final gating, the
immutable snapshot identity (including version isolation), the migration, and
the grounded plain-English explanation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from trading_assistant.database import Base, create_database_engine
from trading_assistant.market_data.types import Candle
from trading_assistant.multi_timeframe import tables as _hierarchy_tables  # noqa: F401
from trading_assistant.multi_timeframe.alignment import evaluate_alignment
from trading_assistant.multi_timeframe.boundaries import (
    TimeframeBoundary,
    resolve_decision_boundary,
)
from trading_assistant.multi_timeframe.confirmation import (
    evaluate_confirmation,
    validate_window,
)
from trading_assistant.multi_timeframe.decision import gate_decision
from trading_assistant.multi_timeframe.errors import HierarchyConflict
from trading_assistant.multi_timeframe.execution import evaluate_execution
from trading_assistant.multi_timeframe.explanation import explain_hierarchy
from trading_assistant.multi_timeframe.hierarchy import (
    BTC_HIERARCHY,
    ROLE_ORDER,
    TimeframeHierarchy,
    TimeframeRole,
    TimeframeStep,
    default_hierarchy,
)
from trading_assistant.multi_timeframe.ladder import ladder_payload
from trading_assistant.multi_timeframe.models import (
    ConfirmationState,
    ContextLayerSnapshot,
    ContextRegime,
    ExecutionState,
    HierarchyAlignment,
    HierarchyDecision,
    HierarchyObservation,
    HierarchySnapshot,
    SetupLayerSnapshot,
)
from trading_assistant.multi_timeframe.parameters import (
    HIERARCHY_LEDGER_RULES_VERSION,
    HIERARCHY_RULES_VERSION,
)
from trading_assistant.multi_timeframe.repository import HierarchyLedgerRepository

EPOCH = datetime(2024, 1, 1, tzinfo=UTC)
EXCHANGE = "mock-exchange"
SYMBOL = "BTC/USDT"
HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)


def candle(
    timeframe: str,
    open_time: datetime,
    close_price: str,
    *,
    open_price: str | None = None,
    high_price: str | None = None,
    low_price: str | None = None,
    symbol: str = SYMBOL,
) -> Candle:
    price = Decimal(close_price)
    return Candle(
        exchange=EXCHANGE,
        symbol=symbol,
        timeframe=timeframe,
        timestamp=open_time,
        open=Decimal(open_price if open_price is not None else close_price),
        high=Decimal(high_price if high_price is not None else close_price),
        low=Decimal(low_price if low_price is not None else close_price),
        close=price,
        volume=Decimal("10"),
    )


def boundary(
    timeframe: str,
    role: TimeframeRole,
    decision_time: datetime,
    *,
    open_time: datetime | None = None,
    known: bool = True,
    stale: bool = False,
) -> TimeframeBoundary:
    from trading_assistant.market_structure.candles import interval_for_timeframe

    interval = interval_for_timeframe(timeframe)
    open_time = open_time if open_time is not None else decision_time - interval
    stored_open = open_time - interval if stale else open_time
    return TimeframeBoundary(
        timeframe=timeframe,
        role=role,
        decision_time=decision_time,
        candle_open_time=open_time,
        candle_close_time=open_time + interval,
        candle=candle(timeframe, open_time, "100") if known else None,
        latest_stored_closed=candle(timeframe, stored_open, "100") if known else None,
    )


def context_layer(
    *,
    regime: ContextRegime = ContextRegime.BULLISH_STRUCTURE,
    available: bool = True,
    timeframe: str = "4h",
    decision_time: datetime = EPOCH,
) -> ContextLayerSnapshot:
    return ContextLayerSnapshot(
        timeframe=timeframe,
        decision_time=decision_time,
        boundary_open=decision_time - timedelta(hours=4),
        boundary_close=decision_time,
        available=available,
        reason=None,
        regime=regime,
        trend_direction="bullish" if regime is ContextRegime.BULLISH_STRUCTURE else "bearish",
        trend_reason="higher_highs_and_lows",
        confirmed_swing_count=4,
        active_range_low=None,
        active_range_high=None,
        nearest_support_band_low=Decimal("90"),
        nearest_support_band_high=Decimal("92"),
        nearest_resistance_band_low=Decimal("110"),
        nearest_resistance_band_high=Decimal("112"),
        latest_close=Decimal("105"),
        candle_count=36,
        missing_candle_count=0,
        stale=False,
        evidence=(),
    )


def setup_layer(
    *,
    setup_state: str = "QUALIFIED",
    direction: str = "bullish",
    band_low: str = "100",
    band_high: str = "102",
    created_at: datetime = EPOCH,
    setup_id: str | None = "setup-1",
    terminal_reason: str | None = None,
    ended_at: datetime | None = None,
    available: bool = True,
    timeframe: str = "1h",
    decision_time: datetime = EPOCH,
) -> SetupLayerSnapshot:
    return SetupLayerSnapshot(
        timeframe=timeframe,
        decision_time=decision_time,
        boundary_open=decision_time - HOUR,
        boundary_close=decision_time,
        available=available,
        reason=None,
        state=None,
        snapshot_status="evaluated",
        snapshot_id="snap-1",
        rules_version="setup-qualification-v1",
        config_fingerprint="fp",
        setup_id=setup_id,
        family="breakout_retest_continuation" if setup_id else None,
        direction=direction if setup_id else None,
        setup_state=setup_state if setup_id else None,
        created_at=created_at if setup_id else None,
        terminal_reason=terminal_reason,
        ended_at=ended_at,
        reference_id="ref-1" if setup_id else None,
        reference_band_low=Decimal(band_low) if setup_id else None,
        reference_band_high=Decimal(band_high) if setup_id else None,
        supporting_rules=("trend_alignment",) if setup_id else (),
        opposing_rules=(),
        pending_rules=(),
        invalidation=None,
        next_required=(),
        candidate_count=1 if setup_id else 0,
        stale=False,
    )


def window_candles(
    timeframe: str, start: datetime, closes: tuple[str, ...], interval: timedelta
) -> tuple[Candle, ...]:
    return tuple(
        candle(timeframe, start + index * interval, close)
        for index, close in enumerate(closes)
    )


# ---------------------------------------------------------------------------
# Hierarchy configuration (versioned roles, not hard-coded strings)
# ---------------------------------------------------------------------------


def test_default_hierarchy_is_the_btc_4h_1h_15m_5m_ladder():
    hierarchy = default_hierarchy()
    assert [(step.role, step.timeframe) for step in hierarchy.steps] == [
        (TimeframeRole.CONTEXT, "4h"),
        (TimeframeRole.SETUP, "1h"),
        (TimeframeRole.CONFIRMATION, "15m"),
        (TimeframeRole.EXECUTION, "5m"),
    ]
    assert hierarchy.timeframes == ("4h", "1h", "15m", "5m")
    assert hierarchy.roles == tuple(role.value for role in ROLE_ORDER)
    assert hierarchy.rules_version == HIERARCHY_RULES_VERSION
    assert BTC_HIERARCHY.fingerprint() == hierarchy.fingerprint()
    assert hierarchy.context.timeframe == "4h"
    assert hierarchy.setup.timeframe == "1h"
    assert hierarchy.confirmation.timeframe == "15m"
    assert hierarchy.execution.timeframe == "5m"
    assert hierarchy.step_for(TimeframeRole.CONFIRMATION).timeframe == "15m"
    assert hierarchy.role_for("5m") is TimeframeRole.EXECUTION
    assert hierarchy.timeframe_for(TimeframeRole.SETUP) == "1h"


def test_hierarchy_roles_are_versioned_config_not_hard_coded_strings():
    custom = TimeframeHierarchy(
        steps=(
            TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="1d"),
            TimeframeStep(role=TimeframeRole.SETUP, timeframe="4h"),
            TimeframeStep(role=TimeframeRole.CONFIRMATION, timeframe="1h"),
            TimeframeStep(role=TimeframeRole.EXECUTION, timeframe="15m"),
        ),
        rules_version="test-hierarchy-v9",
    )
    assert custom.fingerprint() != default_hierarchy().fingerprint()
    assert custom.rules_version == "test-hierarchy-v9"
    assert custom.to_json_dict()["steps"][0] == {"role": "context", "timeframe": "1d"}


def test_hierarchy_rejects_invalid_configurations():
    with pytest.raises(ValueError, match="role may appear at most once"):
        TimeframeHierarchy(
            steps=(
                TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="4h"),
                TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="1h"),
            )
        )
    with pytest.raises(ValueError, match="timeframe may appear at most once"):
        TimeframeHierarchy(
            steps=(
                TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="1h"),
                TimeframeStep(role=TimeframeRole.SETUP, timeframe="1h"),
            )
        )
    with pytest.raises(ValueError, match="CONTEXT"):
        TimeframeHierarchy(steps=(TimeframeStep(role=TimeframeRole.SETUP, timeframe="1h"),))
    with pytest.raises(ValueError, match="strictly decrease"):
        TimeframeHierarchy(
            steps=(
                TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="1h"),
                TimeframeStep(role=TimeframeRole.SETUP, timeframe="4h"),
            )
        )
    # Unparseable timeframes are rejected at the step itself; membership in
    # the configured supported_timeframes is enforced by the service.
    with pytest.raises(ValueError):
        TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="7x")


# ---------------------------------------------------------------------------
# Closed-candle decision boundaries (no lookahead, ever)
# ---------------------------------------------------------------------------


def test_resolve_decision_boundary_selects_latest_closed_candle_per_timeframe():
    hierarchy = default_hierarchy()
    decision_time = EPOCH + 10 * HOUR + 37 * MINUTE  # 10:37 UTC
    candles = {
        "4h": (
            candle("4h", EPOCH, "100"),
            candle("4h", EPOCH + 4 * HOUR, "101"),
            candle("4h", EPOCH + 8 * HOUR, "102"),
        ),
        "1h": tuple(
            candle("1h", EPOCH + index * HOUR, "100") for index in range(11)
        ),
        "15m": tuple(
            candle("15m", EPOCH + index * timedelta(minutes=15), "100")
            for index in range(43)
        ),
        "5m": tuple(
            candle("5m", EPOCH + index * timedelta(minutes=5), "100")
            for index in range(128)
        ),
    }
    resolved = resolve_decision_boundary(decision_time, hierarchy, candles)
    assert resolved.all_candles_known
    # 10:37 UTC: the 08:00→12:00 4H candle is NOT closed; 04:00→08:00 is.
    assert resolved.boundary_for("4h").candle_open_time == EPOCH + 4 * HOUR
    assert resolved.boundary_for("4h").candle_close_time == EPOCH + 8 * HOUR
    # The 10:00→11:00 1H candle closes at 11:00 > 10:37: the boundary is 09:00→10:00.
    assert resolved.boundary_for("1h").candle_open_time == EPOCH + 9 * HOUR
    assert resolved.boundary_for("15m").candle_open_time == EPOCH + 10 * HOUR + 15 * MINUTE
    assert resolved.boundary_for("5m").candle_open_time == EPOCH + 10 * HOUR + 30 * MINUTE
    for timeframe in hierarchy.timeframes:
        selected = resolved.boundary_for(timeframe)
        assert selected.candle_close_time <= decision_time
        assert not selected.stale


def test_four_hour_candle_unavailable_before_close_and_available_after():
    """The 10:00 vs 12:00 UTC 4H boundary case, exactly as specified."""

    hierarchy = default_hierarchy()
    candles = {
        "4h": (
            candle("4h", EPOCH, "100"),               # 00:00→04:00
            candle("4h", EPOCH + 4 * HOUR, "101"),   # 04:00→08:00
            candle("4h", EPOCH + 8 * HOUR, "102"),   # 08:00→12:00 (unclosed at 10:00)
        ),
        "1h": tuple(candle("1h", EPOCH + index * HOUR, "100") for index in range(13)),
        "15m": tuple(
            candle("15m", EPOCH + index * timedelta(minutes=15), "100")
            for index in range(49)
        ),
        "5m": tuple(
            candle("5m", EPOCH + index * timedelta(minutes=5), "100")
            for index in range(145)
        ),
    }
    before = resolve_decision_boundary(EPOCH + 10 * HOUR, hierarchy, candles)
    assert before.boundary_for("4h").candle_open_time == EPOCH + 4 * HOUR
    assert before.boundary_for("4h").candle_close_time == EPOCH + 8 * HOUR

    after = resolve_decision_boundary(EPOCH + 12 * HOUR, hierarchy, candles)
    # At exactly 12:00 UTC the 08:00→12:00 candle has closed: it IS the boundary.
    assert after.boundary_for("4h").candle_open_time == EPOCH + 8 * HOUR
    assert after.boundary_for("4h").candle_close_time == EPOCH + 12 * HOUR


def test_lower_timeframes_never_select_candles_that_are_not_closed():
    hierarchy = default_hierarchy()
    decision_time = EPOCH + 6 * HOUR  # exactly on an hour boundary
    candles = {
        "4h": (candle("4h", EPOCH + 4 * HOUR, "100"),),  # 04:00→08:00 unclosed at 06:00
        "1h": tuple(candle("1h", EPOCH + index * HOUR, "100") for index in range(7)),
        # 15M/5M series deliberately include candles that close AFTER 06:00.
        "15m": tuple(
            candle("15m", EPOCH + index * timedelta(minutes=15), "100")
            for index in range(26)  # last one opens 06:15, closes 06:30 (future)
        ),
        "5m": tuple(
            candle("5m", EPOCH + index * timedelta(minutes=5), "100")
            for index in range(74)  # last one opens 06:05, closes 06:10 (future)
        ),
    }
    resolved = resolve_decision_boundary(decision_time, hierarchy, candles)
    assert resolved.boundary_for("15m").candle_open_time == EPOCH + 5 * HOUR + 45 * MINUTE
    assert resolved.boundary_for("15m").candle_close_time == EPOCH + 6 * HOUR
    assert resolved.boundary_for("5m").candle_open_time == EPOCH + 5 * HOUR + 55 * MINUTE
    assert resolved.boundary_for("5m").candle_close_time == EPOCH + 6 * HOUR
    # The 4H candle closing at 08:00 is future data at 06:00: it is not selected,
    # and the boundary honestly reports that the expected candle is not stored.
    assert resolved.boundary_for("4h").candle_known is False
    assert resolved.boundary_for("4h").stale is True


def test_boundary_selection_refuses_a_future_candle_even_if_stored():
    hierarchy = default_hierarchy()
    decision_time = EPOCH + 2 * HOUR
    future_1h = candle("1h", EPOCH + 2 * HOUR, "100")  # closes 03:00 (future)
    candles = {
        "4h": (candle("4h", EPOCH, "100"),),
        "1h": (candle("1h", EPOCH, "100"), candle("1h", EPOCH + HOUR, "101"), future_1h),
        "15m": tuple(
            candle("15m", EPOCH + index * timedelta(minutes=15), "100")
            for index in range(9)
        ),
        "5m": tuple(
            candle("5m", EPOCH + index * timedelta(minutes=5), "100")
            for index in range(25)
        ),
    }
    resolved = resolve_decision_boundary(decision_time, hierarchy, candles)
    assert resolved.boundary_for("1h").candle_open_time == EPOCH + HOUR
    assert resolved.boundary_for("1h").candle_close_time == EPOCH + 2 * HOUR


# ---------------------------------------------------------------------------
# Alignment: conflict handling (never a silent 4H override)
# ---------------------------------------------------------------------------


def test_aligned_hierarchy():
    alignment = evaluate_alignment(
        context_layer(regime=ContextRegime.BULLISH_STRUCTURE),
        setup_layer(direction="bullish"),
    )
    assert alignment is HierarchyAlignment.ALIGNED


def test_counter_trend_hierarchy_is_explicitly_identified():
    alignment = evaluate_alignment(
        context_layer(regime=ContextRegime.BEARISH_STRUCTURE),
        setup_layer(direction="bullish"),
    )
    assert alignment is HierarchyAlignment.COUNTER_TREND


def test_neutral_hierarchy_inside_a_range():
    alignment = evaluate_alignment(
        context_layer(regime=ContextRegime.RANGE),
        setup_layer(direction="bullish"),
    )
    assert alignment is HierarchyAlignment.NEUTRAL


def test_conflicting_and_unknown_alignment():
    assert (
        evaluate_alignment(
            context_layer(regime=ContextRegime.TRANSITION), setup_layer()
        )
        is HierarchyAlignment.CONFLICTING
    )
    assert (
        evaluate_alignment(
            context_layer(regime=ContextRegime.UNKNOWN), setup_layer()
        )
        is HierarchyAlignment.UNKNOWN
    )
    assert (
        evaluate_alignment(
            context_layer(regime=ContextRegime.BULLISH_STRUCTURE),
            setup_layer(setup_id=None),
        )
        is HierarchyAlignment.UNKNOWN
    )


def test_lower_timeframe_cannot_override_the_4h_context():
    """A 5M 'trigger' with no 1H parent can never create a trade or a regime."""

    decision_time = EPOCH + 6 * HOUR
    setup = setup_layer(setup_id=None, decision_time=decision_time)
    confirmation = evaluate_confirmation(
        setup,
        window_candles("15m", EPOCH + 5 * HOUR, ("101", "102", "103", "104"), timedelta(minutes=15)),
        boundary=boundary("15m", TimeframeRole.CONFIRMATION, decision_time),
    )
    execution = evaluate_execution(
        setup,
        confirmation,
        window_candles("5m", EPOCH + 5 * HOUR, tuple("101" for _ in range(12)), timedelta(minutes=5)),
        boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time),
    )
    decision, reasons, status, counter_trend, waiting_for, _ = gate_decision(
        context=context_layer(regime=ContextRegime.BEARISH_STRUCTURE, decision_time=decision_time),
        setup=setup,
        confirmation=confirmation,
        execution=execution,
        alignment=evaluate_alignment(
            context_layer(regime=ContextRegime.BEARISH_STRUCTURE, decision_time=decision_time),
            setup,
        ),
    )
    assert confirmation.state is ConfirmationState.NOT_APPLICABLE
    assert execution.state is ExecutionState.NOT_ARMED
    assert decision is HierarchyDecision.NO_SETUP
    assert status == "evaluated"
    # The 4H context is untouched: still bearish, never rewritten by 5M data.
    assert counter_trend is False


# ---------------------------------------------------------------------------
# Confirmation layer (15M): every state
# ---------------------------------------------------------------------------


def _confirmation_setup(**kwargs) -> SetupLayerSnapshot:
    defaults = dict(
        band_low="100",
        band_high="102",
        created_at=EPOCH,
        decision_time=EPOCH + 6 * HOUR,
    )
    defaults.update(kwargs)
    return setup_layer(**defaults)


def test_confirmation_waiting_while_price_holds_inside_the_band():
    setup = _confirmation_setup()
    candles = window_candles(
        "15m", EPOCH, ("100.5", "101", "100.8", "101.2"), timedelta(minutes=15)
    )
    result = evaluate_confirmation(
        setup, candles, boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR)
    )
    assert result.state is ConfirmationState.WAITING
    assert result.candle_count == 4
    assert any(e.category == "level_hold" for e in result.evidence)


def test_confirmation_confirming_on_acceptance_with_the_level_held():
    setup = _confirmation_setup()
    candles = window_candles(
        "15m", EPOCH, ("100.5", "101", "102.5", "103"), timedelta(minutes=15)
    )
    result = evaluate_confirmation(
        setup, candles, boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR)
    )
    assert result.state is ConfirmationState.CONFIRMING
    assert any(e.category == "level_acceptance" for e in result.evidence)


def test_confirmation_contradicting_when_the_level_is_lost():
    setup = _confirmation_setup()
    candles = window_candles(
        "15m", EPOCH, ("101", "102.5", "99.5", "99"), timedelta(minutes=15)
    )
    result = evaluate_confirmation(
        setup, candles, boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR)
    )
    assert result.state is ConfirmationState.CONTRADICTING
    assert any(e.category == "level_loss" for e in result.evidence)
    # An earlier acceptance followed by a loss is a false breakout.
    assert any(e.category == "false_breakout" for e in result.evidence)


def test_confirmation_not_applicable_without_an_active_setup():
    setup = _confirmation_setup(setup_id=None)
    result = evaluate_confirmation(
        setup,
        window_candles("15m", EPOCH, ("101",), timedelta(minutes=15)),
        boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR),
    )
    assert result.state is ConfirmationState.NOT_APPLICABLE


def test_confirmation_invalidated_when_the_setup_ended():
    setup = _confirmation_setup(
        setup_id="setup-gone",
        setup_state="NO_SETUP",
        terminal_reason="opposite_close",
        ended_at=EPOCH + 6 * HOUR,
    )
    result = evaluate_confirmation(
        setup, (), boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR)
    )
    assert result.state is ConfirmationState.INVALIDATED


def test_confirmation_waiting_when_no_candle_closed_since_the_setup():
    setup = _confirmation_setup()
    result = evaluate_confirmation(
        setup, (), boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR)
    )
    assert result.state is ConfirmationState.WAITING


def test_confirmation_never_uses_a_candle_that_is_not_closed():
    setup = _confirmation_setup()
    # A 15M candle opening at 05:45 closes at 06:00: closed exactly at the
    # decision time. A candle opening at 06:00 would be lookahead.
    closed = candle("15m", EPOCH + 5 * HOUR + 45 * MINUTE, "101")
    lookahead = candle("15m", EPOCH + 6 * HOUR, "101")
    with pytest.raises(ValueError, match="lookahead"):
        validate_window(
            (closed, lookahead),
            timeframe="15m",
            window_start=EPOCH,
            decision_time=EPOCH + 6 * HOUR,
        )
    with pytest.raises(ValueError, match="lookahead"):
        evaluate_confirmation(
            setup,
            (lookahead,),
            boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR),
        )


def test_confirmation_never_uses_candles_from_before_the_setup():
    setup = _confirmation_setup(created_at=EPOCH + 5 * HOUR)
    early = candle("15m", EPOCH + 4 * HOUR + 45 * MINUTE, "101")
    with pytest.raises(ValueError, match="before the setup became known"):
        evaluate_confirmation(
            setup,
            (early,),
            boundary=boundary("15m", TimeframeRole.CONFIRMATION, EPOCH + 6 * HOUR),
        )


# ---------------------------------------------------------------------------
# Execution layer (5M): every state
# ---------------------------------------------------------------------------


def _confirming_layer(decision_time: datetime) -> object:
    from trading_assistant.multi_timeframe.models import ConfirmationLayerSnapshot

    return ConfirmationLayerSnapshot(
        timeframe="15m",
        decision_time=decision_time,
        boundary_open=decision_time - timedelta(minutes=15),
        boundary_close=decision_time,
        state=ConfirmationState.CONFIRMING,
        reason="accepted",
        window_start=EPOCH,
        window_end=decision_time - timedelta(minutes=15),
        candle_count=4,
        missing_candle_count=0,
        stale=False,
        evidence=(),
    )


def test_execution_not_armed_without_a_qualified_confirmed_setup():
    decision_time = EPOCH + 6 * HOUR
    watch = _confirmation_setup(setup_state="WATCH")
    confirming = _confirming_layer(decision_time)
    candles = window_candles("5m", EPOCH, tuple("101" for _ in range(12)), timedelta(minutes=5))
    result = evaluate_execution(
        watch, confirming, candles, boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time)
    )
    assert result.state is ExecutionState.NOT_ARMED
    assert "not QUALIFIED" in result.reason

    no_setup = _confirmation_setup(setup_id=None)
    result = evaluate_execution(
        no_setup, confirming, candles, boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time)
    )
    assert result.state is ExecutionState.NOT_ARMED


def test_execution_waiting_until_price_reaches_the_entry_zone():
    decision_time = EPOCH + 6 * HOUR
    setup = _confirmation_setup()  # band 100-102, price above it throughout
    candles = window_candles(
        "5m", EPOCH, tuple("103" for _ in range(12)), timedelta(minutes=5)
    )
    result = evaluate_execution(
        setup,
        _confirming_layer(decision_time),
        candles,
        boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time),
    )
    assert result.state is ExecutionState.WAITING
    assert result.armed_at is None


def test_execution_armed_when_price_enters_the_zone_and_holds():
    decision_time = EPOCH + 6 * HOUR
    setup = _confirmation_setup()  # band 100-102
    closes = ("103", "102.5", "101.5", "101", "100.5", "101", "101.2", "101.4", "101.6", "101.8", "101.9", "101.95")
    candles = window_candles("5m", EPOCH, closes, timedelta(minutes=5))
    result = evaluate_execution(
        setup,
        _confirming_layer(decision_time),
        candles,
        boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time),
    )
    assert result.state is ExecutionState.ARMED
    assert result.armed_at == EPOCH + 2 * timedelta(minutes=5)  # first zone touch
    assert result.trigger_at is None
    assert result.entry_zone_low == Decimal("100")
    assert result.entry_zone_high == Decimal("102")


def test_execution_triggered_on_a_close_through_the_far_side():
    decision_time = EPOCH + 6 * HOUR
    setup = _confirmation_setup()
    closes = ("103", "102.5", "101.5", "101", "100.5", "101", "101.5", "102.1", "102.4", "102.6", "102.8", "103")
    candles = window_candles("5m", EPOCH, closes, timedelta(minutes=5))
    result = evaluate_execution(
        setup,
        _confirming_layer(decision_time),
        candles,
        boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time),
    )
    assert result.state is ExecutionState.TRIGGERED
    assert result.armed_at is not None
    assert result.trigger_at == EPOCH + 11 * timedelta(minutes=5)


def test_execution_invalidated_when_the_level_is_lost_on_5m():
    decision_time = EPOCH + 6 * HOUR
    setup = _confirmation_setup()
    closes = ("101", "100.5", "99.5", "99", "99.5", "100", "100.5", "101", "101.5", "102", "102.5", "103")
    candles = window_candles("5m", EPOCH, closes, timedelta(minutes=5))
    result = evaluate_execution(
        setup,
        _confirming_layer(decision_time),
        candles,
        boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time),
    )
    assert result.state is ExecutionState.INVALIDATED
    assert result.armed_at is None


def test_execution_never_uses_a_candle_that_is_not_closed():
    decision_time = EPOCH + 6 * HOUR
    setup = _confirmation_setup()
    lookahead = candle("5m", decision_time, "101")
    with pytest.raises(ValueError, match="lookahead"):
        evaluate_execution(
            setup,
            _confirming_layer(decision_time),
            (lookahead,),
            boundary=boundary("5m", TimeframeRole.EXECUTION, decision_time),
        )


# ---------------------------------------------------------------------------
# Final gating: one deterministic overall decision
# ---------------------------------------------------------------------------


def _gate(
    *,
    context_regime=ContextRegime.BULLISH_STRUCTURE,
    setup_kwargs=None,
    confirmation_state=ConfirmationState.CONFIRMING,
    execution_state=ExecutionState.WAITING,
    decision_time=EPOCH + 6 * HOUR,
):
    from trading_assistant.multi_timeframe.models import (
        ConfirmationLayerSnapshot,
        ExecutionLayerSnapshot,
    )

    setup = setup_layer(**(setup_kwargs or {}), decision_time=decision_time)
    context = context_layer(regime=context_regime, decision_time=decision_time)
    confirmation = ConfirmationLayerSnapshot(
        timeframe="15m",
        decision_time=decision_time,
        boundary_open=decision_time - timedelta(minutes=15),
        boundary_close=decision_time,
        state=confirmation_state,
        reason="test",
        window_start=EPOCH,
        window_end=decision_time - timedelta(minutes=15),
        candle_count=4,
        missing_candle_count=0,
        stale=False,
        evidence=(),
    )
    execution = ExecutionLayerSnapshot(
        timeframe="5m",
        decision_time=decision_time,
        boundary_open=decision_time - timedelta(minutes=5),
        boundary_close=decision_time,
        state=execution_state,
        reason="test",
        window_start=EPOCH,
        window_end=decision_time - timedelta(minutes=5),
        candle_count=12,
        missing_candle_count=0,
        stale=False,
        armed_at=None,
        trigger_at=None,
        entry_zone_low=Decimal("100"),
        entry_zone_high=Decimal("102"),
        latest_close=Decimal("101"),
        evidence=(),
    )
    alignment = evaluate_alignment(context, setup)
    return gate_decision(
        context=context,
        setup=setup,
        confirmation=confirmation,
        execution=execution,
        alignment=alignment,
    )


def test_gating_no_setup():
    decision, reasons, status, _, waiting_for, _ = _gate(setup_kwargs={"setup_id": None})
    assert decision is HierarchyDecision.NO_SETUP
    assert status == "evaluated"
    assert waiting_for


def test_gating_watch_cannot_be_upgraded_by_lower_timeframes():
    decision, reasons, status, _, waiting_for, _ = _gate(
        setup_kwargs={"setup_state": "WATCH"},
        execution_state=ExecutionState.TRIGGERED,  # even a 5M "trigger"
    )
    assert decision is HierarchyDecision.WATCH
    assert "setup_state:WATCH" in reasons


def test_gating_awaiting_confirmation():
    decision, _, _, _, _, _ = _gate(confirmation_state=ConfirmationState.WAITING)
    assert decision is HierarchyDecision.AWAITING_CONFIRMATION
    decision, _, _, _, _, _ = _gate(confirmation_state=ConfirmationState.CONTRADICTING)
    assert decision is HierarchyDecision.AWAITING_CONFIRMATION


def test_gating_awaiting_confirmation_when_alignment_cannot_complete():
    decision, reasons, _, _, _, _ = _gate(context_regime=ContextRegime.TRANSITION)
    assert decision is HierarchyDecision.AWAITING_CONFIRMATION
    assert any("alignment_not_plannable" in reason for reason in reasons)


def test_gating_awaiting_execution():
    decision, _, _, _, waiting_for, _ = _gate(execution_state=ExecutionState.WAITING)
    assert decision is HierarchyDecision.AWAITING_EXECUTION
    assert waiting_for


def test_gating_plannable_requires_the_complete_hierarchy():
    for execution_state in (ExecutionState.ARMED, ExecutionState.TRIGGERED):
        decision, reasons, status, counter_trend, waiting_for, _ = _gate(
            execution_state=execution_state
        )
        assert decision is HierarchyDecision.PLANNABLE
        assert status == "evaluated"
        assert waiting_for == ()


def test_gating_plannable_allows_an_explicitly_flagged_counter_trend():
    decision, reasons, status, counter_trend, _, _ = _gate(
        context_regime=ContextRegime.BEARISH_STRUCTURE,
        setup_kwargs={"direction": "bullish"},
        execution_state=ExecutionState.TRIGGERED,
    )
    assert decision is HierarchyDecision.PLANNABLE
    assert counter_trend is True
    assert "counter_trend_setup_flagged" in reasons


def test_gating_invalidated():
    decision, _, _, _, _, invalidated_if = _gate(execution_state=ExecutionState.INVALIDATED)
    assert decision is HierarchyDecision.INVALIDATED
    assert invalidated_if
    decision, _, _, _, _, _ = _gate(confirmation_state=ConfirmationState.INVALIDATED)
    assert decision is HierarchyDecision.INVALIDATED
    decision, _, _, _, _, _ = _gate(
        setup_kwargs={
            "setup_id": "setup-gone",
            "setup_state": "NO_SETUP",
            "terminal_reason": "opposite_close",
            "ended_at": EPOCH + 6 * HOUR,
        }
    )
    assert decision is HierarchyDecision.INVALIDATED


def test_gating_incomplete_when_context_or_setup_data_is_missing():
    decision, reasons, status, _, waiting_for, _ = _gate(
        setup_kwargs={"available": False}
    )
    assert decision is HierarchyDecision.NO_SETUP
    assert status == "incomplete"
    assert "setup_layer_unavailable" in reasons


# ---------------------------------------------------------------------------
# Immutable forward snapshot identity and version isolation
# ---------------------------------------------------------------------------


def _snapshot(**overrides) -> HierarchySnapshot:
    hierarchy = overrides.pop("hierarchy", default_hierarchy())
    decision_time = overrides.pop("decision_time", EPOCH + 6 * HOUR)
    setup = setup_layer(decision_time=decision_time, **overrides.pop("setup_kwargs", {}))
    context = context_layer(decision_time=decision_time, **overrides.pop("context_kwargs", {}))
    from trading_assistant.multi_timeframe.models import (
        ConfirmationLayerSnapshot,
        ExecutionLayerSnapshot,
    )

    confirmation = ConfirmationLayerSnapshot(
        timeframe="15m",
        decision_time=decision_time,
        boundary_open=decision_time - timedelta(minutes=15),
        boundary_close=decision_time,
        state=ConfirmationState.CONFIRMING,
        reason="test",
        window_start=setup.created_at,
        window_end=decision_time - timedelta(minutes=15),
        candle_count=4,
        missing_candle_count=0,
        stale=False,
        evidence=(),
    )
    execution = ExecutionLayerSnapshot(
        timeframe="5m",
        decision_time=decision_time,
        boundary_open=decision_time - timedelta(minutes=5),
        boundary_close=decision_time,
        state=ExecutionState.WAITING,
        reason="test",
        window_start=setup.created_at,
        window_end=decision_time - timedelta(minutes=5),
        candle_count=12,
        missing_candle_count=0,
        stale=False,
        armed_at=None,
        trigger_at=None,
        entry_zone_low=Decimal("100"),
        entry_zone_high=Decimal("102"),
        latest_close=Decimal("101"),
        evidence=(),
    )
    alignment = evaluate_alignment(context, setup)
    decision, reasons, status, counter_trend, waiting_for, invalidated_if = gate_decision(
        context=context,
        setup=setup,
        confirmation=confirmation,
        execution=execution,
        alignment=alignment,
    )
    candles = {
        "4h": (candle("4h", EPOCH + 4 * HOUR, "100"),),
        "1h": tuple(candle("1h", EPOCH + index * HOUR, "100") for index in range(7)),
        "15m": tuple(
            candle("15m", EPOCH + index * timedelta(minutes=15), "100")
            for index in range(25)
        ),
        "5m": tuple(
            candle("5m", EPOCH + index * timedelta(minutes=5), "100")
            for index in range(73)
        ),
    }
    return HierarchySnapshot(
        hierarchy=hierarchy,
        exchange=EXCHANGE,
        symbol=SYMBOL,
        decision_time=decision_time,
        boundary=resolve_decision_boundary(decision_time, hierarchy, candles),
        context=context,
        setup=setup,
        confirmation=confirmation,
        execution=execution,
        alignment=alignment,
        counter_trend=counter_trend,
        decision=decision,
        status=status,
        reasons=reasons,
        strategy_versions=(("multi_timeframe_hierarchy_rules", HIERARCHY_RULES_VERSION),),
        waiting_for=waiting_for,
        invalidated_if=invalidated_if,
    )


def test_snapshot_identity_is_deterministic_and_excludes_recorded_at():
    first = _snapshot()
    second = _snapshot()
    assert first.identity() == second.identity()
    payload = first.to_json_dict()
    assert "recorded_at" not in payload
    assert payload["hierarchy"]["rules_version"] == HIERARCHY_RULES_VERSION
    assert payload["decision"] == first.decision.value
    assert payload["hierarchy"]["fingerprint"] == first.hierarchy.fingerprint()
    # A different decision content at the same boundary is a different identity.
    other = _snapshot(decision_time=EPOCH + 7 * HOUR)
    assert other.identity() != first.identity()


def test_snapshot_identity_isolates_hierarchy_versions():
    base = _snapshot()
    other_hierarchy = TimeframeHierarchy(
        steps=(
            TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="1d"),
            TimeframeStep(role=TimeframeRole.SETUP, timeframe="4h"),
            TimeframeStep(role=TimeframeRole.CONFIRMATION, timeframe="1h"),
            TimeframeStep(role=TimeframeRole.EXECUTION, timeframe="15m"),
        )
    )
    changed = _snapshot(hierarchy=other_hierarchy)
    assert changed.identity() != base.identity()
    assert changed.hierarchy_fingerprint != base.hierarchy_fingerprint


def test_ledger_observation_round_trip_and_conflict_detection(tmp_path):
    from sqlalchemy import create_engine as _create_engine

    engine = _create_engine(f"sqlite:///{tmp_path / 'ledger.sqlite3'}")
    Base.metadata.create_all(engine, tables=[_hierarchy_tables.ForwardHierarchyObservationRow.__table__])
    repository = HierarchyLedgerRepository(engine)
    snapshot = _snapshot()
    observation = HierarchyObservation.from_snapshot(snapshot, recorded_at=EPOCH)
    stored, created = repository.record_observation(observation)
    assert created is True
    assert stored.observation_id == observation.observation_id
    assert stored.rules_version == HIERARCHY_LEDGER_RULES_VERSION
    # Recording the same deterministic evaluation again is a verified no-op.
    again, created_again = repository.record_observation(
        HierarchyObservation.from_snapshot(snapshot, recorded_at=EPOCH + 99 * HOUR)
    )
    assert created_again is False
    assert again.observation_id == observation.observation_id
    assert repository.counts(exchange=EXCHANGE, symbol=SYMBOL)["observations"] == 1
    # A different evaluation under the same identity is refused loudly.
    from dataclasses import replace as _replace

    tampered = _replace(observation, decision="plannable")
    assert tampered.observation_id == observation.observation_id
    with pytest.raises(HierarchyConflict):
        repository.record_observation(tampered)
    engine.dispose()


# ---------------------------------------------------------------------------
# Grounded plain-English explanation and ladder payload
# ---------------------------------------------------------------------------


def test_explain_hierarchy_is_grounded_plain_english():
    from trading_assistant.multi_timeframe.ladder import overall_phrase

    snapshot = _snapshot()
    explanation = explain_hierarchy(snapshot)
    assert explanation["headline"] == overall_phrase(snapshot.decision)
    assert len(explanation["ladder"]) == 4
    assert [step["label"] for step in explanation["ladder"]] == [
        "4H CONTEXT",
        "1H SETUP",
        "15M CONFIRMATION",
        "5M EXECUTION",
    ]
    # Every layer sentence is present and no internal enum name leaks.
    text = " ".join(explanation["sentences"])
    for enum_name in ("awaiting_execution", "AWAITING_EXECUTION", "bullish_structure"):
        assert enum_name not in text
    assert "4H structure is" in text
    assert "1H breakout / retest setup (bullish) is qualified" in text
    assert explanation["waiting_for_text"].startswith("Waiting for:")
    assert explanation["invalidated_if_text"].startswith("Invalidated if:")


def test_explain_hierarchy_flags_counter_trend_explicitly():
    snapshot = _snapshot(
        context_kwargs={"regime": ContextRegime.BEARISH_STRUCTURE},
        setup_kwargs={"direction": "bullish"},
    )
    explanation = explain_hierarchy(snapshot)
    assert snapshot.counter_trend is True
    assert any("counter to the 4H structure" in s for s in explanation["sentences"])


def test_ladder_payload_uses_plain_english_and_carries_the_snapshot():
    snapshot = _snapshot()
    payload = ladder_payload(snapshot)
    assert payload["available"] is True
    assert payload["decision"] == snapshot.decision.value
    assert payload["overall"] == "WAITING FOR ENTRY TIMING"
    assert len(payload["ladder"]) == 4
    for row in payload["ladder"]:
        assert row["label"] in ("4H CONTEXT", "1H SETUP", "15M CONFIRMATION", "5M EXECUTION")
        # No internal enum names in the displayed states.
        assert row["state"] not in ("awaiting_execution", "ALIGNED", "CONFIRMING")
    assert payload["snapshot"]["decision_time"] == snapshot.to_json_dict()["decision_time"]
    assert payload["limitations"]


# ---------------------------------------------------------------------------
# Migration: additive, backwards-compatible, never drops recorded history
# ---------------------------------------------------------------------------


def test_hierarchy_migration_is_additive_and_refuses_to_drop_rows(tmp_path):
    database_path = tmp_path / "hierarchy_migration.sqlite3"
    url = f"sqlite:///{database_path}"
    config = Config(str(pytest.Path(__file__).resolve().parents[1] / "alembic.ini")) if hasattr(pytest, "Path") else None
    from pathlib import Path

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url

    command.upgrade(config, "0004_forward_testing")
    engine = create_database_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ohlcv_candles "
                "(exchange, symbol, timeframe, timestamp, open, high, low, close, volume) "
                "VALUES ('mock-exchange', 'BTC/USDT', '5m', '1970-01-01 00:00:00.000000', "
                "'10', '11', '9', '10.5', '3')"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    migrated = create_database_engine(url)
    with migrated.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        candles = connection.execute(text("SELECT COUNT(*) FROM ohlcv_candles")).scalar_one()
    table_names = set(inspect(migrated).get_table_names())
    migrated.dispose()

    assert revision == "0005_multi_timeframe_hierarchy"
    assert "forward_hierarchy_observations" in table_names
    assert candles == 1  # existing market history untouched

    # Record one hierarchy observation, then prove the downgrade refuses.
    engine = create_database_engine(url)
    repository = HierarchyLedgerRepository(engine)
    repository.record_observation(
        HierarchyObservation.from_snapshot(_snapshot(), recorded_at=EPOCH)
    )
    engine.dispose()
    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        command.downgrade(config, "0004_forward_testing")

    # And the SQLite immutability triggers really block UPDATE and DELETE.
    engine = create_database_engine(url)
    with engine.begin() as connection:
        with pytest.raises(Exception, match="append-only"):
            connection.execute(
                text(
                    "UPDATE forward_hierarchy_observations SET decision = 'plannable'"
                )
            )
        with pytest.raises(Exception, match="append-only"):
            connection.execute(text("DELETE FROM forward_hierarchy_observations"))
    observations = HierarchyLedgerRepository(engine).counts(
        exchange=EXCHANGE, symbol=SYMBOL
    )
    engine.dispose()
    assert observations["observations"] == 1
