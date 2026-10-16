"""Opt-in collateralised linear research contract; no loop, feed or order API."""

from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal as D
from .models import identity


@dataclass(frozen=True)
class DirectionalModel:
    """Explicit conservative assumptions, NOT historical exchange execution.

    Every position posts100% of entry notional (1x). Positive scheduled funding
    is paid by longs to shorts. Additional adverse carry is a scenario cost,
    not an assertion that a perpetual has a Spot borrow charge.
    """

    maintenance_rate: D = D("0.01")
    liquidation_fee_bps: D = D("100")
    carry_bps_per_day: D = D("10")
    funding_reserve_bps_per_day: D = D("30")
    mark_source: str = "trade_price_proxy_assumption"
    provenance: str = "declared conservative research assumptions; not venue verified"
    version: str = "unified-collateralised-linear-v1"

    def __post_init__(self):
        for name in (
            "maintenance_rate",
            "liquidation_fee_bps",
            "carry_bps_per_day",
            "funding_reserve_bps_per_day",
        ):
            x = getattr(self, name)
            if not isinstance(x, D) or not x.is_finite() or x < 0:
                raise ValueError("finite nonnegative directional parameters required")
        if not D("0.004") <= self.maintenance_rate <= D("0.5"):
            raise ValueError("maintenance scenario must be0.4%..50%")
        if (
            self.liquidation_fee_bps > 1000
            or self.carry_bps_per_day > 1000
            or self.funding_reserve_bps_per_day > 1000
        ):
            raise ValueError("directional cost scenario exceeds1000bps")
        if self.mark_source not in {"recorded_marks", "trade_price_proxy_assumption"}:
            raise ValueError(
                "explicit recorded marks or price-proxy assumption required"
            )
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError("directional assumptions require provenance")
        if self.version != "unified-collateralised-linear-v1":
            raise ValueError("unsupported directional contract")

    def validate(self, execution, strategy, exit_policy, entry_policy):
        if execution.risk_fraction > D("0.005") or execution.max_exposure > D("0.25"):
            raise ValueError("directional research retains0.5% risk/25% exposure caps")
        if execution.max_positions != 1:
            raise ValueError("one shared position required")
        if exit_policy != "strategy_default" or entry_policy is not None:
            raise ValueError(
                "directional v1 requires bounded strategy holding and next-open entry"
            )
        if type(strategy.max_hold) is not int or not 1 <= strategy.max_hold <= 8760:
            raise ValueError("finite1..8760bar carry-reserve horizon required")

    @property
    def fingerprint(self):
        return identity(asdict(self))


@dataclass(frozen=True)
class FundingEvent:
    at: datetime
    rate: D
    mark: D

    def __post_init__(self):
        if self.at.tzinfo is None or self.at.utcoffset().total_seconds() != 0:
            raise ValueError("funding timestamp must be explicit UTC")
        if (
            not isinstance(self.rate, D)
            or not self.rate.is_finite()
            or abs(self.rate) > D("0.1")
            or not isinstance(self.mark, D)
            or not self.mark.is_finite()
            or self.mark <= 0
        ):
            raise ValueError(
                "finite funding rate and positive settlement mark required"
            )


def direction(signal):
    value = signal.get("direction", "LONG")
    if value not in {"LONG", "SHORT"}:
        raise ValueError("signal direction must be LONG or SHORT; abstain with None")
    return 1 if value == "LONG" else -1


def validate_signal(signal, candle, known):
    sign = direction(signal)
    ref, stop, target = (D(signal[k]) for k in ("reference", "stop", "target"))
    if (
        not all(x.is_finite() and x > 0 for x in (ref, stop, target))
        or ref != candle.close
        or signal["known_at"] != known.isoformat()
        or signal["candle_open"] != candle.timestamp.isoformat()
        or sign * (ref - stop) <= 0
        or sign * (target - ref) <= 0
    ):
        raise ValueError(
            "causal closed-candle reference and directional protective levels required"
        )
    return sign
