"""Frozen, content-addressed research definitions; Decimal money throughout."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal as D
from decimal import Context, localcontext, ROUND_HALF_EVEN
from functools import wraps
from hashlib import sha256
import json
from pathlib import Path
from functools import lru_cache
from . import LAB_VERSION


def decimal_context(function):
    @wraps(function)
    def call(*args, **kwargs):
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            return function(*args, **kwargs)

    return call


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda v: (
            str(v)
            if isinstance(v, D)
            else v.isoformat()
            if isinstance(v, datetime)
            else asdict(v)
        ),
    )


def identity(value):
    return sha256((LAB_VERSION + ":" + canonical(value)).encode()).hexdigest()


@lru_cache(maxsize=1)
def research_code_fingerprint():
    """Versioned core source identity, excluding presentation-only modules.

    Cached for one process: restart after installing code; never hot-swap a
    running experiment. A changed core tree creates a new cohort, not a rewrite.
    """
    root = Path(__file__).parents[1]
    digest = sha256(b"brain-research-source-v2\0")
    for file in sorted(root.rglob("*.py")):
        relative = file.relative_to(root)
        if (
            relative.parts[0] in {"web", "ai_explanation"}
            or relative.as_posix() == "strategy_lab/report.py"
        ):
            continue
        digest.update(relative.as_posix().encode() + b"\0" + file.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class Execution:
    initial_capital: D = D("10000")
    risk_fraction: D = D("0.005")
    max_exposure: D = D("0.25")
    fee_bps: D = D("10")
    spread_bps: D = D("2")
    slippage_bps: D = D("3")
    max_entry_gap_bps: D = D("50")
    quantity_step: D = D("0.00001")
    minimum_notional: D = D("10")
    max_positions: int = 1
    sizing: str = "cash-spot-long-only-v1"
    ambiguous_policy: str = "adverse-stop-first-v1"

    def __post_init__(self):
        for name in (
            "initial_capital",
            "risk_fraction",
            "max_exposure",
            "fee_bps",
            "spread_bps",
            "slippage_bps",
            "max_entry_gap_bps",
            "quantity_step",
            "minimum_notional",
        ):
            v = getattr(self, name)
            if not isinstance(v, D) or not v.is_finite() or v < 0:
                raise ValueError("execution values must be finite nonnegative Decimals")
        if (
            self.initial_capital <= 0
            or self.quantity_step <= 0
            or self.minimum_notional <= 0
            or not 0 < self.risk_fraction <= D("0.02")
            or not 0 < self.max_exposure <= 1
        ):
            raise ValueError("invalid capital, paper risk or exposure")
        if any(
            v > D("1000")
            for v in (
                self.fee_bps,
                self.spread_bps,
                self.slippage_bps,
                self.max_entry_gap_bps,
            )
        ):
            raise ValueError("execution cost/gap assumptions must not exceed1000bps")
        if (
            type(self.max_positions) is not int
            or self.max_positions != 1
            or self.sizing != "cash-spot-long-only-v1"
            or self.ambiguous_policy != "adverse-stop-first-v1"
        ):
            raise ValueError(
                "only the verified single-position cash model is implemented"
            )

    @property
    def impact(self):
        return (self.spread_bps / 2 + self.slippage_bps) / D(10000)

    @property
    def fee(self):
        return self.fee_bps / D(10000)


@dataclass(frozen=True)
class Strategy:
    family: str = "sma_cross"
    timeframe: str = "1h"
    fast: int = 20
    slow: int = 50
    atr_period: int = 14
    stop_atr: D = D("2")
    reward_r: D = D("2")
    max_hold: int = 48
    rsi_period: int = 14
    rsi_low: D = D("30")
    version: str = "lab-strategy-v1"

    def __post_init__(self):
        if self.family not in {
            "sma_cross",
            "donchian",
            "rsi_recovery",
            "momentum",
        } or self.timeframe not in {"1h", "4h"}:
            raise ValueError("unsupported frozen strategy family/timeframe")
        for n in (
            self.fast,
            self.slow,
            self.atr_period,
            self.max_hold,
            self.rsi_period,
        ):
            if type(n) is not int or not 2 <= n <= 1000:
                raise ValueError("lookbacks/holding budget must be bounded integers")
        if not self.fast < self.slow or any(
            not isinstance(v, D) or not v.is_finite() or v <= 0
            for v in (self.stop_atr, self.reward_r, self.rsi_low)
        ):
            raise ValueError("invalid strategy parameters")
        if not self.rsi_low < 50 or self.version != "lab-strategy-v1":
            raise ValueError("invalid RSI threshold or unsupported strategy version")

    @property
    def fingerprint(self):
        return identity(
            {
                "definition": asdict(self),
                "decision_source_sha256": research_code_fingerprint(),
            }
        )

    @property
    def warmup(self):
        return max(self.slow + 1, self.atr_period + 1, self.rsi_period + 2)


def candidates(max_candidates=16):
    """Small predetermined sensible grid, not random parameter mining."""
    if type(max_candidates) is not int or not 1 <= max_candidates <= 128:
        raise ValueError("candidate budget must be 1..128")
    result = []
    for family in ("sma_cross", "donchian", "rsi_recovery", "momentum"):
        for fast, slow in ((10, 30), (20, 50)):
            for reward in (D("2"), D("3")):
                result.append(
                    Strategy(family=family, fast=fast, slow=slow, reward_r=reward)
                )
    return tuple(result[:max_candidates])
