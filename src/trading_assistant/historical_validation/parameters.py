"""Versioned, non-optimising controls for Step 11 historical validation.

These controls govern *evaluation only*: chronological split boundaries,
observation horizon, sample-reporting floor, explicitly stated friction
scenarios, and the replay policy cohort for the Step 6 planning path.  They
intentionally contain no setup, pattern, qualification, or planning
thresholds.  Those remain owned by Steps 3–6.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

from trading_assistant.costs import (
    FRICTION_ASSUMPTIONS_VERSION,  # noqa: F401  (re-exported import path)
    FrictionAssumptions,  # noqa: F401  (re-exported import path)
)
from trading_assistant.market_structure.numeric import as_decimal, require_int
from trading_assistant.market_structure.snapshot import to_jsonable

# v2 (trade-planning-v3 consistency): the Step 6 planning path of a replay now
# runs under the declared friction scenario by default — a declared non-zero
# scenario gates the replay with the same rule and scenario the forward path
# certifies paper plans under (pre-v2 reports ignored the declared scenario
# entirely), while an all-zero scenario declares no costs and runs the pre-v3
# planning pass without certifying anything. The pre-v3 behaviour under a
# non-zero scenario remains available only as the explicit
# ``unverified_v2_replay`` cohort below; the fingerprints of the rule regimes
# and cohorts differ by construction.
HISTORICAL_VALIDATION_RULES_VERSION = "historical-validation-v2"

#: Replay policy cohorts for the Step 6 planning path (see
#: ``ValidationConfig.replay_policy``).
REPLAY_POLICY_ECONOMIC_GATE = "economic_gate"
REPLAY_POLICY_UNVERIFIED_V2 = "unverified_v2_replay"
REPLAY_POLICIES = (REPLAY_POLICY_ECONOMIC_GATE, REPLAY_POLICY_UNVERIFIED_V2)


def fingerprint(*parts: object) -> str:
    """Return a canonical, version-prefixed identity for validation material."""

    payload = json.dumps(to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    return sha256(
        (HISTORICAL_VALIDATION_RULES_VERSION + ":" + payload).encode()
    ).hexdigest()


def _utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ChronologicalSplit:
    """An explicit strict chronological development/out-of-sample split.

    Bounds are decision timestamps (base-candle close boundaries), not source
    retrieval timestamps.  The strict ``development_end < out_of_sample_start``
    rule prevents one qualifying snapshot from entering both cohorts.
    """

    development_start: datetime
    development_end: datetime
    out_of_sample_start: datetime
    out_of_sample_end: datetime

    def __post_init__(self) -> None:
        start = _utc(self.development_start, field_name="development_start")
        end = _utc(self.development_end, field_name="development_end")
        if start > end:
            raise ValueError("development_start must not be after development_end")
        object.__setattr__(self, "development_start", start)
        object.__setattr__(self, "development_end", end)
        if self.out_of_sample_start is None or self.out_of_sample_end is None:
            raise ValueError(
                "an explicit split requires out_of_sample_start and out_of_sample_end"
            )
        oos_start = _utc(self.out_of_sample_start, field_name="out_of_sample_start")
        oos_end = _utc(self.out_of_sample_end, field_name="out_of_sample_end")
        if oos_start > oos_end:
            raise ValueError("out_of_sample_start must not be after out_of_sample_end")
        if end >= oos_start:
            raise ValueError(
                "development_end must be strictly before out_of_sample_start"
            )
        object.__setattr__(self, "out_of_sample_start", oos_start)
        object.__setattr__(self, "out_of_sample_end", oos_end)


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """Evaluation-only configuration for one deterministic validation report.

    If ``split`` is omitted, the engine derives a chronological 70/30 split
    from the available decision boundaries.  That policy is included in the
    report fingerprint and the resolved timestamps are always reported.  An
    explicit split is preferred for formal research and is never adjusted from
    outcome results.

    ``replay_policy`` selects the Step 6 planning cohort of the replay:

    * ``"economic_gate"`` (default) — a declared *non-zero* ``friction``
      scenario is forced into the planning parameters, exactly as the forward
      path does (trade-planning-v3): the gate and the reported friction share
      one scenario, so a replay plan and a forward paper plan decided under
      the same declared costs are gate-consistent.  An all-zero ``friction``
      declares no costs, so the gate is not evaluated and the replay runs the
      pre-v3 planning pass: a replay record is a diagnostic of the planning
      pipeline, never a certification (the forward path withholds every paper
      plan under the same zero scenario).
    * ``"unverified_v2_replay"`` — explicit opt-in to the pre-v3 planning
      behaviour under *any* declared scenario (no cost model in effect;
      ``economic_viability`` not evaluated).  Those records form a
      *distinguishable* cohort (this field is part of the config fingerprint
      and the report's provenance) that must never be compared with gated
      forward decisions.
    """

    split: ChronologicalSplit | None = None
    out_of_sample_fraction: Decimal = Decimal("0.30")
    observation_horizon_candles: int = 20
    minimum_sample_size: int = 20
    friction: FrictionAssumptions = FrictionAssumptions()
    replay_policy: str = REPLAY_POLICY_ECONOMIC_GATE

    def __post_init__(self) -> None:
        if self.split is not None and not isinstance(self.split, ChronologicalSplit):
            raise TypeError("split must be ChronologicalSplit or None")
        fraction = as_decimal(
            self.out_of_sample_fraction, name="out_of_sample_fraction"
        )
        if not Decimal(0) < fraction < Decimal(1):
            raise ValueError("out_of_sample_fraction must be between 0 and 1")
        object.__setattr__(self, "out_of_sample_fraction", fraction.normalize())
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
        if not isinstance(self.friction, FrictionAssumptions):
            raise TypeError("friction must be FrictionAssumptions")
        if self.replay_policy not in REPLAY_POLICIES:
            raise ValueError(
                "replay_policy must be one of: " + ", ".join(REPLAY_POLICIES)
            )

    def fingerprint(self) -> str:
        return fingerprint("validation-config", self)
