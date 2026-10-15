"""Frozen conservative research gates; no claim of a guaranteed statistical edge.

Block-bootstrap quantiles are exploratory under temporal dependence. Dividing
the tail budget by the FULL declared search size is a conservative safeguard,
not proof of family-wise error control for dependent/nonstationary returns.
"""

from dataclasses import dataclass
from decimal import Decimal as D
import random
from .models import identity, decimal_context


@dataclass(frozen=True)
class ValidationRules:
    minimum_development_trades: int = 100
    minimum_fold_trades: int = 20
    minimum_holdout_trades: int = 50
    max_drawdown: D = D("0.20")
    maximum_ambiguity_fraction: D = D("0.10")
    alpha: D = D("0.05")
    bootstrap_samples: int = 4000
    block_trades: int = 5
    version: str = "research-validation-v1"

    def __post_init__(self):
        for n in (
            self.minimum_development_trades,
            self.minimum_fold_trades,
            self.minimum_holdout_trades,
            self.block_trades,
        ):
            if type(n) is not int or n < 1:
                raise ValueError("positive sample budgets required")
        if (
            type(self.bootstrap_samples) is not int
            or not 1000 <= self.bootstrap_samples <= 20000
        ):
            raise ValueError("bounded bootstrap budget required")
        for n in (self.max_drawdown, self.maximum_ambiguity_fraction, self.alpha):
            if not isinstance(n, D) or not n.is_finite() or not 0 < n < 1:
                raise ValueError("invalid validation fraction")


@decimal_context
def bootstrap_lower(values, *, search_size, rules, seed):
    if type(search_size) is not int or not 1 <= search_size <= 128:
        raise ValueError("bounded complete search denominator required")
    data = tuple(D(str(x)) for x in values)
    if not data or any(not x.is_finite() for x in data):
        return None
    # Preserve chronological trade blocks; adjacent windows can overlap and
    # the sampled circular blocks are an explicitly documented approximation.
    rng = random.Random(int(identity(seed)[:16], 16))
    means = []
    n = len(data)
    for _ in range(rules.bootstrap_samples):
        sampled = []
        while len(sampled) < n:
            start = rng.randrange(n)
            sampled.extend(data[(start + j) % n] for j in range(rules.block_trades))
        means.append(sum(sampled[:n], D(0)) / n)
    means.sort()
    quantile = int(D(len(means)) * rules.alpha / D(search_size))
    return means[max(0, quantile - 1)]


@decimal_context
def gate(results, *, search_size, rules, stage, seed):
    if stage not in {"development", "walk_forward", "holdout"}:
        raise ValueError("invalid validation stage")
    minimum = (
        rules.minimum_development_trades
        if stage == "development"
        else rules.minimum_holdout_trades
        if stage == "holdout"
        else rules.minimum_fold_trades
    )
    reasons = []
    sample = results["closed_trades"]
    lower = None
    if sample < minimum:
        reasons.append("insufficient_" + stage + "_sample")
    if results["unresolved_gap_positions"] or results["net_return"] is None:
        reasons.append("unresolved_data")
    if results["open_positions"] or results["pending_entries"]:
        reasons.append("unfinished_position_or_pending_entry")
    if results["expectancy_r"] is None or D(results["expectancy_r"]) <= 0:
        reasons.append("nonpositive_after_cost_expectancy")
    if results["net_return"] is None or D(results["net_return"]) <= 0:
        reasons.append("nonpositive_after_cost_return")
    if (
        results["maximum_close_mark_drawdown"] is None
        or D(results["maximum_close_mark_drawdown"]) > rules.max_drawdown
    ):
        reasons.append("drawdown_limit")
    if (
        sample
        and D(results["ambiguous_adverse_trades"]) / sample
        > rules.maximum_ambiguity_fraction
    ):
        reasons.append("excess_intrabar_ambiguity")
    if not reasons:
        lower = bootstrap_lower(
            results["net_r_values"], search_size=search_size, rules=rules, seed=seed
        )
        if lower is None or lower <= 0:
            reasons.append("block_bootstrap_lower_not_positive")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "sample_size": sample,
        "block_bootstrap_lower_r": None if lower is None else str(lower),
        "full_search_size": search_size,
        "tail_budget": str(rules.alpha / D(search_size)),
        "stage": stage,
        "rules_version": rules.version,
        "uncertainty_limit": "exploratory block-bootstrap; not proven family-wise error control or a profit guarantee",
    }
