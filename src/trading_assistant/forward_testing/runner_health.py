"""Read-only heartbeat recency evidence, separate from market/strategy decisions."""

from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from statistics import median

from trading_assistant.market_data.timeframes import require_utc_datetime


@dataclass(frozen=True, slots=True)
class RunnerHealthPolicy:
    """Reader policy; an observed gap is not proof of the actual polling interval.

    The 360-second default monitoring floor allows for the documented default
    60-second poll and 300-second transient backoff. Three positive gaps are
    required before observed cadence can widen the floor. Operators can state a
    tolerance for long processing; a stale result never asserts process death.
    """

    minimum_tolerance_seconds: int = 360
    cadence_multiplier: int = 3
    sample_size: int = 12
    minimum_cadence_gaps: int = 3

    def __post_init__(self):
        for name in ("minimum_tolerance_seconds", "cadence_multiplier", "sample_size", "minimum_cadence_gaps"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 2 <= self.sample_size <= 1000:
            raise ValueError("sample_size must be between 2 and 1000")
        if self.minimum_cadence_gaps >= self.sample_size:
            raise ValueError("minimum_cadence_gaps must be smaller than sample_size")


DEFAULT_POLICY = RunnerHealthPolicy()


def assess_runner_health(
    heartbeat, *, now: datetime, recent_heartbeats=(),
    tolerance_seconds: int | None = None, policy: RunnerHealthPolicy = DEFAULT_POLICY,
) -> dict:
    """Project evidence only: no clock reads, database writes or process probes."""

    now = require_utc_datetime(now, field_name="now")
    if tolerance_seconds is not None and (
        isinstance(tolerance_seconds, bool) or not isinstance(tolerance_seconds, int)
        or not 1 <= tolerance_seconds <= 86_400
    ):
        raise ValueError("tolerance_seconds must be an integer between 1 and 86400")
    timestamps = sorted({
        row.recorded_at for row in islice(recent_heartbeats, policy.sample_size)
        if row.recorded_at <= now
    })
    gaps = [(right - left).total_seconds() for left, right in zip(timestamps, timestamps[1:])]
    cadence = median(gaps) if gaps else None
    trusted = len(gaps) >= policy.minimum_cadence_gaps
    tolerance = policy.minimum_tolerance_seconds
    source = "policy_floor"
    if trusted and cadence * policy.cadence_multiplier > tolerance:
        tolerance = cadence * policy.cadence_multiplier
        source = "observed_heartbeats"
    if tolerance_seconds is not None:
        tolerance, source = tolerance_seconds, "operator_setting"
    age = None if heartbeat is None else (now - heartbeat.recorded_at).total_seconds()
    result = {
        "policy_version": "runner-health-v1", "evaluated_at": now.isoformat(),
        "age_seconds": age, "tolerance_seconds": tolerance, "tolerance_source": source,
        "cadence_seconds": cadence, "cadence_gap_count": len(gaps),
        "cadence_source": "observed_heartbeats" if trusted else "insufficient_evidence" if gaps else "unconfirmed",
        "heartbeat_status": None if heartbeat is None else str(heartbeat.status),
        "state": "NEVER_RUN", "alive_confirmed": False,
        "detail": "No runner heartbeat has been recorded; liveness is unconfirmed.",
    }
    if heartbeat is None:
        return result
    if heartbeat.recorded_at > now:
        state, detail = "CLOCK_INCONSISTENT", "The heartbeat is in the future; clock consistency and liveness are unconfirmed."
    elif heartbeat.last_error or str(heartbeat.status) == "ERROR":
        state, detail = "FAILED", "The newest heartbeat reports a processing error."
    elif str(heartbeat.status) == "STOPPED":
        state, detail = "STOPPED", "The newest heartbeat reports that the runner stopped."
    elif str(heartbeat.status) not in {"STARTED", "PROCESSED", "IDLE"}:
        state, detail = "UNCONFIRMED", "The recorded runner status does not confirm healthy operation."
    elif age > tolerance:
        state, detail = "STALE_UNCONFIRMED", f"Heartbeat {age:g}s old exceeds {tolerance:g}s tolerance; liveness is unconfirmed, not proof that the process died."
    else:
        state, detail = "CONFIRMED_ALIVE", f"Heartbeat {age:g}s old is within {tolerance:g}s monitoring tolerance; recent activity was reported, not a process probe."
        result["alive_confirmed"] = True
    result.update(state=state, detail=detail)
    return result
