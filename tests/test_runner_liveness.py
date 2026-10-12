"""Runner heartbeat liveness: the server-owned verdict behind the header pill.

Defect under repair (see ``docs/runner-health.md``): the dashboard derived its
"RUNNER STARTED/PROCESSED/IDLE" all-clear from the newest heartbeat's *status*
alone. Heartbeat age was displayed and never decisive, so a runner that had been
silently dead for days still read healthy whenever its stored candles stayed
current and no catch-up was pending — and a heartbeat claiming a future instant
read healthy too.

These tests are offline and synthetic only: temporary migrated SQLite databases,
the labelled fixture candles, injected clocks, and heartbeats written through the
real service API. Nothing touches the network, an exchange, project data, or a
strategy rule.

Two layers are pinned:

* the pure policy helper (``forward_testing.liveness``) — every state, every
  tolerance boundary, cadence observation, and operator override;
* ``ForwardTestService.status()`` — the verdict the API actually ships, including
  the reported false-healthy case and its recovery.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from forward_fixtures import (
    EXCHANGE,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    bar,
    labelled_series,
    make_harness,
    make_service,
)

from trading_assistant.forward_testing.liveness import (
    RUNNER_LIVENESS_POLICY_VERSION,
    RunnerLiveness,
    RunnerLivenessState,
    assess_runner_liveness,
    observe_runner_cadence,
)
from trading_assistant.forward_testing.parameters import (
    HeartbeatStatus,
    RunnerLivenessPolicy,
    RunnerSettings,
)

#: Assessment instant used by the pure-policy tests (a whole candle boundary).
NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)

DEFAULT_POLICY = RunnerLivenessPolicy()


def trail(*ages_seconds: float) -> tuple[datetime, ...]:
    """Heartbeat ``recorded_at`` values, newest first, from their ages."""

    return tuple(NOW - timedelta(seconds=age) for age in ages_seconds)


def spaced(interval_seconds: int, rows: int) -> tuple[datetime, ...]:
    """A heartbeat trail whose newest rows are ``interval_seconds`` apart."""

    return trail(*(index * interval_seconds for index in range(rows)))


def assess(
    recorded_at_newest_first,
    *,
    status: HeartbeatStatus | str = HeartbeatStatus.IDLE,
    last_error: str | None = None,
    now: datetime = NOW,
    policy: RunnerLivenessPolicy | None = None,
    configured_poll_interval_seconds: Decimal | None = None,
) -> RunnerLiveness:
    return assess_runner_liveness(
        recorded_at_newest_first=recorded_at_newest_first,
        newest_status=status,
        newest_last_error=last_error,
        now=now,
        policy=policy if policy is not None else DEFAULT_POLICY,
        configured_poll_interval_seconds=configured_poll_interval_seconds,
    )


# ----------------------------------------------------------------------
# A: the pure policy helper — states
# ----------------------------------------------------------------------


def test_a_recent_heartbeat_confirms_the_runner_is_alive() -> None:
    verdict = assess(trail(30))
    assert verdict.state is RunnerLivenessState.CONFIRMED_ALIVE
    assert verdict.alive_confirmed is True
    assert verdict.heartbeat_present is True
    assert verdict.age_seconds == 30
    assert verdict.heartbeat_status == HeartbeatStatus.IDLE.value
    assert verdict.policy_version == RUNNER_LIVENESS_POLICY_VERSION
    # The verdict states what it checked, with the exact numbers.
    assert "30s" in verdict.detail
    assert f"{verdict.tolerance_seconds}s" in verdict.detail
    assert verdict.tolerance_source in ("policy_floor", "observed_cadence", "operator_setting")


def test_every_running_heartbeat_status_can_confirm_liveness() -> None:
    for status in (
        HeartbeatStatus.STARTED,
        HeartbeatStatus.PROCESSED,
        HeartbeatStatus.IDLE,
        # NO_DATA is a statement about the market, not about the process: the
        # process was alive enough to record it. Market freshness is judged
        # separately (``market_data.data_health``).
        HeartbeatStatus.NO_DATA,
    ):
        verdict = assess(trail(5), status=status)
        assert verdict.state is RunnerLivenessState.CONFIRMED_ALIVE, status
        assert verdict.alive_confirmed is True, status


def test_a_stale_heartbeat_is_unconfirmed_and_never_claimed_dead() -> None:
    verdict = assess(trail(int(DEFAULT_POLICY.minimum_tolerance_seconds) + 60))
    assert verdict.state is RunnerLivenessState.STALE_UNCONFIRMED
    assert verdict.alive_confirmed is False
    assert verdict.age_seconds == int(DEFAULT_POLICY.minimum_tolerance_seconds) + 60
    assert verdict.tolerance_seconds == int(DEFAULT_POLICY.minimum_tolerance_seconds)
    # Exact numbers, and the honest reading: unconfirmed, not proof of death.
    assert f"{verdict.age_seconds}s" in verdict.detail
    assert f"{verdict.tolerance_seconds}s" in verdict.detail
    assert "not confirmed alive" in verdict.detail
    for forbidden in ("died", "dead", "crashed", "has stopped"):
        assert forbidden not in verdict.detail.lower()


def test_a_missing_heartbeat_is_never_run_and_never_healthy() -> None:
    verdict = assess(())
    assert verdict.state is RunnerLivenessState.NEVER_RUN
    assert verdict.alive_confirmed is False
    assert verdict.heartbeat_present is False
    assert verdict.heartbeat_status is None
    assert verdict.recorded_at is None
    assert verdict.age_seconds is None
    assert verdict.tolerance_seconds == int(DEFAULT_POLICY.minimum_tolerance_seconds)
    assert "never recorded" in verdict.detail


def test_a_failed_heartbeat_reports_the_failure() -> None:
    errored = assess(trail(12), status=HeartbeatStatus.ERROR, last_error="DNS failure")
    assert errored.state is RunnerLivenessState.FAILED
    assert errored.alive_confirmed is False
    assert "DNS failure" in errored.detail

    # A running status whose newest row still carries an error is a failure too:
    # only the NEWEST row is judged, and it says an error was recorded.
    carrying = assess(trail(12), last_error="boom")
    assert carrying.state is RunnerLivenessState.FAILED
    assert carrying.alive_confirmed is False

    # An old failure is still reported as the failure it recorded, with its age.
    ancient = assess(trail(9 * 86_400), status=HeartbeatStatus.ERROR, last_error="boom")
    assert ancient.state is RunnerLivenessState.FAILED
    assert ancient.alive_confirmed is False
    assert f"{ancient.age_seconds}s" in ancient.detail


def test_a_stopped_runner_is_reported_as_stopped_however_old_the_row_is() -> None:
    recent = assess(trail(40), status=HeartbeatStatus.STOPPED)
    assert recent.state is RunnerLivenessState.STOPPED
    assert recent.alive_confirmed is False
    assert "not running" in recent.detail

    # A clean stop is a definitive statement, so it outranks staleness: the
    # runner said it stopped, and nothing newer contradicts it.
    ancient = assess(trail(9 * 86_400), status=HeartbeatStatus.STOPPED)
    assert ancient.state is RunnerLivenessState.STOPPED
    assert ancient.alive_confirmed is False


def test_a_future_heartbeat_is_a_clock_inconsistency_and_never_healthy() -> None:
    verdict = assess(trail(-7_200))
    assert verdict.state is RunnerLivenessState.CLOCK_INCONSISTENT
    assert verdict.alive_confirmed is False
    assert verdict.age_seconds == -7_200
    assert "clock" in verdict.detail.lower()
    assert "7200s" in verdict.detail


def test_a_future_heartbeat_is_never_healthy_however_small_the_gap() -> None:
    """A negative age is never a liveness certificate — not even by one second.

    The diagnostic band only explains the wording of the clock disagreement; it
    can never license a positive verdict, so +1s, +3s and a large future instant
    all read as a clock inconsistency.
    """

    policy = RunnerLivenessPolicy(clock_skew_diagnostic_seconds=Decimal(5))
    for age in (-1, -3, -5, -6, -3_600, -7_200):
        verdict = assess(trail(age), policy=policy)
        assert verdict.state is RunnerLivenessState.CLOCK_INCONSISTENT, age
        assert verdict.alive_confirmed is False, age
        assert verdict.heartbeat_present is True, age
        # The signed age stays honest: it is not clamped into the past.
        assert verdict.age_seconds == age, age
        assert verdict.clock_skew_diagnostic_seconds == 5
        assert "clock" in verdict.detail.lower()
        assert f"{-age}s" in verdict.detail

    # Inside the band the wording explains a small drift; beyond it the wording
    # says the clocks materially disagree. Neither is alive.
    small = assess(trail(-3), policy=policy).detail
    large = assess(trail(-7_200), policy=policy).detail
    assert small != large
    assert "5s" in small and "diagnostic" in small


def test_exact_now_and_the_past_boundary_are_judged_by_the_stated_tolerance() -> None:
    """The boundary is exact: age 0 and age == tolerance are alive, +1s is not."""

    verdict = assess(trail(0))
    assert verdict.age_seconds == 0
    assert verdict.state is RunnerLivenessState.CONFIRMED_ALIVE
    assert verdict.alive_confirmed is True

    boundary = int(DEFAULT_POLICY.minimum_tolerance_seconds)
    at_boundary = assess(trail(boundary))
    assert at_boundary.age_seconds == boundary
    assert at_boundary.state is RunnerLivenessState.CONFIRMED_ALIVE
    assert at_boundary.alive_confirmed is True

    past_boundary = assess(trail(boundary + 1))
    assert past_boundary.age_seconds == boundary + 1
    assert past_boundary.state is RunnerLivenessState.STALE_UNCONFIRMED
    assert past_boundary.alive_confirmed is False


def test_an_unrecognised_heartbeat_status_is_unknown_not_healthy() -> None:
    verdict = assess(trail(10), status="SOMETHING_NEW")
    assert verdict.state is RunnerLivenessState.UNKNOWN
    assert verdict.alive_confirmed is False
    assert "SOMETHING_NEW" in verdict.detail


def test_the_newest_row_decides_so_a_recovery_clears_an_older_failure() -> None:
    # Trail: a healthy row now, an ERROR row one interval earlier. Only the
    # newest row feeds the verdict, so the older failure cannot persist.
    verdict = assess(trail(4, 64), status=HeartbeatStatus.PROCESSED)
    assert verdict.state is RunnerLivenessState.CONFIRMED_ALIVE
    assert verdict.alive_confirmed is True


# ----------------------------------------------------------------------
# B: the pure policy helper — tolerance boundaries
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_seconds", "expected_state"),
    [
        (0, RunnerLivenessState.CONFIRMED_ALIVE),
        (179, RunnerLivenessState.CONFIRMED_ALIVE),
        (180, RunnerLivenessState.CONFIRMED_ALIVE),  # exactly at tolerance
        (181, RunnerLivenessState.STALE_UNCONFIRMED),  # one second beyond
        (360, RunnerLivenessState.STALE_UNCONFIRMED),
    ],
)
def test_the_tolerance_boundary_is_exact(
    age_seconds: int, expected_state: RunnerLivenessState
) -> None:
    policy = RunnerLivenessPolicy(minimum_tolerance_seconds=Decimal(180))
    verdict = assess(trail(age_seconds), policy=policy)
    assert verdict.state is expected_state
    assert verdict.tolerance_seconds == 180
    assert verdict.alive_confirmed is (expected_state is RunnerLivenessState.CONFIRMED_ALIVE)


def test_the_default_policy_floor_is_documented_and_bounded() -> None:
    # 360s = the runner's documented default 60s poll interval plus its
    # documented 300s maximum transient backoff: the longest window in which a
    # healthy default-cadence runner may legitimately stay silent.
    assert DEFAULT_POLICY.minimum_tolerance_seconds == Decimal(360)
    assert DEFAULT_POLICY.cadence_multiplier == Decimal(3)
    assert DEFAULT_POLICY.clock_skew_diagnostic_seconds == Decimal(5)
    assert DEFAULT_POLICY.cadence_sample_size == 12
    assert DEFAULT_POLICY.minimum_cadence_gaps == 3
    assert DEFAULT_POLICY.explicit_tolerance_seconds is None
    assert assess(trail(360)).state is RunnerLivenessState.CONFIRMED_ALIVE
    assert assess(trail(361)).state is RunnerLivenessState.STALE_UNCONFIRMED


# ----------------------------------------------------------------------
# C: cadence — long processing and custom cadence are evidence, not defaults
# ----------------------------------------------------------------------


def test_an_observed_custom_cadence_widens_the_tolerance() -> None:
    # A runner that polls every 600s: 12 newest rows, 11 gaps of 600s.
    verdict = assess(spaced(600, 12))
    assert verdict.cadence_seconds == 600
    assert verdict.cadence_source == "observed_heartbeats"
    assert verdict.cadence_gap_count == 11
    assert verdict.heartbeat_rows_examined == 12
    assert verdict.tolerance_seconds == 1_800
    assert verdict.tolerance_source == "observed_cadence"
    # 1500s of silence is inside three of this runner's own observed intervals,
    # so it is honestly confirmed alive rather than falsely stale.
    assert verdict.state is RunnerLivenessState.CONFIRMED_ALIVE
    assert "600s" in verdict.detail and "1800s" in verdict.detail

    # The same 600s cadence, but the newest row is one second past 3 x 600s.
    beyond = assess(trail(*(1_801 + 600 * index for index in range(12))))
    assert beyond.cadence_seconds == 600
    assert beyond.tolerance_seconds == 1_800
    assert beyond.state is RunnerLivenessState.STALE_UNCONFIRMED
    assert beyond.alive_confirmed is False


def test_a_fast_cadence_never_tightens_the_tolerance_below_the_floor() -> None:
    cadence, gaps = observe_runner_cadence(spaced(10, 12), policy=DEFAULT_POLICY)
    assert cadence == 10 and gaps == 11
    verdict = assess(spaced(10, 12))
    assert verdict.cadence_seconds == 10
    assert verdict.tolerance_seconds == int(DEFAULT_POLICY.minimum_tolerance_seconds)
    assert verdict.tolerance_source == "policy_floor"


def test_a_single_heartbeat_leaves_the_cadence_unconfirmed() -> None:
    verdict = assess(trail(30))
    assert verdict.cadence_seconds is None
    assert verdict.cadence_source == "unconfirmed"
    assert verdict.cadence_gap_count == 0
    assert verdict.heartbeat_rows_examined == 1
    assert verdict.tolerance_source == "policy_floor"


def test_a_single_observed_gap_is_reported_but_not_trusted_as_a_cadence() -> None:
    """One gap can be an outage or a restart; it is evidence, not a cadence."""

    verdict = assess(spaced(3_600, 2))
    assert verdict.cadence_seconds == 3_600
    assert verdict.cadence_source == "insufficient_evidence"
    assert verdict.cadence_gap_count == 1
    assert verdict.tolerance_seconds == int(DEFAULT_POLICY.minimum_tolerance_seconds)
    assert verdict.tolerance_source == "policy_floor"
    # So an hour of silence after one hour-old gap is honestly unconfirmed.
    stale = assess(spaced(3_600, 2), now=NOW + timedelta(seconds=900))
    assert stale.state is RunnerLivenessState.STALE_UNCONFIRMED


def test_the_reading_process_poll_interval_is_never_used_as_the_cadence() -> None:
    """The web process is not the runner: its own default says nothing."""

    verdict = assess(trail(300), configured_poll_interval_seconds=Decimal(60))
    # The reading process's configured interval is reported for transparency and
    # never used as the runner's cadence: the tolerance is the documented floor.
    assert verdict.configured_poll_interval_seconds == Decimal(60)
    assert verdict.cadence_seconds is None
    assert verdict.cadence_source == "unconfirmed"
    assert verdict.tolerance_seconds == 360
    assert verdict.tolerance_source == "policy_floor"
    assert verdict.state is RunnerLivenessState.CONFIRMED_ALIVE

    # A one-second test cadence in the reading process must not make a 300s-old
    # heartbeat look stale either.
    fast_reader = assess(trail(300), configured_poll_interval_seconds=Decimal(1))
    assert fast_reader.tolerance_seconds == 360
    assert fast_reader.state is RunnerLivenessState.CONFIRMED_ALIVE


def test_zero_gaps_inside_one_pass_do_not_collapse_the_cadence() -> None:
    # One pass writes STARTED/PROCESSED/IDLE rows at a single clock instant, so
    # the newest gaps can legitimately be 0s. They are not a cadence.
    recorded = trail(0, 0, 0, 600, 1_200, 1_800)
    cadence, gaps = observe_runner_cadence(recorded, policy=DEFAULT_POLICY)
    assert cadence == 600
    assert gaps == 3


def test_one_outlier_gap_does_not_inflate_the_tolerance() -> None:
    # A two-day outage gap between otherwise 60s-cadence heartbeats must not buy
    # the runner days of silence: the median is used, never the maximum.
    ages = [index * 60 for index in range(11)]
    ages.append(11 * 60 + 2 * 86_400)
    cadence, gaps = observe_runner_cadence(trail(*ages), policy=DEFAULT_POLICY)
    assert cadence == 60
    assert gaps == 11
    verdict = assess(trail(*ages))
    assert verdict.tolerance_seconds == int(DEFAULT_POLICY.minimum_tolerance_seconds)


def test_an_operator_tolerance_override_is_used_and_named() -> None:
    policy = RunnerLivenessPolicy(explicit_tolerance_seconds=Decimal(7_200))
    verdict = assess(trail(3_600), policy=policy)
    assert verdict.tolerance_seconds == 7_200
    assert verdict.tolerance_source == "operator_setting"
    assert verdict.state is RunnerLivenessState.CONFIRMED_ALIVE
    assert "operator setting" in verdict.detail

    beyond = assess(trail(7_201), policy=policy)
    assert beyond.state is RunnerLivenessState.STALE_UNCONFIRMED
    assert beyond.tolerance_seconds == 7_200

    # An override outranks an observed cadence, and the cadence is still reported.
    both = assess(spaced(600, 12), policy=policy)
    assert both.cadence_seconds == 600
    assert both.tolerance_seconds == 7_200
    assert both.tolerance_source == "operator_setting"


def test_a_long_processing_pass_can_be_stated_by_the_operator() -> None:
    """A catch-up pass longer than the derived tolerance is declared, not guessed."""

    policy = RunnerLivenessPolicy(explicit_tolerance_seconds=Decimal(5_400))
    verdict = assess(trail(4_800), policy=policy)
    assert verdict.state is RunnerLivenessState.CONFIRMED_ALIVE
    assert verdict.tolerance_seconds == 5_400

    # Without that statement the same silence is honestly unconfirmed — and the
    # verdict says so without claiming the process died.
    undeclared = assess(trail(4_800))
    assert undeclared.state is RunnerLivenessState.STALE_UNCONFIRMED
    assert "not confirmed alive" in undeclared.detail


# ----------------------------------------------------------------------
# D: policy validation boundaries
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"minimum_tolerance_seconds": Decimal(0)}, "minimum_tolerance_seconds"),
        ({"minimum_tolerance_seconds": Decimal(-1)}, "minimum_tolerance_seconds"),
        ({"minimum_tolerance_seconds": Decimal(86_401)}, "minimum_tolerance_seconds"),
        ({"cadence_multiplier": Decimal(0)}, "cadence_multiplier"),
        ({"cadence_multiplier": Decimal("100.5")}, "cadence_multiplier"),
        ({"clock_skew_diagnostic_seconds": Decimal(-1)}, "clock_skew_diagnostic_seconds"),
        ({"clock_skew_diagnostic_seconds": Decimal(3_601)}, "clock_skew_diagnostic_seconds"),
        ({"cadence_sample_size": 1}, "cadence_sample_size"),
        ({"cadence_sample_size": 1_001}, "cadence_sample_size"),
        ({"minimum_cadence_gaps": 0}, "minimum_cadence_gaps"),
        ({"minimum_cadence_gaps": 1_001}, "minimum_cadence_gaps"),
        ({"explicit_tolerance_seconds": Decimal(0)}, "explicit_tolerance_seconds"),
        ({"explicit_tolerance_seconds": Decimal(86_401)}, "explicit_tolerance_seconds"),
    ],
)
def test_the_liveness_policy_rejects_impossible_values(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RunnerLivenessPolicy(**kwargs)


def test_the_liveness_policy_accepts_its_documented_boundaries() -> None:
    policy = RunnerLivenessPolicy(
        minimum_tolerance_seconds=Decimal(1),
        cadence_multiplier=Decimal(100),
        clock_skew_diagnostic_seconds=Decimal(3_600),
        cadence_sample_size=1_000,
        minimum_cadence_gaps=1,
        explicit_tolerance_seconds=Decimal(86_400),
    )
    assert policy.minimum_tolerance_seconds == Decimal(1)
    assert policy.explicit_tolerance_seconds == Decimal(86_400)
    # Plain ints/strings are coerced to exact decimals, never floats.
    coerced = RunnerLivenessPolicy(minimum_tolerance_seconds=120)
    assert coerced.minimum_tolerance_seconds == Decimal(120)


def test_the_liveness_policy_is_carried_by_the_operational_runner_settings() -> None:
    settings = RunnerSettings()
    assert isinstance(settings.liveness, RunnerLivenessPolicy)
    assert settings.liveness == DEFAULT_POLICY
    custom = RunnerSettings(
        liveness=RunnerLivenessPolicy(explicit_tolerance_seconds=Decimal(900))
    )
    assert custom.liveness.explicit_tolerance_seconds == Decimal(900)
    assert custom.interval_seconds == RunnerSettings().interval_seconds
    with pytest.raises(TypeError):
        RunnerSettings(liveness={"minimum_tolerance_seconds": 1})


def test_the_verdict_is_json_serialisable_with_exact_numbers() -> None:
    payload = assess(trail(1_800)).to_json_dict()
    assert payload["state"] == "STALE_UNCONFIRMED"
    assert payload["alive_confirmed"] is False
    assert payload["age_seconds"] == 1_800
    assert payload["tolerance_seconds"] == 360
    assert payload["recorded_at"] == (
        NOW - timedelta(seconds=1_800)
    ).isoformat().replace("+00:00", "Z")
    assert payload["policy_version"] == RUNNER_LIVENESS_POLICY_VERSION
    assert payload["clock_skew_diagnostic_seconds"] == 5
    assert isinstance(payload["detail"], str) and payload["detail"]
    assert isinstance(payload["configured_poll_interval_seconds"], (str, type(None)))


# ----------------------------------------------------------------------
# E: the service payload — the reported defect, end to end
# ----------------------------------------------------------------------


def _harness_with_one_recorded_close():
    """A temp DB whose newest close is recorded and whose candles are current."""

    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.processed_boundaries, "the fixture close must be recorded"
    return harness


def test_status_reports_a_stale_runner_while_market_data_stays_current() -> None:
    """The exact reported false-healthy case, on a temporary database.

    The runner recorded the newest close and then went silent. Thirty minutes
    later the stored candles are still CURRENT and nothing is pending, so the
    old dashboard read a healthy "RUNNER PROCESSED". The server now owns the
    liveness verdict and says the runner is not confirmed alive.
    """

    harness = _harness_with_one_recorded_close()
    try:
        later = harness.clock["t"] + timedelta(minutes=30)
        status = harness.service.status(now=later)

        # Market freshness is genuinely current and nothing is pending: the
        # runner verdict must not be derived from either.
        assert status["market_data"]["data_health"] == "CURRENT"
        assert status["sample"]["pending_catch_up_boundaries"] == 0
        assert status["runner"] is not None
        assert status["runner"]["status"] in ("STARTED", "PROCESSED", "IDLE")
        assert status["runner"]["last_error"] is None
        assert status["runner"]["heartbeat_age_seconds"] >= 1_800

        liveness = status["runner_liveness"]
        assert liveness["state"] == "STALE_UNCONFIRMED"
        assert liveness["alive_confirmed"] is False
        assert liveness["age_seconds"] == status["runner"]["heartbeat_age_seconds"]
        assert liveness["policy_version"] == RUNNER_LIVENESS_POLICY_VERSION
        assert f"{liveness['age_seconds']}s" in liveness["detail"]
        assert "not confirmed alive" in liveness["detail"]
        # Stale is not a death claim, and not a market-data claim.
        for forbidden in ("died", "dead", "crashed"):
            assert forbidden not in liveness["detail"].lower()
        assert status["market_data"]["data_health"] != "STALE"
    finally:
        harness.engine.dispose()


def test_status_confirms_a_runner_that_just_recorded_a_heartbeat() -> None:
    harness = _harness_with_one_recorded_close()
    try:
        status = harness.service.status(now=harness.clock["t"] + timedelta(seconds=30))
        liveness = status["runner_liveness"]
        assert liveness["state"] == "CONFIRMED_ALIVE"
        assert liveness["alive_confirmed"] is True
        assert liveness["heartbeat_status"] == status["runner"]["status"]
        assert liveness["recorded_at"] is not None
        assert liveness["tolerance_seconds"] == 360
        assert status["market_data"]["data_health"] == "CURRENT"
        assert status["sample"]["pending_catch_up_boundaries"] == 0
    finally:
        harness.engine.dispose()


def test_status_reports_never_run_when_no_heartbeat_row_exists() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    try:
        status = harness.service.status(now=harness.clock["t"])
        assert status["runner"] is None
        liveness = status["runner_liveness"]
        assert liveness["state"] == "NEVER_RUN"
        assert liveness["alive_confirmed"] is False
        assert liveness["heartbeat_present"] is False
        assert liveness["recorded_at"] is None
        assert liveness["age_seconds"] is None
        assert SYMBOL in liveness["detail"] and TIMEFRAME in liveness["detail"]
    finally:
        harness.engine.dispose()


def test_status_reports_a_failed_heartbeat_as_a_failure() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    try:
        harness.service.record_runner_event(
            status=HeartbeatStatus.ERROR,
            detail="forward runner pass failed (ConnectionError)",
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            now=harness.clock["t"],
            last_error="ConnectionError: DNS resolution failed",
            error_type="ConnectionError",
        )
        liveness = harness.service.status(now=harness.clock["t"] + timedelta(seconds=5))[
            "runner_liveness"
        ]
        assert liveness["state"] == "FAILED"
        assert liveness["alive_confirmed"] is False
        assert liveness["heartbeat_status"] == "ERROR"
        assert liveness["last_error"] == "ConnectionError: DNS resolution failed"
        assert "DNS resolution failed" in liveness["detail"]
    finally:
        harness.engine.dispose()


@pytest.mark.parametrize("ahead_seconds", [1, 3, 7_200])
def test_status_reports_every_future_heartbeat_as_a_clock_inconsistency(
    ahead_seconds: int,
) -> None:
    """A heartbeat later than the server instant is never healthy, by any amount."""

    harness = _harness_with_one_recorded_close()
    try:
        future = harness.clock["t"] + timedelta(seconds=ahead_seconds)
        harness.service.record_runner_event(
            status=HeartbeatStatus.IDLE,
            detail="synthetic fixture heartbeat recorded ahead of the server clock",
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            now=future,
        )
        # Assessed at the harness instant, so every fixture heartbeat is
        # genuinely ahead of the server clock — by +1s and +3s as well as +2h.
        status = harness.service.status(now=harness.clock["t"])
        liveness = status["runner_liveness"]
        assert liveness["state"] == "CLOCK_INCONSISTENT"
        assert liveness["alive_confirmed"] is False
        assert liveness["age_seconds"] == -ahead_seconds
        assert f"{ahead_seconds}s" in liveness["detail"]
        assert "clock" in liveness["detail"].lower()
        # The unchanged runner block still reports the recorded facts verbatim.
        assert status["runner"]["heartbeat_age_seconds"] == -ahead_seconds
    finally:
        harness.engine.dispose()


def test_status_reports_a_stopped_runner_as_not_running() -> None:
    harness = _harness_with_one_recorded_close()
    try:
        stopped_at = harness.clock["t"] + timedelta(seconds=60)
        harness.service.record_runner_event(
            status=HeartbeatStatus.STOPPED,
            detail="forward runner stopped cleanly; recorded history is intact",
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            now=stopped_at,
        )
        liveness = harness.service.status(now=stopped_at + timedelta(seconds=5))[
            "runner_liveness"
        ]
        assert liveness["state"] == "STOPPED"
        assert liveness["alive_confirmed"] is False
        assert liveness["heartbeat_status"] == "STOPPED"
        assert "not running" in liveness["detail"]
    finally:
        harness.engine.dispose()


def test_a_fresh_heartbeat_after_silence_clears_the_stale_verdict() -> None:
    harness = _harness_with_one_recorded_close()
    try:
        stale_at = harness.clock["t"] + timedelta(minutes=30)
        stale = harness.service.status(now=stale_at)["runner_liveness"]
        assert stale["state"] == "STALE_UNCONFIRMED"
        assert stale["alive_confirmed"] is False

        # The runner comes back and processes the boundary it missed: the newest
        # row is healthy again, so the verdict clears on the next read.
        harness.store((bar(21, 126, low=123),))
        harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
        harness.run(refresh_market_data=False)
        recovered = harness.service.status(now=harness.clock["t"])["runner_liveness"]
        assert recovered["state"] == "CONFIRMED_ALIVE"
        assert recovered["alive_confirmed"] is True
        # One gap between the two passes is not trusted as a cadence, so the
        # documented floor still applies.
        assert recovered["cadence_source"] == "insufficient_evidence"
        assert recovered["tolerance_source"] == "policy_floor"
    finally:
        harness.engine.dispose()


def test_the_observed_cadence_of_a_slow_runner_is_used_by_the_service() -> None:
    """A 600s-cadence runner is not judged by the reading process's defaults."""

    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    try:
        base = harness.clock["t"]
        for index in range(12):
            harness.service.record_runner_event(
                status=HeartbeatStatus.IDLE,
                detail="synthetic fixture heartbeat for cadence observation",
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                now=base + timedelta(seconds=600 * index),
            )
        status = harness.service.status(now=base + timedelta(seconds=600 * 11 + 1_500))
        liveness = status["runner_liveness"]
        assert liveness["cadence_seconds"] == 600
        assert liveness["cadence_source"] == "observed_heartbeats"
        assert liveness["cadence_gap_count"] == 11
        assert liveness["heartbeat_rows_examined"] == 12
        assert liveness["tolerance_seconds"] == 1_800
        assert liveness["tolerance_source"] == "observed_cadence"
        assert liveness["state"] == "CONFIRMED_ALIVE"

        # Beyond three observed intervals the same runner is unconfirmed.
        later = harness.service.status(now=base + timedelta(seconds=600 * 11 + 1_801))
        assert later["runner_liveness"]["state"] == "STALE_UNCONFIRMED"
    finally:
        harness.engine.dispose()


