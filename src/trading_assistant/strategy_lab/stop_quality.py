"""Explicit, research-only stop suitability; reject rather than widen a stop."""

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from decimal import Decimal as D, InvalidOperation
from trading_assistant.market_data.timeframes import timeframe_to_milliseconds


@dataclass(frozen=True)
class StopQuality:
    minimum_atr_fraction: D
    minimum_roundtrip_cost_multiple: D
    minimum_observed_range_fraction: D
    minimum_observed_gap_multiple: D
    volatility_lookback: int
    version: str = "stop-quality-v1"

    def __post_init__(self):
        for n in (
            "minimum_atr_fraction",
            "minimum_roundtrip_cost_multiple",
            "minimum_observed_range_fraction",
            "minimum_observed_gap_multiple",
        ):
            v = getattr(self, n)
            if not isinstance(v, D) or not v.is_finite() or not 0 <= v <= 100:
                raise ValueError(
                    "explicit finite nonnegative bounded stop-quality thresholds required"
                )
        if (
            type(self.volatility_lookback) is not int
            or not 2 <= self.volatility_lookback <= 1000
            or self.version != "stop-quality-v1"
        ):
            raise ValueError("invalid versioned stop-quality lookback")

    def assess(self, signal, candles, *, known_at, execution):
        result = {
            "version": self.version,
            "known_at": known_at.isoformat(),
            "thresholds": quality_definition(self),
            "reasons": [],
            "original_stop": signal["stop"],
            "original_reference": signal["reference"],
            "alternative_stop_selected": False,
            "structural_scope": "original planner/detector stop retained; no new invalidation claimed",
        }
        ref, stop = D(signal["reference"]), D(signal["stop"])
        evidence = signal.get("evidence", {})
        try:
            atr = D(evidence.get("atr", "NaN"))
        except (InvalidOperation, TypeError, ValueError):
            atr = D("NaN")
        atr_at = evidence.get("atr_known_at")
        if atr_at:
            try:
                at = datetime.fromisoformat(atr_at)
                assert at.tzinfo is not None and at <= known_at
            except (ValueError, TypeError, AssertionError):
                result["reasons"].append("stop_quality_future_or_invalid_atr_time")
                return result
        if not ref.is_finite() or not stop.is_finite() or not 0 < stop < ref:
            result["reasons"].append("stop_quality_invalid_original_levels")
            return result
        if not atr.is_finite() or atr <= 0:
            result["reasons"].append("stop_quality_missing_original_atr")
            return result
        window = tuple(candles[-self.volatility_lookback - 1 :])
        if len(window) != self.volatility_lookback + 1:
            result["reasons"].append("stop_quality_insufficient_observed_volatility")
            return result
        interval = timedelta(
            milliseconds=timeframe_to_milliseconds(window[-1].timeframe)
        )
        scope = (window[-1].exchange, window[-1].symbol, window[-1].timeframe)
        if (
            window[-1].timestamp + interval != known_at
            or any((c.exchange, c.symbol, c.timeframe) != scope for c in window)
            or any(
                b.timestamp - a.timestamp != interval
                for a, b in zip(window, window[1:])
            )
        ):
            result["reasons"].append("stop_quality_noncausal_or_incomplete_window")
            return result
        distance = ref - stop
        observed = [
            max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
            for a, b in zip(window, window[1:])
        ]
        mean_range = sum(observed, D(0)) / len(observed)
        max_gap = max(abs(b.open - a.close) for a, b in zip(window, window[1:]))
        # Exact flat-reference loss/BTC: adverse buy/sell prices and both fees.
        roundtrip = 2 * ref * (execution.impact + execution.fee)
        required = {
            "stop_quality_atr_distance": atr * self.minimum_atr_fraction,
            "stop_quality_roundtrip_cost_distance": roundtrip
            * self.minimum_roundtrip_cost_multiple,
            "stop_quality_observed_range_distance": mean_range
            * self.minimum_observed_range_fraction,
            "stop_quality_observed_gap_distance": max_gap
            * self.minimum_observed_gap_multiple,
        }
        result.update(
            original_atr=str(atr),
            original_atr_timeframe=evidence.get("atr_timeframe", scope[2]),
            original_atr_known_at=atr_at or known_at.isoformat(),
            stop_distance=str(distance),
            exact_flat_roundtrip_cost_per_btc=str(roundtrip),
            mean_observed_true_range=str(mean_range),
            maximum_observed_gap=str(max_gap),
            source_first_open=window[0].timestamp.isoformat(),
            source_last_close=known_at.isoformat(),
            timeframe=scope[2],
            required_distances={k: str(v) for k, v in required.items()},
        )
        result["reasons"] = [k for k, v in required.items() if distance < v]
        return result


def quality_definition(policy):
    if policy is not None and not isinstance(policy, StopQuality):
        raise ValueError("stop_quality must be None or StopQuality")
    return (
        None
        if policy is None
        else {k: str(v) if isinstance(v, D) else v for k, v in asdict(policy).items()}
    )
