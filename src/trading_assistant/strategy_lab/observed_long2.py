"""Isolated, training-only discovery of 2%+ BTC LONG opportunities.

This module learns *hypotheses*, NOT trade decisions or a profitability claim.
The subsequent simulator integration must be independently verified.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal as D
from hashlib import sha256

from . import discovery as original
from .data import candle_fingerprint
from .large_move import label_long_move
from .models import canonical

VERSION = "observed-long2-training-v1"
HORIZON_BARS = 120
MIN_MOVE = D("0.02")
ADVERSE_SCREEN = D("-2")  # Percent: label-only screen; NOT an execution stop.
MIN_LEAF_SUPPORT = 120
MAX_DEPTH = 3


@dataclass(frozen=True)
class OpportunityRule:
    """Pure research description; no runner adapter or order path."""

    conditions: tuple[original.Condition, ...]
    training_fingerprint: str
    source_fingerprint: str
    training_samples: int
    observed_success_rate: D
    unconditional_success_rate: D
    version: str = VERSION
    timeframe: str = "1h"
    warmup: int = 121
    max_hold: int = 120

    def __post_init__(self):
        if (
            self.version != VERSION
            or self.timeframe != "1h"
            or self.warmup != 121
            or self.max_hold != HORIZON_BARS
            or not 1 <= len(self.conditions) <= MAX_DEPTH
            or self.training_samples < MIN_LEAF_SUPPORT
            or not (D(0) <= self.observed_success_rate <= D(1))
            or not (D(0) <= self.unconditional_success_rate <= D(1))
            or any(not isinstance(c, original.Condition) for c in self.conditions)
            or any(len(s) != 64 or any(ch not in "0123456789abcdef" for ch in s)
                   for s in (self.training_fingerprint, self.source_fingerprint))
        ):
            raise ValueError("invalid training-only long2 rule")

    @property
    def fingerprint(self):
        return sha256((VERSION + ":" + canonical(asdict(self))).encode()).hexdigest()


def discover_long2(candles):
    """One fixed bounded fit on Apr--Sep2023. No evaluation data accepted.

    Success means BTC touched +2% of the next open inside 120 hourly candles,
    WITHOUT an observed <=-2% drawdown on/before that touch. The adverse
    screen is deliberately conservative, including same-candle ambiguity.
    This is NOT a simulation of stops, trade returns, or executable fills.
    """
    if (
        type(candles) is not tuple
        or not original.scope(candles)
        or candles[0].timestamp != original.TRAIN_START
        or candles[-1].timestamp + original.HOUR != original.TRAIN_END
        or len(candles) < HORIZON_BARS + 240
    ):
        raise ValueError("exact contiguous Apr-Sep2023 discovery prefix required")
    training_id = candle_fingerprint(candles)
    source_id = original.research_code_fingerprint()
    rows = []
    for i in range(30, len(candles) - HORIZON_BARS):
        known = candles[i].timestamp + original.HOUR
        f = original.features(candles[i - 30:i + 1], known_at=known)
        if f is None:
            continue
        # Only the designated training prefix is ever supplied for labeling.
        label = label_long_move(candles, i, horizon_bars=HORIZON_BARS, threshold=MIN_MOVE)
        success = int(label["touched_target"] and D(label["worst_before_hit_pct"]) > ADVERSE_SCREEN)
        rows.append((f, success))
    if len(rows) < 240:
        raise ValueError("insufficient fully observed labelled training examples")

    base = D(sum(y for _, y in rows)) / D(len(rows))
    leaves = []
    trials = 0

    def impurity(group):
        positive = sum(y for _, y in group)
        return D(positive) * D(len(group) - positive) / D(len(group))

    def split(group, path, depth):
        nonlocal trials
        if depth == MAX_DEPTH or len(group) < 2 * MIN_LEAF_SUPPORT:
            leaves.append((path, group))
            return
        current = impurity(group)
        best = None
        for feature in original.FEATURES:
            ordered = sorted(row[0][feature] for row in group)
            for threshold in sorted({ordered[(len(ordered) - 1) * q // 4] for q in (1, 2, 3)}):
                left = [r for r in group if r[0][feature] <= threshold]
                right = [r for r in group if r[0][feature] > threshold]
                trials += 1
                if min(len(left), len(right)) < MIN_LEAF_SUPPORT:
                    continue
                gain = current - impurity(left) - impurity(right)
                if gain > 0 and (best is None or gain > best[0]):
                    best = (gain, feature, threshold, left, right)
        if best is None:
            leaves.append((path, group))
            return
        _, feature, threshold, left, right = best
        split(left, path + (original.Condition(feature, "le", threshold),), depth + 1)
        split(right, path + (original.Condition(feature, "gt", threshold),), depth + 1)

    split(rows, (), 0)
    hypotheses = []
    for conditions, members in leaves:
        if not conditions:
            hypotheses.append({"accepted": False, "reason": "no_positive_split", "support": len(members)})
            continue
        success = D(sum(x[1] for x in members)) / D(len(members))
        r = OpportunityRule(conditions, training_id, source_id, len(members), success, base)
        admitted = success > base
        hypotheses.append({
            "rule": asdict(r), "id": r.fingerprint,
            "accepted": admitted,
            "reason": None if admitted else "not_above_unconditional_training_success",
        })
    assert len(hypotheses) <= 8
    return {
        "version": VERSION, "training_fingerprint": training_id,
        "training_start": original.TRAIN_START.isoformat(),
        "training_end_exclusive": original.TRAIN_END.isoformat(),
        "horizon_bars": HORIZON_BARS, "minimum_upside_percent": "2",
        "adverse_label_screen_percent": str(ADVERSE_SCREEN),
        "adverse_label_is_not_stop": True,
        "valid_training_rows": len(rows),
        "unconditional_success_rate": str(base),
        "split_trials": trials, "maximum_split_trials": 168,
        "hypotheses": hypotheses,
        "evaluation_or_trading_performed": False,
    }


def load_opportunity_rule(record):
    """Load only a typed frozen candidate; no arbitrary executable text."""
    from . import discovery as original

    value = dict(record)
    value["conditions"] = tuple(
        original.Condition(c["feature"], c["operator"], D(c["threshold"]))
        for c in value["conditions"]
    )
    value["observed_success_rate"] = D(value["observed_success_rate"])
    value["unconditional_success_rate"] = D(value["unconditional_success_rate"])
    return OpportunityRule(**value)


def decide_long2(candles, rule, *, known_at):
    """Causal, optional paper LONG setup from a frozen observed-movement rule.

    The rule learns which conditions predict 2%+ moves. The STRUCTURAL exit
    heuristic is predeclared, not itself learned: aim just below the preceding
    120h high, protect beneath preceding 24h low plus an ATR buffer, require
    at least 2% potential even after maximum allowed entry gap/impact.

    This is a bounded feasibility experiment, not a claim of a learned optimum.
    No futures, shorts, money interface or new financial accounting.
    """
    from datetime import datetime, UTC
    from . import discovery as original
    from .models import research_code_fingerprint
    from trading_assistant.market_structure.volatility import (
        calculate_volatility, VolatilityParameters,
    )

    if not isinstance(rule, OpportunityRule) or rule.source_fingerprint != research_code_fingerprint():
        raise ValueError("frozen long2 candidate/source mismatch")
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    if known_at > cutoff or (
        known_at == cutoff and (
            not candles
            or candles[-1].timestamp != cutoff - original.HOUR
            or any(c.timestamp >= cutoff for c in candles)
        )
    ):
        raise ValueError("protected 2025 price admission refused")
    if known_at < original.TRAIN_END:
        raise ValueError("cannot trade discovery training period")
    closed = tuple(c for c in candles if c.timestamp + original.HOUR <= known_at)[-rule.warmup:]
    if len(closed) != rule.warmup or not original.scope(closed) or closed[-1].timestamp + original.HOUR != known_at:
        return None
    f = original.features(closed[-31:], known_at=known_at)
    if f is None or not all(c.matches(f) for c in rule.conditions):
        return None

    ref = closed[-1].close
    historical_high = max(c.high for c in closed[:-1])
    # The target is just below observed prior resistance, rather than forcing
    # a synthetic +2% target irrespective of actual available market structure.
    target = historical_high * D("0.9995")
    # Require 2% room even if the existing simulator accepts its maximum
    # 50bp next-open gap plus the 11bp adverse stressed fill impact.
    maximum_accepted_fill = ref * D("1.005") * D("1.0011")
    if target < maximum_accepted_fill * D("1.02"):
        return None
    atr = calculate_volatility(
        closed[-31:], interval=original.HOUR, as_of=known_at,
        parameters=VolatilityParameters(period=14),
    ).atr
    if atr is None or atr <= 0:
        return None
    prior_low = min(c.low for c in closed[-25:-1])
    stop = prior_low - atr * D("0.1")
    if stop <= 0 or stop >= ref or (ref - stop) / ref > D("0.02"):
        return None
    return {
        "known_at": known_at.isoformat(),
        "candle_open": closed[-1].timestamp.isoformat(),
        "reference": str(ref),
        "stop": str(stop),
        "target": str(target),
        "evidence": {
            "family": VERSION,
            "rule_id": rule.fingerprint,
            "training_success_rate": str(rule.observed_success_rate),
            "prior_120h_high": str(historical_high),
            "prior_24h_low": str(prior_low),
            "atr": str(atr),
            "projected_upside_from_worst_fill_pct": str((target / maximum_accepted_fill - 1) * 100),
            "stop_and_target": "predeclared structural heuristic, not a learned exit policy",
            "features": {k: str(v) for k, v in f.items()},
        },
    }
