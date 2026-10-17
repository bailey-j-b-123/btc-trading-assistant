"""Research-only deterministic logistic TAKE/REJECT filter; no trading/execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Mapping

VERSION = "brain-ai-filter-logistic-v1"
FEATURE_NAMES = (
    "direction", "atr_pct", "trend_strength_atr", "directional_momentum24",
    "relative_volume24", "rsi14",
)
OUTCOME_EMBARGO = timedelta(hours=120)
MAX_FEATURE_STANDARD_DEVIATIONS = 8.0  # conservative out-of-distribution fail-closed guard
MAX_ABS_COEFFICIENT = 40.0  # above this value logits are necessarily saturation-prone


class FilterError(ValueError):
    """Invalid training data, prediction input, or serialized model."""


@dataclass(frozen=True)
class TrainingExample:
    features: Mapping[str, object]
    net_r: float
    signal_time: datetime
    outcome_time: datetime


def _finite_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FilterError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise FilterError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise FilterError(f"{label} must be a finite number")
    return number


def _finite_vector(values: Iterable[float], label: str) -> list[float]:
    result = list(values)
    if not all(math.isfinite(x) for x in result):
        raise FilterError(f"{label} has a non-finite derived value")
    return result


def _features(raw: Mapping[str, object]) -> tuple[float, ...]:
    if not isinstance(raw, Mapping) or set(raw) != set(FEATURE_NAMES):
        raise FilterError("features must contain exactly the required feature names")
    direction = raw["direction"]
    if direction not in ("LONG", "SHORT"):
        raise FilterError("direction must be LONG or SHORT")
    values = [1.0 if direction == "LONG" else -1.0]
    for name in FEATURE_NAMES[1:]:
        values.append(_finite_float(raw[name], name))
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
        threshold = _finite_float(threshold, "threshold")
        l2 = _finite_float(l2, "l2")
        learning_rate = _finite_float(learning_rate, "learning_rate")
        if not 0 < threshold < 1:
            raise FilterError("threshold must be between zero and one")
        if l2 < 0 or type(steps) is not int or steps < 1 or learning_rate <= 0:
            raise FilterError("invalid optimizer configuration")
        self.threshold, self.l2, self.steps, self.learning_rate = threshold, l2, steps, learning_rate
        self._weights: tuple[float, ...] | None = None
        self._fingerprint: str | None = None

    @property
    def fitted(self) -> bool:
        return self._weights is not None

    def _vector(self, features: tuple[float, ...]) -> list[float]:
        """Compute scaled features, rejecting finite values that overflow on scaling."""
        scaled = []
        for j in range(5):
            diff = features[j + 1] - self._means[j]
            if not math.isfinite(diff):
                raise FilterError("feature-centering overflow")
            value = diff / self._scales[j]
            if not math.isfinite(value):
                raise FilterError("feature-standardization overflow")
            if abs(value) > MAX_FEATURE_STANDARD_DEVIATIONS:
                raise FilterError("feature outside fitted training distribution")
            scaled.append(value)
        return [1.0] + scaled + [features[0]]

    def _logit(self, vector: list[float]) -> float:
        # Clamp only AFTER confirming the dot-product is finite; min/max alone
        # silently turns +/-infinity into a confident TAKE/REJECT.
        terms = _finite_vector((a * b for a, b in zip(self._weights, vector)), "model logit")
        z = sum(terms)
        if not math.isfinite(z):
            raise FilterError("model logit has non-finite derived value")
        return max(-40.0, min(40.0, z))

    def fit(self, examples: Iterable[TrainingExample], *, training_cutoff: datetime, test_start: datetime | None = None) -> "LogisticTakeRejectFilter":
        cutoff = _utc(training_cutoff, "training_cutoff")
        if test_start is not None and _utc(test_start, "test_start") - cutoff < OUTCOME_EMBARGO:
            raise FilterError("test_start must be at least 120 hours after training_cutoff")
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
            try:
                label = int(_finite_float(row.net_r, "net_r") > 0)
            except FilterError as exc:
                raise FilterError("net_r must be finite") from exc
            parsed.append((_features(row.features), label))
        if len({label for _, label in parsed}) != 2:
            raise FilterError("training data must contain both positive and non-positive outcomes")

        # Fit scaling on training data only; do not learn from test set.
        means = [0.0] * 5
        scales = [1.0] * 5
        for j in range(5):
            values = [x[j + 1] for x, _ in parsed]
            means[j] = sum(values) / len(parsed)
            if not math.isfinite(means[j]):
                raise FilterError("training mean overflow")
            centered = _finite_vector((v - means[j] for v in values), "training centering")
            squares = _finite_vector((v * v for v in centered), "training variance")
            variance = sum(squares) / len(parsed)
            if not math.isfinite(variance):
                raise FilterError("training variance overflow")
            scales[j] = math.sqrt(variance) or 1.0
            if not math.isfinite(scales[j]) or scales[j] <= 0:
                raise FilterError("invalid training scale")
        self._means, self._scales = tuple(means), tuple(scales)
        xmat = [self._vector(x) for x, _ in parsed]
        weights = [0.0] * 7
        labels = [label for _, label in parsed]

        for _ in range(self.steps):
            gradient = [0.0] * 7
            for vector, y in zip(xmat, labels):
                terms = _finite_vector((a * b for a, b in zip(weights, vector)), "training logit")
                z = sum(terms)
                if not math.isfinite(z):
                    raise FilterError("training logit overflow")
                z = max(-40.0, min(40.0, z))
                p = 1.0 / (1.0 + math.exp(-z))
                for j in range(7):
                    gradient[j] += (p - y) * vector[j] / len(parsed)
            for j in range(1, 7):
                gradient[j] += self.l2 * weights[j]
            _finite_vector(gradient, "model gradient")
            weights = _finite_vector(
                (w - self.learning_rate * g for w, g in zip(weights, gradient)),
                "model weights",
            )
        if any(abs(v) > MAX_ABS_COEFFICIENT for v in weights):
            raise FilterError("fitted model coefficients exceed safe magnitude")
        self._weights = tuple(weights)
        payload = self._payload()
        self._fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return self

    def predict(self, features: Mapping[str, object]) -> Prediction:
        if not self.fitted:
            raise FilterError("model is not fitted")
        vector = self._vector(_features(features))
        z = self._logit(vector)
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
            if not isinstance(data, dict):
                raise FilterError("invalid model file")
            fingerprint = data.pop("fingerprint")
            expected = {"version", "feature_names", "threshold", "l2", "steps", "learning_rate",
                        "means", "scales", "weights"}
            if set(data) != expected or data.get("version") != VERSION or data.get("feature_names") != list(FEATURE_NAMES):
                raise FilterError("unsupported model version or feature schema")
            actual = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            if fingerprint != actual:
                raise FilterError("model fingerprint mismatch")
            model = cls(data["threshold"], data["l2"], data["steps"], data["learning_rate"])
            if not all(type(data[k]) is list and len(data[k]) == n
                       for k, n in (("means", 5), ("scales", 5), ("weights", 7))):
                raise FilterError("invalid model parameter dimensions")
            model._means = tuple(_finite_float(v, "model mean") for v in data["means"])
            model._scales = tuple(_finite_float(v, "model scale") for v in data["scales"])
            model._weights = tuple(_finite_float(v, "model weight") for v in data["weights"])
            if any(abs(v) > MAX_ABS_COEFFICIENT for v in model._weights):
                raise FilterError("serialized coefficient magnitude is unsafe")
            if any(v <= 0 for v in model._scales):
                raise FilterError("invalid model scales")
            model._fingerprint = fingerprint
            return model
        except FilterError:
            raise
        except (OSError, KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise FilterError("invalid model file") from exc
