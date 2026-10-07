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
    "ctx.multi_timeframe.exchange": "Step 13 hierarchy exchange",
    "ctx.multi_timeframe.symbol": "Step 13 hierarchy symbol",
    "ctx.multi_timeframe.decision_time": "Step 13 hierarchy decision instant",
    "ctx.multi_timeframe.status": "Step 13 hierarchy evaluation status",
    "ctx.multi_timeframe.decision": "Step 13 overall hierarchy decision",
    "ctx.multi_timeframe.alignment": "Step 13 hierarchy alignment",
    "ctx.multi_timeframe.counter_trend": "Step 13 counter-trend flag",
    "ctx.multi_timeframe.rules_version": "Step 13 hierarchy rules version",
    "ctx.multi_timeframe.hierarchy_fingerprint": "Step 13 hierarchy fingerprint",
    "ctx.multi_timeframe.reasons[*]": "Step 13 hierarchy reason",
    "ctx.multi_timeframe.waiting_for[*]": "Step 13 hierarchy waiting-for item",
    "ctx.multi_timeframe.invalidated_if[*]": "Step 13 hierarchy invalidated-if item",
    "ctx.multi_timeframe.strategy_versions[*].name": "Step 13 strategy version name",
    "ctx.multi_timeframe.strategy_versions[*].version": (
        "Step 13 strategy version value"
    ),
    "ctx.multi_timeframe.hierarchy.timeframes[*]": "Step 13 hierarchy timeframe",
    "ctx.multi_timeframe.hierarchy.steps[*].role": "Step 13 hierarchy step role",
    "ctx.multi_timeframe.hierarchy.steps[*].timeframe": (
        "Step 13 hierarchy step timeframe"
    ),
    "ctx.multi_timeframe.hierarchy.rules_version": (
        "Step 13 hierarchy configuration rules version"
    ),
    "ctx.multi_timeframe.hierarchy.fingerprint": (
        "Step 13 hierarchy configuration fingerprint"
    ),
    "ctx.multi_timeframe.context.timeframe": "Step 13 context timeframe",
    "ctx.multi_timeframe.context.available": "Step 13 context availability",
    "ctx.multi_timeframe.context.regime": "Step 13 context regime",
    "ctx.multi_timeframe.context.trend_direction": "Step 13 context trend direction",
    "ctx.multi_timeframe.context.trend_reason": "Step 13 context trend reason",
    "ctx.multi_timeframe.context.confirmed_swing_count": (
        "Step 13 context confirmed swing count"
    ),
    "ctx.multi_timeframe.context.active_range_low": "Step 13 context range low",
    "ctx.multi_timeframe.context.active_range_high": "Step 13 context range high",
    "ctx.multi_timeframe.context.latest_close": "Step 13 context latest close",
    "ctx.multi_timeframe.context.candle_count": "Step 13 context candle count",
    "ctx.multi_timeframe.context.stale": "Step 13 context staleness flag",
    "ctx.multi_timeframe.context.reason": "Step 13 context reason",
    "ctx.multi_timeframe.context.evidence[*].category": (
        "Step 13 context evidence category"
    ),
    "ctx.multi_timeframe.context.evidence[*].status": (
        "Step 13 context evidence status"
    ),
    "ctx.multi_timeframe.context.evidence[*].reason": (
        "Step 13 context evidence reason"
    ),
    "ctx.multi_timeframe.context.evidence[*].timeframe": (
        "Step 13 context evidence timeframe"
    ),
    "ctx.multi_timeframe.setup.timeframe": "Step 13 setup timeframe",
    "ctx.multi_timeframe.setup.available": "Step 13 setup availability",
    "ctx.multi_timeframe.setup.state": "Step 13 aggregate setup state",
    "ctx.multi_timeframe.setup.snapshot_status": "Step 13 setup snapshot status",
    "ctx.multi_timeframe.setup.snapshot_id": "Step 13 setup snapshot id",
    "ctx.multi_timeframe.setup.rules_version": "Step 13 setup rules version",
    "ctx.multi_timeframe.setup.config_fingerprint": (
        "Step 13 setup configuration fingerprint"
    ),
    "ctx.multi_timeframe.setup.setup_id": "Step 13 active setup id",
    "ctx.multi_timeframe.setup.family": "Step 13 active setup family",
    "ctx.multi_timeframe.setup.direction": "Step 13 active setup direction",
    "ctx.multi_timeframe.setup.setup_state": "Step 13 active setup state",
    "ctx.multi_timeframe.setup.created_at": "Step 13 setup creation instant",
    "ctx.multi_timeframe.setup.terminal_reason": "Step 13 setup terminal reason",
    "ctx.multi_timeframe.setup.ended_at": "Step 13 setup ended instant",
    "ctx.multi_timeframe.setup.reference_id": "Step 13 setup reference id",
    "ctx.multi_timeframe.setup.reference_band_low": (
        "Step 13 setup reference band low"
    ),
    "ctx.multi_timeframe.setup.reference_band_high": (
        "Step 13 setup reference band high"
    ),
    "ctx.multi_timeframe.setup.supporting_rules[*]": (
        "Step 13 setup supporting rule id"
    ),
    "ctx.multi_timeframe.setup.opposing_rules[*]": "Step 13 setup opposing rule id",
    "ctx.multi_timeframe.setup.pending_rules[*]": "Step 13 setup pending rule id",
    "ctx.multi_timeframe.setup.invalidation": "Step 13 setup invalidation",
    "ctx.multi_timeframe.setup.next_required[*]": (
        "Step 13 setup next required item"
    ),
    "ctx.multi_timeframe.setup.candidate_count": "Step 13 setup candidate count",
    "ctx.multi_timeframe.setup.stale": "Step 13 setup staleness flag",
    "ctx.multi_timeframe.setup.reason": "Step 13 setup reason",
    "ctx.multi_timeframe.confirmation.timeframe": "Step 13 confirmation timeframe",
    "ctx.multi_timeframe.confirmation.state": "Step 13 confirmation state",
    "ctx.multi_timeframe.confirmation.reason": "Step 13 confirmation reason",
    "ctx.multi_timeframe.confirmation.window_start": (
        "Step 13 confirmation window start"
    ),
    "ctx.multi_timeframe.confirmation.window_end": (
        "Step 13 confirmation window end"
    ),
    "ctx.multi_timeframe.confirmation.candle_count": (
        "Step 13 confirmation candle count"
    ),
    "ctx.multi_timeframe.confirmation.missing_candle_count": (
        "Step 13 confirmation missing candle count"
    ),
    "ctx.multi_timeframe.confirmation.stale": "Step 13 confirmation staleness flag",
    "ctx.multi_timeframe.confirmation.evidence[*].category": (
        "Step 13 confirmation evidence category"
    ),
    "ctx.multi_timeframe.confirmation.evidence[*].status": (
        "Step 13 confirmation evidence status"
    ),
    "ctx.multi_timeframe.confirmation.evidence[*].reason": (
        "Step 13 confirmation evidence reason"
    ),
    "ctx.multi_timeframe.confirmation.evidence[*].timeframe": (
        "Step 13 confirmation evidence timeframe"
    ),
    "ctx.multi_timeframe.execution.timeframe": "Step 13 execution timeframe",
    "ctx.multi_timeframe.execution.state": "Step 13 execution state",
    "ctx.multi_timeframe.execution.reason": "Step 13 execution reason",
    "ctx.multi_timeframe.execution.window_start": (
        "Step 13 execution window start"
    ),
    "ctx.multi_timeframe.execution.window_end": "Step 13 execution window end",
    "ctx.multi_timeframe.execution.candle_count": "Step 13 execution candle count",
    "ctx.multi_timeframe.execution.missing_candle_count": (
        "Step 13 execution missing candle count"
    ),
    "ctx.multi_timeframe.execution.stale": "Step 13 execution staleness flag",
    "ctx.multi_timeframe.execution.armed_at": "Step 13 execution armed instant",
    "ctx.multi_timeframe.execution.trigger_at": "Step 13 execution trigger instant",
    "ctx.multi_timeframe.execution.entry_zone_low": "Step 13 entry zone low",
    "ctx.multi_timeframe.execution.entry_zone_high": "Step 13 entry zone high",
    "ctx.multi_timeframe.execution.latest_close": "Step 13 execution latest close",
    "ctx.multi_timeframe.execution.evidence[*].category": (
        "Step 13 execution evidence category"
    ),
    "ctx.multi_timeframe.execution.evidence[*].status": (
        "Step 13 execution evidence status"
    ),
    "ctx.multi_timeframe.execution.evidence[*].reason": (
        "Step 13 execution evidence reason"
    ),
    "ctx.multi_timeframe.execution.evidence[*].timeframe": (
        "Step 13 execution evidence timeframe"
    ),
    "ctx.multi_timeframe.boundary.boundaries[*].timeframe": (
        "Step 13 boundary timeframe"
    ),
    "ctx.multi_timeframe.boundary.boundaries[*].role": "Step 13 boundary role",
    "ctx.multi_timeframe.boundary.boundaries[*].candle_open_time": (
        "Step 13 boundary candle open instant"
    ),
    "ctx.multi_timeframe.boundary.boundaries[*].candle_close_time": (
        "Step 13 boundary candle close instant"
    ),
    "ctx.multi_timeframe.boundary.boundaries[*].candle_known": (
        "Step 13 boundary candle-known flag"
    ),
    "ctx.multi_timeframe.boundary.boundaries[*].stale": "Step 13 boundary stale flag",
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
