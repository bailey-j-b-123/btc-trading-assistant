"""Versioned, non-optimising controls for Step 12 live forward paper testing.

These controls govern *observation only*: how many closed candles a paper
observation may look forward, how many boundaries one catch-up pass may record,
the sample-reporting floor, and the explicitly stated friction scenario. They
contain **no** setup, pattern, qualification, or planning thresholds — those
remain owned by Steps 3–6 and are never touched here. Nothing in this module
searches, tunes, or selects a parameter from results.

Every stored forward value is built from these helpers, so identities are
deterministic SHA-256 fingerprints of canonical JSON rather than random UUIDs,
exactly like Steps 4–7 and Step 11. Versions are recorded on every row: a future
semantic change produces a new version and therefore different identities
instead of silently reinterpreting earlier forward history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any

from trading_assistant.historical_validation.parameters import FrictionAssumptions
from trading_assistant.market_structure.numeric import as_decimal, require_int
from trading_assistant.market_structure.snapshot import to_jsonable

#: Version of the forward ledger contract (identity + stored fields).
#:
#: v2 adds the deterministic no-trade reasons: at most one unresolved paper
#: trade exists per instrument at a time (a second plannable setup is recorded
#: with a reason instead of a paper plan), and a monitored setup that was
#: plannable earlier but whose decision-time entry no longer reaches the
#: mandatory reward-to-risk floor is recorded as MISSED. A v1 ledger allowed
#: concurrent paper plans, so the cohorts must never be merged silently.
#:
#: v3 splits slot occupancy from scored settlement. ``AMBIGUOUS`` remains a
#: terminal unscored outcome (never re-observed, never a win/loss/confirmed
#: fill) but keeps occupying the one-active paper-trade slot until the original
#: observation horizon elapses. v2 treated ``AMBIGUOUS`` as freeing the
#: instrument immediately, so v2 and v3 cohorts must never be merged silently.
FORWARD_LEDGER_RULES_VERSION = "forward-ledger-v3"

#: Version of the closed-candle forward runner/cycle contract.
FORWARD_RUNNER_RULES_VERSION = "forward-runner-v1"

#: Version of the forward evaluation controls (horizon, catch-up, sample floor).
FORWARD_PARAMETERS_VERSION = "forward-parameters-v1"

#: Recorded on a candidate observation when its PLANNABLE Step 6 plan is refused
#: because the instrument already has one occupying (active) paper trade on some
#: timeframe — the guard is instrument-wide, never per-timeframe: the candidate
#: is still monitored, but no second paper trade is created. An ``AMBIGUOUS``
#: paper trade occupies the slot until its original observation horizon elapses.
PAPER_TRADE_ACTIVE_REASON = "NO TRADE — BTC paper trade already active."

#: Recorded on a candidate observation that was genuinely plannable earlier, is
#: still a valid (QUALIFIED, not invalidated) opportunity, and is not being
#: paper-traded, but whose decision-time reward-to-risk has deteriorated below
#: the mandatory 1R floor: at the price actually available now, no remaining
#: genuine structural target reaches 1R. The
#: opportunity is gone; nothing is chased, no old entry is reused, no stop is
#: squeezed, and no target is invented.
MISSED_OPPORTUNITY_REASON = (
    "MISSED — price moved before execution; remaining reward-to-risk is below 1R."
)


def fingerprint(*parts: object) -> str:
    """Return a canonical, version-prefixed identity for forward material."""

    payload = json.dumps(to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    return sha256((FORWARD_LEDGER_RULES_VERSION + ":" + payload).encode()).hexdigest()


def canonical_json(value: object) -> str:
    """Exact stored rendering of any forward value (sorted keys, no whitespace)."""

    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"))


class CycleStatus(StrEnum):
    """What one recorded candle-close processing pass could establish.

    ``COMPLETE``
        A closed base candle, a Step 3/4 frame, and a Step 5 snapshot were all
        available at this boundary, so the recorded decision is a real one.
    ``MISSING_CANDLE``
        No stored candle exists for this boundary's own interval. The system
        says so, records nothing else, and never fabricates the candle.
    ``MISSING_FRAME``
        The base candle exists but no exact Step 3/4 frame could be built for
        this boundary, so no conclusion is recorded.
    ``INSUFFICIENT_HISTORY``
        Too little history existed at this boundary to reach the configured
        minimum; recorded as unknown rather than guessed.
    """

    COMPLETE = "COMPLETE"
    MISSING_CANDLE = "MISSING_CANDLE"
    MISSING_FRAME = "MISSING_FRAME"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class DataHealth(StrEnum):
    """Deterministic market-data health for one forward decision boundary.

    ``CURRENT``
        The latest expected closed candle is stored and the analysed window has
        no missing candles.
    ``STALE``
        Stored candles stop before the latest expected closed candle; the
        staleness count is recorded.
    ``INCOMPLETE``
        Candles are current but at least one expected candle inside the analysed
        window is missing (a real gap in the stored series).
    ``HISTORICAL``
        The evaluated boundary is older than the newest boundary at the clock:
        a catch-up pass over past closes, never presented as current data.
    ``UNKNOWN``
        No stored candles exist for this instrument/timeframe.
    """

    CURRENT = "CURRENT"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    HISTORICAL = "HISTORICAL"
    UNKNOWN = "UNKNOWN"


class HeartbeatStatus(StrEnum):
    """Runner lifecycle states as recorded by the forward runner itself.

    A heartbeat is a statement about the *process*, never about the market. The
    dashboard shows the newest heartbeat (including its server-computed age)
    and derives the SYSTEM OK/WARNING verdict from freshness, completeness,
    pending catch-up, and heartbeat status plus the absence of a recorded
    error — heartbeat age is displayed, never decisive.
    """

    STARTED = "STARTED"
    PROCESSED = "PROCESSED"
    IDLE = "IDLE"
    NO_DATA = "NO_DATA"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class VersionSeparation(StrEnum):
    """How forward statistics treated incompatible recorded versions.

    ``SINGLE_VERSION``
        Every stored cycle carries one identical strategy/config version
        fingerprint, so combining them is meaningful.
    ``SEPARATED``
        More than one version fingerprint is stored. Forward statistics are
        reported per version cohort and the combined figures are explicitly
        withheld: results produced by different rules are never silently mixed.
    """

    SINGLE_VERSION = "SINGLE_VERSION"
    SEPARATED = "SEPARATED"


@dataclass(frozen=True, slots=True)
class ForwardParameters:
    """Evaluation-only configuration for one forward testing session.

    ``observation_horizon_candles`` caps how many closed candles after a paper
    plan may be observed (the same idea Step 11 uses for historical cohorts), so
    a plan that never resolves becomes ``OPEN_AT_CUTOFF`` rather than staying
    open forever. ``minimum_history_candles`` is a *runner precondition* (how
    much stored history must exist before a forward decision is recorded), not a
    strategy rule: the qualification engine still decides everything it always
    decided. ``max_catch_up_candles`` bounds how many missed boundaries one pass
    records, and any remainder is reported as pending instead of being skipped.
    """

    rules_version: str = FORWARD_PARAMETERS_VERSION
    observation_horizon_candles: int = 20
    minimum_sample_size: int = 20
    minimum_history_candles: int = 10
    max_catch_up_candles: int = 720
    friction: FrictionAssumptions = FrictionAssumptions()

    def __post_init__(self) -> None:
        if not isinstance(self.rules_version, str) or not self.rules_version.strip():
            raise ValueError("rules_version must be a non-empty string")
        object.__setattr__(self, "rules_version", self.rules_version.strip())
        require_int(
            self.observation_horizon_candles,
            name="observation_horizon_candles",
            minimum=1,
            maximum=100_000,
        )
        require_int(
            self.minimum_sample_size,
            name="minimum_sample_size",
            minimum=1,
            maximum=1_000_000,
        )
        require_int(
            self.minimum_history_candles,
            name="minimum_history_candles",
            minimum=1,
            maximum=100_000,
        )
        require_int(
            self.max_catch_up_candles,
            name="max_catch_up_candles",
            minimum=1,
            maximum=100_000,
        )
        if not isinstance(self.friction, FrictionAssumptions):
            raise TypeError("friction must be FrictionAssumptions")

    def fingerprint(self) -> str:
        """Versioned identity of this exact forward evaluation configuration."""

        return fingerprint("forward-parameters", self)


@dataclass(frozen=True, slots=True)
class RunnerSettings:
    """Operational (non-strategy) runner settings: cadence, retries, logging.

    ``interval_seconds`` is how often the runner looks for a newly closed
    candle; it can never change what a closed candle means. Retries are
    conservative and bounded: a failed public-market-data request is retried a
    few times and then reported as an error, never filled in from a guess.

    Failures are classified, not lumped together. A *transient external*
    failure (connection timeout, DNS failure, connection reset, exchange
    temporarily unavailable) is recoverable: it drives the bounded exponential
    backoff between passes (``transient_backoff_min_seconds`` growing to
    ``transient_backoff_max_seconds``) and never counts toward
    ``stop_after_errors``, so a temporary internet outage cannot kill the
    long-running process. A *local/structural* failure (invalid configuration,
    schema/programming error, deterministic invariant violation, and - per the
    PR #22 semantics - a SQLite lock failure, which stops the runner
    immediately) does count toward ``stop_after_errors`` and stops the runner
    when the limit is reached.

    ``bootstrap_candles`` is the operational data-acquisition depth used only for
    the very first public download, when nothing is stored yet and no explicit
    ``backfill_start`` was given: the runner seeds itself with that many newest
    *closed* candles. It contains no setup, pattern, qualification, planning or
    risk threshold and cannot change what a closed candle means; a deeper or
    shallower seed changes only how much history the unchanged Steps 3-6 have to
    work with at the first boundary. It is deliberately not part of the recorded
    strategy/config version fingerprints. It is also capped from below by the
    configured ``minimum_history_candles`` runner precondition.
    """

    interval_seconds: Decimal = Decimal(60)
    fetch_max_attempts: int = 3
    fetch_retry_backoff_seconds: Decimal = Decimal(5)
    stop_after_errors: int = 10
    #: Closed candles the runner seeds itself with when no history is stored.
    #: 240 is two full range-lookback windows (``ranges.lookback_candles`` = 120),
    #: keeps the first correctness-first Step 5 replay bounded, and stays inside
    #: the 720-candle rolling window of the default exchange (asking for the full
    #: window would itself introduce a spurious one-candle leading gap, because
    #: that window's newest entry is still forming). Use ``--backfill-start`` or
    #: ``--bootstrap-candles`` for deeper history.
    bootstrap_candles: int = 240
    #: Lower bound of the bounded exponential backoff the runner applies between
    #: passes after *transient* network failures (connectivity loss, DNS
    #: failure, exchange temporarily unavailable, request timeout). Transient
    #: failures never count toward ``stop_after_errors`` - a temporary internet
    #: outage must not kill the long-running process - but they are never
    #: retried in a tight loop either.
    transient_backoff_min_seconds: Decimal = Decimal(5)
    #: Upper bound of that backoff. The wait grows 5s, 10s, 20s, ... and is
    #: capped here, so a long outage costs at most one attempt per interval
    #: instead of a busy loop, and never sleeps for hours.
    transient_backoff_max_seconds: Decimal = Decimal(300)

    def __post_init__(self) -> None:
        interval = as_decimal(self.interval_seconds, name="interval_seconds")
        if interval <= 0:
            raise ValueError("interval_seconds must be positive")
        object.__setattr__(self, "interval_seconds", interval)
        backoff = as_decimal(
            self.fetch_retry_backoff_seconds, name="fetch_retry_backoff_seconds"
        )
        if backoff < 0:
            raise ValueError("fetch_retry_backoff_seconds must not be negative")
        object.__setattr__(self, "fetch_retry_backoff_seconds", backoff)
        transient_min = as_decimal(
            self.transient_backoff_min_seconds, name="transient_backoff_min_seconds"
        )
        if not Decimal(0) < transient_min <= Decimal(600):
            raise ValueError(
                "transient_backoff_min_seconds must be in (0, 600] seconds"
            )
        object.__setattr__(self, "transient_backoff_min_seconds", transient_min)
        transient_max = as_decimal(
            self.transient_backoff_max_seconds, name="transient_backoff_max_seconds"
        )
        if not transient_min <= transient_max <= Decimal(3_600):
            raise ValueError(
                "transient_backoff_max_seconds must be between "
                "transient_backoff_min_seconds and 3600 seconds"
            )
        object.__setattr__(self, "transient_backoff_max_seconds", transient_max)
        require_int(
            self.fetch_max_attempts, name="fetch_max_attempts", minimum=1, maximum=10
        )
        require_int(
            self.stop_after_errors,
            name="stop_after_errors",
            minimum=1,
            maximum=100_000,
        )
        require_int(
            self.bootstrap_candles,
            name="bootstrap_candles",
            minimum=1,
            maximum=100_000,
        )

    def transient_backoff_seconds(self, consecutive_transient_failures: int) -> Decimal:
        """Deterministic bounded exponential backoff for transient failures.

        ``delay(n) = min(min_seconds * 2 ** (n - 1), max_seconds)`` for the
        ``n``-th consecutive transient failure: 5s, 10s, 20s, 40s, ... capped
        at ``transient_backoff_max_seconds``. The policy is a pure function of
        the failure count, so it is trivially testable, never a busy loop, and
        never sleeps for hours. A fully successful pass resets the count.
        """

        if consecutive_transient_failures < 1:
            return self.transient_backoff_min_seconds
        growth = self.transient_backoff_min_seconds * (
            Decimal(2) ** (consecutive_transient_failures - 1)
        )
        return min(growth, self.transient_backoff_max_seconds)

    def fingerprint(self) -> str:
        return fingerprint("runner-settings", self)


def strategy_version_material(
    *,
    setup_rules_version: str,
    planning_rules_version: str,
    setup_config_fingerprint: str,
    planning_config_fingerprint: str,
    pattern_config_fingerprint: str | None = None,
    structure_config_fingerprint: str | None = None,
    forward_parameters_fingerprint: str,
    friction_fingerprint: str,
) -> dict[str, Any]:
    """The exact version/fingerprint tuple recorded on every forward cycle.

    Step 12 stores this material (and its fingerprint) rather than re-deriving
    it at read time: a later configuration change produces different recorded
    versions, and forward statistics then separate the cohorts instead of
    presenting numbers from two different rule sets as one result.
    """

    return {
        "setup_qualification_rules": setup_rules_version,
        "trade_planning_rules": planning_rules_version,
        "setup_config_fingerprint": setup_config_fingerprint,
        "planning_config_fingerprint": planning_config_fingerprint,
        "pattern_config_fingerprint": pattern_config_fingerprint,
        "structure_config_fingerprint": structure_config_fingerprint,
        "forward_parameters_fingerprint": forward_parameters_fingerprint,
        "friction_assumptions_fingerprint": friction_fingerprint,
        "forward_ledger_rules": FORWARD_LEDGER_RULES_VERSION,
        "forward_runner_rules": FORWARD_RUNNER_RULES_VERSION,
    }


__all__ = [
    "FORWARD_LEDGER_RULES_VERSION",
    "FORWARD_PARAMETERS_VERSION",
    "FORWARD_RUNNER_RULES_VERSION",
    "MISSED_OPPORTUNITY_REASON",
    "PAPER_TRADE_ACTIVE_REASON",
    "CycleStatus",
    "DataHealth",
    "ForwardParameters",
    "FrictionAssumptions",
    "HeartbeatStatus",
    "RunnerSettings",
    "VersionSeparation",
    "canonical_json",
    "fingerprint",
    "strategy_version_material",
]
