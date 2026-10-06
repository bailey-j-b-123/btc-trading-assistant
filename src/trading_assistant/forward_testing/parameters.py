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
FORWARD_LEDGER_RULES_VERSION = "forward-ledger-v1"

#: Version of the closed-candle forward runner/cycle contract.
FORWARD_RUNNER_RULES_VERSION = "forward-runner-v1"

#: Version of the forward evaluation controls (horizon, catch-up, sample floor).
FORWARD_PARAMETERS_VERSION = "forward-parameters-v1"


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
    dashboard derives runner status from the newest heartbeat plus its age.
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
