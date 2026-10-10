"""Phase 3 — journal-outcome-v2 1-minute ordering evidence (pure semantics).

These tests pin the deterministic behaviour of ``observe_outcome`` under the
journal-outcome-v2 rules: genuine, confirmed stored 1-minute candles may order
events inside one ambiguous higher-timeframe candle, and nothing else changes.
The plan's entry, stop, targets, planning timestamp, and minimum-1R levels are
never influenced by the 1-minute series. Whenever the evidence is missing,
incomplete, inconsistent with the higher-timeframe candle, or still ambiguous
at 1-minute granularity, the observation stays an explicitly unscored
``AMBIGUOUS`` — the favourable result is never chosen.

Everything is offline and deterministic: no network, no database, no clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from trading_assistant.journaling.observation import (
    ENTRY_AND_EXIT_SAME_CANDLE,
    RESOLUTION_COVERAGE_INCOMPLETE,
    RESOLUTION_CONSISTENCY_CONFLICT,
    RESOLUTION_NO_CANDLES,
    RESOLUTION_SAME_MINUTE_AMBIGUOUS,
    RESOLUTION_TIMEFRAME,
    RESOLUTION_USED,
    STOP_AND_TARGET_SAME_CANDLE,
    observe_outcome,
)
from trading_assistant.journaling.parameters import (
    OUTCOME_RESOLUTION_RULES_VERSION,
    OUTCOME_RULES_VERSION,
    OutcomeParameters,
)
from trading_assistant.journaling.types import (
    OutcomeObservation,
    OutcomeStatus,
    ProposedPlanLevels,
)
from trading_assistant.market_data.types import Candle

EPOCH = datetime(2024, 1, 1, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
FIVE_MINUTES = timedelta(minutes=5)

V2 = OutcomeParameters(rules_version=OUTCOME_RESOLUTION_RULES_VERSION)


def htf(index: int, o: str, h: str, l: str, c: str, *, timeframe: str = "1h") -> Candle:
    step = HOUR if timeframe == "1h" else FIVE_MINUTES
    return Candle(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe=timeframe,
        timestamp=EPOCH + step * index,
        open=D(o),
        high=D(h),
        low=D(l),
        close=D(c),
        volume=D("1"),
    )


def minute_path(points: list[str], *, close: str, length: int) -> tuple[str, ...]:
    """Expand explicit path points to ``length`` prices ending at ``close``.

    The last explicit point is repeated until index ``length - 1``, which is
    set to ``close`` — mirroring a price path that settles after its moves.
    """

    values = list(points)
    if len(values) > length - 1:
        raise ValueError("too many path points for the requested length")
    values += [values[-1]] * (length - 1 - len(values)) + [close]
    return tuple(values)


def build_minutes(
    htf_candle: Candle,
    *,
    path: tuple[str, ...],
    extra_lows: dict[int, str] | None = None,
    extra_highs: dict[int, str] | None = None,
    opens: dict[int, str] | None = None,
    interval_minutes: int = 60,
) -> tuple[Candle, ...]:
    """Compose ``interval_minutes`` 1-minute candles for one higher-timeframe
    candle from a price path (minute ``i`` opens at ``path[i]`` and closes at
    ``path[i + 1]``) plus optional wicks. ``opens`` overrides one minute's
    open so a genuine gap between two minutes can be modelled. Raises when the
    composition does not reproduce the higher-timeframe candle exactly — the
    same integrity guard the observation applies, so tests fail loudly on bad
    evidence.
    """

    if len(path) != interval_minutes + 1:
        raise ValueError("path length must be interval_minutes + 1")
    lows = {index: D(value) for index, value in (extra_lows or {}).items()}
    highs = {index: D(value) for index, value in (extra_highs or {}).items()}
    open_overrides = {
        index: D(value) for index, value in (opens or {}).items()
    }
    base_open = htf_candle.timestamp
    bars: list[Candle] = []
    for index in range(interval_minutes):
        open_price = open_overrides.get(index, D(path[index]))
        close_price = D(path[index + 1])
        low = min(open_price, close_price, lows.get(index, open_price))
        high = max(open_price, close_price, highs.get(index, close_price))
        bars.append(
            Candle(
                exchange="binance",
                symbol="BTC/USDT",
                timeframe="1m",
                timestamp=base_open + MINUTE * index,
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=D("1"),
            )
        )
    if bars[0].open != htf_candle.open:
        raise ValueError("path[0] must equal the higher-timeframe open")
    if bars[-1].close != htf_candle.close:
        raise ValueError("path[-1] must equal the higher-timeframe close")
    if max(bar.high for bar in bars) != htf_candle.high:
        raise ValueError("a wick must reproduce the higher-timeframe high")
    if min(bar.low for bar in bars) != htf_candle.low:
        raise ValueError("a wick must reproduce the higher-timeframe low")
    return tuple(bars)


def plan_levels(
    *,
    entry: str = "100",
    stop: str = "95",
    targets: tuple[str, ...] = ("110",),
    direction: str = "bullish",
    timeframe: str = "1h",
    as_of: datetime = EPOCH,
) -> ProposedPlanLevels:
    entry_d, stop_d = D(entry), D(stop)
    return ProposedPlanLevels(
        plan_id="plan-1",
        exchange="binance",
        symbol="BTC/USDT",
        timeframe=timeframe,
        direction=direction,
        entry=entry_d,
        stop=stop_d,
        targets=tuple(D(target) for target in targets),
        risk_per_unit=abs(entry_d - stop_d),
        as_of=as_of,
        setup_id="setup-1",
    )


def ambiguous_entry_stop_candle() -> Candle:
    """A 1h candle touching entry 100 and stop 95, but not target 110."""

    return htf(1, "101.5", "101.5", "94", "95")


def resolving_minutes_entry_then_stop(htf_candle: Candle) -> tuple[Candle, ...]:
    """Entry touched at minute 0 alone; stop touched at minute 40 alone."""

    path = minute_path(
        ["101.5", "100.8", "100", "96"], close="95", length=61
    )
    return build_minutes(
        htf_candle, path=path, extra_lows={0: "100", 40: "94"}, extra_highs={0: "101.5"}
    )


def observe(
    levels,
    candles,
    through_index: int,
    *,
    parameters=None,
    resolution_candles=None,
    timeframe: str = "1h",
):
    step = HOUR if timeframe == "1h" else FIVE_MINUTES
    kwargs = {}
    if resolution_candles is not None:
        kwargs["resolution_candles"] = resolution_candles
    return observe_outcome(
        journal_id="journal-1",
        levels=levels,
        candles=candles,
        observed_through=EPOCH + step * through_index,
        parameters=parameters,
        **kwargs,
    )


# ----------------------------------------------------------------------
# Baselines: v1 semantics are untouched, v2 without evidence matches v1
# ----------------------------------------------------------------------


def test_v1_same_candle_entry_and_exit_is_ambiguous() -> None:
    levels = plan_levels()
    candles = (htf(0, "101", "102", "100.5", "101.5"), ambiguous_entry_stop_candle())
    observation = observe(levels, candles, 1)
    assert observation.observation_rules_version == OUTCOME_RULES_VERSION
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.ambiguity_kind == ENTRY_AND_EXIT_SAME_CANDLE
    assert observation.entry_ordered is False
    assert observation.resolution_timeframe is None
    assert observation.resolution_attempted is False


def test_v1_refuses_resolution_candles() -> None:
    levels = plan_levels()
    candles = (htf(0, "101", "102", "100.5", "101.5"), ambiguous_entry_stop_candle())
    minutes = resolving_minutes_entry_then_stop(candles[1])
    with pytest.raises(ValueError, match="journal-outcome-v2"):
        observe(levels, candles, 1, resolution_candles=minutes)


def test_v2_without_evidence_records_the_same_ambiguity_with_a_reason() -> None:
    levels = plan_levels()
    candles = (htf(0, "101", "102", "100.5", "101.5"), ambiguous_entry_stop_candle())
    baseline = observe(levels, candles, 1)
    observation = observe(levels, candles, 1, parameters=V2, resolution_candles=[])
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.ambiguity_kind == ENTRY_AND_EXIT_SAME_CANDLE
    assert observation.entry_ordered is False
    assert observation.entry_reached is True
    assert observation.resolution_timeframe == RESOLUTION_TIMEFRAME
    assert observation.resolution_attempted is True
    assert observation.resolution_used is False
    assert observation.resolution_reason == RESOLUTION_NO_CANDLES
    assert observation.resolution_expected_candles == 60
    assert observation.resolution_present_candles == 0
    assert observation.resolution_missing_candles == 60
    # The v1/v2 statuses agree; only the version and the resolution audit
    # metadata differ, so the favourable result is never smuggled in.
    assert baseline.status is observation.status
    assert baseline.ambiguity_kind == observation.ambiguity_kind


# ----------------------------------------------------------------------
# Successful ordering from genuine, complete, consistent 1-minute evidence
# ----------------------------------------------------------------------


def test_entry_strictly_before_stop_is_resolved_to_stopped() -> None:
    levels = plan_levels()
    candles = (htf(0, "101", "102", "100.5", "101.5"), ambiguous_entry_stop_candle())
    minutes = resolving_minutes_entry_then_stop(candles[1])
    observation = observe(levels, candles, 1, parameters=V2, resolution_candles=minutes)
    assert observation.status is OutcomeStatus.STOPPED
    assert observation.entry_ordered is True
    assert observation.entry_timestamp == candles[1].timestamp  # minute 0 open
    assert observation.stop_timestamp == candles[1].timestamp + MINUTE * 40
    assert observation.resolution_used is True
    assert observation.resolution_reason == RESOLUTION_USED
    assert observation.resolution_expected_candles == 60
    assert observation.resolution_present_candles == 60
    assert observation.resolution_missing_candles == 0
    assert [event.token for event in observation.events] == ["entry", "stop"]
    assert all(event.ordering.value == "ordered" for event in observation.events)


def test_entry_strictly_before_target_is_resolved_to_targets_reached() -> None:
    levels = plan_levels()
    # Candle 1 touches entry 100 and target 110 but not stop 95.
    candle = htf(1, "101.5", "111", "100", "109")
    candles = (htf(0, "101", "102", "100.5", "101.5"), candle)
    path = minute_path(["101.5", "103", "106", "108"], close="109", length=61)
    minutes = build_minutes(
        candle, path=path, extra_highs={20: "111"}, extra_lows={0: "100"}
    )
    observation = observe(levels, candles, 1, parameters=V2, resolution_candles=minutes)
    assert observation.status is OutcomeStatus.TARGETS_REACHED
    assert observation.targets_reached == (0,)
    assert observation.resolution_used is True
    assert observation.entry_timestamp == candle.timestamp
    assert observation.entry_ordered is True


def test_stop_before_entry_is_resolved_to_invalidated_before_entry() -> None:
    """The 1m path jumps through the entry level without trading it: the stop
    wick is the only genuine touch, so the plan premise fails pre-entry."""

    levels = plan_levels()
    candle = htf(0, "101.5", "101.5", "94", "95")
    # Minutes 0-4 stay above the entry; minute 5 opens below it (a gap the
    # 1-minute ranges evidence) and wicks through the stop; entry 100 is
    # inside the HTF range but inside no 1m range.
    path = minute_path(
        ["101.5", "101.5", "101.5", "101.5", "100.5", "100.5", "95"],
        close="95",
        length=61,
    )
    minutes = build_minutes(
        candle, path=path, extra_lows={5: "94"}, opens={5: "99.5"}
    )
    assert all(not (m.low <= D("100") <= m.high) for m in minutes)
    observation = observe(
        levels, (candle,), 0, parameters=V2, resolution_candles=minutes
    )
    assert observation.status is OutcomeStatus.INVALIDATED_BEFORE_ENTRY
    assert observation.stop_pre_entry is True
    assert observation.entry_ordered is False
    assert observation.resolution_used is True


def test_pre_entry_target_touch_then_ordered_entry_continues() -> None:
    levels = plan_levels()
    # HTF candle touches entry 100 and target 110: ambiguous at 1h. The 1m
    # evidence touches the target first (pre-entry), then orders the entry.
    candle = htf(0, "99", "111", "98.5", "100.5")
    points = (
        ["99"]
        + ["99.5"] * 5
        + ["109"] * 25
        + ["100.4"]
    )
    path = minute_path(points, close="100.5", length=61)
    minutes = build_minutes(
        candle,
        path=path,
        extra_highs={5: "111"},
        extra_lows={30: "99.9", 40: "98.5"},
        opens={5: "109.5", 30: "100.2"},
    )
    observation = observe(
        levels, (candle,), 0, parameters=V2, resolution_candles=minutes
    )
    assert observation.status is OutcomeStatus.OPEN_AT_CUTOFF
    assert observation.entry_ordered is True
    assert observation.targets_pre_entry == (0,)
    assert observation.targets_reached == ()
    assert observation.resolution_used is True


def test_stop_and_target_candle_is_resolved_by_the_minute_order() -> None:
    levels = plan_levels()
    first = htf(0, "99", "102", "99", "101")  # orders the entry alone
    # Candle 1 touches stop 95 and target 110 after the entry is ordered.
    second = htf(1, "101", "111", "94", "105")
    candles = (first, second)
    flat = minute_path(["101", "105"], close="105", length=61)
    target_first = build_minutes(
        second, path=flat, extra_highs={10: "111"}, extra_lows={50: "94"}
    )
    stop_first = build_minutes(
        second, path=flat, extra_highs={50: "111"}, extra_lows={10: "94"}
    )
    resolved_target = observe(
        levels, candles, 1, parameters=V2, resolution_candles=target_first
    )
    assert resolved_target.status is OutcomeStatus.TARGETS_REACHED
    assert resolved_target.resolution_used is True
    resolved_stop = observe(
        levels, candles, 1, parameters=V2, resolution_candles=stop_first
    )
    assert resolved_stop.status is OutcomeStatus.STOPPED
    assert resolved_stop.resolution_used is True


def test_three_ambiguous_candles_are_all_resolved_in_one_observation() -> None:
    """Candle 1: entry is jumped, only a pre-entry target touch (continues).
    Candle 2: entry ordered, first target reached (continues). Candle 3: last
    target before the stop (terminal)."""

    levels = plan_levels(targets=("108", "110"))
    quiet = htf(0, "101", "102", "100.5", "101.5")  # touches nothing
    first = htf(1, "99", "109", "99", "107")  # entry + target 1, no stop
    second = htf(2, "107", "108.5", "99.5", "107.5")  # entry + target 1
    third = htf(3, "107.5", "111", "94", "105")  # stop + target 2
    candles = (quiet, first, second, third)

    path_first = minute_path(
        ["99", "99.5", "99.5", "99.5", "99.5", "99.5", "107"], close="107", length=61
    )
    minutes_first = build_minutes(
        first,
        path=path_first,
        extra_highs={10: "109"},
        extra_lows={0: "99"},
        opens={5: "107.5"},
    )
    # The entry 100 lies inside the HTF range but inside no 1m range.
    assert all(not (m.low <= D("100") <= m.high) for m in minutes_first)

    path_second = minute_path(["107", "106", "101", "100.5"], close="107.5", length=61)
    minutes_second = build_minutes(
        second,
        path=path_second,
        extra_lows={2: "100", 50: "99.5"},
        extra_highs={20: "108.5"},
    )

    path_third = minute_path(["107.5", "106"], close="105", length=61)
    minutes_third = build_minutes(
        third, path=path_third, extra_highs={10: "111"}, extra_lows={30: "94"}
    )

    observation = observe(
        levels,
        candles,
        3,
        parameters=V2,
        resolution_candles=minutes_first + minutes_second + minutes_third,
    )
    assert observation.status is OutcomeStatus.TARGETS_REACHED
    assert observation.targets_reached == (0, 1)
    assert observation.resolution_used is True
    assert observation.resolution_expected_candles == 180
    assert observation.resolution_present_candles == 180


# ----------------------------------------------------------------------
# Refusals: the result stays an explicitly unscored AMBIGUOUS
# ----------------------------------------------------------------------


def test_same_minute_entry_and_exit_stays_ambiguous() -> None:
    levels = plan_levels()
    candle = ambiguous_entry_stop_candle()
    candles = (htf(0, "101", "102", "100.5", "101.5"), candle)
    # Minute 0 alone spans from the entry down through the stop: both levels
    # are touched within one minute.
    path = minute_path(["101.5", "96", "95"], close="95", length=61)
    minutes = build_minutes(candle, path=path, extra_lows={0: "94"})
    observation = observe(levels, candles, 1, parameters=V2, resolution_candles=minutes)
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.ambiguity_kind == ENTRY_AND_EXIT_SAME_CANDLE
    assert observation.entry_ordered is False
    assert observation.resolution_attempted is True
    assert observation.resolution_used is False
    assert observation.resolution_reason == RESOLUTION_SAME_MINUTE_AMBIGUOUS
    assert observation.resolution_missing_candles == 0


def test_missing_minutes_keep_the_ambiguity_and_report_the_gap() -> None:
    levels = plan_levels()
    candles = (htf(0, "101", "102", "100.5", "101.5"), ambiguous_entry_stop_candle())
    complete = resolving_minutes_entry_then_stop(candles[1])
    partial = tuple(candle for candle in complete if candle.timestamp.minute != 20)
    observation = observe(levels, candles, 1, parameters=V2, resolution_candles=partial)
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.resolution_reason == RESOLUTION_COVERAGE_INCOMPLETE
    assert observation.resolution_expected_candles == 60
    assert observation.resolution_present_candles == 59
    assert observation.resolution_missing_candles == 1
    expected_missing_open = candles[1].timestamp + MINUTE * 20
    assert [
        (gap.start, gap.end, gap.missing_count)
        for gap in observation.resolution_missing_ranges
    ] == [(expected_missing_open, expected_missing_open, 1)]


def _replace_minute(
    minutes: tuple[Candle, ...], index: int, **prices: str
) -> tuple[Candle, ...]:
    original = minutes[index]
    replaced = Candle(
        exchange=original.exchange,
        symbol=original.symbol,
        timeframe=original.timeframe,
        timestamp=original.timestamp,
        open=D(prices.get("open", format(original.open, "f"))),
        high=D(prices.get("high", format(original.high, "f"))),
        low=D(prices.get("low", format(original.low, "f"))),
        close=D(prices.get("close", format(original.close, "f"))),
        volume=original.volume,
    )
    return minutes[:index] + (replaced,) + minutes[index + 1 :]


def test_consistency_conflict_keeps_the_ambiguity() -> None:
    levels = plan_levels()
    candle = ambiguous_entry_stop_candle()
    candles = (htf(0, "101", "102", "100.5", "101.5"), candle)
    good = resolving_minutes_entry_then_stop(candle)
    for broken in (
        _replace_minute(good, 0, open="999"),  # first open != HTF open
        _replace_minute(good, 59, close="999"),  # last close != HTF close
        _replace_minute(good, 10, high="200"),  # minute high above HTF high
        _replace_minute(good, 10, low="10"),  # minute low below HTF low
    ):
        observation = observe(
            levels, candles, 1, parameters=V2, resolution_candles=broken
        )
        assert observation.status is OutcomeStatus.AMBIGUOUS
        assert observation.resolution_reason == RESOLUTION_CONSISTENCY_CONFLICT
        assert observation.resolution_used is False


def test_resolution_progress_before_a_later_same_minute_keeps_partial_state() -> None:
    """Entry ordered at minute 0, then stop and target co-touched at minute 40:
    the observation stays AMBIGUOUS (never a win), but the proven ordered
    entry is recorded rather than discarded."""

    levels = plan_levels()
    candle = htf(1, "101.5", "111", "94", "105")
    candles = (htf(0, "101", "102", "100.5", "101.5"), candle)
    path = minute_path(["101.5", "103", "102", "104"], close="105", length=61)
    minutes = build_minutes(
        candle,
        path=path,
        extra_lows={0: "100", 40: "94"},
        extra_highs={0: "101.5", 40: "111"},
    )
    observation = observe(levels, candles, 1, parameters=V2, resolution_candles=minutes)
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.ambiguity_kind == STOP_AND_TARGET_SAME_CANDLE
    assert observation.entry_ordered is True
    assert observation.entry_timestamp == candle.timestamp
    assert observation.resolution_used is False
    assert observation.resolution_reason == RESOLUTION_SAME_MINUTE_AMBIGUOUS


# ----------------------------------------------------------------------
# Timeframes, alignment, and window validation
# ----------------------------------------------------------------------


def test_five_minute_plan_uses_five_resolution_minutes() -> None:
    levels = plan_levels(timeframe="5m")
    candle = htf(1, "101.5", "101.5", "94", "95", timeframe="5m")
    candles = (htf(0, "101", "102", "100.5", "101.5", timeframe="5m"), candle)
    path = minute_path(
        ["101.5", "100.8", "100", "96", "95"], close="95", length=6
    )
    minutes = build_minutes(
        candle,
        path=path,
        extra_lows={0: "100", 3: "94"},
        extra_highs={0: "101.5"},
        interval_minutes=5,
    )
    assert len(minutes) == 5
    observation = observe(
        levels, candles, 1, parameters=V2, resolution_candles=minutes, timeframe="5m"
    )
    assert observation.status is OutcomeStatus.STOPPED
    assert observation.resolution_expected_candles == 5
    assert observation.resolution_used is True


def test_resolution_candles_for_a_1m_plan_are_rejected() -> None:
    levels = plan_levels(timeframe="1m")
    candle = Candle(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="1m",
        timestamp=EPOCH,
        open=D("101"),
        high=D("101"),
        low=D("94"),
        close=D("95"),
        volume=D("1"),
    )
    with pytest.raises(ValueError, match="strictly finer"):
        observe_outcome(
            journal_id="journal-1",
            levels=levels,
            candles=(candle,),
            observed_through=EPOCH,
            parameters=V2,
            resolution_candles=(candle,),
        )


def test_resolution_candle_validation_is_strict() -> None:
    levels = plan_levels()
    candle = ambiguous_entry_stop_candle()
    candles = (htf(0, "101", "102", "100.5", "101.5"), candle)
    good = resolving_minutes_entry_then_stop(candle)
    base = good[0]

    def call(resolution_candles) -> None:
        observe(levels, candles, 1, parameters=V2, resolution_candles=resolution_candles)

    def variant(**fields) -> Candle:
        return Candle(
            exchange=fields.get("exchange", base.exchange),
            symbol=fields.get("symbol", base.symbol),
            timeframe=fields.get("timeframe", "1m"),
            timestamp=fields.get("timestamp", base.timestamp),
            open=fields.get("open", base.open),
            high=fields.get("high", base.high),
            low=fields.get("low", base.low),
            close=fields.get("close", base.close),
            volume=base.volume,
        )

    with pytest.raises(ValueError, match="instrument"):
        call((variant(symbol="ETH/USDT"),))
    with pytest.raises(ValueError, match="1m"):
        call((variant(timeframe="5m"),))
    with pytest.raises(ValueError, match="align"):
        call((variant(timestamp=base.timestamp + timedelta(seconds=30)),))
    with pytest.raises(ValueError, match="window span"):
        call(good + (variant(timestamp=candle.timestamp + HOUR),))
    with pytest.raises(ValueError, match="repeat"):
        call((good[0],) + good)
    with pytest.raises(ValueError, match="high must not be below"):
        call((variant(high=D("90"), low=D("99")),))
    with pytest.raises(TypeError, match="Candle"):
        call(("not-a-candle",))


def test_htf_gap_still_takes_precedence_over_resolution() -> None:
    levels = plan_levels()
    candle2 = ambiguous_entry_stop_candle()
    minutes = resolving_minutes_entry_then_stop(candle2)
    # Candle index 1 is absent from the HTF series: the window is incomplete
    # before any resolution evidence is consulted.
    candles = (htf(0, "101", "102", "100.5", "101.5"), htf(2, "95", "96", "94", "95"))
    observation = observe(levels, candles, 2, parameters=V2, resolution_candles=minutes)
    assert observation.status is OutcomeStatus.INCOMPLETE_DATA
    assert observation.missing_candle_count == 1


# ----------------------------------------------------------------------
# Identity, determinism, and payload compatibility
# ----------------------------------------------------------------------


def test_v1_and_v2_observations_have_distinct_ids_but_v1_is_stable() -> None:
    levels = plan_levels()
    candles = (htf(0, "101", "102", "100.5", "101.5"), ambiguous_entry_stop_candle())
    v1_first = observe(levels, candles, 1)
    v1_second = observe(levels, candles, 1)
    v2 = observe(levels, candles, 1, parameters=V2, resolution_candles=[])
    assert v1_first.id == v1_second.id
    assert v1_first.id != v2.id
    assert v1_first.to_json_dict() == v1_second.to_json_dict()


def test_v1_payloads_keep_their_exact_historical_shape() -> None:
    levels = plan_levels()
    candles = (htf(0, "101", "102", "100.5", "101.5"), ambiguous_entry_stop_candle())
    payload = observe(levels, candles, 1).to_json_dict()
    assert not any(key.startswith("resolution_") for key in payload)
    v2_payload = observe(
        levels, candles, 1, parameters=V2, resolution_candles=[]
    ).to_json_dict()
    assert v2_payload["resolution_timeframe"] == "1m"
    assert v2_payload["resolution_reason"] == RESOLUTION_NO_CANDLES


def test_stored_v1_payload_without_resolution_keys_still_decodes() -> None:
    levels = plan_levels()
    candles = (htf(0, "101", "102", "100.5", "101.5"), ambiguous_entry_stop_candle())
    legacy_payload = observe(levels, candles, 1).to_json_dict()
    decoded = OutcomeObservation.from_json_dict(legacy_payload)
    assert decoded.resolution_timeframe is None
    assert decoded.resolution_attempted is False
    assert decoded.resolution_missing_ranges == ()


def test_v2_payload_round_trips_losslessly() -> None:
    levels = plan_levels()
    candle = ambiguous_entry_stop_candle()
    candles = (htf(0, "101", "102", "100.5", "101.5"), candle)
    partial = resolving_minutes_entry_then_stop(candle)[1:]  # coverage gap
    observation = observe(levels, candles, 1, parameters=V2, resolution_candles=partial)
    decoded = OutcomeObservation.from_json_dict(observation.to_json_dict())
    assert decoded == observation
    assert decoded.resolution_reason == RESOLUTION_COVERAGE_INCOMPLETE
    assert decoded.resolution_missing_ranges == observation.resolution_missing_ranges


def test_resolution_is_deterministic_for_identical_inputs() -> None:
    levels = plan_levels()
    candle = ambiguous_entry_stop_candle()
    candles = (htf(0, "101", "102", "100.5", "101.5"), candle)
    minutes = resolving_minutes_entry_then_stop(candle)
    first = observe(levels, candles, 1, parameters=V2, resolution_candles=minutes)
    second = observe(levels, candles, 1, parameters=V2, resolution_candles=minutes)
    assert first.id == second.id
    assert first.to_json_dict() == second.to_json_dict()


def test_no_ambiguity_means_no_resolution_audit_beyond_the_policy() -> None:
    levels = plan_levels()
    # Entry ordered alone on candle 0, stop alone on candle 1: never ambiguous.
    candles = (htf(0, "99", "102", "99", "101"), htf(1, "101", "101", "94", "95"))
    observation = observe(levels, candles, 1, parameters=V2, resolution_candles=[])
    assert observation.status is OutcomeStatus.STOPPED
    assert observation.resolution_timeframe == RESOLUTION_TIMEFRAME
    assert observation.resolution_attempted is False
    assert observation.resolution_used is False
    assert observation.resolution_reason is None
