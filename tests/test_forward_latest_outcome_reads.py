"""Newest-outcome reads preserve facts while avoiding obsolete payload work."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event, select

from forward_fixtures import (
    EPOCH, EXCHANGE, INTERVAL, QUALIFYING_BOUNDARY, SYMBOL, TIMEFRAME,
    bar, clock_at, labelled_series, make_harness, make_service,
)
from test_forward_no_trade_reason import pass_on
from trading_assistant.forward_testing import repository as repository_module
from trading_assistant.forward_testing.tables import (
    ForwardCycleRow, ForwardObservationRow, ForwardPaperOutcomeRow, ForwardPaperPlanRow,
)
from trading_assistant.journaling.types import OutcomeStatus

SCOPE = {"exchange": EXCHANGE, "symbol": SYMBOL, "timeframe": TIMEFRAME}


def _plan_harness(request):
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    request.addfinalizer(harness.engine.dispose)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=True).paper_plans_created == 1
    return harness


@pytest.fixture(scope="module")
def histories(request):
    """Two naturally qualified plans, each observed under stock rules 20 times."""
    harness = _plan_harness(request)
    ledger = harness.service.ledger
    first = ledger.paper_plans(**SCOPE)[0]
    assert first.observation_horizon_candles == 20
    source = labelled_series()
    for index in range(21, 41):
        # Enter, remain open, then genuinely stop within the unchanged horizon.
        candle = (
            bar(index, 120, high=126, low=116)
            if index == 40
            else bar(index, 126, high=127, low=123 if index == 21 else 125)
        )
        source += (candle,)
        harness.source.set_candles(source)
        harness.advance_to(candle.timestamp + INTERVAL)
        harness.run(refresh_market_data=True)
    first_chain = ledger.outcome_versions(first.paper_plan_id)
    assert len(first_chain) == 20
    assert first_chain[-1].observation.status is OutcomeStatus.STOPPED

    # The settled trade frees the instrument's existing slot for the 15m plan.
    _, boundary, result = pass_on(harness, "15m")
    assert result.paper_plans_created == 1
    second = ledger.paper_plans(**{**SCOPE, "timeframe": "15m"})[0]
    assert second.observation_horizon_candles == 20
    clock = {"t": clock_at(boundary)}
    service = make_service(
        harness.engine, harness.settings, None,
        clock=lambda: clock["t"], ledger_start=boundary,
    )
    step = timedelta(minutes=15)
    for index in range(21, 41):
        candle = replace(
            bar(index, 126, high=127, low=123 if index == 21 else 125),
            timeframe="15m", timestamp=EPOCH + index * step,
        )
        harness.store((candle,))
        clock["t"] = clock_at(candle.timestamp + step)
        service.run_once(timeframe="15m", refresh_market_data=False)
    second_chain = ledger.outcome_versions(second.paper_plan_id)
    assert len(second_chain) == 20
    return harness, {TIMEFRAME: first_chain, "15m": second_chain}


@pytest.mark.parametrize("timeframe", [None, TIMEFRAME, "15m"])
def test_latest_read_decodes_only_the_newest_payload_per_matching_plan(histories, timeframe):
    harness, chains = histories
    expected = tuple(sorted(
        (chain[-1] for scope, chain in chains.items() if timeframe is None or scope == timeframe),
        key=lambda item: item.paper_plan_id,
    ))
    original = repository_module._outcome_from_row
    with patch.object(repository_module, "_outcome_from_row", wraps=original) as decoder:
        result = harness.service.ledger.latest_outcomes(**{**SCOPE, "timeframe": timeframe})
    assert result == expected
    assert decoder.call_count == len(expected), (
        "latest-only reads must not materialize and decode superseded payloads: "
        f"decoded {decoder.call_count} for {len(expected)} returned plans"
    )


def _frozen_rows(engine):
    frozen = {}
    with engine.connect() as connection:
        for model in (ForwardCycleRow, ForwardObservationRow, ForwardPaperPlanRow, ForwardPaperOutcomeRow):
            table = model.__table__
            primary = next(iter(table.primary_key.columns))
            frozen[table.name] = tuple(
                tuple(row.items())
                for row in connection.execute(select(table).order_by(primary)).mappings()
            )
    return frozen


def test_latest_scope_order_and_all_frozen_history_remain_unchanged(histories):
    harness, chains = histories
    ledger = harness.service.ledger
    before = _frozen_rows(harness.engine)
    statements = []

    def selects_only(connection, cursor, statement, parameters, context, executemany):
        assert statement.lstrip().upper().startswith("SELECT"), statement
        statements.append(statement)

    event.listen(harness.engine, "before_cursor_execute", selects_only)
    try:
        for timeframe in (None, TIMEFRAME, "15m", "4h"):
            expected = tuple(sorted(
                (chain[-1] for scope, chain in chains.items() if timeframe is None or scope == timeframe),
                key=lambda item: item.paper_plan_id,
            ))
            assert ledger.latest_outcomes(**{**SCOPE, "timeframe": timeframe}) == expected
        for empty_scope in ({**SCOPE, "exchange": "offline-other"}, {**SCOPE, "symbol": "ETH/USDT"}):
            assert ledger.latest_outcomes(**empty_scope) == ()
        for chain in chains.values():
            assert ledger.outcome_versions(chain[0].paper_plan_id) == chain
            assert all(ledger.get_outcome(item.outcome_id) == item for item in chain)
    finally:
        event.remove(harness.engine, "before_cursor_execute", selects_only)
    assert statements
    assert _frozen_rows(harness.engine) == before
    with harness.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_frozen_plan_without_observation_outcome_is_not_returned(request):
    harness = _plan_harness(request)
    ledger = harness.service.ledger
    assert len(ledger.paper_plans(**SCOPE)) == 1
    assert ledger.latest_outcomes(**SCOPE) == ()
    assert ledger.latest_outcomes(**{**SCOPE, "timeframe": None}) == ()


def test_latest_version_is_sequence_not_shared_recording_time(request):
    harness = _plan_harness(request)
    ledger = harness.service.ledger
    plan = ledger.paper_plans(**SCOPE)[0]
    boundary = QUALIFYING_BOUNDARY + INTERVAL
    harness.advance_to(boundary)
    arguments = {
        **SCOPE, "boundary": boundary, "interval": INTERVAL,
        "recorded_at": harness.clock["t"],
    }
    # The real observer sees the unavailable closed entry candle as a gap.
    assert harness.service._update_outcomes(**arguments) == 1
    first = ledger.outcome_versions(plan.paper_plan_id)[0]
    assert first.observation.status is OutcomeStatus.INCOMPLETE_DATA
    harness.store((bar(21, 126, low=123),))
    # Same frozen clock and cutoff, now with genuine delayed entry evidence.
    assert harness.service._update_outcomes(**arguments) == 1
    versions = ledger.outcome_versions(plan.paper_plan_id)
    assert [item.sequence for item in versions] == [1, 2]
    assert versions[0].recorded_at == versions[1].recorded_at
    assert versions[0].observed_through == versions[1].observed_through
    assert versions[1].observation.entry_reached
    assert ledger.latest_outcomes(**SCOPE) == (versions[1],)
    assert ledger.get_outcome(first.outcome_id) == first
