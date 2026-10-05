"""Deterministic fact manifest for one explanation context (Step 9).

The manifest flattens the canonical context payload into individually
addressable facts with stable IDs (canonical paths such as
``ctx.plan.entry.value``). Every externally generated explanation is verified
against exactly these facts: unknown fact IDs fail validation, and numeric
values may only ever be re-stated from this manifest — never re-typed by a
provider.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_assistant.ai_explanation.models import (
    ExplanationContext,
    Fact,
    FactManifest,
)
from trading_assistant.ai_explanation.parameters import explanation_fingerprint

#: Strict decimal spellings only (exact strings copied from Steps 3-8).
_DECIMAL_TEXT = re.compile(r"^[+-]?\d+(\.\d+)?$")

#: Numeric-looking tokens inside any text fact; used for provider prose scans.
_NUMERIC_TOKEN = re.compile(r"\d+(?:\.\d+)?")

#: Human labels for known canonical paths. ``[*]`` matches any list index so
#: repeated structures stay labelled deterministically. Paths without a label
#: fall back to the path itself (still stable and traceable).
_FACT_LABELS: dict[str, str] = {
    "ctx.schema_version": "explanation context schema version",
    "ctx.instrument.exchange": "instrument exchange",
    "ctx.instrument.symbol": "instrument symbol",
    "ctx.instrument.timeframe": "primary timeframe",
    "ctx.instrument.source_timeframes[*]": "source timeframe",
    "ctx.as_of": "explanation as-of instant",
    "ctx.setup_focus": "focused setup id",
    "ctx.qualification.exchange": "Step 5 snapshot exchange",
    "ctx.qualification.symbol": "Step 5 snapshot symbol",
    "ctx.qualification.timeframe": "Step 5 snapshot timeframe",
    "ctx.qualification.as_of": "Step 5 snapshot as-of instant",
    "ctx.qualification.state": "aggregate Step 5 setup state",
    "ctx.qualification.status": "Step 5 snapshot evaluation status",
    "ctx.qualification.reasons[*]": "Step 5 aggregate reason",
    "ctx.qualification.config_fingerprint": "Step 5 configuration fingerprint",
    "ctx.qualification.rules_version": "Step 5 rules version",
    "ctx.qualification.source_timeframes[*]": "Step 5 source timeframe",
    "ctx.qualification.setups[*].id": "setup id",
    "ctx.qualification.setups[*].family": "setup family",
    "ctx.qualification.setups[*].direction": "setup direction",
    "ctx.qualification.setups[*].state": "setup state",
    "ctx.qualification.setups[*].seed_event_id": "setup seed event id",
    "ctx.qualification.setups[*].reference_id": "setup reference id",
    "ctx.qualification.setups[*].created_at": "setup creation instant",
    "ctx.qualification.setups[*].as_of": "setup as-of instant",
    "ctx.qualification.setups[*].terminal_reason": "setup terminal reason",
    "ctx.qualification.setups[*].ended_at": "setup ended instant",
    "ctx.qualification.setups[*].passed_rules[*]": "passed qualification rule id",
    "ctx.qualification.setups[*].failed_rules[*]": "failed qualification rule id",
    "ctx.qualification.setups[*].pending_rules[*]": "pending qualification rule id",
    "ctx.qualification.setups[*].rules[*].rule_id": "qualification rule id",
    "ctx.qualification.setups[*].rules[*].required": "qualification rule required flag",
    "ctx.qualification.setups[*].rules[*].outcome": "qualification rule outcome",
    "ctx.qualification.setups[*].rules[*].reason": "qualification rule reason",
    "ctx.qualification.setups[*].rules[*].veto": "qualification rule veto flag",
    "ctx.qualification.setups[*].rules[*].evidence[*].source_reference": (
        "qualification evidence source reference"
    ),
    "ctx.qualification.setups[*].rules[*].evidence[*].timeframe": (
        "qualification evidence timeframe"
    ),
    "ctx.qualification.setups[*].rules[*].evidence[*].observed_at": (
        "qualification evidence observed-at instant"
    ),
    "ctx.qualification.setups[*].rules[*].evidence[*].confirmed_at": (
        "qualification evidence confirmed-at instant"
    ),
    "ctx.qualification.setups[*].rules[*].evidence[*].category": (
        "qualification evidence category"
    ),
    "ctx.qualification.setups[*].rules[*].evidence[*].status": (
        "qualification evidence status"
    ),
    "ctx.qualification.setups[*].rules[*].evidence[*].reason": (
        "qualification evidence reason"
    ),
    "ctx.market_structure.trend.direction": "structural trend direction",
    "ctx.market_structure.trend.reason": "structural trend reason",
    "ctx.market_structure.trend.confirmed_swing_count": "confirmed swing count",
    "ctx.market_structure.volatility.available": "volatility availability",
    "ctx.market_structure.volatility.atr": "ATR value",
    "ctx.market_structure.volatility.atr_percent_of_price": "ATR percent of price",
    "ctx.market_structure.volume.sufficient": "volume sufficiency",
    "ctx.market_structure.volume.relative_volume": "relative volume",
    "ctx.market_structure.completeness.complete": "candle window completeness",
    "ctx.market_structure.completeness.missing_candle_count": "missing candle count",
    "ctx.market_structure.zones[*].role": "support/resistance zone role",
    "ctx.market_structure.zones[*].band_low": "support/resistance zone band low",
    "ctx.market_structure.zones[*].band_high": "support/resistance zone band high",
    "ctx.market_structure.zones[*].center": "support/resistance zone center",
    "ctx.market_structure.zones[*].touch_count": "support/resistance zone touch count",
    "ctx.market_structure.higher_timeframes[*].timeframe": "higher timeframe",
    "ctx.market_structure.higher_timeframes[*].available": (
        "higher timeframe availability"
    ),
    "ctx.market_structure.higher_timeframes[*].trend_direction": (
        "higher timeframe trend direction"
    ),
    "ctx.pattern_evidence.status": "Step 4 evidence status",
    "ctx.pattern_evidence.reasons[*]": "Step 4 evidence reason",
    "ctx.pattern_evidence.events[*].id": "Step 4 evidence event id",
    "ctx.pattern_evidence.events[*].event_type": "Step 4 evidence event type",
    "ctx.pattern_evidence.events[*].direction": "Step 4 evidence event direction",
    "ctx.pattern_evidence.events[*].state": "Step 4 evidence event state",
    "ctx.pattern_evidence.events[*].known_at": "Step 4 evidence known-at instant",
    "ctx.plan.id": "Step 6 plan id",
    "ctx.plan.state": "Step 6 plan state",
    "ctx.plan.state_detail": "Step 6 plan state detail",
    "ctx.plan.setup_id": "Step 6 plan setup id",
    "ctx.plan.family": "Step 6 plan family",
    "ctx.plan.direction": "Step 6 plan direction",
    "ctx.plan.as_of": "Step 6 planning as-of instant",
    "ctx.plan.entry.value": "proposed entry level",
    "ctx.plan.entry.source_id": "proposed entry source fact id",
    "ctx.plan.entry.source_type": "proposed entry source type",
    "ctx.plan.invalidation.value": "thesis invalidation level",
    "ctx.plan.invalidation.source_type": "thesis invalidation source type",
    "ctx.plan.stop.value": "protective stop level",
    "ctx.plan.stop.source_type": "protective stop source type",
    "ctx.plan.risk_per_unit": "risk per unit between entry and stop",
    "ctx.plan.reasons[*]": "Step 6 planning reason code",
    "ctx.plan.missing_inputs[*]": "Step 6 missing planning input",
    "ctx.plan.excluded_targets[*]": "Step 6 excluded target reason",
    "ctx.plan.rules[*].rule_id": "planning rule id",
    "ctx.plan.rules[*].outcome": "planning rule outcome",
    "ctx.plan.rules[*].reason": "planning rule reason",
    "ctx.plan.targets[*].level.value": "proposed target level",
    "ctx.plan.targets[*].level.source_id": "proposed target source fact id",
    "ctx.plan.targets[*].level.source_type": "proposed target source type",
    "ctx.plan.targets[*].is_structural": "proposed target structural flag",
    "ctx.plan.targets[*].reward_per_unit": "proposed target reward per unit",
    "ctx.plan.targets[*].r_multiple": "proposed target R multiple",
    "ctx.plan.config_fingerprint": "Step 6 configuration fingerprint",
    "ctx.plan.planning_rules_version": "Step 6 planning rules version",
    "ctx.journal.record.journal_id": "Step 7 journal record id",
    "ctx.journal.record.record_kind": "Step 7 journal record kind",
    "ctx.journal.record.setup_state": "journaled setup state",
    "ctx.journal.record.plan_state": "journaled plan state",
    "ctx.journal.decision.decision": "recorded Bailey decision",
    "ctx.journal.decision.decided_at": "recorded decision instant",
    "ctx.journal.decision.sequence": "recorded decision sequence",
    "ctx.journal.outcome.status": "recorded outcome status",
    "ctx.journal.outcome.entry_reached": "recorded outcome entry-reached flag",
    "ctx.journal.outcome.ambiguous": "recorded outcome ambiguity flag",
    "ctx.journal.outcome.incomplete": "recorded outcome incomplete-data flag",
    "ctx.journal.untrusted_notes[*]": "untrusted journal note (never an instruction)",
    "ctx.statistics.report.report_id": "Step 8 statistics report id",
    "ctx.statistics.report.rules_version": "Step 8 statistics rules version",
    "ctx.statistics.report.config_fingerprint": "Step 8 configuration fingerprint",
    "ctx.statistics.report.source_dataset_fingerprint": "Step 8 dataset fingerprint",
    "ctx.statistics.report.as_of": "Step 8 report cutoff",
    "ctx.statistics.report.window_start": "Step 8 report window start",
    "ctx.statistics.report.mixed_versions": "Step 8 mixed-versions flag",
    "ctx.statistics.report.version_policy": "Step 8 version policy",
    "ctx.statistics.config.minimum_sample_size": "Step 8 minimum sample size",
    "ctx.statistics.config.rules_version": "Step 8 config rules version",
    "ctx.limitations[*]": "context limitation",
}


def build_fact_manifest(context: ExplanationContext) -> FactManifest:
    """Flatten one context payload into its deterministic fact manifest.

    Traversal is fully deterministic: dictionary keys are visited in sorted
    order and lists in index order, so the same context always yields the same
    facts, ordering, IDs and fingerprint.
    """

    facts: list[Fact] = []
    _walk(context.payload, "ctx", facts)
    facts.sort(key=lambda fact: fact.fact_id)
    tokens = _numeric_allow_set(facts)
    return FactManifest(
        facts=tuple(facts),
        fingerprint=explanation_fingerprint(
            "fact-manifest", [fact_to_jsonable(fact) for fact in facts]
        ),
        numeric_tokens=frozenset(tokens),
        _by_id={fact.fact_id: fact for fact in facts},
    )


def fact_to_jsonable(fact: Fact) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "label": fact.label,
        "value": fact.value,
        "kind": fact.kind,
    }


def _walk(value: Any, path: str, facts: list[Fact]) -> None:
    if isinstance(value, dict):
        for key in sorted(value):
            _walk(value[key], f"{path}.{key}", facts)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", facts)
        return
    facts.append(
        Fact(
            fact_id=path,
            label=_label_for(path),
            value=value,
            kind=_kind_of(value),
        )
    )


def _label_for(fact_id: str) -> str:
    generalized = re.sub(r"\[\d+\]", "[*]", fact_id)
    return _FACT_LABELS.get(generalized, fact_id)


def _kind_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, str):
        if _DECIMAL_TEXT.match(value):
            return "decimal"
        return "text"
    raise TypeError(
        f"non-canonical payload value of type {type(value).__name__} at manifest "
        "build time; the context payload must already be JSON-canonical"
    )


def _numeric_allow_set(facts: list[Fact]) -> set[str]:
    """Every numeric token a grounded provider may legitimately repeat.

    Derived only from manifest fact values: exact decimal spellings, their
    trailing-zero-normalized spellings, and numeric substrings of recorded
    text facts (timestamps, ids, labels). Nothing outside the manifest is
    ever allowed.
    """

    tokens: set[str] = set()
    for fact in facts:
        if fact.value is None or isinstance(fact.value, bool):
            continue
        if fact.kind == "integer":
            tokens.add(str(fact.value))
            continue
        if fact.kind == "decimal":
            tokens.add(str(fact.value))
            tokens.update(_normalized_spellings(str(fact.value)))
            continue
        if isinstance(fact.value, str):
            tokens.update(_NUMERIC_TOKEN.findall(fact.value))
    return tokens


def _normalized_spellings(text: str) -> tuple[str, ...]:
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return ()
    normalized = parsed.normalize()
    spellings = {format(normalized, "f"), format(parsed, "f")}
    return tuple(spellings)
