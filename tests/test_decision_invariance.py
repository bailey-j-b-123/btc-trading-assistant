"""Decision-invariance regression tests for history extension (the backfill precondition).

SYNTHETIC: every candle here comes from a deterministic seeded random walk written in the same
decimal-string shape Binance returns. It is not real market data and proves nothing about live
BRAIN decisions on Binance.

What is compared is the decision path itself: ``DashboardService._evaluate`` (the bounded
replay the dashboard and planning use) followed by ``plan_trade`` for each qualified setup.
Identifiers that are hashes of their evidence are excluded from the comparison, because they
legitimately differ when the evidence window differs; the decision fields are compared exactly.

Known result (documented in docs/decision-invariance.md): the decision is NOT invariant when
older candles are appended. A reference level formed before the stored history starts can
create live setups that a shorter history does not see. The strict xfail below records that
defect so it cannot be forgotten, and it fails loudly once the defect is fixed.

The reference-provenance gate (docs/reference-provenance.md) bounds the *other* direction of
that risk: a plan is withheld whenever a live setup's own level evidence reaches the start of
stored history. These tests therefore also compare plan eligibility and identities — not only
setup lists — and assert the safety property the gate does provide: whatever a shallow record
does plan, it plans on levels its own history accounts for. Under-reporting near the stored
start is a limit of the record and stays an open defect.

Runtime: about two minutes (measured 58 s before the provenance pair was added), dominated by
full 1,000-candle replays.
"""

from __future__ import annotations

import random
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from web_fixtures import insert_candles, make_settings, migrated_engine

from trading_assistant.market_data.types import Candle
from trading_assistant.setup_qualification.provenance import assess_reference_provenance
from trading_assistant.setup_qualification.models import SetupState
from trading_assistant.trade_planning.planner import plan_trade
from trading_assistant.web.dashboard_service import DashboardService
from trading_assistant.web.state import AppState

EPOCH = datetime(2025, 1, 1, tzinfo=UTC)
SERIES_LENGTH = 1000
FULL_START = 0
RECENT_START = 500  # the stored history begins here in the "recent" state
DEEP_START = 300  # a shorter-than-full history used by the known-defect check
BOUNDARY_WARM = 940  # the stored history starts 440 closes earlier: decision identical
BOUNDARY_SHALLOW = 820  # the stored history starts 520 closes earlier: decision changes


def synthetic_series(length: int = SERIES_LENGTH, seed: int = 7) -> tuple[Candle, ...]:
    """SYNTHETIC seeded walk in Binance-style decimal strings (not real data)."""

    rnd = random.Random(seed)
    close = Decimal("60000.00")
    candles = []
    for index in range(length):
        open_ = close
        close = (open_ * (Decimal(1) + Decimal(str(round(rnd.gauss(0, 0.004), 6))))).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
        high = max(open_, close) * (Decimal(1) + Decimal(str(round(abs(rnd.gauss(0, 0.002)), 6))))
        low = min(open_, close) * (Decimal(1) - Decimal(str(round(abs(rnd.gauss(0, 0.002)), 6))))
        candles.append(
            Candle(
                exchange="binance",
                symbol="BTC/USDT",
                timeframe="1h",
                timestamp=EPOCH + timedelta(hours=index),
                open=open_.quantize(Decimal("0.01")),
                high=high.quantize(Decimal("0.01")),
                low=low.quantize(Decimal("0.01")),
                close=close,
                volume=Decimal(str(round(rnd.uniform(5, 50), 4))),
            )
        )
    return tuple(candles)


def _decision(service: DashboardService, boundary_index: int) -> dict:
    """The decision fields at one boundary: snapshot, every setup, and each qualified plan."""

    as_of = EPOCH + timedelta(hours=boundary_index)
    snapshot, frame, _frames = service._evaluate(  # noqa: SLF001 - the exact dashboard path
        exchange="binance", symbol="BTC/USDT", timeframe="1h", as_of=as_of
    )
    setups = []
    for setup in snapshot.setups:
        entry = {
            "id": setup.id,
            "family": setup.family.value,
            "direction": setup.direction,
            "state": setup.state.value,
            "terminal_reason": setup.terminal_reason,
        }
        if setup.state.value == "QUALIFIED":
            plan = plan_trade(snapshot=snapshot, frame=frame, setup_id=setup.id)
            entry["plan_state"] = plan.state.value
        setups.append(entry)
    return {"snapshot_state": snapshot.state.value, "setups": setups}


def _decisions(candles, boundaries: tuple[int, ...], label: str) -> dict[int, dict]:
    engine, url = migrated_engine(Path(tempfile.mkdtemp()), name=f"{label}.sqlite3")
    try:
        insert_candles(engine, candles)
        state = AppState(engine, settings=make_settings(url), clock=lambda: EPOCH + timedelta(hours=SERIES_LENGTH))
        service = DashboardService(state)
        return {boundary: _decision(service, boundary) for boundary in boundaries}
    finally:
        engine.dispose()


