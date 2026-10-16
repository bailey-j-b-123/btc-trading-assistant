"""Bounded data-derived rules. Pure learning receives only the discovery prefix.

No data access, named-strategy grid, eval, code generation or account interface.
Future returns are training labels only, with their full horizon inside training.
"""

from dataclasses import dataclass, asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from statistics import mean

from .data import candle_fingerprint
from .models import identity, decimal_context, research_code_fingerprint
from trading_assistant.market_structure.volatility import (
    calculate_volatility,
    VolatilityParameters,
)

TRAIN_START = datetime(2023, 4, 1, tzinfo=UTC)
TRAIN_END = datetime(2023, 10, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
FEATURES = (
    "signed_body",
    "upper_wick",
    "lower_wick",
    "close_location",
    "relative_volume",
    "relative_range",
    "opening_gap_in_prior_range",
    "prior_range_position",
)
PERIODS = (
    ("2023_forward", "2023-10-01", "2024-01-01"),
    ("reused_validation_1", "2024-01-01", "2024-05-01"),
    ("reused_validation_2", "2024-05-01", "2024-09-01"),
    ("reused_validation_3", "2024-09-01", "2025-01-01"),
)
PERIODS += tuple(
    (
        f"robustness_2024_{month:02}",
        f"2024-{month:02}-01",
        f"2024-{month + 2:02}-01" if month < 11 else "2025-01-01",
    )
    for month in (1, 3, 5, 7, 9, 11)
)

VERSION = "observed-rule-discovery-v1"


def scope(candles):
    return (
        bool(candles)
        and all(
            (c.exchange, c.symbol, c.timeframe) == ("binance", "BTC/USDT", "1h")
            for c in candles
        )
        and all(b.timestamp - a.timestamp == HOUR for a, b in zip(candles, candles[1:]))
    )


def features(candles, *, known_at):
    """Latest closed candle geometry against ONLY preceding24 closed candles."""
    closed = tuple(c for c in candles if c.timestamp + HOUR <= known_at)
    if len(closed) < 31:
        return None
    w = closed[-31:]
    if not scope(w) or w[-1].timestamp + HOUR != known_at:
        return None
    c = w[-1]
    prior = w[-25:-1]
    span = c.high - c.low
    volume = mean(x.volume for x in prior)
    ordinary_range = mean(x.high - x.low for x in prior)
    bottom = min(x.low for x in prior)
    top = max(x.high for x in prior)
    if span <= 0 or volume <= 0 or ordinary_range <= 0 or top <= bottom:
        return None
    return dict(
        signed_body=(c.close - c.open) / span,
        upper_wick=(c.high - max(c.open, c.close)) / span,
        lower_wick=(min(c.open, c.close) - c.low) / span,
        close_location=(c.close - c.low) / span,
        relative_volume=c.volume / volume,
        relative_range=span / ordinary_range,
        opening_gap_in_prior_range=(c.open - w[-2].close) / ordinary_range,
        prior_range_position=(c.close - bottom) / (top - bottom),
    )


@dataclass(frozen=True)
class Condition:
    feature: str
    operator: str
    threshold: D

    def __post_init__(self):
        if (
            self.feature not in FEATURES
            or self.operator not in ("le", "gt")
            or not isinstance(self.threshold, D)
            or not self.threshold.is_finite()
        ):
            raise ValueError("bounded finite feature condition required")

    def matches(self, values):
        return (
            values[self.feature] <= self.threshold
            if self.operator == "le"
            else values[self.feature] > self.threshold
        )


@dataclass(frozen=True)
class Rule:
    conditions: tuple[Condition, ...]
    training_fingerprint: str
    fit_source_sha256: str
    training_samples: int
    training_mean_bps: D
    version: str = VERSION
    timeframe: str = "1h"
    warmup: int = 31
    max_hold: int = 48

    def __post_init__(self):
        if (
            not isinstance(self.conditions, tuple)
            or not 1 <= len(self.conditions) <= 3
            or any(not isinstance(c, Condition) for c in self.conditions)
        ):
            raise ValueError("one to three learned conditions required")
        if (
            self.version != VERSION
            or self.timeframe != "1h"
            or self.warmup != 31
            or self.max_hold != 48
            or type(self.training_samples) is not int
            or self.training_samples < 120
        ):
            raise ValueError("frozen discovery rule required")
        if (
            not isinstance(self.training_mean_bps, D)
            or not self.training_mean_bps.is_finite()
        ):
            raise ValueError("finite training estimate required")
        for v in (self.training_fingerprint, self.fit_source_sha256):
            if (
                not isinstance(v, str)
                or len(v) != 64
                or any(c not in "0123456789abcdef" for c in v)
            ):
                raise ValueError("content-addressed discovery provenance required")

    @property
    def fingerprint(self):
        return identity(asdict(self))

    def text(self):
        return " AND ".join(
            f"{c.feature} {'<=' if c.operator == 'le' else '>'} {c.threshold}"
            for c in self.conditions
        )


def load_rule(value):
    r = dict(value)
    r["conditions"] = tuple(
        Condition(c["feature"], c["operator"], D(c["threshold"]))
        for c in r["conditions"]
    )
    r["training_mean_bps"] = D(r["training_mean_bps"])
    return Rule(**r)


@decimal_context
def discover(candles):
    """One fixed regression tree; at most eight disjoint leaf hypotheses.

    Thresholds and feature interactions are fitted from training observations;
    names, feature periods and stop/target configurations are not searched.
    No evaluation data/store/file/network handle is accepted by this function.
    """
    if (
        not isinstance(candles, tuple)
        or not scope(candles)
        or candles[0].timestamp != TRAIN_START
        or candles[-1].timestamp + HOUR != TRAIN_END
    ):
        raise ValueError("only exact contiguous Apr-Sep2023 discovery prefix accepted")
    training = candle_fingerprint(candles)
    source = research_code_fingerprint()
    rows = []
    excluded = 0
    for i in range(30, len(candles) - 24):
        known = candles[i].timestamp + HOUR
        values = features(candles[: i + 1], known_at=known)
        if values is None:
            excluded += 1
            continue
        label_at = candles[i + 24].timestamp + HOUR
        assert label_at <= TRAIN_END
        rows.append(
            (
                values,
                (candles[i + 24].close / candles[i].close - 1) * 10000,
                known,
                label_at,
            )
        )
    if len(rows) < 240:
        raise ValueError("insufficient valid training observations")
    unconditional = mean(r[1] for r in rows)
    leaves = []
    trials = 0

    def loss(group):
        y = [r[1] for r in group]
        return sum(v * v for v in y) - sum(y) ** 2 / len(y)

    def split(group, path, depth):
        nonlocal trials
        if depth == 3 or len(group) < 240:
            leaves.append((path, group))
            return
        original = loss(group)
        best = None
        for feature in FEATURES:
            ordered = sorted(r[0][feature] for r in group)
            for threshold in sorted(
                {ordered[(len(ordered) - 1) * q // 4] for q in (1, 2, 3)}
            ):
                left = [r for r in group if r[0][feature] <= threshold]
                right = [r for r in group if r[0][feature] > threshold]
                trials += 1
                if min(len(left), len(right)) < 120:
                    continue
                gain = original - loss(left) - loss(right)
                if gain > 0 and (best is None or gain > best[0]):
                    best = (gain, feature, threshold, left, right)
        if best is None:
            leaves.append((path, group))
            return
        _, feature, threshold, left, right = best
        split(left, path + (Condition(feature, "le", threshold),), depth + 1)
        split(right, path + (Condition(feature, "gt", threshold),), depth + 1)

    split(rows, (), 0)
    accepted = []
    hypotheses = []
    for path, group in leaves:
        estimate = mean(r[1] for r in group)
        if not path:
            hypotheses.append(
                {
                    "accepted": False,
                    "reason": "no_positive_split_gain",
                    "samples": len(group),
                }
            )
            continue
        rule = Rule(path, training, source, len(group), estimate)
        admitted = estimate > max(D(0), unconditional)
        hypotheses.append(
            {
                "id": rule.fingerprint,
                "rule": asdict(rule),
                "text": rule.text(),
                "accepted": admitted,
                "reason": None
                if admitted
                else "not_above_zero_and_unconditional_training_return",
            }
        )
        if admitted:
            accepted.append(rule)
    assert len(hypotheses) <= 8 and len({r.fingerprint for r in accepted}) == len(
        accepted
    )
    return {
        "version": VERSION,
        "training_fingerprint": training,
        "fit_source_sha256": source,
        "training_start": TRAIN_START,
        "training_end_exclusive": TRAIN_END,
        "valid_training_rows": len(rows),
        "invalid_geometry_rows": excluded,
        "label_horizon_hours": 24,
        "first_decision": rows[0][2],
        "last_decision": rows[-1][2],
        "last_label_available": rows[-1][3],
        "unconditional_training_mean_bps": unconditional,
        "split_trials": trials,
        "maximum_split_trials": 168,
        "family_bound": 176,
        "hypotheses": hypotheses,
        "admitted_ids": [r.fingerprint for r in accepted],
    }


@decimal_context
def decide(candles, rule, *, known_at):
    if (
        not isinstance(rule, Rule)
        or rule.fit_source_sha256 != research_code_fingerprint()
    ):
        raise ValueError("frozen learned rule/source mismatch")
    if known_at > datetime(2025, 1, 1, tzinfo=UTC):
        raise ValueError(
            "sealed2025 and later data excluded from this discovery experiment"
        )
    if known_at < TRAIN_END:
        raise ValueError("learned rules cannot retrospectively trade discovery data")
    values = features(candles, known_at=known_at)
    if values is None or not all(c.matches(values) for c in rule.conditions):
        return None
    closed = tuple(c for c in candles if c.timestamp + HOUR <= known_at)[-31:]
    atr = calculate_volatility(
        closed,
        interval=HOUR,
        as_of=known_at,
        parameters=VolatilityParameters(period=14),
    ).atr
    if atr is None or atr <= 0 or closed[-1].close <= 2 * atr:
        return None
    ref = closed[-1].close
    return {
        "known_at": known_at.isoformat(),
        "candle_open": closed[-1].timestamp.isoformat(),
        "reference": str(ref),
        "stop": str(ref - 2 * atr),
        "target": str(ref + 4 * atr),
        "evidence": {
            "family": VERSION,
            "rule_id": rule.fingerprint,
            "atr": str(atr),
            "features": {k: str(v) for k, v in values.items()},
            "regime": "learned_leaf",
            "closed_count": 31,
            "window_start": closed[0].timestamp.isoformat(),
        },
    }