def test_the_service_honours_an_explicit_operator_tolerance() -> None:
    harness = _harness_with_one_recorded_close()
    try:
        settings = harness.settings.model_copy(
            update={"runner_liveness_tolerance_seconds": 7_200}
        )
        service = make_service(
            harness.engine, settings, None, clock=lambda: harness.clock["t"]
        )
        status = service.status(now=harness.clock["t"] + timedelta(minutes=30))
        liveness = status["runner_liveness"]
        assert liveness["tolerance_seconds"] == 7_200
        assert liveness["tolerance_source"] == "operator_setting"
        assert liveness["state"] == "CONFIRMED_ALIVE"

        # The same instant is unconfirmed under the documented default policy.
        default = harness.service.status(now=harness.clock["t"] + timedelta(minutes=30))
        assert default["runner_liveness"]["state"] == "STALE_UNCONFIRMED"
    finally:
        harness.engine.dispose()


def test_a_constructor_policy_overrides_the_derived_tolerance() -> None:
    harness = _harness_with_one_recorded_close()
    try:
        service = make_service(
            harness.engine,
            harness.settings,
            None,
            clock=lambda: harness.clock["t"],
            runner_settings=RunnerSettings(
                liveness=RunnerLivenessPolicy(
                    minimum_tolerance_seconds=Decimal(60),
                    cadence_multiplier=Decimal(2),
                )
            ),
        )
        liveness = service.status(now=harness.clock["t"] + timedelta(seconds=90))[
            "runner_liveness"
        ]
        assert liveness["tolerance_seconds"] == 60
        assert liveness["tolerance_source"] == "policy_floor"
        assert liveness["state"] == "STALE_UNCONFIRMED"
    finally:
        harness.engine.dispose()


