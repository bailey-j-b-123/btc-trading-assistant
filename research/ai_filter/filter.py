"""Research-only deterministic logistic TAKE/REJECT filter; no trading/execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

VERSION = "brain-ai-filter-logistic-v1"
FEATURE_NAMES = (
    "direction", "atr_pct", "trend_strength_atr", "directional_momentum24",
    "relative_volume24", "rsi14",
)


class FilterError(ValueError):
    """Invalid training data or serialized model."""


@dataclass(frozen=True)
class TrainingExample:
    features: Mapping[str, object]
    net_r: float
    signal_time: datetime
    outcome_time: datetime


def _features(raw: Mapping[str, object]) -> tuple[float, ...]:
    if not isinstance(raw, Mapping) or set(raw) != set(FEATURE_NAMES):
        raise FilterError("features must contain exactly the required feature names")
    direction = raw["direction"]
    if direction not in ("LONG", "SHORT"):
        raise FilterError("direction must be LONG or SHORT")
    values: list[float] = [1.0 if direction == "LONG" else -1.0]
    for name in FEATURE_NAMES[1:]:
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FilterError(f"{name} must be a finite number")
        number = float(value)
        if not math.isfinite(number):
            raise FilterError(f"{name} must be a finite number")
        values.append(number)
    return tuple(values)


def _utc(dt: datetime, label: str) -> datetime:
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise FilterError(f"{label} must be timezone-aware")
    return dt


@dataclass(frozen=True)
class Prediction:
    decision: str
    probability: float
    model_version: str
    fingerprint: str


class LogisticTakeRejectFilter:
    """Small full-batch L2 logistic model with fixed iteration count and no RNG."""

    def __init__(self, threshold: float = 0.5, l2: float = 1.0, steps: int = 2500, learning_rate: float = 0.05):
        if not math.isfinite(threshold) or not 0 < threshold < 1:
            raise FilterError("threshold must be between zero and one")
        if not math.isfinite(l2) or l2 < 0 or steps < 1 or not math.isfinite(learning_rate) or learning_rate <= 0:
            raise FilterError("invalid optimizer configuration")
        self.threshold, self.l2, self.steps, self.learning_rate = threshold, l2, steps, learning_rate
        self._weights: tuple[float, ...] | None = None
        self._fingerprint: str | None = None

    @property
    def fitted(self) -> bool:
        return self._weights is not None

    def fit(self, examples: Iterable[TrainingExample], *, training_cutoff: datetime, test_start: datetime | None = None) -> "LogisticTakeRejectFilter":
        cutoff = _utc(training_cutoff, "training_cutoff")
        if test_start is not None and _utc(test_start, "test_start") <= cutoff:
            raise FilterError("test_start must be strictly after training_cutoff")
        rows = list(examples)
        if len(rows) < 2:
            raise FilterError("at least two training examples are required")
        parsed: list[tuple[tuple[float, ...], int]] = []
        for row in rows:
            if not isinstance(row, TrainingExample):
                raise FilterError("training rows must be TrainingExample values")
            signal, outcome = _utc(row.signal_time, "signal_time"), _utc(row.outcome_time, "outcome_time")
            if signal >= outcome:
                raise FilterError("outcome_time must follow signal_time")
            if outcome > cutoff:
                raise FilterError("training outcome crosses training_cutoff")
            if isinstance(row.net_r, bool) or not isinstance(row.net_r, (int, float)) or not math.isfinite(float(row.net_r)):
                raise FilterError("net_r must be finite")
            parsed.append((_features(row.features), int(float(row.net_r) > 0)))
        if len({label for _, label in parsed}) != 2:
            raise FilterError("training data must contain both positive and non-positive outcomes")
        # Standardise numeric predictors from training data only; direction is already -1/+1.
        means = [0.0] * 6
        scales = [1.0] * 6
        for j in range(1, 6):
            means[j] = sum(x[j] for x, _ in parsed) / len(parsed)
            variance = sum((x[j] - means[j]) ** 2 for x, _ in parsed) / len(parsed)
            scales[j] = math.sqrt(variance) or 1.0
        xmat = [[1.0] + [(x[j] - means[j]) / scales[j] for j in range(1, 6)] + [x[0]] for x, _ in parsed]
        # order: bias, five standardized numeric values, signed direction
        weights = [0.0] * 7
        for _ in range(self.steps):
            grad = [0.0] * 7
            for vector, (_, y) in zip(xmat, parsed):
                z = max(-40.0, min(40.0, sum(a * b for a, b in zip(weights, vector))))
                p = 1.0 / (1.0 + math.exp(-z))
                for j in range(7):
                    grad[j] += (p - y) * vector[j] / len(parsed)
            for j in range(1, 7):
                grad[j] += self.l2 * weights[j]
            weights = [w - self.learning_rate * g for w, g in zip(weights, grad)]
        self._means, self._scales, self._weights = tuple(means[1:]), tuple(scales[1:]), tuple(weights)
        payload = self._payload()
        self._fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return self

    def predict(self, features: Mapping[str, object]) -> Prediction:
        if not self.fitted:
            raise FilterError("model is not fitted")
        raw = _features(features)
        vector = [1.0] + [(raw[j + 1] - self._means[j]) / self._scales[j] for j in range(5)] + [raw[0]]
        z = max(-40.0, min(40.0, sum(a * b for a, b in zip(self._weights, vector))))
        probability = 1.0 / (1.0 + math.exp(-z))
        return Prediction("TAKE" if probability >= self.threshold else "REJECT", probability, VERSION, self._fingerprint)

    def _payload(self) -> dict:
        if not self.fitted:
            raise FilterError("model is not fitted")
        return {"version": VERSION, "feature_names": list(FEATURE_NAMES), "threshold": self.threshold, "l2": self.l2,
                "steps": self.steps, "learning_rate": self.learning_rate, "means": list(self._means),
                "scales": list(self._scales), "weights": list(self._weights)}

    def save(self, path: str | Path) -> None:
        payload = self._payload()
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        Path(path).write_text(json.dumps({**payload, "fingerprint": fingerprint}, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "LogisticTakeRejectFilter":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            fingerprint = data.pop("fingerprint")
            if data.get("version") != VERSION or data.get("feature_names") != list(FEATURE_NAMES):
                raise FilterError("unsupported model version or feature schema")
            actual = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            if fingerprint != actual:
                raise FilterError("model fingerprint mismatch")
            model = cls(data["threshold"], data["l2"], data["steps"], data["learning_rate"])
            model._means, model._scales, model._weights = tuple(data["means"]), tuple(data["scales"]), tuple(data["weights"])
            if len(model._means) != 5 or len(model._scales) != 5 or len(model._weights) != 7 or not all(math.isfinite(float(v)) for v in (*model._means, *model._scales, *model._weights)):
                raise FilterError("invalid model parameters")
            if any(v <= 0 for v in model._scales):
                raise FilterError("invalid model scales")
            model._fingerprint = fingerprint
            return model
        except FilterError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise FilterError("invalid model file") from exc
