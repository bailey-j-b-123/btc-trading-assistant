"""Pure proposed-level metric helpers shared by Step 11 and Step 12.

These functions were originally private helpers inside
``historical_validation.service``. They contain no strategy rule, no threshold,
and no calibration knob: they only turn an already-computed deterministic
Step 7 ``OutcomeObservation`` into unit-neutral observational numbers, exactly
as Step 11 has always reported them.

Step 11 keeps importing them under its historical private names, so its
behaviour is byte-for-byte unchanged; Step 12 forward reporting imports the same
implementations so live-forward and historical numbers are computed by one
shared definition rather than two copied ones.

Nothing here is realised profit, an order, a fill, an account, a position, or a
profitability claim.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from decimal import Decimal

from trading_assistant.historical_validation.models import (
    MetricStatus,
    RateMetric,
    RDistribution,
    ValueCount,
)
from trading_assistant.historical_validation.parameters import FrictionAssumptions
from trading_assistant.journaling.types import OutcomeObservation, OutcomeStatus
from trading_assistant.market_structure.numeric import quantize_derived

#: Outcomes that are fully decided by proposed levels (no residual trajectory).
COMPLETED_OUTCOMES = frozenset(
    {
        OutcomeStatus.ENTRY_NOT_REACHED,
        OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
        OutcomeStatus.STOPPED,
        OutcomeStatus.STOPPED_AFTER_TARGETS,
        OutcomeStatus.TARGETS_REACHED,
    }
)

#: Completed outcomes plus the still-open-but-clean trajectory state.
CLEAN_OUTCOMES = frozenset(COMPLETED_OUTCOMES | {OutcomeStatus.OPEN_AT_CUTOFF})


def enum_text(value: object) -> str:
    """Render an enum (or any value) as its stable string form."""

    return str(getattr(value, "value", value))


def counts(values: Iterable[str]) -> tuple[ValueCount, ...]:
    """Deterministic value/count rows, sorted by value."""

    counter = Counter(values)
    return tuple(ValueCount(value, counter[value]) for value in sorted(counter))


def counts_from_counter(counter: Counter[str]) -> tuple[ValueCount, ...]:
    return tuple(ValueCount(key, counter[key]) for key in sorted(counter))


def rate(
    metric: str,
    numerator: int,
    denominator: int,
    definition: str,
    minimum_sample_size: int,
) -> RateMetric:
    """One rate with its exact numerator, denominator and definition.

    Below the configured reporting floor the percentage is withheld while the
    counts stay visible, so a small sample can never masquerade as a rate.
    """

    status = (
        MetricStatus.SUFFICIENT_DATA
        if denominator >= minimum_sample_size
        else MetricStatus.INSUFFICIENT_DATA
    )
    percentage = (
        quantize_derived(Decimal(numerator) * Decimal(100) / Decimal(denominator))
        if denominator and status is MetricStatus.SUFFICIENT_DATA
        else None
    )
    return RateMetric(metric, numerator, denominator, percentage, status, definition)


def distribution(
    metric: str,
    values: Sequence[Decimal],
    excluded: Counter[str],
    records_considered: int,
    minimum_sample_size: int,
    definition: str,
) -> RDistribution:
    """One deterministic R distribution, with exclusions reported separately."""

    ordered = tuple(sorted(values))
    sufficiently_sampled = len(ordered) >= minimum_sample_size
    middle = len(ordered) // 2
    median_value: Decimal | None
    if not ordered:
        median_value = None
    elif len(ordered) % 2:
        median_value = quantize_derived(ordered[middle])
    else:
        median_value = quantize_derived((ordered[middle - 1] + ordered[middle]) / 2)
    return RDistribution(
        metric=metric,
        records_considered=records_considered,
        sample_size=len(ordered),
        status=(
            MetricStatus.SUFFICIENT_DATA
            if sufficiently_sampled
            else MetricStatus.INSUFFICIENT_DATA
        ),
        excluded=counts_from_counter(excluded),
        values=ordered,
        average=(
            quantize_derived(sum(ordered, Decimal(0)) / Decimal(len(ordered)))
            if sufficiently_sampled
            else None
        ),
        median=median_value if sufficiently_sampled else None,
        minimum=quantize_derived(ordered[0]) if sufficiently_sampled else None,
        maximum=quantize_derived(ordered[-1]) if sufficiently_sampled else None,
        definition=definition,
    )


def terminal_endpoint(observation: OutcomeObservation) -> Decimal | None:
    """The proposed level that closes the trajectory, or ``None`` when none does."""

    if not observation.entry_ordered:
        return None
    if observation.status is OutcomeStatus.STOPPED:
        if observation.stop_reached and not observation.stop_pre_entry:
            return observation.stop_level
        return None
    if (
        observation.status is OutcomeStatus.TARGETS_REACHED
        and observation.targets_reached
    ):
        reached = tuple(
            observation.target_levels[index]
            for index in observation.targets_reached
            if 0 <= index < len(observation.target_levels)
        )
        if len(reached) != len(observation.targets_reached):
            return None
        return max(reached) if observation.direction == "bullish" else min(reached)
    return None


def directional_r(observation: OutcomeObservation, endpoint: Decimal) -> Decimal:
    """Unit-neutral R of one endpoint relative to the proposed entry and risk."""

    move = (
        endpoint - observation.entry_level
        if observation.direction == "bullish"
        else observation.entry_level - endpoint
    )
    return quantize_derived(move / observation.risk_per_unit)


def r_exclusion(observation: OutcomeObservation) -> str:
    """Why one observation has no raw observational R."""

    if observation.status is OutcomeStatus.STOPPED_AFTER_TARGETS:
        return "STOPPED_AFTER_TARGETS_NO_PARTIAL_EXIT_POLICY"
    if observation.status is OutcomeStatus.AMBIGUOUS:
        return "AMBIGUOUS_OUTCOME"
    if observation.status is OutcomeStatus.INCOMPLETE_DATA:
        return "INCOMPLETE_OUTCOME"
    if observation.status is OutcomeStatus.OPEN_AT_CUTOFF:
        return "NO_TERMINAL_PROPOSED_LEVEL_R"
    if (
        observation.status
        in {
            OutcomeStatus.ENTRY_NOT_REACHED,
            OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
        }
        or not observation.entry_ordered
    ):
        return "ENTRY_NOT_ORDERED"
    return "TERMINAL_LEVEL_NOT_ESTABLISHED"


def friction_adjusted_r(
    observation: OutcomeObservation,
    endpoint: Decimal,
    assumptions: FrictionAssumptions,
) -> Decimal:
    """Hypothetical R after the declared adverse slippage and two-sided fees.

    This is a scenario calculation over observational levels only. It models no
    size, no account, no leverage, no margin, no fill probability and no
    realised profit.
    """

    scale = Decimal(10_000)
    entry_slippage = assumptions.entry_slippage_bps / scale
    exit_slippage = assumptions.exit_slippage_bps / scale
    fee_rate = assumptions.fee_bps / scale
    if observation.direction == "bullish":
        entry = observation.entry_level * (Decimal(1) + entry_slippage)
        exit_price = endpoint * (Decimal(1) - exit_slippage)
        gross = exit_price - entry
    else:
        entry = observation.entry_level * (Decimal(1) - entry_slippage)
        exit_price = endpoint * (Decimal(1) + exit_slippage)
        gross = entry - exit_price
    fees = (entry + exit_price) * fee_rate
    return quantize_derived((gross - fees) / observation.risk_per_unit)


def r_values(
    observations: Sequence[OutcomeObservation],
) -> tuple[
    tuple[Decimal, ...],
    Counter[str],
    tuple[tuple[OutcomeObservation, Decimal], ...],
]:
    """Raw observational R values plus explicit exclusions and endpoints.

    Only clean terminal proposed-level trajectories contribute a value, exactly
    as Step 11 defines. Everything else is counted as an exclusion reason so the
    denominator is never silently shrunk.
    """

    values: list[Decimal] = []
    excluded: Counter[str] = Counter()
    endpoints: list[tuple[OutcomeObservation, Decimal]] = []
    for observation in observations:
        endpoint = terminal_endpoint(observation)
        if endpoint is None:
            excluded[r_exclusion(observation)] += 1
            continue
        values.append(directional_r(observation, endpoint))
        endpoints.append((observation, endpoint))
    return tuple(values), excluded, tuple(endpoints)


__all__ = [
    "CLEAN_OUTCOMES",
    "COMPLETED_OUTCOMES",
    "counts",
    "counts_from_counter",
    "directional_r",
    "distribution",
    "enum_text",
    "friction_adjusted_r",
    "r_exclusion",
    "r_values",
    "rate",
    "terminal_endpoint",
]
