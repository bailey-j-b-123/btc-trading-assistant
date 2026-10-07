"""Versioned multi-timeframe hierarchy configuration (Step 13, part A).

The hierarchy is ONE decision system, not four independent strategies. Each
timeframe has exactly one role, and the roles are ordered:

    CONTEXT      (4h)  — where we are: market regime / major structure
    SETUP        (1h)  — what we may trade: the primary opportunity
    CONFIRMATION (15m) — whether the 1H idea is confirming
    EXECUTION    (5m)  — when the entry may be ready

Lower timeframes refine higher-timeframe information; they never override it.
The hierarchy is a deterministic, versioned configuration object: no timeframe
string is hard-coded anywhere in the decision logic, so the hierarchy can be
changed later without redesigning the system. The BTC defaults remain exactly
``4h / 1h / 15m / 5m``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trading_assistant.market_data.timeframes import (
    timeframe_to_milliseconds,
)
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.multi_timeframe.parameters import (
    HIERARCHY_RULES_VERSION,
    fingerprint,
)


class TimeframeRole(StrEnum):
    """The single job each timeframe plays in the hierarchy."""

    CONTEXT = "context"
    SETUP = "setup"
    CONFIRMATION = "confirmation"
    EXECUTION = "execution"


#: Canonical role order: each layer may only refine the layer above it.
ROLE_ORDER: tuple[TimeframeRole, ...] = (
    TimeframeRole.CONTEXT,
    TimeframeRole.SETUP,
    TimeframeRole.CONFIRMATION,
    TimeframeRole.EXECUTION,
)


@dataclass(frozen=True, slots=True)
class TimeframeStep:
    """One timeframe and the single role it plays in the hierarchy."""

    role: TimeframeRole
    timeframe: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, TimeframeRole):
            raise TypeError("role must be a TimeframeRole")
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty string")
        # Raises for unsupported fixed-duration timeframes.
        timeframe_to_milliseconds(self.timeframe)

    @property
    def duration_ms(self) -> int:
        return timeframe_to_milliseconds(self.timeframe)

    def to_json_dict(self) -> dict[str, object]:
        return {"role": self.role.value, "timeframe": self.timeframe}


@dataclass(frozen=True, slots=True)
class TimeframeHierarchy:
    """The complete, validated, versioned timeframe hierarchy.

    Validation guarantees the decision system can rely on:

    * every role appears at most once (a role is a job, not a timeframe list);
    * every timeframe appears at most once;
    * timeframes strictly decrease in duration from CONTEXT to EXECUTION, so a
      lower timeframe can never be asked to interpret a longer one;
    * CONTEXT and SETUP are always present: a hierarchy without market context
      or without a setup authority is not a hierarchy.
    """

    steps: tuple[TimeframeStep, ...]
    rules_version: str = HIERARCHY_RULES_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.steps, tuple):
            raise TypeError("steps must be an immutable tuple of TimeframeStep")
        if not self.steps:
            raise ValueError("hierarchy must contain at least one step")
        if not isinstance(self.rules_version, str) or not self.rules_version.strip():
            raise ValueError("rules_version must be a non-empty string")
        object.__setattr__(self, "rules_version", self.rules_version.strip())
        roles = [step.role for step in self.steps]
        timeframes = [step.timeframe for step in self.steps]
        if len(set(roles)) != len(roles):
            raise ValueError("each timeframe role may appear at most once")
        if len(set(timeframes)) != len(timeframes):
            raise ValueError("each timeframe may appear at most once")
        if TimeframeRole.CONTEXT not in roles:
            raise ValueError("hierarchy must define a CONTEXT timeframe")
        if TimeframeRole.SETUP not in roles:
            raise ValueError("hierarchy must define a SETUP timeframe")
        # Roles must appear in canonical order (CONTEXT first, EXECUTION last).
        present_order = [role for role in ROLE_ORDER if role in roles]
        if roles != present_order:
            raise ValueError(
                "hierarchy roles must appear in canonical order "
                + " -> ".join(role.value for role in ROLE_ORDER)
            )
        # Durations must strictly decrease: lower timeframes refine, never
        # reinterpret, the timeframe above them.
        durations = [step.duration_ms for step in self.steps]
        if any(later >= earlier for earlier, later in zip(durations, durations[1:])):
            raise ValueError(
                "hierarchy timeframes must strictly decrease in duration from "
                "CONTEXT to EXECUTION"
            )

    @property
    def context(self) -> TimeframeStep:
        return self.step_for(TimeframeRole.CONTEXT)

    @property
    def setup(self) -> TimeframeStep:
        return self.step_for(TimeframeRole.SETUP)

    @property
    def confirmation(self) -> TimeframeStep | None:
        return self._optional(TimeframeRole.CONFIRMATION)

    @property
    def execution(self) -> TimeframeStep | None:
        return self._optional(TimeframeRole.EXECUTION)

    @property
    def timeframes(self) -> tuple[str, ...]:
        return tuple(step.timeframe for step in self.steps)

    @property
    def roles(self) -> tuple[TimeframeRole, ...]:
        return tuple(step.role for step in self.steps)

    def step_for(self, role: TimeframeRole) -> TimeframeStep:
        for step in self.steps:
            if step.role is role:
                return step
        raise KeyError(f"hierarchy has no {role.value} timeframe")

    def timeframe_for(self, role: TimeframeRole) -> str:
        return self.step_for(role).timeframe

    def has_role(self, role: TimeframeRole) -> bool:
        return any(step.role is role for step in self.steps)

    def role_for(self, timeframe: str) -> TimeframeRole:
        for step in self.steps:
            if step.timeframe == timeframe:
                return step.role
        raise KeyError(f"timeframe {timeframe!r} is not part of this hierarchy")

    def _optional(self, role: TimeframeRole) -> TimeframeStep | None:
        for step in self.steps:
            if step.role is role:
                return step
        return None

    def fingerprint(self) -> str:
        """Versioned identity of this exact hierarchy configuration.

        The fingerprint is part of every recorded hierarchy observation's
        identity on purpose: two different hierarchies must never share a
        ledger row, and a hierarchy change is a visible version change rather
        than a silent reinterpretation of earlier decisions.
        """

        return fingerprint(
            "timeframe-hierarchy",
            self.rules_version,
            tuple(step.to_json_dict() for step in self.steps),
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "rules_version": self.rules_version,
            "fingerprint": self.fingerprint(),
            "steps": [step.to_json_dict() for step in self.steps],
        }


def default_hierarchy() -> TimeframeHierarchy:
    """The BTC default hierarchy: 4H context, 1H setup, 15M confirmation, 5M execution."""

    return TimeframeHierarchy(
        steps=(
            TimeframeStep(TimeframeRole.CONTEXT, "4h"),
            TimeframeStep(TimeframeRole.SETUP, "1h"),
            TimeframeStep(TimeframeRole.CONFIRMATION, "15m"),
            TimeframeStep(TimeframeRole.EXECUTION, "5m"),
        )
    )


#: The BTC hierarchy used everywhere unless a caller supplies another one.
BTC_HIERARCHY = default_hierarchy()


__all__ = [
    "BTC_HIERARCHY",
    "ROLE_ORDER",
    "TimeframeHierarchy",
    "TimeframeRole",
    "TimeframeStep",
    "default_hierarchy",
    "to_jsonable",
]
