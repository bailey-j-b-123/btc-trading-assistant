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

    def __post_init__(self):
        if (
            self.version != VERSION
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
