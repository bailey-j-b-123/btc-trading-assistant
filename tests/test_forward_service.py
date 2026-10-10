"""Step 12 forward-testing service: deterministic offline tests.

Everything here runs against temporary SQLite databases, synthetic labelled
candles, a fake public OHLCV source, and injected clocks. No test touches the
network, an exchange, an account, or real market data, and nothing in this file
asserts anything about profitability.

Covered acceptance criteria (Step 12 M): closed-candles-only, unfinished candle
ignored, one decision per close, chronological catch-up, restart idempotency,
duplicate candle/decision/paper-plan prevention, future candles cannot alter a
recorded decision, stale data cannot produce a fresh conclusion, gapped data,
API/network failure handling, WATCH/NO_SETUP/QUALIFIED-without-a-plannable-plan
create no paper trade, PLANNABLE creates a paper observation, entry not reached,
invalidated before entry, stop, targets, stopped after targets, same-candle
ambiguity, incomplete data, unresolved/open, long and short, multiple families,
version separation, immutability, Step 7/Step 11 data unmodified, raw market
history preserved, and the absence of any order/account surface.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from forward_fixtures import (
    EPOCH,
    EXCHANGE,
    INTERVAL,
    SYMBOL,
    TIMEFRAME,
    FakeExchange,
    Harness,
    RollingWindowExchange,
    bar,
    clock_at,
    extend,
    labelled_series,
    make_harness,
    mirrored,
    sweep_reversal_series,
    two_target_series,
    watch_only_series,
)
from trading_assistant.forward_testing import (
    CycleStatus,
    DataHealth,
    ForwardParameters,
    HeartbeatStatus,
    RunnerSettings,
    VersionSeparation,
)
from trading_assistant.forward_testing.parameters import fingerprint
from trading_assistant.journaling.types import OutcomeStatus
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import datetime_to_milliseconds
from trading_assistant.setup_qualification import (
    QualificationParameters,
    QualificationService,
    SetupState,
)
from trading_assistant.trade_planning import PlanningParameters

QUALIFYING_BOUNDARY = EPOCH + 21 * INTERVAL
LONG_ENTRY = D("124")
LONG_STOP = D("117")
LONG_TARGET = D("138")
HORIZON = 20


def harness_with_a_paper_plan() -> Harness:
    """The labelled series, processed at its qualifying close."""

    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.status is HeartbeatStatus.PROCESSED
    return harness


def plans_with_levels(harness: Harness):
    plans = harness.plans()
    assert plans
    for plan in plans:
        assert plan.entry == LONG_ENTRY
        assert plan.stop == LONG_STOP
        assert plan.targets == (LONG_TARGET,)
    return plans


def latest_statuses(harness: Harness) -> list[str]:
    return sorted(outcome.observation.status.value for outcome in harness.latest_outcomes())


# ----------------------------------------------------------------------
# A/B/C: closed candles, one decision per close, the immutable ledger
# ----------------------------------------------------------------------


def test_one_decision_per_closed_candle() -> None:
    harness = harness_with_a_paper_plan()
    cycles = harness.cycles()
    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.status is CycleStatus.COMPLETE
    assert cycle.as_of == QUALIFYING_BOUNDARY
    assert cycle.candle_open_time == QUALIFYING_BOUNDARY - INTERVAL
    assert cycle.data_health is DataHealth.CURRENT
    assert cycle.snapshot_state is not None
    assert cycle.snapshot_id is not None


def test_an_unfinished_candle_is_never_analysed() -> None:
    harness = harness_with_a_paper_plan()
    # A candle that has not closed yet, and a candle that closes in the future.
    unfinished = bar(21, 150, high=160, low=140)
    harness.store((unfinished,))
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.status in {HeartbeatStatus.IDLE, HeartbeatStatus.NO_DATA}
    assert len(harness.cycles()) == 1
    assert harness.cycles()[0].as_of == QUALIFYING_BOUNDARY
    # The unfinished candle cannot have influenced the recorded decisions.
    for observation in harness.observations():
        assert observation.candle_open_time < unfinished.timestamp
    for plan in harness.plans():
        assert plan.plan_as_of == QUALIFYING_BOUNDARY


def test_public_refresh_keeps_forming_candle_out_of_step12_analysis() -> None:
    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY,
        backfill_start=EPOCH,
        store_series=False,
    )
    unfinished = bar(21, 150, high=160, low=140)
    harness.source.set_candles(labelled_series() + (unfinished,))
    harness.advance_to(QUALIFYING_BOUNDARY)

    result = harness.run(refresh_market_data=True)

    assert result.market_data_error is None
    stored = harness.candles().candles
    assert len(stored) == 21
    assert all(candle.timestamp < unfinished.timestamp for candle in stored)
    cycles = harness.cycles()
    assert len(cycles) == 1
    assert cycles[0].as_of == QUALIFYING_BOUNDARY
    assert cycles[0].candle_open_time == unfinished.timestamp - INTERVAL
    assert all(
        observation.candle_open_time < unfinished.timestamp
        for observation in harness.observations()
    )


def test_stale_feed_cannot_produce_a_fresh_conclusion() -> None:
    harness = harness_with_a_paper_plan()
    harness.advance_to(QUALIFYING_BOUNDARY + 6 * INTERVAL)
    result = harness.run(refresh_market_data=False)
    # Missed closes are recorded explicitly as missing-candle cycles: nothing is
    # silently skipped, and no missing close becomes a fresh conclusion.
    assert result.status is HeartbeatStatus.PROCESSED
    missing = [cycle for cycle in harness.cycles() if cycle.status is CycleStatus.MISSING_CANDLE]
    assert len(missing) == 6
    assert all(cycle.data_health is DataHealth.STALE for cycle in missing)
    assert all(cycle.snapshot_id is None for cycle in missing)
    assert all(cycle.snapshot_state is None for cycle in missing)
    assert all(cycle.observation_count == 0 for cycle in missing)
    assert all(cycle.notes for cycle in missing)
    # No observation and no paper plan came from a stale window.
    assert all(
        item.as_of == QUALIFYING_BOUNDARY for item in harness.observations()
    )
    # One unresolved paper trade per instrument: the labelled series qualifies
    # two setups at this close and only the first is paper-traded.
    assert len(harness.plans()) == 1
    status = harness.service.status()
    assert status["market_data"]["data_health"] == DataHealth.STALE.value
    assert status["market_data"]["staleness_intervals"] >= 5
    # The stale verdict is explicit rather than hidden behind the old rows, and
    # the missing closes stay pending instead of being silently skipped.
    assert status["market_data"]["data_health_detail"]
    # The six closes stay pending: they were recorded as missing-candle cycles and
    # no candle ever arrived for them, so they are still awaiting their own data.
    assert status["sample"]["pending_catch_up_boundaries"] == 6
    # Nothing was left over by the catch-up cap in this pass.
    assert result.pending_boundaries == 0


def test_a_recovered_candle_completes_a_previously_missing_close() -> None:
    harness = harness_with_a_paper_plan()
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    first = harness.run(refresh_market_data=False)
    assert first.cycles_recorded == 1
    missing = [
        cycle for cycle in harness.cycles() if cycle.status is CycleStatus.MISSING_CANDLE
    ]
    assert len(missing) == 1
    assert harness.plans() and harness.observations()
    # The candle arrives after the fact: the same close is re-analysed and the
    # recovered conclusion is recorded as its own row, without touching the
    # missing-candle row that already stated the gap.
    recovered = harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    assert recovered.cycles_recorded == 1
    at_boundary = [
        cycle
        for cycle in harness.cycles()
        if cycle.as_of == QUALIFYING_BOUNDARY + INTERVAL
    ]
    assert {cycle.status for cycle in at_boundary} == {
        CycleStatus.MISSING_CANDLE,
        CycleStatus.COMPLETE,
    }
    assert missing[0] in at_boundary


def test_restart_is_idempotent_and_records_nothing_twice() -> None:
    harness = harness_with_a_paper_plan()
    before = harness.counts()
    # A brand-new service object over the same database is a restart.
    restarted = Harness(
        engine=harness.engine,
        settings=harness.settings,
        clock=harness.clock,
        service=type(harness.service)(
            harness.engine,
            settings=harness.settings,
            clock=lambda: harness.clock["t"],
            parameters=harness.service.parameters,
            market_data_service=harness.source,
            ledger_start=QUALIFYING_BOUNDARY,
        ),
    )
    result = restarted.run(refresh_market_data=False)
    assert result.status in {HeartbeatStatus.IDLE, HeartbeatStatus.NO_DATA}
    assert restarted.counts() == before
    # Same wall-clock/other clock: identical deterministic identities.
    assert [cycle.cycle_id for cycle in restarted.cycles()] == [
        cycle.cycle_id for cycle in harness.cycles()
    ]
    assert [item.observation_id for item in restarted.observations()] == [
        item.observation_id for item in harness.observations()
    ]


def test_same_close_recorded_live_or_during_catch_up_is_identical() -> None:
    """A close processed live and the same close caught up later cannot differ."""

    live = harness_with_a_paper_plan()
    later_series = labelled_series() + (
        bar(21, 126, low=123),
        bar(22, 130, low=124),
        bar(23, 132, low=125),
    )
    catch_up = make_harness(series=later_series, ledger_start=QUALIFYING_BOUNDARY)
    catch_up.advance_to(QUALIFYING_BOUNDARY + 3 * INTERVAL)
    assert catch_up.run(refresh_market_data=False).cycles_recorded == 4
    live_cycle = live.cycles()[0]
    caught_cycle = next(
        cycle for cycle in catch_up.cycles() if cycle.as_of == QUALIFYING_BOUNDARY
    )
    assert caught_cycle.cycle_id == live_cycle.cycle_id
    assert caught_cycle.snapshot_id == live_cycle.snapshot_id
    assert caught_cycle.snapshot_json == live_cycle.snapshot_json
    at_boundary = [
        item for item in catch_up.observations() if item.as_of == QUALIFYING_BOUNDARY
    ]
    assert [item.observation_id for item in at_boundary] == [
        item.observation_id for item in live.observations()
    ]
    assert [item.setup_json for item in at_boundary] == [
        item.setup_json for item in live.observations()
    ]
    # Freshness and the recording instant are recorded metadata; the decision
    # identity is the close plus the deterministic content, so a caught-up row is
    # the same decision.
    caught_health = {cycle.data_health for cycle in catch_up.cycles()}
    assert DataHealth.HISTORICAL in caught_health
    assert DataHealth.CURRENT in caught_health
    # The paper plan created at that close is the same frozen plan either way.
    live_ids = {plan.paper_plan_id for plan in live.plans()}
    assert live_ids <= {plan.paper_plan_id for plan in catch_up.plans()}
    metadata = {"data_health", "recorded_at"}
    for stored in catch_up.plans():
        if stored.paper_plan_id in live_ids:
            original = next(
                plan for plan in live.plans() if plan.paper_plan_id == stored.paper_plan_id
            )
            assert {
                key: value
                for key, value in stored.to_json_dict().items()
                if key not in metadata
            } == {
                key: value
                for key, value in original.to_json_dict().items()
                if key not in metadata
            }


def test_catch_up_processes_missed_closes_chronologically_and_once() -> None:
    series = labelled_series() + (
        bar(21, 126, low=123),
        bar(22, 130, low=124),
        bar(23, 132, low=125),
    )
    harness = make_harness(series=series, ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY + 3 * INTERVAL)
    result = harness.run(refresh_market_data=False)
    assert result.status is HeartbeatStatus.PROCESSED
    assert result.processed_boundaries == tuple(
        QUALIFYING_BOUNDARY + index * INTERVAL for index in range(4)
    )
    assert result.pending_boundaries == 0
    cycles = harness.cycles()
    assert [cycle.as_of for cycle in cycles] == [
        QUALIFYING_BOUNDARY + index * INTERVAL for index in range(4)
    ]
    assert len({cycle.cycle_id for cycle in cycles}) == 4
    # Second pass finds nothing new.
    assert harness.run(refresh_market_data=False).cycles_recorded == 0
    assert len(harness.cycles()) == 4


def test_catch_up_is_capped_and_the_remainder_is_reported() -> None:
    series = labelled_series() + tuple(
        bar(index, 128, low=124) for index in range(21, 25)
    )
    capped = ForwardParameters(max_catch_up_candles=2)
    harness = make_harness(
        series=series, ledger_start=QUALIFYING_BOUNDARY, parameters=capped
    )
    harness.advance_to(QUALIFYING_BOUNDARY + 4 * INTERVAL)
    first = harness.run(refresh_market_data=False)
    assert first.pending_boundaries > 0
    assert "still pending" in first.detail
    assert first.cycles_recorded == 2
    passes = [first]
    while passes[-1].pending_boundaries:
        passes.append(harness.run(refresh_market_data=False))
    boundaries = [cycle.as_of for cycle in harness.cycles()]
    assert boundaries == sorted(boundaries)
    assert len(boundaries) == len(set(boundaries)) == 5
    assert all(result.cycles_recorded <= 2 for result in passes)


def test_future_candles_cannot_alter_a_recorded_decision() -> None:
    harness = harness_with_a_paper_plan()
    cycle_before = harness.cycles()[0].to_json_dict()
    observations_before = [item.to_json_dict() for item in harness.observations()]
    plans_before = [plan.to_json_dict() for plan in harness.plans()]
    # Later candles move the market violently: the recorded decisions stay.
    harness.step((bar(21, 150, high=151, low=123),), refresh_market_data=False)
    harness.step((bar(22, 100, high=152, low=99),), refresh_market_data=False)
    assert harness.cycles()[0].to_json_dict() == cycle_before
    assert [item.to_json_dict() for item in harness.observations()[:6]] == (
        observations_before
    )
    assert [plan.to_json_dict() for plan in harness.plans()] == plans_before


def test_duplicate_candles_and_repeated_passes_never_duplicate_decisions() -> None:
    series = labelled_series()
    harness = make_harness(series=series, ledger_start=QUALIFYING_BOUNDARY)
    harness.store(series)  # store the same closed candles a second time
    harness.advance_to(QUALIFYING_BOUNDARY)
    first = harness.run(refresh_market_data=False)
    counts = harness.counts()
    for _ in range(3):
        assert harness.run(refresh_market_data=False).status in {
            HeartbeatStatus.IDLE,
            HeartbeatStatus.NO_DATA,
        }
    assert harness.counts() == counts
    assert first.observations_recorded == len(harness.observations())


def test_a_setup_that_stays_plannable_creates_exactly_one_paper_plan() -> None:
    series = labelled_series() + (bar(21, 126, low=123),)
    harness = make_harness(series=series, ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    harness.run(refresh_market_data=False)
    # The series qualifies two setups at this close; the second is refused a
    # second paper trade by the one-active-paper-trade policy and stays a
    # monitored observation instead.
    assert len(harness.plans()) == 1
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    second = harness.run(refresh_market_data=False)
    assert second.paper_plans_created == 0
    assert len(harness.plans()) == 1
    # The later observation still points at the frozen plan it belongs to.
    later = [item for item in harness.observations() if item.as_of > QUALIFYING_BOUNDARY]
    assert later
    assert all(
        item.paper_plan_id is None or item.paper_plan_id in {p.paper_plan_id for p in harness.plans()}
        for item in later
    )


def test_gapped_window_is_recorded_as_incomplete_and_never_planned() -> None:
    series = labelled_series()
    gapped = tuple(candle for candle in series if candle.timestamp != series[8].timestamp)
    harness = make_harness(series=gapped, ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.status is HeartbeatStatus.PROCESSED
    cycle = harness.cycles()[0]
    assert cycle.data_health is DataHealth.INCOMPLETE
    assert cycle.missing_candle_count == 1
    assert any("incomplete" in note for note in cycle.notes)
    assert harness.plans() == ()
    assert all(item.plan_state is None for item in harness.observations())
    assert result.data_health is DataHealth.INCOMPLETE


# ----------------------------------------------------------------------
# Bootstrap: a cold start seeds itself through the unchanged Step 2 path
# ----------------------------------------------------------------------


def _rolling_source(series: tuple, forming=None) -> "RollingWindowExchange":
    source = RollingWindowExchange()
    source.set_candles(series)
    if forming is not None:
        source.set_forming_candle(forming)
    return source


def test_cold_start_bootstraps_public_closed_candles_without_a_backfill_start() -> None:
    """The documented first run seeds history instead of refusing to fetch.

    Regression for the live-data failure: with nothing stored and no
    ``--backfill-start``, the runner raised ``ForwardDataUnavailable`` *before
    contacting the exchange at all*, so no closed candle could ever reach
    storage and every pass ended in ``NO_DATA`` with no forward conclusion.
    """

    series = labelled_series()
    forming = bar(21, 150, high=160, low=140)
    source = _rolling_source(series, forming)
    harness = make_harness(
        series=(),
        store_series=False,
        source=source,
        ledger_start=QUALIFYING_BOUNDARY,
    )
    harness.advance_to(QUALIFYING_BOUNDARY)

    result = harness.run(refresh_market_data=True)

    # The runner really fetched public data and the failure is gone.
    assert source.calls == 1
    # The implicit bootstrap window ends at the latest close and is exactly the
    # documented default depth.
    assert source.requests == [
        datetime_to_milliseconds(
            QUALIFYING_BOUNDARY
            - INTERVAL
            - (RunnerSettings().bootstrap_candles - 1) * INTERVAL
        )
    ]
    assert result.market_data_error is None
    assert result.market_data_error_type is None
    assert result.status is HeartbeatStatus.PROCESSED

    # Only closed candles were stored: the still-forming candle is excluded.
    stored = harness.candles().candles
    assert [candle.timestamp for candle in stored] == [
        candle.timestamp for candle in series
    ]
    assert all(candle.timestamp < forming.timestamp for candle in stored)

    # A real conclusion is recorded at the latest closed boundary, from exactly
    # the same unchanged Steps 3-6 pipeline the stored-history tests exercise.
    assert result.processed_boundaries == (QUALIFYING_BOUNDARY,)
    cycles = harness.cycles()
    assert len(cycles) == 1
    assert cycles[0].status is CycleStatus.COMPLETE
    assert cycles[0].as_of == QUALIFYING_BOUNDARY
    assert len(harness.plans()) == 1  # one active paper trade, at most


@pytest.mark.parametrize(
    ("bootstrap_candles", "minimum_history_candles", "expected_depth"),
    [
        (10, 10, 10),
        (3, 10, 10),  # never fewer than the runner's own precondition
        (30, 10, 30),
    ],
)
def test_bootstrap_depth_is_bounded_by_configuration(
    bootstrap_candles: int, minimum_history_candles: int, expected_depth: int
) -> None:
    series = tuple(bar(index, 100 + index) for index in range(30))
    forming = bar(30, 200, high=201, low=199)
    source = _rolling_source(series, forming)
    harness = make_harness(
        series=(),
        store_series=False,
        source=source,
        ledger_start=EPOCH + 30 * INTERVAL,
        parameters=ForwardParameters(minimum_history_candles=minimum_history_candles),
        runner_settings=RunnerSettings(
            interval_seconds=D(1),
            fetch_max_attempts=1,
            fetch_retry_backoff_seconds=D(0),
            bootstrap_candles=bootstrap_candles,
        ),
    )
    harness.advance_to(EPOCH + 30 * INTERVAL)

    result = harness.run(refresh_market_data=True)

    assert result.market_data_error is None
    # The download starts exactly ``expected_depth`` closed candles before the
    # latest close; the forming candle is fetched but never stored.
    assert source.requests == [
        datetime_to_milliseconds(EPOCH + (30 - expected_depth) * INTERVAL)
    ]
    stored = harness.candles().candles
    assert [candle.timestamp for candle in stored] == [
        candle.timestamp for candle in series[30 - expected_depth :]
    ]
    assert forming.timestamp not in {candle.timestamp for candle in stored}
    # The pass records that the forming candle was fetched and excluded.
    heartbeat = harness.service.ledger.latest_heartbeat(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert json.loads(heartbeat.market_data_json)["excluded_open_count"] == 1


def test_explicit_backfill_start_is_aligned_to_the_timeframe() -> None:
    """``--backfill-start`` accepts any UTC instant; Step 2 needs a candle open.

    Regression: an unaligned instant (for example ``10:22``) made Step 2 raise
    ``ValueError: start_time must align to the requested timeframe``, so the whole
    pass stored nothing.  The runner now moves the requested instant *up* to the
    next candle open - never earlier than asked - and downloads from there.
    """

    series = labelled_series()
    forming = bar(21, 150, high=160, low=140)
    source = _rolling_source(series, forming)
    requested = EPOCH + 10 * INTERVAL + timedelta(minutes=22)
    harness = make_harness(
        series=(),
        store_series=False,
        source=source,
        ledger_start=QUALIFYING_BOUNDARY,
        backfill_start=requested,
        runner_settings=RunnerSettings(
            interval_seconds=D(1), fetch_max_attempts=1, fetch_retry_backoff_seconds=D(0)
        ),
    )
    harness.advance_to(QUALIFYING_BOUNDARY)

    result = harness.run(refresh_market_data=True)

    assert result.market_data_error is None
    # 10:22 is not a candle open, so the download starts at 11:00 - later, never
    # earlier, than the instant the operator asked for.
    assert source.requests == [datetime_to_milliseconds(EPOCH + 11 * INTERVAL)]
    assert result.status is HeartbeatStatus.PROCESSED
    assert result.processed_boundaries == (QUALIFYING_BOUNDARY,)
    stored = harness.candles().candles
    assert [candle.timestamp for candle in stored] == [
        candle.timestamp for candle in series[11:]
    ]
    assert forming.timestamp not in {candle.timestamp for candle in stored}


def test_aligned_explicit_backfill_start_is_left_exactly_as_requested() -> None:
    series = labelled_series()
    source = _rolling_source(series)
    aligned = EPOCH + 12 * INTERVAL
    harness = make_harness(
        series=(),
        store_series=False,
        source=source,
        ledger_start=QUALIFYING_BOUNDARY,
        backfill_start=aligned,
    )
    harness.advance_to(QUALIFYING_BOUNDARY)

    result = harness.run(refresh_market_data=True)

    assert result.market_data_error is None
    assert source.requests == [datetime_to_milliseconds(aligned)]
    assert len(harness.candles().candles) == 21 - 12


def test_refresh_after_bootstrap_fetches_only_newly_closed_candles() -> None:
    """The bootstrap branch never replaces the incremental Step 2 update path."""

    series = labelled_series()
    fresh = bar(21, 126, low=123)
    source = _rolling_source(series)
    source.set_forming_candle(fresh)
    harness = make_harness(
        series=(),
        store_series=False,
        source=source,
        ledger_start=QUALIFYING_BOUNDARY,
        runner_settings=RunnerSettings(
            interval_seconds=D(1),
            fetch_max_attempts=1,
            fetch_retry_backoff_seconds=D(0),
            bootstrap_candles=21,
        ),
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    first = harness.run(refresh_market_data=True)
    assert first.market_data_error is None
    assert len(harness.candles().candles) == 21

    # The next candle closes: the refresh starts after the latest stored candle
    # and stores exactly the one newly closed candle.
    following = bar(22, 150, high=160, low=140)
    source.set_candles(series + (fresh,))
    source.set_forming_candle(following)
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)

    second = harness.run(refresh_market_data=True)

    assert second.market_data_error is None
    assert source.requests[-1] == datetime_to_milliseconds(EPOCH + 21 * INTERVAL)
    stored = harness.candles().candles
    assert [candle.timestamp for candle in stored] == [
        candle.timestamp for candle in series + (fresh,)
    ]
    assert following.timestamp not in {candle.timestamp for candle in stored}
    assert second.processed_boundaries == (QUALIFYING_BOUNDARY + INTERVAL,)


# ----------------------------------------------------------------------
# A/J: market-data failures are reported, never fabricated
# ----------------------------------------------------------------------


def test_market_data_failure_is_reported_and_never_invented() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.source.set_candles(labelled_series() + (bar(21, 126, low=123),))
    harness.source.fail_calls = (1, 2)
    harness.source.failing_exception = ConnectionError
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    stored_before = len(harness.candles().candles)
    result = harness.run(refresh_market_data=True)
    assert result.market_data_error is not None
    assert "underlying ConnectionError: simulated offline exchange" in result.market_data_error
    assert result.market_data_error_type == "ExchangeDataError"
    assert "market-data error" in result.detail
    assert len(harness.candles().candles) == stored_before
    # Already-stored closed candles are still real data and may be processed.
    assert result.status in {HeartbeatStatus.PROCESSED, HeartbeatStatus.NO_DATA}
    if result.status is HeartbeatStatus.PROCESSED:
        assert all(
            cycle.status is CycleStatus.COMPLETE
            or cycle.status is CycleStatus.MISSING_CANDLE
            for cycle in harness.cycles()
        )


def test_market_data_failure_is_retried_conservatively_then_succeeds() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.source.set_candles(labelled_series() + (bar(21, 126, low=123),))
    harness.source.fail_calls = (1,)
    harness.source.failing_exception = ConnectionError
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    stored_before = len(harness.candles().candles)
    result = harness.run(refresh_market_data=True)
    assert result.market_data_error is None
    assert harness.source.calls >= 2
    assert result.status is HeartbeatStatus.PROCESSED
    # The retried public fetch is what supplied the newly closed candle.
    assert len(harness.candles().candles) == stored_before + 1


def test_refresh_without_stored_history_refuses_to_invent_candles() -> None:
    harness = make_harness(series=(), ledger_start=QUALIFYING_BOUNDARY)
    harness.source = FakeExchange()
    harness.source.set_candles(labelled_series())
    harness.service._market_data = None
    harness.service._market_data_factory = None
    harness.service._market_data_service = None  # type: ignore[method-assign]
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=True)
    assert result.status is HeartbeatStatus.NO_DATA
    assert result.market_data_error is not None
    assert harness.counts()["cycles"] == 0
    assert harness.candles().candles == ()


def test_failed_refresh_leaves_the_status_honestly_stale() -> None:
    harness = harness_with_a_paper_plan()
    harness.source.set_candles(
        harness.series + (bar(21, 126, low=123), bar(22, 128, low=125))
    )
    harness.source.fail_calls = (1, 2)
    harness.advance_to(QUALIFYING_BOUNDARY + 2 * INTERVAL)
    harness.run(refresh_market_data=True)
    status = harness.service.status()
    assert status["market_data"]["data_health"] in {
        DataHealth.STALE.value,
        DataHealth.INCOMPLETE.value,
        DataHealth.UNKNOWN.value,
    }
    assert status["market_data"]["data_health"] != DataHealth.CURRENT.value
    assert status["runner"]["last_error"]


# ----------------------------------------------------------------------
# D: paper semantics
# ----------------------------------------------------------------------


def test_watch_and_no_setup_never_create_a_paper_trade() -> None:
    harness = make_harness(series=watch_only_series(), ledger_start=QUALIFYING_BOUNDARY + INTERVAL)
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 0
    assert harness.plans() == ()
    states = {item.setup_state.value for item in harness.observations()}
    assert states <= {"WATCH", "NO_SETUP"}
    assert all(not item.is_paper_observation for item in harness.observations())


def test_qualified_without_a_plannable_plan_creates_no_paper_trade() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.service.planning_parameters = PlanningParameters(min_r_multiple=D("100"))
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 0
    assert harness.plans() == ()
    assert any(
        item.setup_state.value == "QUALIFIED"
        and (item.plan_state is None or item.plan_state.value != "PLANNABLE")
        for item in harness.observations()
    )


def test_plannable_creates_a_paper_observation_and_never_claims_a_fill() -> None:
    harness = harness_with_a_paper_plan()
    plans = plans_with_levels(harness)
    assert len(plans) == 1
    for plan in plans:
        assert plan.risk_per_unit == LONG_ENTRY - LONG_STOP
        assert plan.invalidation == LONG_STOP
        assert plan.observation_horizon_candles == HORIZON
        assert plan.direction == "bullish"
        stored = plan.to_json_dict()
        text = str(stored).lower()
        for forbidden in (
            "was filled",
            "fill price",
            "filled at",
            "executed at",
            "position size",
            "position_size",
            "quantity",
            "leverage",
            "liquidation",
            "realised",
            "realized",
            "pnl",
            "equity",
        ):
            assert forbidden not in text
    observations = harness.observations()
    assert [item for item in observations if item.is_paper_observation]
    assert all(item.plan_json for item in observations if item.is_paper_observation)
    status = harness.service.status()
    assert status["paper_label"] == "PAPER OBSERVATION — NO REAL ORDER"
    assert status["label"] == "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE"
    assert status["market_data_label"] == "LIVE MARKET DATA"


def test_paper_plan_is_built_only_from_the_exact_plannable_projection() -> None:
    harness = harness_with_a_paper_plan()
    plan = harness.plans()[0]
    assert plan.plan_config_fingerprint
    assert plan.planning_rules_version
    assert plan.version_fingerprint
    assert plan.friction_fingerprint
    assert plan.forward_parameters_fingerprint
    assert plan.strategy_versions
    assert plan.plan_as_of == QUALIFYING_BOUNDARY


# ----------------------------------------------------------------------
# D/E: outcome trajectories (Step 7 semantics reused, never favourable)
# ----------------------------------------------------------------------


def test_entry_not_reached_is_unresolved_until_the_horizon_completes() -> None:
    harness = harness_with_a_paper_plan()
    harness.step((bar(21, 126, low=125),), refresh_market_data=False)
    outcome = harness.latest_outcomes()[0]
    assert outcome.observation.status is OutcomeStatus.ENTRY_NOT_REACHED
    assert outcome.observation.entry_reached is False
    plan = harness.plans()[0]
    reported = harness.service.status()["unresolved_paper_plan_count"]
    assert reported == 1  # the interim verdict is not a settled outcome
    assert outcome.observed_through == plan.plan_as_of
    # After the full horizon with no entry touch, it settles as not reached.
    harness.step(
        tuple(
            bar(index, 126, low=125)
            for index in range(22, 22 + HORIZON - 1)
        ),
        refresh_market_data=False,
    )
    settled = harness.latest_outcomes()[0]
    assert settled.observation.status is OutcomeStatus.ENTRY_NOT_REACHED
    assert harness.service.status()["unresolved_paper_plan_count"] == 0


def test_entry_touched_but_open_is_open_at_cutoff() -> None:
    harness = harness_with_a_paper_plan()
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    for outcome in harness.latest_outcomes():
        assert outcome.observation.status is OutcomeStatus.OPEN_AT_CUTOFF
        assert outcome.observation.entry_reached is True
        assert outcome.observation.entry_ordered is True
        assert outcome.observation.targets_reached == ()


def test_reaching_the_proposed_target_is_recorded_and_excluded_from_raw_r() -> None:
    harness = harness_with_a_paper_plan()
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    harness.step((bar(22, 140, high=141, low=124),), refresh_market_data=False)
    outcome = harness.latest_outcomes()[0]
    assert outcome.observation.status is OutcomeStatus.TARGETS_REACHED
    assert outcome.observation.targets_reached == (0,)
    report = harness.report()
    # TARGETS_REACHED *is* eligible: raw R uses the furthest reached target.
    assert report.metrics.raw_observational_r.sample_size == 1


def test_stop_after_an_ordered_entry_is_stopped() -> None:
    harness = harness_with_a_paper_plan()
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    harness.step((bar(22, 120, high=126, low=116),), refresh_market_data=False)
    for outcome in harness.latest_outcomes():
        assert outcome.observation.status is OutcomeStatus.STOPPED
        assert outcome.observation.stop_reached is True
        assert outcome.observation.stop_pre_entry is False


def test_stop_after_targets_is_reported_and_excluded_from_raw_r() -> None:
    # Two genuine structural targets are needed for a stop after a target
    # reach: with a single target the trajectory completes at that target.
    harness = make_harness(series=two_target_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    harness.run(refresh_market_data=False)
    plans = harness.plans()
    assert plans and all(len(plan.targets) == 2 for plan in plans)
    harness.step((bar(21, 130, high=131, low=123),), refresh_market_data=False)
    harness.step((bar(22, 140, high=141, low=132),), refresh_market_data=False)
    assert {
        outcome.observation.status.value for outcome in harness.latest_outcomes()
    } == {OutcomeStatus.OPEN_AT_CUTOFF.value}
    harness.step((bar(23, 120, high=130, low=116),), refresh_market_data=False)
    for outcome in harness.latest_outcomes():
        assert outcome.observation.status is OutcomeStatus.STOPPED_AFTER_TARGETS
        assert outcome.observation.targets_reached == (0,)
    report = harness.report()
    excluded = {
        item.value: item.count for item in report.metrics.raw_observational_r.excluded
    }
    assert excluded.get("STOPPED_AFTER_TARGETS_NO_PARTIAL_EXIT_POLICY") == 1
    assert report.metrics.raw_observational_r.sample_size == 0


def test_invalidated_before_entry_is_recorded() -> None:
    harness = harness_with_a_paper_plan()
    harness.step((bar(21, 120, high=121, low=116),), refresh_market_data=False)
    for outcome in harness.latest_outcomes():
        assert outcome.observation.status is OutcomeStatus.INVALIDATED_BEFORE_ENTRY
        assert outcome.observation.entry_reached is False
        assert outcome.observation.stop_pre_entry is True


def test_same_candle_entry_and_exit_is_ambiguous_never_favourable() -> None:
    harness = harness_with_a_paper_plan()
    harness.step((bar(21, 130, high=139, low=123),), refresh_market_data=False)
    for outcome in harness.latest_outcomes():
        assert outcome.observation.status is OutcomeStatus.AMBIGUOUS
        assert outcome.observation.ambiguous is True
        assert outcome.observation.targets_reached == ()
    report = harness.report()
    assert report.metrics.ambiguous_count == 1
    assert any("ambiguous" in warning for warning in report.warnings)


def test_incomplete_data_in_the_window_is_recorded_as_incomplete() -> None:
    harness = harness_with_a_paper_plan()
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    # Remove a candle that has already closed: the window now has a real gap.
    gap_candle = bar(22, 128, low=125)
    harness.step((gap_candle,), refresh_market_data=False)
    with harness.engine.begin() as connection:
        connection.exec_driver_sql(
            "DELETE FROM ohlcv_candles WHERE timestamp = ?",
            (gap_candle.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),),
        )
    harness.step((bar(23, 129, low=126),), refresh_market_data=False)
    statuses = latest_statuses(harness)
    assert OutcomeStatus.INCOMPLETE_DATA.value in statuses
    report = harness.report()
    assert report.metrics.incomplete_data_count >= 1


def test_unresolved_paper_plans_are_always_visible() -> None:
    harness = harness_with_a_paper_plan()
    status = harness.service.status()
    assert status["unresolved_paper_plan_count"] == 1
    assert len(status["unresolved_paper_plan_ids"]) == 1
    payload = harness.service.observations_payload(limit=10)
    assert payload["label"] == "PAPER OBSERVATION — NO REAL ORDER"
    assert payload["paper_plans"]
    assert all(item["latest_outcome"] is None for item in payload["paper_plans"])


def test_outcome_versions_are_appended_and_never_rewritten() -> None:
    harness = harness_with_a_paper_plan()
    plan = harness.plans()[0]
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    harness.step((bar(22, 140, high=141, low=124),), refresh_market_data=False)
    versions = harness.outcome_versions(plan.paper_plan_id)
    assert [version.sequence for version in versions] == [1, 2]
    assert versions[1].supersedes_outcome_id == versions[0].outcome_id
    assert versions[0].observation.status is OutcomeStatus.OPEN_AT_CUTOFF
    assert versions[1].observation.status is OutcomeStatus.TARGETS_REACHED
    # The earliest version is still stored with its original cutoff.
    assert versions[0].observed_through == plan.plan_as_of
    assert versions[1].observed_through == plan.plan_as_of + INTERVAL


def test_short_side_is_tracked_with_the_same_semantics() -> None:
    short_series = mirrored(labelled_series())
    harness = make_harness(series=short_series, ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 1
    plans = harness.plans()
    assert all(plan.direction == "bearish" for plan in plans)
    entry = plans[0].entry
    stop = plans[0].stop
    target = plans[0].targets[0]
    # Short geometry: the stop sits above the entry and the target below it.
    assert stop > entry and target < entry
    # The same geometry mirrored: entry touched, then the target reached.
    harness.step((bar(21, entry + D("2"), high=stop - D("1"), low=entry - D("1")),), refresh_market_data=False)
    assert {
        outcome.observation.status.value for outcome in harness.latest_outcomes()
    } == {OutcomeStatus.OPEN_AT_CUTOFF.value}
    harness.step((bar(22, target + D("1"), high=entry + D("1"), low=target - D("1")),), refresh_market_data=False)
    statuses = latest_statuses(harness)
    assert statuses == [OutcomeStatus.TARGETS_REACHED.value]


def test_two_families_are_recorded_and_reported_separately() -> None:
    harness = make_harness(series=sweep_reversal_series(), ledger_start=QUALIFYING_BOUNDARY + 2 * INTERVAL)
    harness.advance_to(QUALIFYING_BOUNDARY + 2 * INTERVAL)
    assert harness.run(refresh_market_data=False).paper_plans_created >= 1
    families = {plan.family.value for plan in harness.plans()}
    assert "failed_breakout_sweep_reversal" in families
    observation_families = {item.setup_family.value for item in harness.observations()}
    assert len(observation_families) >= 2
    report = harness.report()
    family_rows = {
        row.value: row for row in report.breakdowns if row.dimension == "setup_family"
    }
    assert "failed_breakout_sweep_reversal" in family_rows
    assert family_rows["failed_breakout_sweep_reversal"].metrics.paper_plan_count >= 1


# ----------------------------------------------------------------------
# H: version separation
# ----------------------------------------------------------------------


def test_different_parameter_versions_are_never_silently_combined() -> None:
    harness = harness_with_a_paper_plan()
    default_fingerprint = harness.cycles()[0].version_fingerprint
    # A second, differently configured service records into the same ledger.
    # A stricter floor than the v2 default (1) is a genuinely different
    # planning version; the old fixture used 1, which is now the default.
    harness.service.planning_parameters = PlanningParameters(min_r_multiple=D("2"))
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    new_fingerprint = harness.cycles()[-1].version_fingerprint
    assert new_fingerprint != default_fingerprint
    ledger = harness.ledger()
    assert ledger.version_separation is VersionSeparation.SEPARATED
    report = harness.report()
    assert report.combined_metrics_available is False
    assert report.combined_metrics_unavailable_reason
    assert len(report.version_cohorts) == 2
    assert any("withheld" in warning for warning in report.warnings)
    # The earlier cycle is untouched by the new version.
    assert harness.cycles()[0].version_fingerprint == default_fingerprint


def test_version_fingerprint_covers_parameters_and_rules() -> None:
    harness = harness_with_a_paper_plan()
    cycle = harness.cycles()[0]
    keys = {key for key, _ in cycle.strategy_versions}
    assert {
        "forward_ledger_rules",
        "forward_runner_rules",
        "forward_parameters",
        "friction_assumptions",
        "setup_qualification_rules",
        "trade_planning_rules",
        "outcome_observation_rules",
    } <= keys
    assert dict(cycle.strategy_versions)["forward_parameters"] == harness.service.parameters.fingerprint()
    assert cycle.version_fingerprint == fingerprint("forward-versions", cycle.strategy_versions)


# ----------------------------------------------------------------------
# L/M: boundaries — nothing else is touched
# ----------------------------------------------------------------------


def test_step_seven_journal_tables_are_never_written_by_forward_testing() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    tables = (
        "journal_records",
        "journal_decisions",
        "journal_outcomes",
        "journal_outcome_events",
    )

    def counts() -> dict[str, int]:
        with harness.engine.connect() as connection:
            return {
                table: connection.exec_driver_sql(
                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed names
                ).scalar_one()
                for table in tables
            }

    before = counts()
    harness.advance_to(QUALIFYING_BOUNDARY)
    harness.run(refresh_market_data=False)
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    harness.step((bar(22, 140, high=141, low=124),), refresh_market_data=False)
    assert counts() == before == {table: 0 for table in tables}


def test_raw_market_history_is_never_deleted_or_rewritten() -> None:
    series = labelled_series()
    harness = make_harness(series=series, ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    harness.run(refresh_market_data=False)
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    stored = harness.candles().candles
    assert len(stored) == len(series) + 1
    for original, after in zip(series, stored, strict=False):
        assert after.timestamp == original.timestamp
        assert after.open == original.open
        assert after.high == original.high
        assert after.low == original.low
        assert after.close == original.close
        assert after.volume == original.volume


def test_forward_ledger_rows_are_immutable_at_the_database_level() -> None:
    import sqlalchemy

    harness = harness_with_a_paper_plan()
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        with harness.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE forward_cycles SET data_health = 'CURRENT'"
            )
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        with harness.engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM forward_paper_plans")


def test_no_order_or_account_capability_exists_in_the_forward_package() -> None:
    import inspect

    from trading_assistant import forward_testing

    modules = [
        forward_testing,
        forward_testing.service,
        forward_testing.runner,
        forward_testing.repository,
        forward_testing.reporting,
        forward_testing.parameters,
        forward_testing.models,
    ]
    forbidden = (
        "create_order",
        "create_market_order",
        "cancel_order",
        "fetch_balance",
        "fetch_positions",
        "set_leverage",
        "set_margin_mode",
        "withdraw",
        "transfer",
        "private_key",
        "api_key",
        "api_secret",
        "eval(",
        "exec(",
    )
    for module in modules:
        source = inspect.getsource(module)
        for token in forbidden:
            assert token not in source, f"{module.__name__} contains {token!r}"


def test_forward_service_refuses_to_fetch_without_an_explicit_source() -> None:
    harness = make_harness(
        series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY, source=False
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=True)
    assert result.market_data_error_type == "ForwardNotConfigured"
    # Already-stored closed candles are real data and may be processed; nothing
    # was fetched and nothing was invented.
    assert harness.source is None
    assert harness.counts()["cycles"] == 1


def test_public_market_data_source_only_is_used() -> None:
    """The forward package never imports a network or exchange client directly."""

    import inspect

    from trading_assistant.forward_testing import service as service_module

    source = inspect.getsource(service_module)
    assert "create_market_data_service" not in source
    assert "ccxt" not in source
    assert "requests.get" not in source


def test_timeframe_must_be_supported() -> None:
    harness = harness_with_a_paper_plan()
    with pytest.raises(ValueError):
        harness.service.status(timeframe="7m")
    with pytest.raises(ValueError):
        harness.service.run_once(timeframe="7m", refresh_market_data=False)


def test_duplicate_boundary_catch_up_is_never_recorded_twice() -> None:
    series = labelled_series() + (bar(21, 126, low=123), bar(22, 128, low=125))
    harness = make_harness(series=series, ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY + 2 * INTERVAL)
    assert harness.run(refresh_market_data=False).cycles_recorded == 3
    counts = harness.counts()
    # Even after the clock is rewound and the pass is repeated, nothing repeats.
    harness.clock["t"] = clock_at(QUALIFYING_BOUNDARY + 2 * INTERVAL)
    harness.run(refresh_market_data=False)
    assert harness.counts() == counts


def test_candle_repository_holds_every_forward_boundary_candle() -> None:
    harness = harness_with_a_paper_plan()
    repository = CandleRepository(harness.engine)
    for cycle in harness.cycles():
        stored = repository.get_candles(
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe="1h",
            start_time=cycle.candle_open_time,
            end_time=cycle.candle_open_time,
        ).candles
        assert len(stored) == 1
        assert stored[0].timestamp == cycle.candle_open_time


def test_status_is_read_only_and_never_records_anything() -> None:
    harness = harness_with_a_paper_plan()
    counts = harness.counts()
    for _ in range(3):
        payload = harness.service.status()
        assert payload["sample"]["cycles"] == counts["cycles"]
    assert harness.counts() == counts
    assert payload["market_data"]["closed_candle_policy"]
    assert "unfinished candle is never used" in payload["market_data"]["closed_candle_policy"]


def test_service_requires_timezone_aware_instants() -> None:
    harness = harness_with_a_paper_plan()
    with pytest.raises(ValueError):
        harness.service.run_once(
            now=QUALIFYING_BOUNDARY.replace(tzinfo=None), refresh_market_data=False
        )
    with pytest.raises(ValueError):
        harness.service.status(now=QUALIFYING_BOUNDARY.replace(tzinfo=None))


def test_ledger_snapshot_reports_pending_and_versions() -> None:
    harness = harness_with_a_paper_plan()
    ledger = harness.ledger()
    assert ledger.pending_catch_up_boundaries == 0
    assert len(ledger.version_fingerprints) == 1
    assert ledger.version_separation is VersionSeparation.SINGLE_VERSION
    assert ledger.paper_plans and ledger.latest_outcomes == ()


# ----------------------------------------------------------------------
# Bounded replay: long histories record the same decisions whatever the
# pass shape, and the replay window never grows with stored history
# ----------------------------------------------------------------------


def _padded_series(pad: int = 20):
    """The labelled series shifted after ``pad`` flat pre-history candles."""

    flat = tuple(bar(i, 100) for i in range(pad))
    shifted = tuple(
        replace(c, timestamp=EPOCH + INTERVAL * (pad + i))
        for i, c in enumerate(labelled_series())
    )
    tail = tuple(bar(pad + 21 + i, 124) for i in range(5))
    return flat + shifted + tail


def _decisions(harness) -> list:
    """The recorded decisions: which setup, at which close, in which state."""

    return sorted(
        (item.setup_id, item.as_of, item.setup_state.value)
        for item in harness.observations()
    )


def test_bounded_replay_records_long_history_decisions_independently_of_pass_shape() -> (
    None
):
    """A 47-close history with pre-history longer than the replay window.

    The window binds (the first processed close is 21 closes after the first
    stored candle), yet one pass and two passes record the same decisions: the
    same (setup, close, state) records and the same frozen bull-plan levels
    the unpadded series produces. Only the diagnostic envelope (ancient
    terminals inside snapshot_json) may differ with pass shape — decisions
    never do.
    """
    series = _padded_series()
    boundary = EPOCH + 41 * INTERVAL  # the shifted qualifying close
    end = EPOCH + 46 * INTERVAL

    single = make_harness(series=series, ledger_start=boundary)
    single.advance_to(end)
    first = single.run(refresh_market_data=False)
    assert first.status is HeartbeatStatus.PROCESSED
    assert first.pending_boundaries == 0
    assert [cycle.status for cycle in single.cycles()] == [CycleStatus.COMPLETE] * 6

    split = make_harness(series=series, ledger_start=boundary)
    split.advance_to(boundary + 2 * INTERVAL)
    split.run(refresh_market_data=False)
    split.advance_to(end)
    second = split.run(refresh_market_data=False)
    assert second.status is HeartbeatStatus.PROCESSED
    assert second.pending_boundaries == 0
    assert len(split.cycles()) == len(single.cycles()) == 6

    assert _decisions(split) == _decisions(single)
    plans_with_levels(single)
    plans_with_levels(split)

    # The replay floor stays within one window of the newest close: a longer
    # history can never drag the replay back to genesis.
    _, _, floor = single.service._ledger_setup_index(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert floor is not None
    assert end - floor <= 11 * INTERVAL


def test_ledger_setup_index_floor_tracks_only_unresolved_setups() -> None:
    """The replay floor is the oldest creation time without a terminal row."""

    fresh = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    ids, terminal_ids, floor = fresh.service._ledger_setup_index(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert ids == frozenset()
    assert terminal_ids == frozenset()
    assert floor is None

    harness = harness_with_a_paper_plan()
    ids, terminal_ids, floor = harness.service._ledger_setup_index(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert ids == {item.setup_id for item in harness.observations()}
    assert terminal_ids == {
        item.setup_id
        for item in harness.observations()
        if item.setup_ended_at is not None
    }
    assert floor == min(item.setup_created_at for item in harness.observations())

    # A recorded terminal older than every live setup does not move the floor.
    # Terminal rows carry no plan: the ledger CHECK constraints require it.
    template = harness.observations()[0]
    no_plan = dict(
        plan_id=None,
        plan_state=None,
        plan_json=None,
        plan_entry=None,
        plan_invalidation=None,
        plan_stop=None,
        plan_risk_per_unit=None,
        plan_targets=(),
        plan_target_r_multiples=(),
        plan_config_fingerprint=None,
        planning_rules_version=None,
        paper_plan_id=None,
    )
    ancient = replace(
        template,
        observation_id="test-ancient-terminal",
        setup_id="test-ancient-setup",
        setup_state=SetupState.NO_SETUP,
        setup_created_at=EPOCH,
        setup_ended_at=EPOCH + INTERVAL,
        setup_terminal_reason="maximum_bars_elapsed",
        **no_plan,
    )
    harness.service.ledger.insert_observation(ancient)
    ids, terminal_ids, floor_after = harness.service._ledger_setup_index(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert "test-ancient-setup" in ids
    assert "test-ancient-setup" in terminal_ids
    assert floor_after == floor

    # Resolving every setup clears the floor entirely.
    for index, setup_id in enumerate(sorted(ids)):
        terminal = replace(
            template,
            observation_id=f"test-terminal-{index}",
            setup_id=setup_id,
            setup_state=SetupState.NO_SETUP,
            setup_created_at=template.setup_created_at,
            setup_ended_at=QUALIFYING_BOUNDARY,
            setup_terminal_reason="maximum_bars_elapsed",
            **no_plan,
        )
        harness.service.ledger.insert_observation(terminal)
    _, _, cleared = harness.service._ledger_setup_index(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert cleared is None


def _terminal_rows_by_setup(harness):
    rows: dict[str, list] = {}
    for observation in harness.observations():
        if observation.setup_ended_at is not None:
            rows.setdefault(observation.setup_id, []).append(observation)
    return rows


def test_live_observed_setups_record_their_terminal_transition_exactly_once():
    """A setup observed live still records its ending, exactly once.

    Regression test for the terminal-recording gate: gating on the
    merely-observed set skipped every live setup's terminal row, pinned the
    replay floor at the oldest observation ever, and let the replay window
    grow without bound. The gate is the terminally-recorded set, so after a
    26-boundary run every setup the full replay calls terminal has exactly
    one terminal row mirroring the replay's own verdict, every setup still
    unresolved is genuinely live, and a repeat pass records nothing new.
    """
    end = EPOCH + 46 * INTERVAL
    series = extend(labelled_series(), tuple((21 + i, 100) for i in range(25)))
    harness = make_harness(series=series, ledger_start=EPOCH + 21 * INTERVAL)
    harness.advance_to(end)
    result = harness.run(refresh_market_data=False)
    assert result.status is HeartbeatStatus.PROCESSED
    assert len(harness.cycles()) == 26

    replay = QualificationService(harness.engine).snapshot(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, as_of=end
    )
    verdict = {setup.id: setup for setup in replay.setups}
    recorded = {
        observation.setup_id for observation in harness.observations()
    }
    assert recorded, "the run must record setups or the test is vacuous"
    assert recorded <= set(verdict)
    terminal = _terminal_rows_by_setup(harness)
    for setup_id in recorded:
        mate = verdict[setup_id]
        rows = terminal.get(setup_id, [])
        if mate.ended_at is None:
            assert rows == [], f"live setup {setup_id} must have no terminal row"
            assert mate.state in (SetupState.WATCH, SetupState.QUALIFIED)
        else:
            assert len(rows) == 1, f"terminal setup {setup_id} must end once"
            assert rows[0].setup_ended_at == mate.ended_at
            assert rows[0].setup_terminal_reason == mate.terminal_reason

    _, terminal_ids, floor = harness.service._ledger_setup_index(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert terminal_ids == frozenset(terminal)
    assert terminal_ids == {
        setup_id for setup_id in recorded if verdict[setup_id].ended_at is not None
    }
    for setup_id in recorded - terminal_ids:
        assert verdict[setup_id].state in (
            SetupState.WATCH,
            SetupState.QUALIFIED,
        ), f"unresolved setup {setup_id} must be genuinely live"
    assert floor is None or end - floor <= 11 * INTERVAL

    before = len(harness.observations())
    repeat = harness.run(refresh_market_data=False)
    assert repeat.status is HeartbeatStatus.IDLE
    assert len(harness.observations()) == before


def test_version_change_pins_floor_but_passes_stay_correct():
    """A mid-stream config change pins the floor; passes stay correct.

    Setup ids embed the config fingerprint, so setups recorded live under
    the old config can never resolve under the new one: the replay floor
    pins at the oldest live old-config creation and the replay widens to
    cover it. The pass must still succeed, record the new config's setups,
    mirror the new config's own replay verdicts, and never duplicate a
    terminal row.
    """
    series = extend(labelled_series(), tuple((21 + i, 62160) for i in range(12)))
    harness = make_harness(series=series, ledger_start=EPOCH + 21 * INTERVAL)
    harness.advance_to(EPOCH + 21 * INTERVAL)
    first = harness.run(refresh_market_data=False)
    assert first.status is HeartbeatStatus.PROCESSED
    old_ids, old_terminal, old_floor = harness.service._ledger_setup_index(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert old_floor is not None, "the fixture must leave old setups live"
    assert old_ids - old_terminal, "the fixture must leave old setups live"

    harness.service.qualification_parameters = QualificationParameters(
        continuation_max_bars=3, reversal_max_bars=3, range_max_bars=3
    )
    end = EPOCH + 26 * INTERVAL
    harness.advance_to(end)
    second = harness.run(refresh_market_data=False)
    assert second.status is HeartbeatStatus.PROCESSED

    _, new_terminal, new_floor = harness.service._ledger_setup_index(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert new_floor == old_floor
    assert (old_ids - old_terminal) & new_terminal == frozenset()

    terminal = _terminal_rows_by_setup(harness)
    for setup_id, rows in terminal.items():
        assert len(rows) == 1, f"terminal setup {setup_id} recorded twice"
    replay = QualificationService(harness.engine).snapshot(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of=end,
        parameters=harness.service.qualification_parameters,
    )
    verdict = {setup.id: setup for setup in replay.setups}
    fresh_ids = {
        observation.setup_id
        for observation in harness.observations()
        if observation.setup_id not in old_ids
    }
    assert fresh_ids, "the new config must record its own setups"
    for setup_id in fresh_ids:
        mate = verdict[setup_id]
        rows = terminal.get(setup_id, [])
        if mate.ended_at is None:
            assert rows == []
        else:
            assert len(rows) == 1
            assert rows[0].setup_ended_at == mate.ended_at
            assert rows[0].setup_terminal_reason == mate.terminal_reason


def test_later_passes_advance_past_an_unfinished_close_without_conflict_or_churn() -> None:
    """Regression: a pass after new closes must not re-run complete closes.

    Before the fix, the pending range restarted at the earliest unfinished close,
    re-ran complete closes from data that had since changed, and the append-only
    check refused the pass with ForwardConflict. Now: complete closes are skipped,
    unfinished closes are retried, identical retries add no row, and a close
    whose data changed gets its own versioned row.
    """

    harness = harness_with_a_paper_plan()
    # Pass 1: the qualifying close is complete; the next close has no candle yet
    # (an unfinished, missing-candle cycle).
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    first = harness.run(refresh_market_data=False)
    assert first.status is HeartbeatStatus.PROCESSED
    rows_after_first = len(harness.cycles())

    # Pass 2: three more closes arrive. This is exactly the pass that used to raise.
    harness.step(
        (bar(21, 126, low=123), bar(22, 130, low=124), bar(23, 132, low=125)),
        refresh_market_data=False,
    )
    second_rows = len(harness.cycles())
    assert second_rows > rows_after_first
    complete_at_qualifying = [
        cycle
        for cycle in harness.cycles()
        if cycle.as_of == QUALIFYING_BOUNDARY and cycle.complete
    ]
    assert len(complete_at_qualifying) == 1  # the complete close was never re-run

    # Pass 3 with nothing new: IDLE, and no row is appended for any close.
    idle = harness.run(refresh_market_data=False)
    assert idle.status is HeartbeatStatus.IDLE
    assert len(harness.cycles()) == second_rows
