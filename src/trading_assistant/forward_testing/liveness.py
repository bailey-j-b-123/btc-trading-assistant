"""Server-owned runner liveness: is the forward runner still reporting?

The dashboard used to derive its "RUNNER STARTED/PROCESSED/IDLE" all-clear from
the newest heartbeat's *status* alone. Heartbeat age was displayed and never
decisive, so a runner that had been silently dead for days still read healthy
whenever its stored candles stayed current and no catch-up was pending.

This module owns the missing judgement. It is a read-only assessment over the
heartbeat trail already in the ledger:

* a heartbeat whose age exceeds the documented operational tolerance is
  ``STALE_UNCONFIRMED`` — *not confirmed alive*, never "the process died";
* a heartbeat recorded after the assessing instant is a clock inconsistency and
  is NEVER confirmed alive, however small the disagreement (the diagnostic band
  only explains the wording);
* a clean ``STOPPED`` row is definitive, an ``ERROR`` row (or a recorded
  ``last_error``) is a failure, and a missing trail is ``NEVER_RUN``;
* only a heartbeat whose status is a running state and whose age is inside the
  tolerance is ``CONFIRMED_ALIVE``.

Market freshness is a different question, answered by ``web/freshness.py`` and
``market_data.data_health``: a stale runner says nothing about stored candles.

Nothing here writes, fetches, trades, or changes a strategy rule; the tolerance
comes from ``RunnerLivenessPolicy`` (``forward_testing.parameters``) and the
runner's own observed cadence, never from a hard-coded constant and never from
the reading process's own poll interval. See ``docs/runner-health.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from typing import Any

from trading_assistant.forward_testing.parameters import (
    RUNNER_LIVENESS_POLICY_VERSION,
    HeartbeatStatus,
    RunnerLivenessPolicy,
    RunnerSettings,
)
from trading_assistant.market_structure.numeric import as_decimal
from trading_assistant.market_structure.snapshot import to_jsonable

__all__ = [
    "CADENCE_INSUFFICIENT_EVIDENCE",
    "CADENCE_OBSERVED_HEARTBEATS",
    "CADENCE_UNCONFIRMED",
    "RUNNER_LIVENESS_POLICY_VERSION",
    "TOLERANCE_OBSERVED_CADENCE",
    "TOLERANCE_OPERATOR_SETTING",
    "TOLERANCE_POLICY_FLOOR",
    "RunnerLiveness",
    "RunnerLivenessState",
    "assess_runner_liveness",
    "observe_runner_cadence",
    "resolve_runner_liveness_policy",
]

#: Heartbeat statuses that mean "a running process recorded this row".
RUNNING_HEARTBEAT_STATUSES = (
    HeartbeatStatus.STARTED,
    HeartbeatStatus.PROCESSED,
    HeartbeatStatus.IDLE,
    HeartbeatStatus.NO_DATA,
)

#: How the tolerance was resolved (mirrors ``RunnerLivenessPolicy`` precedence).
TOLERANCE_OPERATOR_SETTING = "operator_setting"
TOLERANCE_OBSERVED_CADENCE = "observed_cadence"
TOLERANCE_POLICY_FLOOR = "policy_floor"

#: How much the observed cadence can be trusted.
CADENCE_OBSERVED_HEARTBEATS = "observed_heartbeats"
CADENCE_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
CADENCE_UNCONFIRMED = "unconfirmed"


class RunnerLivenessState(StrEnum):
    """The server's verdict on the runner process. Only one is alive-confirmed."""

    NEVER_RUN = "NEVER_RUN"
    CLOCK_INCONSISTENT = "CLOCK_INCONSISTENT"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    STALE_UNCONFIRMED = "STALE_UNCONFIRMED"
    CONFIRMED_ALIVE = "CONFIRMED_ALIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RunnerLiveness:
    """The complete, JSON-ready liveness verdict for one instrument.

    Every number is reported explicitly so the dashboard can quote the server
    verbatim instead of recomputing an age from the browser clock. ``age_seconds``
    is signed and honest: a negative value means the recorded instant is later
    than the assessing instant.
    """

    policy_version: str
    state: RunnerLivenessState
    alive_confirmed: bool
    heartbeat_present: bool
    heartbeat_status: str | None
    recorded_at: datetime | None
    assessed_at: datetime
    age_seconds: int | None
    tolerance_seconds: int
    tolerance_source: str
    cadence_seconds: int | None
    cadence_source: str
    cadence_gap_count: int
    heartbeat_rows_examined: int
    clock_skew_diagnostic_seconds: int
    configured_poll_interval_seconds: Decimal | None
    last_error: str | None
    detail: str

    def to_json_dict(self) -> dict[str, Any]:
        """JSON-safe projection (UTC ``Z`` datetimes, exact decimals as strings)."""

        return dict(to_jsonable(self))


