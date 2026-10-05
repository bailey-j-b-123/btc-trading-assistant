"""Offline deterministic Step 7 tests: journaling types and outcome observation.

Every scenario uses synthetic candles over the existing Step 5/6 fixtures or
hand-built proposed-plan projections; nothing contacts a network, an exchange,
or project-persistent data. The rules under test are documented in
``journaling.observation``; these tests pin the exact touch, ordering,
ambiguity, gap, cutoff and MFE/MAE semantics.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal as D

import pytest
from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL
from test_setup_qualification import (
    at,
    breakout,
    candidate,
    frame,
    result,
)

from trading_assistant.journaling import (
    ENTRY_AND_EXIT_SAME_CANDLE,
    STOP_AND_TARGET_SAME_CANDLE,
    JournalError,
    OutcomeObservation,
    OutcomeParameters,
    OutcomeStatus,
    ProposedPlanLevels,
    canonical_json,
    normalize_note,
    observe_outcome,
)
from trading_assistant.market_data.types import Candle, CandleGap
from trading_assistant.trade_planning import (
    PlanningParameters,
    PlanState,
    TradePlanResult,
    plan_trade,
)

JOURNAL_ID = "journal-record-under-test"
LONG_ENTRY = D(100)
LONG_STOP = D(95)
LONG_TARGETS = (D(110), D(120))


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def levels(
    *,
    direction: str = "bullish",
    entry: str = "100",
    stop: str = "95",
    targets: tuple[str, ...] = ("110", "120"),
    risk: str | None = None,
    as_of_index: int = 0,
    symbol: str = SYMBOL,
    exchange: str = EXCHANGE,
    timeframe: str = "1h",
    plan_id: str = "plan-under-test",
    setup_id: str | None = "setup-under-test",
) -> ProposedPlanLevels:
    entry_value = D(entry)
    stop_value = D(stop)
    return ProposedPlanLevels(
        plan_id=plan_id,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,  # type: ignore[arg-type]
        entry=entry_value,
        stop=stop_value,
        targets=tuple(D(target) for target in targets),
        risk_per_unit=D(risk) if risk is not None else abs(entry_value - stop_value),
        as_of=at(as_of_index),
        setup_id=setup_id,
    )


def candle(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    symbol: str = SYMBOL,
    exchange: str = EXCHANGE,
    timeframe: str = "1h",
) -> Candle:
    return Candle(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        timestamp=EPOCH + INTERVAL * index,
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(10),
    )


def observe(
    plan: ProposedPlanLevels,
    candles,
    cutoff_index: int,
    *,
    parameters: OutcomeParameters | None = None,
    journal_id: str = JOURNAL_ID,
) -> OutcomeObservation:
    return observe_outcome(
        journal_id=journal_id,
        levels=plan,
        candles=tuple(candles),
        observed_through=at(cutoff_index),
        parameters=parameters,
    )


def events_of(observation: OutcomeObservation) -> list[tuple[str, int, str, bool]]:
    return [
        (event.kind.value, event.sequence, event.ordering.value, event.co_touched)
        for event in observation.events
    ]


# ---------------------------------------------------------------------------
# Entry, stop and target touch semantics
# ---------------------------------------------------------------------------


def test_entry_reached_then_first_target_ordered_and_open_at_cutoff():
    plan = levels()
    observation = observe(
        plan,
        [
            candle(0, open_="100", high="101", low="99", close="100"),
            candle(1, open_="100", high="111", low="100", close="110"),
        ],
        1,
    )
    assert observation.status is OutcomeStatus.OPEN_AT_CUTOFF
    assert observation.entry_reached and observation.entry_ordered
    assert observation.entry_timestamp == at(0)
    assert observation.entry_candle_index == 0
    assert observation.targets_reached == (0,)
    assert observation.targets_pre_entry == ()
    assert not observation.stop_reached
    assert observation.first_touch_order == (("entry",), ("target_1",))
    assert events_of(observation) == [
        ("entry", 0, "ordered", False),
        ("target", 1, "ordered", False),
    ]
    assert not observation.ambiguous and not observation.incomplete
    assert observation.expected_candle_count == 2
    assert observation.observed_candle_count == 2
    assert observation.evaluated_through == at(1)
    assert observation.evaluated_low == D(99)
    assert observation.evaluated_high == D(111)
    assert observation.post_entry_low == D(99)
    assert observation.post_entry_high == D(111)


def test_entry_not_reached_is_not_a_trade_and_post_entry_extremes_stay_unknown():
    plan = levels()
    observation = observe(
        plan,
        [candle(0, open_="102", high="103", low="101.5", close="102.5")],
        0,
    )
    assert observation.status is OutcomeStatus.ENTRY_NOT_REACHED
    assert not observation.entry_reached and not observation.entry_ordered
    assert observation.entry_timestamp is None
    assert observation.targets_reached == () and not observation.stop_reached
    assert observation.events == ()
    assert observation.post_entry_low is None
    assert observation.post_entry_high is None
    # The proposed-entry-relative market excursion is still recorded honestly.
    assert observation.mfe_price_move == D(3)
    assert observation.mae_price_move == D(0)


def test_entry_requires_the_level_to_be_traded_not_merely_surpassed():
    plan = levels()
    observation = observe(
        plan,
        [candle(0, open_="121", high="124", low="121", close="122")],
        0,
    )
    # Price jumped over both targets without ever trading at the entry: the
    # touches are recorded for audit but cannot count as reached targets.
    assert observation.status is OutcomeStatus.ENTRY_NOT_REACHED
    assert not observation.entry_reached
    assert observation.targets_reached == ()
    assert observation.targets_pre_entry == (0, 1)
    assert observation.first_touch_order == (("target_1", "target_2"),)
    assert events_of(observation) == [
        ("target", 0, "pre_entry", True),
        ("target", 0, "pre_entry", True),
    ]


def test_target_and_stop_conditions_are_threshold_conditions():
    # A candle entirely above a long target still satisfies "high >= target".
    plan = levels(targets=("110",))
    reached = observe(
        plan,
        [
            candle(0, open_="99", high="101", low="99", close="100"),
            candle(1, open_="112", high="118", low="112", close="117"),
        ],
        1,
    )
    assert reached.status is OutcomeStatus.TARGETS_REACHED
    assert reached.targets_reached == (0,)

    # A candle entirely below a long stop still satisfies "low <= stop".
    stopped = observe(
        plan,
        [
            candle(0, open_="99", high="101", low="99", close="100"),
            candle(1, open_="96", high="96", low="90", close="91"),
        ],
        1,
    )
    assert stopped.status is OutcomeStatus.STOPPED
    assert stopped.stop_reached and not stopped.stop_pre_entry
    assert stopped.stop_timestamp == at(1)
    assert stopped.targets_reached == ()


def test_stop_before_entry_invalidates_without_an_entry():
    plan = levels()
    observation = observe(
        plan,
        [candle(0, open_="94.5", high="99.5", low="94", close="95")],
        0,
    )
    assert observation.status is OutcomeStatus.INVALIDATED_BEFORE_ENTRY
    assert not observation.entry_reached
    assert observation.stop_reached and observation.stop_pre_entry
    assert observation.stop_timestamp == at(0)
    assert observation.targets_reached == ()
    assert observation.first_touch_order == (("stop",),)
    assert events_of(observation) == [("stop", 0, "pre_entry", False)]


def test_multiple_targets_co_touched_in_one_candle_are_not_ordered_against_each_other():
    plan = levels()
    observation = observe(
        plan,
        [
            candle(0, open_="100", high="101", low="99", close="100"),
            candle(1, open_="100", high="121", low="100", close="120"),
        ],
        1,
    )
    assert observation.status is OutcomeStatus.TARGETS_REACHED
    assert observation.targets_reached == (0, 1)
    assert observation.first_touch_order == (("entry",), ("target_1", "target_2"))
    assert events_of(observation) == [
        ("entry", 0, "ordered", False),
        ("target", 1, "ordered", True),
        ("target", 1, "ordered", True),
    ]


def test_first_touch_ordering_then_stopped_after_targets():
    plan = levels()
    observation = observe(
        plan,
        [
            candle(0, open_="99", high="101", low="99", close="100"),
            candle(1, open_="100", high="111", low="100", close="110"),
            candle(2, open_="110", high="111", low="94", close="96"),
        ],
        2,
    )
    assert observation.status is OutcomeStatus.STOPPED_AFTER_TARGETS
    assert observation.targets_reached == (0,)
    assert observation.stop_reached and not observation.stop_pre_entry
    assert observation.first_touch_order == (
        ("entry",),
        ("target_1",),
        ("stop",),
    )
    assert observation.evaluated_through == at(2)


def test_short_direction_is_symmetric():
    plan = levels(
        direction="bearish",
        entry="100",
        stop="105",
        targets=("90", "80"),
    )
    observation = observe(
        plan,
        [
            candle(0, open_="100", high="101", low="99", close="100"),
            candle(1, open_="100", high="100", low="89.5", close="90"),
            candle(2, open_="92", high="106", low="92", close="105"),
        ],
        2,
    )
    assert observation.status is OutcomeStatus.STOPPED_AFTER_TARGETS
    assert observation.entry_reached and observation.entry_ordered
    assert observation.targets_reached == (0,)
    assert observation.stop_reached
    assert observation.first_touch_order == (
        ("entry",),
        ("target_1",),
        ("stop",),
    )


# ---------------------------------------------------------------------------
# Ambiguity: never guess the favourable result
# ---------------------------------------------------------------------------


def test_same_candle_stop_and_target_is_ambiguous_and_not_a_win():
    plan = levels()
    observation = observe(
        plan,
        [
            candle(0, open_="100", high="101", low="99", close="100"),
            candle(1, open_="100", high="125", low="94", close="100"),
        ],
        1,
    )
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.ambiguous
    assert observation.ambiguity_kind == STOP_AND_TARGET_SAME_CANDLE
    assert observation.ambiguity_timestamp == at(1)
    # Facts are recorded, but nothing is guessed.
    assert observation.stop_reached
    assert observation.targets_reached == ()
    assert observation.first_touch_order == (
        ("entry",),
        ("stop", "target_1", "target_2"),
    )
    assert events_of(observation)[1:] == [
        ("stop", 1, "ambiguous", True),
        ("target", 1, "ambiguous", True),
        ("target", 1, "ambiguous", True),
    ]
    assert observation.status not in (
        OutcomeStatus.TARGETS_REACHED,
        OutcomeStatus.STOPPED,
        OutcomeStatus.STOPPED_AFTER_TARGETS,
    )


def test_same_candle_entry_and_stop_is_ambiguous():
    plan = levels()
    observation = observe(
        plan,
        [candle(0, open_="99", high="101", low="94", close="95")],
        0,
    )
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.ambiguity_kind == ENTRY_AND_EXIT_SAME_CANDLE
    assert observation.entry_reached is True
    assert observation.entry_ordered is False
    assert observation.stop_reached is True
    assert observation.targets_reached == ()
    assert observation.first_touch_order == (("entry", "stop"),)
    assert (
        observation.post_entry_low is None
    )  # an unentered plan has no post-entry path
    assert events_of(observation) == [
        ("entry", 0, "ambiguous", True),
        ("stop", 0, "ambiguous", True),
    ]


def test_same_candle_entry_and_target_is_ambiguous():
    plan = levels()
    observation = observe(
        plan,
        [candle(0, open_="100", high="111", low="99", close="110")],
        0,
    )
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.ambiguity_kind == ENTRY_AND_EXIT_SAME_CANDLE
    assert observation.targets_reached == ()
    assert observation.first_touch_order == (("entry", "target_1"),)
    assert events_of(observation) == [
        ("entry", 0, "ambiguous", True),
        ("target", 0, "ambiguous", True),
    ]


# ---------------------------------------------------------------------------
# Gaps and unknown data
# ---------------------------------------------------------------------------


def test_missing_candle_inside_the_window_is_unknown_not_a_loss_or_win():
    plan = levels(targets=("110",))
    observation = observe(
        plan,
        [
            candle(0, open_="99", high="101", low="99", close="100"),
            candle(2, open_="100", high="111", low="100", close="110"),
        ],
        2,
    )
    assert observation.status is OutcomeStatus.INCOMPLETE_DATA
    assert observation.incomplete
    assert observation.missing_candle_count == 1
    assert observation.missing_ranges == (
        CandleGap(start=at(1), end=at(1), missing_count=1),
    )
    assert observation.expected_candle_count == 3
    assert observation.observed_candle_count == 2
    assert observation.evaluated_through == at(0)
    # The target touch after the gap is never counted.
    assert observation.targets_reached == ()
    assert observation.first_touch_order == (("entry",),)
    # Unknown must not be reported as a loss, a win, or "entry not reached".
    assert observation.status not in (
        OutcomeStatus.ENTRY_NOT_REACHED,
        OutcomeStatus.STOPPED,
        OutcomeStatus.TARGETS_REACHED,
        OutcomeStatus.OPEN_AT_CUTOFF,
    )


def test_terminal_event_before_a_gap_still_stands_with_explicit_incompleteness():
    plan = levels(targets=("110",))
    observation = observe(
        plan,
        [
            candle(0, open_="99", high="101", low="99", close="100"),
            candle(1, open_="100", high="111", low="100", close="110"),
            candle(3, open_="110", high="112", low="108", close="109"),
        ],
        3,
    )
    assert observation.status is OutcomeStatus.TARGETS_REACHED
    assert observation.incomplete
    assert observation.missing_candle_count == 1
    assert observation.evaluated_through == at(1)
    assert observation.targets_reached == (0,)
    # Excursions stop at the terminal candle, never at the post-gap candle.
    assert observation.evaluated_high == D(111)


def test_missing_first_candle_is_unknown_with_no_excursions():
    plan = levels()
    observation = observe(plan, [], 1)
    assert observation.status is OutcomeStatus.INCOMPLETE_DATA
    assert observation.expected_candle_count == 2
    assert observation.observed_candle_count == 0
    assert observation.missing_candle_count == 2
    assert observation.evaluated_through is None
    assert observation.mfe_price_move is None and observation.mae_price_move is None
    assert observation.mfe_r is None and observation.mae_r is None
    assert observation.missing_ranges == (
        CandleGap(start=at(0), end=at(1), missing_count=2),
    )


def test_incomplete_window_never_claims_entry_not_reached():
    plan = levels()
    observation = observe(
        plan,
        [candle(0, open_="105", high="106", low="104", close="105")],
        2,
    )
    assert observation.status is OutcomeStatus.INCOMPLETE_DATA
    assert not observation.entry_reached
    assert observation.missing_candle_count == 2


def test_adjacent_missing_candles_merge_into_one_reported_range():
    plan = levels()
    observation = observe(
        plan,
        [candle(0, open_="99", high="101", low="99", close="100")],
        3,
    )
    assert observation.missing_ranges == (
        CandleGap(start=at(1), end=at(3), missing_count=3),
    )
    assert observation.missing_candle_count == 3


# ---------------------------------------------------------------------------
# MFE / MAE
# ---------------------------------------------------------------------------


def test_long_mfe_mae_absolute_and_r():
    plan = levels()
    observation = observe(
        plan,
        [
            candle(0, open_="99", high="101", low="99", close="100"),
            candle(1, open_="100", high="112", low="97", close="108"),
        ],
        1,
    )
    assert observation.evaluated_low == D(97)
    assert observation.evaluated_high == D(112)
    assert observation.mfe_price_move == D(12)
    assert observation.mae_price_move == D(3)
    assert observation.mfe_r == D("2.40000000")
    assert observation.mae_r == D("0.60000000")
    assert observation.risk_per_unit == D(5)


def test_short_mfe_mae_absolute_and_r():
    plan = levels(direction="bearish", entry="100", stop="105", targets=("90",))
    observation = observe(
        plan,
        [
            candle(0, open_="100", high="101", low="99", close="100"),
            candle(1, open_="100", high="100.5", low="88", close="89"),
        ],
        1,
    )
    assert observation.mfe_price_move == D(12)  # 100 - 88
    assert observation.mae_price_move == D(1)  # 101 - 100 over the evaluated window
    assert observation.mfe_r == D("2.40000000")
    assert observation.mae_r == D("0.20000000")
    assert observation.post_entry_low == D(88)
    assert observation.post_entry_high == D(101)


def test_mfe_and_mae_are_clamped_and_use_only_the_evaluated_window():
    plan = levels()
    observation = observe(
        plan,
        [candle(0, open_="121", high="124", low="121", close="122")],
        0,
    )
    assert observation.mfe_price_move == D(24)
    assert observation.mae_price_move == D(0)
    assert observation.mfe_r == D("4.80000000")
    assert observation.mae_r == D(0)


# ---------------------------------------------------------------------------
# Window, cutoff and anti-lookahead
# ---------------------------------------------------------------------------


def test_only_candles_inside_the_window_are_accepted():
    plan = levels()
    with pytest.raises(ValueError, match="inside the observation window"):
        observe(plan, [candle(1, open_="100", high="101", low="99", close="100")], 0)
    with pytest.raises(ValueError, match="must not precede"):
        observe(plan, [candle(0, open_="100", high="101", low="99", close="100")], -1)
    with pytest.raises(ValueError, match="inside the observation window"):
        observe(
            plan,
            [
                candle(0, open_="100", high="101", low="99", close="100"),
                candle(1, open_="100", high="101", low="99", close="100"),
            ],
            0,
        )


def test_mismatched_instrument_or_duplicate_or_unaligned_candles_are_refused():
    plan = levels()
    mismatched = replace(
        candle(0, open_="100", high="101", low="99", close="100"), symbol="ETH/USD"
    )
    with pytest.raises(ValueError, match="does not match the plan instrument"):
        observe(plan, [mismatched], 0)
    duplicated = candle(0, open_="100", high="101", low="99", close="100")
    with pytest.raises(ValueError, match="repeat an open time"):
        observe(plan, [duplicated, duplicated], 0)
    unaligned = replace(
        candle(0, open_="100", high="101", low="99", close="100"),
        timestamp=EPOCH + timedelta(minutes=30),
    )
    with pytest.raises(ValueError, match="align to the timeframe"):
        observe(plan, [unaligned], 0)
    with pytest.raises(TypeError, match="Candle"):
        observe(plan, ["not a candle"], 0)


def test_cutoff_and_argument_validation():
    plan = levels()
    with pytest.raises(ValueError, match="aligned candle open time"):
        observe_outcome(
            journal_id=JOURNAL_ID,
            levels=plan,
            candles=(),
            observed_through=at(0) + timedelta(minutes=30),
        )
    with pytest.raises(ValueError, match="must not precede"):
        observe_outcome(
            journal_id=JOURNAL_ID,
            levels=plan,
            candles=(),
            observed_through=at(0) - INTERVAL,
        )
    with pytest.raises(ValueError, match="journal_id"):
        observe_outcome(
            journal_id="  ", levels=plan, candles=(), observed_through=at(0)
        )
    with pytest.raises(TypeError, match="ProposedPlanLevels"):
        observe_outcome(
            journal_id=JOURNAL_ID,
            levels="nope",
            candles=(),
            observed_through=at(0),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="OutcomeParameters"):
        observe_outcome(
            journal_id=JOURNAL_ID,
            levels=plan,
            candles=(),
            observed_through=at(0),
            parameters="nope",  # type: ignore[arg-type]
        )


def test_same_inputs_reproduce_the_same_observation():
    plan = levels()
    candles = (
        candle(0, open_="99", high="101", low="99", close="100"),
        candle(1, open_="100", high="112", low="97", close="108"),
    )
    first = observe(plan, candles, 1)
    second = observe(plan, tuple(reversed(candles))[::-1], 1)
    assert first == second
    assert first.id == second.id


def test_chronological_replay_reproduces_every_historical_cutoff():
    """Growing the candle series never updates an observation made earlier."""

    plan = levels(targets=("110",))  # one target, so the trajectory is short
    candles = (
        candle(0, open_="99", high="101", low="99", close="100"),  # entry
        candle(1, open_="100", high="111", low="100", close="110"),  # target 1
        candle(2, open_="110", high="111", low="94", close="96"),  # stop
    )
    replay = [observe(plan, candles[: index + 1], index) for index in range(3)]
    assert [item.status for item in replay] == [
        OutcomeStatus.OPEN_AT_CUTOFF,
        OutcomeStatus.TARGETS_REACHED,
        OutcomeStatus.TARGETS_REACHED,  # the terminal event stands after later candles
    ]
    # Every historical observation is identical when recomputed from scratch from
    # only the candles known at that cutoff, and keeps its identity afterwards.
    assert replay[0] == observe(plan, candles[:1], 0)
    assert replay[1] == observe(plan, candles[:2], 1)
    assert replay[0].evaluated_through == at(0)
    assert replay[1].evaluated_through == at(1) and replay[1].stop_reached is False
    # Evaluation stops at the first terminal outcome: the later stop candle is
    # outside the evaluated window and is never counted against the plan.
    assert replay[2].evaluated_through == at(1)
    assert replay[2].stop_reached is False
    assert replay[2].observed_through == at(2)


def test_different_cutoff_or_configuration_changes_the_observation_identity():
    plan = levels()
    candles = (
        candle(0, open_="99", high="101", low="99", close="100"),
        candle(1, open_="100", high="112", low="97", close="108"),
    )
    t1 = observe(plan, candles[:1], 0)
    t2 = observe(plan, candles, 1)
    assert t1.id != t2.id
    assert t1.observed_through == at(0) and t2.observed_through == at(1)
    other_config = observe(
        plan,
        candles[:1],
        0,
        parameters=OutcomeParameters(rules_version="journal-outcome-v2"),
    )
    assert other_config.id != t1.id
    assert other_config.observation_rules_version == "journal-outcome-v2"


def test_future_candles_cannot_alter_a_historical_observation():
    plan = levels(targets=("110",))
    through_t1 = [
        candle(0, open_="99", high="101", low="99", close="100"),
        candle(1, open_="100", high="105", low="98", close="104"),
    ]
    historical = observe(plan, through_t1, 1)
    assert historical.status is OutcomeStatus.OPEN_AT_CUTOFF

    # The later candle completes the target, but it can never reach the T1
    # observation: the same T1 cutoff over the same T1 rows is byte-identical,
    # and handing the function a candle after the cutoff is refused outright.
    future = [
        *through_t1,
        candle(2, open_="104", high="112", low="104", close="110"),
    ]
    recomputed = observe(plan, [c for c in future if c.timestamp <= at(1)], 1)
    assert recomputed == historical
    assert recomputed.id == historical.id
    with pytest.raises(ValueError, match="inside the observation window"):
        observe(plan, future, 1)
    assert observe(plan, future, 2).status is OutcomeStatus.TARGETS_REACHED
    assert observe(plan, future, 2).id != historical.id


def test_observation_is_frozen_and_its_payload_round_trips_losslessly():
    plan = levels()
    observation = observe(
        plan,
        [
            candle(0, open_="99", high="101", low="99", close="100"),
            candle(1, open_="100", high="121", low="94", close="100"),
        ],
        1,
    )
    with pytest.raises(FrozenInstanceError):
        observation.status = OutcomeStatus.TARGETS_REACHED  # type: ignore[misc]
    payload = observation.to_json_dict()
    assert OutcomeObservation.from_json_dict(payload) == observation
    payload["status"] = "TARGETS_REACHED"
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert OutcomeObservation.from_json_dict(observation.to_json_dict()) == observation


def test_malformed_stored_payload_is_refused():
    plan = levels()
    observation = observe(plan, [], 0)
    payload = observation.to_json_dict()
    del payload["risk_per_unit"]
    with pytest.raises(JournalError, match="malformed"):
        OutcomeObservation.from_json_dict(payload)
    damaged = dict(observation.to_json_dict(), status="NOT_A_STATUS")
    with pytest.raises(JournalError, match="malformed"):
        OutcomeObservation.from_json_dict(damaged)


# ---------------------------------------------------------------------------
# Proposed-plan projection
# ---------------------------------------------------------------------------


def test_projection_from_a_plannable_plan_and_from_its_stored_json():
    snapshot, planning_frame, setup = result_and_setup()
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.PLANNABLE
    projection = ProposedPlanLevels.from_plan(plan)
    assert projection.plan_id == plan.id
    assert projection.entry == plan.entry.value
    assert projection.stop == plan.stop.value
    assert projection.targets == tuple(t.level.value for t in plan.targets)
    assert projection.risk_per_unit == plan.risk_per_unit
    assert projection.direction == plan.direction
    assert projection.as_of == plan.as_of
    from_payload = ProposedPlanLevels.from_payload(plan.to_json_dict())
    assert from_payload == projection


def test_projection_refuses_plans_that_are_not_plannable():
    seed = breakout()
    watch_frames = [frame(6, (seed,), close=112)]
    watch = result(watch_frames)
    setup = candidate(watch, seed)
    plan = plan_trade(snapshot=watch, frame=watch_frames[-1], setup_id=setup.id)
    assert plan.state is PlanState.NO_PLAN
    with pytest.raises(ValueError, match="PLANNABLE"):
        ProposedPlanLevels.from_plan(plan)
    with pytest.raises(TypeError, match="TradePlanResult"):
        ProposedPlanLevels.from_plan("not a plan")
    assert isinstance(plan, TradePlanResult)

    no_target = plan_trade(
        snapshot=snapshot_of_qualified()[0],
        frame=snapshot_of_qualified()[1],
        setup_id=snapshot_of_qualified()[2].id,
        parameters=PlanningParameters(r_multiple_fallbacks=()),
    )
    assert no_target.state is PlanState.NO_PLAN
    with pytest.raises(ValueError, match="PLANNABLE"):
        ProposedPlanLevels.from_plan(no_target)


def test_projection_rejects_inconsistent_numbers():
    with pytest.raises(ValueError, match="stop < entry"):
        levels(direction="bullish", entry="100", stop="105", targets=("110",))
    with pytest.raises(ValueError, match="target > entry"):
        levels(direction="bullish", entry="100", stop="95", targets=("99",))
    with pytest.raises(ValueError, match="stop > entry"):
        levels(direction="bearish", entry="100", stop="95", targets=("90",))
    with pytest.raises(ValueError, match="target < entry"):
        levels(direction="bearish", entry="100", stop="105", targets=("101",))
    with pytest.raises(ValueError, match="non-empty"):
        levels(targets=())
    with pytest.raises(ValueError, match="must not repeat"):
        levels(targets=("110", "110"))
    with pytest.raises(ValueError, match="risk_per_unit must be positive"):
        levels(risk="0")
    with pytest.raises(ValueError, match="direction"):
        levels(direction="sideways")


def test_projection_from_payload_refuses_malformed_or_unplannable_json():
    with pytest.raises(ValueError, match="PLANNABLE"):
        ProposedPlanLevels.from_payload({"state": "NO_PLAN"})
    snapshot, planning_frame, setup = result_and_setup()
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    payload = plan.to_json_dict()
    payload["entry"] = {"value": None}
    with pytest.raises((TypeError, ValueError), match="decimal string"):
        ProposedPlanLevels.from_payload(payload)
    broken = dict(plan.to_json_dict(), direction="sideways")
    with pytest.raises(ValueError, match="direction"):
        ProposedPlanLevels.from_payload(broken)


# ---------------------------------------------------------------------------
# Notes, symbol-generic behaviour and packaging hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "note,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("  keep exactly this wording  ", "keep exactly this wording"),
        ("line one\nline two\ttabbed", "line one\nline two\ttabbed"),
    ],
)
def test_note_normalization_keeps_wording_bounded(note, expected):
    assert normalize_note(note) == expected


def test_note_validation_rejects_unsafe_input():
    with pytest.raises(TypeError, match="string"):
        normalize_note(12)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="NUL"):
        normalize_note("a\x00b")
    with pytest.raises(ValueError, match="control characters"):
        normalize_note("a\x01b")
    with pytest.raises(ValueError, match="2000"):
        normalize_note("x" * 2001)


@pytest.mark.parametrize("symbol", ["ETH/USD", "SOL/USDC"])
def test_observation_is_symbol_generic(symbol):
    plan = levels(symbol=symbol)
    observation = observe(
        plan,
        [
            candle(0, open_="99", high="101", low="99", close="100", symbol=symbol),
            candle(1, open_="100", high="111", low="100", close="110", symbol=symbol),
        ],
        1,
    )
    assert observation.symbol == symbol
    assert observation.status is OutcomeStatus.OPEN_AT_CUTOFF
    assert observation.targets_reached == (0,)
    btc = observe(
        levels(),
        [
            candle(0, open_="99", high="101", low="99", close="100"),
            candle(1, open_="100", high="111", low="100", close="110"),
        ],
        1,
    )
    assert observation.id != btc.id


def test_journal_payload_keys_contain_no_execution_or_statistics_fields():
    plan = levels()
    observation = observe(
        plan,
        [
            candle(0, open_="99", high="101", low="99", close="100"),
            candle(1, open_="100", high="112", low="97", close="108"),
        ],
        1,
    )
    payload = observation.to_json_dict()

    def walk_keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from walk_keys(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk_keys(item)

    forbidden = {
        "balance",
        "leverage",
        "quantity",
        "notional",
        "margin",
        "position",
        "pnl",
        "fee",
        "funding",
        "slippage",
        "commission",
        "fill",
        "fills",
        "account",
        "api_key",
        "secret",
        "win_rate",
        "expectancy",
        "profit_factor",
        "sharpe",
        "equity",
        "realised_pnl",
        "realized_pnl",
    }
    keys = {key.lower() for key in walk_keys(payload)}
    assert forbidden.isdisjoint(keys)
    assert "status" in keys and "mfe_r" in keys and "mae_r" in keys


def test_journaling_sources_contain_no_exchange_or_secret_tokens():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for path in sorted((root / "src/trading_assistant/journaling").glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in (
            "ccxt",
            "api_key",
            "apikey",
            "secret",
            "passphrase",
            "load_markets",
            "requests.",
            "urllib",
            "http://",
            "https://",
        ):
            assert token not in text, (path.name, token)


def test_canonical_json_is_stable_and_sort_keyed():
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json(
        {"a": (1, 2), "b": 1}
    )


# ---------------------------------------------------------------------------
# Shared fixture plumbing
# ---------------------------------------------------------------------------


def snapshot_of_qualified():
    from test_trade_planning import qualified

    return qualified()


def result_and_setup():
    snapshot, planning_frame, setup = snapshot_of_qualified()
    return snapshot, planning_frame, setup