def test_status_liveness_writes_nothing_and_reads_only_the_newest_rows() -> None:
    harness = _harness_with_one_recorded_close()
    try:
        before = harness.counts()
        rows_before = len(
            harness.service.ledger.recent_heartbeats(
                exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, limit=1_000
            )
        )
        for offset in (0, 60, 1_800, 86_400):
            payload = harness.service.status(
                now=harness.clock["t"] + timedelta(seconds=offset)
            )
            assert (
                payload["runner_liveness"]["policy_version"]
                == RUNNER_LIVENESS_POLICY_VERSION
            )
        assert harness.counts() == before
        rows_after = harness.service.ledger.recent_heartbeats(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, limit=1_000
        )
        assert len(rows_after) == rows_before
    finally:
        harness.engine.dispose()


def test_the_cadence_trail_is_bounded_by_the_policy_sample_size() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    try:
        base = harness.clock["t"]
        for index in range(30):
            harness.service.record_runner_event(
                status=HeartbeatStatus.IDLE,
                detail="synthetic fixture heartbeat",
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                now=base + timedelta(seconds=60 * index),
            )
        rows = harness.service.ledger.recent_heartbeats(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, limit=12
        )
        assert len(rows) == 12
        # Newest first, matching ``latest_heartbeat`` exactly.
        newest = harness.service.ledger.latest_heartbeat(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
        )
        assert rows[0].recorded_at == newest.recorded_at
        assert rows[0].heartbeat_id == newest.heartbeat_id
        recorded = [row.recorded_at for row in rows]
        assert recorded == sorted(recorded, reverse=True)

        liveness = harness.service.status(now=base + timedelta(seconds=60 * 29 + 30))[
            "runner_liveness"
        ]
        assert liveness["heartbeat_rows_examined"] == 12
        assert liveness["cadence_seconds"] == 60
        assert liveness["tolerance_seconds"] == 360
        assert liveness["tolerance_source"] == "policy_floor"

        with pytest.raises(ValueError):
            harness.service.ledger.recent_heartbeats(
                exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, limit=0
            )
        with pytest.raises(ValueError):
            harness.service.ledger.recent_heartbeats(
                exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, limit=1_001
            )
    finally:
        harness.engine.dispose()


