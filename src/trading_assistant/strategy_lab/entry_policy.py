"""Optional, preregistered research execution; never used by the live planner."""

from dataclasses import asdict, dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PullbackEntry:
    """Wait for a capped next-open entry, retaining the original quantity/levels.

    A candle merely touching the price is NOT a fill. Only a subsequent open
    whose adverse execution price fits the cap can fill. This deliberately
    misses intrabar retracements rather than guessing their ordering.
    """

    retracement_fraction: Decimal = Decimal("0.25")
    max_wait_bars: int = 6
    version: str = "pullback-open-v1"

    def __post_init__(self):
        if (
            not isinstance(self.retracement_fraction, Decimal)
            or not self.retracement_fraction.is_finite()
            or not 0 < self.retracement_fraction < 1
            or type(self.max_wait_bars) is not int
            or not 1 <= self.max_wait_bars <= 1000
            or self.version != "pullback-open-v1"
        ):
            raise ValueError("invalid versioned pullback entry definition")

    def limit(self, signal):
        reference, stop, target = (
            Decimal(signal[k]) for k in ("reference", "stop", "target")
        )
        if not all(v.is_finite() for v in (reference, stop, target)) or not (
            0 < stop < reference < target
        ):
            raise ValueError("pullback requires finite long structural levels")
        return reference - self.retracement_fraction * (reference - stop)


def entry_definition(policy):
    if policy is not None and not isinstance(policy, PullbackEntry):
        raise ValueError("entry_policy must be None or PullbackEntry")
    # String fields match JSON checkpoints, so restart comparisons are exact.
    return (
        None
        if policy is None
        else {
            **asdict(policy),
            "retracement_fraction": str(policy.retracement_fraction),
        }
    )