def _status_value(status: HeartbeatStatus | str | None) -> str | None:
    if status is None:
        return None
    return status.value if isinstance(status, HeartbeatStatus) else str(status)


def _median_floor(values: Sequence[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    # An even count takes the lower of the two middle values: the median is a
    # deliberate, conservative floor, never an average that inflates it.
    return ordered[middle - 1]


def observe_runner_cadence(
    recorded_at_newest_first: Sequence[datetime],
    *,
    policy: RunnerLivenessPolicy,
) -> tuple[int | None, int]:
    """Median positive gap between the newest heartbeat rows, and how many.

    Only the newest ``policy.cadence_sample_size`` rows are examined. Rows written
    at one instant inside a single pass (a STARTED/PROCESSED/IDLE triple has zero
    gaps) are not a cadence and are excluded; a single restart or outage can
    produce one huge gap, so the median is used rather than the maximum. The
    returned gap count lets a caller decide whether the cadence is trustworthy
    (``policy.minimum_cadence_gaps``); the cadence itself is still reported when
    there is too little evidence, but with ``insufficient_evidence``.
    """

    rows = tuple(recorded_at_newest_first)[: policy.cadence_sample_size]
    # ``rows`` is newest first, so the left element of each pair is the later
    # instant: a positive gap is the time the runner went quiet between two rows.
    gaps = [
        int((newer - older).total_seconds())
        for newer, older in zip(rows, rows[1:], strict=False)
    ]
    positive = [gap for gap in gaps if gap > 0]
    if not positive:
        return None, 0
    return max(1, _median_floor(positive)), len(positive)


def _resolve_tolerance(
    policy: RunnerLivenessPolicy,
    *,
    cadence_seconds: int | None,
    cadence_source: str,
) -> tuple[int, str]:
    if policy.explicit_tolerance_seconds is not None:
        return int(policy.explicit_tolerance_seconds), TOLERANCE_OPERATOR_SETTING
    floor = int(policy.minimum_tolerance_seconds)
    if cadence_source == CADENCE_OBSERVED_HEARTBEATS and cadence_seconds is not None:
        widened = int(
            (Decimal(cadence_seconds) * policy.cadence_multiplier).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        if widened > floor:
            return widened, TOLERANCE_OBSERVED_CADENCE
    return floor, TOLERANCE_POLICY_FLOOR


def assess_runner_liveness(
    *,
    recorded_at_newest_first: Sequence[datetime],
    newest_status: HeartbeatStatus | str | None,
    newest_last_error: str | None,
    now: datetime,
    policy: RunnerLivenessPolicy,
    configured_poll_interval_seconds: Decimal | None = None,
    instrument_label: str | None = None,
) -> RunnerLiveness:
    """Assess whether the forward runner is still reporting, with exact numbers.

    Precedence, most definitive first: no trail at all (``NEVER_RUN``), a future
    instant (``CLOCK_INCONSISTENT``), a clean stop (``STOPPED``), a recorded
    failure (``FAILED``), a running status whose age exceeds the tolerance
    (``STALE_UNCONFIRMED``), a running status inside the tolerance
    (``CONFIRMED_ALIVE``), and anything else (``UNKNOWN``).
    """

    rows = tuple(recorded_at_newest_first)
    newest = rows[0] if rows else None
    # With no heartbeat row there is no recorded status and no recorded failure:
    # whatever the caller passed alongside an empty trail is not evidence.
    status_value = None if newest is None else _status_value(newest_status)
    if newest is None:
        newest_last_error = None
    cadence_seconds, gap_count = observe_runner_cadence(rows, policy=policy)
    if gap_count >= policy.minimum_cadence_gaps:
        cadence_source = CADENCE_OBSERVED_HEARTBEATS
    elif gap_count:
        cadence_source = CADENCE_INSUFFICIENT_EVIDENCE
    else:
        cadence_source = CADENCE_UNCONFIRMED
    tolerance_seconds, tolerance_source = _resolve_tolerance(
        policy, cadence_seconds=cadence_seconds, cadence_source=cadence_source
    )
    skew_band = int(policy.clock_skew_diagnostic_seconds)
    age_seconds = (
        None if newest is None else int((now - newest).total_seconds())
    )

    if newest is None:
        state = RunnerLivenessState.NEVER_RUN
    elif age_seconds is not None and age_seconds < 0:
        state = RunnerLivenessState.CLOCK_INCONSISTENT
    elif status_value == HeartbeatStatus.STOPPED.value:
        state = RunnerLivenessState.STOPPED
    elif status_value == HeartbeatStatus.ERROR.value or newest_last_error:
        state = RunnerLivenessState.FAILED
    elif (
        status_value in {status.value for status in RUNNING_HEARTBEAT_STATUSES}
        and age_seconds is not None
        and age_seconds > tolerance_seconds
    ):
        state = RunnerLivenessState.STALE_UNCONFIRMED
    elif status_value in {status.value for status in RUNNING_HEARTBEAT_STATUSES}:
        state = RunnerLivenessState.CONFIRMED_ALIVE
    else:
        state = RunnerLivenessState.UNKNOWN

    detail = _describe(
        instrument_label=instrument_label,
        state=state,
        status_value=status_value,
        age_seconds=age_seconds,
        tolerance_seconds=tolerance_seconds,
        tolerance_source=tolerance_source,
        cadence_seconds=cadence_seconds,
        cadence_source=cadence_source,
        skew_band=skew_band,
        recorded_at=newest,
        last_error=newest_last_error,
    )
    return RunnerLiveness(
        policy_version=RUNNER_LIVENESS_POLICY_VERSION,
        state=state,
        alive_confirmed=state is RunnerLivenessState.CONFIRMED_ALIVE,
        heartbeat_present=newest is not None,
        heartbeat_status=status_value,
        recorded_at=newest,
        assessed_at=now,
        age_seconds=age_seconds,
        tolerance_seconds=tolerance_seconds,
        tolerance_source=tolerance_source,
        cadence_seconds=cadence_seconds,
        cadence_source=cadence_source,
        cadence_gap_count=gap_count,
        heartbeat_rows_examined=len(rows[: policy.cadence_sample_size]),
        clock_skew_diagnostic_seconds=skew_band,
        configured_poll_interval_seconds=configured_poll_interval_seconds,
        last_error=newest_last_error,
        detail=detail,
    )


def _tolerance_phrase(
    tolerance_seconds: int,
    tolerance_source: str,
    *,
    cadence_seconds: int | None,
    cadence_source: str,
) -> str:
    if tolerance_source == TOLERANCE_OPERATOR_SETTING:
        return "operator setting"
    if tolerance_source == TOLERANCE_OBSERVED_CADENCE and cadence_seconds is not None:
        return (
            f"{tolerance_seconds}s = the policy multiplier applied to the "
            f"{cadence_seconds}s cadence observed from the newest heartbeat rows"
        )
    return "policy floor"


def _describe(
    *,
    instrument_label: str | None = None,
    state: RunnerLivenessState,
    status_value: str | None,
    age_seconds: int | None,
    tolerance_seconds: int,
    tolerance_source: str,
    cadence_seconds: int | None,
    cadence_source: str,
    skew_band: int,
    recorded_at: datetime | None,
    last_error: str | None,
) -> str:
    """One honest sentence per state: exact numbers, no death claim, no guess."""

    status_text = status_value if status_value is not None else "UNKNOWN"
    cadence_note = ""
    if cadence_source == CADENCE_INSUFFICIENT_EVIDENCE and cadence_seconds is not None:
        cadence_note = (
            f" A single observed gap of {cadence_seconds}s is reported but not "
            "trusted as a cadence, so the tolerance is not widened by it."
        )
    if state is RunnerLivenessState.NEVER_RUN:
        where = f" for {instrument_label}" if instrument_label else ""
        return (
            f"No heartbeat row exists{where}: the forward runner has never "
            "recorded a pass, so it is not confirmed alive."
        )
    if state is RunnerLivenessState.CLOCK_INCONSISTENT:
        assert age_seconds is not None and recorded_at is not None
        ahead = -age_seconds
        if ahead <= skew_band:
            return (
                f"The newest heartbeat ({status_text}) claims recorded_at "
                f"{recorded_at.isoformat().replace('+00:00', 'Z')}, {ahead}s ahead of "
                f"this server's clock — inside the {skew_band}s clock-drift "
                "diagnostic band, but still in the future, so liveness is not "
                "confirmed. The clocks must agree before any time-based verdict "
                "is trusted."
            )
        return (
            f"The newest heartbeat ({status_text}) claims recorded_at "
            f"{recorded_at.isoformat().replace('+00:00', 'Z')}, {ahead}s ahead of "
            f"this server's clock (clock-drift diagnostic band {skew_band}s), so no "
            "time-based verdict is trusted and the runner is not confirmed alive."
        )
    if state is RunnerLivenessState.STOPPED:
        return (
            f"The newest heartbeat (STOPPED) records a clean stop {age_seconds}s "
            "ago: the runner is not running. Recorded history is intact."
        )
    if state is RunnerLivenessState.FAILED:
        recorded_error = last_error or "no error detail was recorded"
        return (
            f"The newest heartbeat ({status_text}) was recorded {age_seconds}s ago "
            f"and reports a failure: {recorded_error}."
        )
    if state is RunnerLivenessState.STALE_UNCONFIRMED:
        return (
            f"The newest heartbeat ({status_text}) was recorded {age_seconds}s ago, "
            f"beyond the {tolerance_seconds}s tolerance "
            f"({_tolerance_phrase(tolerance_seconds, tolerance_source, cadence_seconds=cadence_seconds, cadence_source=cadence_source)}), "
            "so the runner is not confirmed alive. That is not proof the process "
            f"stopped: no heartbeat has been recorded since.{cadence_note}"
        )
    if state is RunnerLivenessState.CONFIRMED_ALIVE:
        return (
            f"The newest heartbeat ({status_text}) was recorded {age_seconds}s ago, "
            f"inside the {tolerance_seconds}s tolerance "
            f"({_tolerance_phrase(tolerance_seconds, tolerance_source, cadence_seconds=cadence_seconds, cadence_source=cadence_source)}), "
            f"so the runner process is confirmed alive.{cadence_note}"
        )
    return (
        f"The newest heartbeat records an unrecognised status {status_text!r}: "
        "liveness is unknown, so the runner is not confirmed alive."
    )


def resolve_runner_liveness_policy(
    runner_settings: RunnerSettings | None = None,
    *,
    operator_tolerance_seconds: Decimal | int | None = None,
) -> RunnerLivenessPolicy:
    """Resolve the operational liveness policy for a reader of the trail.

    Documented precedence: a tolerance stated by the caller (for example an
    explicitly constructed ``RunnerSettings``) wins; otherwise the operator's
    environment/configuration statement
    (``TRADING_ASSISTANT_RUNNER_LIVENESS_TOLERANCE_SECONDS``) is used verbatim and
    reported as ``operator_setting``; otherwise the policy stays as derived — the
    observed cadence widened by ``cadence_multiplier``, floored at
    ``minimum_tolerance_seconds``.

    The reading process's own poll interval is deliberately *not* part of this
    resolution: the web app is not the runner (``docs/runner-health.md``).
    """

    policy = (runner_settings if runner_settings is not None else RunnerSettings()).liveness
    if policy.explicit_tolerance_seconds is not None or operator_tolerance_seconds is None:
        return policy
    return replace(
        policy,
        explicit_tolerance_seconds=as_decimal(
            operator_tolerance_seconds,
            name="runner_liveness_tolerance_seconds",
        ),
    )