def _provenance_record(candles, boundary_index: int, label: str) -> dict:
    """Live setups, plan eligibility, and the reference-provenance verdict at one close.

    ``candles`` is the whole stored series for that state, so the stored start the
    gate judges against is exactly the start the runner and dashboard would see.
    """

    engine, url = migrated_engine(Path(tempfile.mkdtemp()), name=f"{label}.sqlite3")
    try:
        insert_candles(engine, candles)
        state = AppState(
            engine,
            settings=make_settings(url),
            clock=lambda: EPOCH + timedelta(hours=SERIES_LENGTH),
        )
        service = DashboardService(state)
        as_of = EPOCH + timedelta(hours=boundary_index)
        snapshot, frame, _frames = service._evaluate(  # noqa: SLF001 - the exact dashboard path
            exchange="binance", symbol="BTC/USDT", timeframe="1h", as_of=as_of
        )
        assessment = assess_reference_provenance(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            decision_time=as_of,
            frame=frame,
            snapshot=snapshot,
            first_stored_open=candles[0].timestamp,
        )
        plans: dict[str, dict] = {}
        for setup in snapshot.setups:
            if setup.state is not SetupState.QUALIFIED:
                continue
            plan = plan_trade(snapshot=snapshot, frame=frame, setup_id=setup.id)
            entry = assessment.entry_for(setup.id)
            plans[setup.id] = {
                "plan_state": plan.state.value,
                "provenance": None if entry is None else entry.status.value,
                "blocked": False if entry is None else entry.blocked,
            }
        return {
            "live_setup_ids": frozenset(
                setup.id
                for setup in snapshot.setups
                if setup.state in (SetupState.WATCH, SetupState.QUALIFIED)
            ),
            "setup_states": {setup.id: setup.state.value for setup in snapshot.setups},
            "plans": plans,
            "provenance_status": assessment.status.value,
            "blocked": frozenset(assessment.blocked_setup_ids),
            "first_stored_open": candles[0].timestamp,
        }
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def series() -> tuple[Candle, ...]:
    return synthetic_series()


@pytest.fixture(scope="module")
def provenance_at_shallow_boundary(series) -> dict[str, dict]:
    """Full history and two shallow starts at the boundary docs show as differing."""

    return {
        "full": _provenance_record(series, BOUNDARY_SHALLOW, "prov-full"),
        "start300": _provenance_record(series[DEEP_START:], BOUNDARY_SHALLOW, "prov-deep"),
        "start500": _provenance_record(series[RECENT_START:], BOUNDARY_SHALLOW, "prov-recent"),
    }


@pytest.fixture(scope="module")
def full_history(series) -> dict[int, dict]:
    return _decisions(series, (BOUNDARY_SHALLOW, BOUNDARY_WARM), "full")


def test_decision_is_identical_when_stored_history_starts_well_before_the_boundary(series, full_history):
    recent = _decisions(series[RECENT_START:], (BOUNDARY_WARM,), "recent")
    assert recent[BOUNDARY_WARM] == full_history[BOUNDARY_WARM]


def test_candles_after_as_of_never_change_the_decision_at_as_of(series):
    """No look-ahead: the decision reads nothing newer than its boundary."""

    truncated = series[RECENT_START : BOUNDARY_WARM + 1]  # ends one candle after the boundary opens
    full_tail = series[RECENT_START:]
    at_boundary = _decisions(truncated, (BOUNDARY_WARM,), "truncated")
    with_future = _decisions(full_tail, (BOUNDARY_WARM,), "future")
    assert at_boundary[BOUNDARY_WARM] == with_future[BOUNDARY_WARM]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known defect: a shorter stored history misses live setups whose reference level "
        "formed before the stored start. The 131-close depth gate does not cover reference age, "
        "and the reference-provenance gate bounds what such a record may *plan*, not what it can "
        "see — so under-reporting near the stored start persists. See docs/decision-invariance.md "
        "and docs/reference-provenance.md. Remove this marker when the defect is fixed."
    ),
)
def test_a_shallow_stored_start_does_not_change_live_setups_at_a_later_boundary(series, full_history):
    shallow = _decisions(series[DEEP_START:], (BOUNDARY_SHALLOW,), "shallow")
    assert shallow[BOUNDARY_SHALLOW] == full_history[BOUNDARY_SHALLOW]


def test_plan_eligibility_shrinks_with_a_shallow_start_without_inventing_a_setup(
    provenance_at_shallow_boundary,
) -> None:
    """Characterisation of the defect, in terms of what could be traded.

    The audit of PR #42 asked for plan eligibility and identities to be compared, not
    only setup lists: a missed setup only matters if it would have been a plan. This
    pins the exact shape of the damage — a shorter record is a strict subset, shared
    setups keep their state, and no setup exists that full history does not have.
    """

    full = provenance_at_shallow_boundary["full"]
    for start in ("start300", "start500"):
        shallow = provenance_at_shallow_boundary[start]
        assert shallow["live_setup_ids"] < full["live_setup_ids"]
        shared = shallow["live_setup_ids"] & full["live_setup_ids"]
        assert shared, "a shallow record must still see most of the market"
        assert all(
            shallow["setup_states"][setup_id] == full["setup_states"][setup_id]
            for setup_id in shared
        )
        # No planned setup appears in the shallow record that full history does not
        # also plan: the shorter record can under-report, never over-report.
        assert set(shallow["plans"]) <= set(full["plans"])
        assert len(shallow["plans"]) < len(full["plans"])


def test_a_shallow_record_never_plans_on_a_level_it_cannot_account_for(
    provenance_at_shallow_boundary,
) -> None:
    """The safety property the reference-provenance gate guarantees at this boundary.

    Whatever a short record does plan, it plans on evidence its own stored history
    accounts for. A verdict that cannot be established would withhold the plan, so
    an accepted plan plus a blocked or unverifiable setup is a contradiction, and
    the assertion failing here means the gate stopped covering the decision.
    """

    for state in provenance_at_shallow_boundary.values():
        assert state["blocked"] == frozenset()
        assert state["provenance_status"] == "COMPLETE"
        planned = {
            setup_id
            for setup_id, entry in state["plans"].items()
            if entry["plan_state"] == "PLANNABLE"
        }
        for setup_id in planned:
            assert state["plans"][setup_id]["provenance"] == "COMPLETE"
            assert state["plans"][setup_id]["blocked"] is False