def test_liveness_is_scoped_to_one_instrument() -> None:
    """Another instrument's fresh heartbeat cannot confirm this one."""

    harness = _harness_with_one_recorded_close()
    try:
        fresh_elsewhere = harness.clock["t"] + timedelta(minutes=29)
        harness.service.record_runner_event(
            status=HeartbeatStatus.PROCESSED,
            detail="synthetic fixture heartbeat for a different timeframe",
            symbol=SYMBOL,
            timeframe="15m",
            now=fresh_elsewhere,
        )
        liveness = harness.service.status(now=fresh_elsewhere + timedelta(minutes=1))[
            "runner_liveness"
        ]
        assert liveness["state"] == "STALE_UNCONFIRMED"
        other = harness.service.status(
            timeframe="15m", now=fresh_elsewhere + timedelta(minutes=1)
        )["runner_liveness"]
        assert other["state"] == "CONFIRMED_ALIVE"
    finally:
        harness.engine.dispose()


# ----------------------------------------------------------------------
# F: the verdict reaches the dashboard API unchanged
# ----------------------------------------------------------------------


def test_the_forward_api_ships_the_liveness_verdict_to_the_dashboard() -> None:
    from web_fixtures import make_client

    harness = _harness_with_one_recorded_close()
    try:
        stale_clock = harness.clock["t"] + timedelta(minutes=30)
        client = make_client(harness.engine, harness.settings, clock=stale_clock)
        status = client.get("/api/forward", params={"limit": 1}).json()["status"]
        assert status["market_data"]["data_health"] == "CURRENT"
        assert status["sample"]["pending_catch_up_boundaries"] == 0
        assert status["runner"]["status"] in ("STARTED", "PROCESSED", "IDLE")
        assert status["runner_liveness"]["state"] == "STALE_UNCONFIRMED"
        assert status["runner_liveness"]["alive_confirmed"] is False

        live_clock = harness.clock["t"] + timedelta(seconds=10)
        live_client = make_client(harness.engine, harness.settings, clock=live_clock)
        live = live_client.get("/api/forward", params={"limit": 1}).json()
        assert live["status"]["runner_liveness"]["state"] == "CONFIRMED_ALIVE"
        assert live["status"]["runner_liveness"]["alive_confirmed"] is True
    finally:
        harness.engine.dispose()
