"""Narrative rendering (Step 9, part B).

Two things live here:

* :class:`ExplanationRenderer` — the provider-independent rendering interface.
  Anything that turns a deterministic context plus manifest into a narrative
  implements it; a future LLM/API provider is wired in through the provider
  adapter in ``provider.py``, never by replacing this interface.
* :class:`LocalTemplateRenderer` — the deterministic local reference
  implementation. It only re-states facts already present in the context,
  works fully offline, and is the safety baseline: even when an external
  provider is used, the authoritative numeric content always comes from the
  manifest, never from prose.

Every number emitted by the local renderer is the exact canonical spelling of
a manifest fact; nothing is computed, rounded, inferred or invented here.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from trading_assistant.ai_explanation.models import (
    ExplanationContext,
    ExplanationSection,
    FactManifest,
    RenderedNarrative,
)
from trading_assistant.ai_explanation.parameters import (
    LOCAL_RENDERER_ID,
    LOCAL_RENDERER_VERSION,
)

#: The fixed ten-section explanation skeleton (order and titles are stable).
SECTION_TITLES: tuple[str, ...] = (
    "WHAT THE ENGINE SEES",
    "WHY IT MATTERS",
    "EVIDENCE FOR",
    "EVIDENCE AGAINST",
    "UNKNOWN / MISSING INFORMATION",
    "CURRENT SETUP STATE",
    "PROPOSED PLAN",
    "HISTORICAL EVIDENCE",
    "INVALIDATION / WHAT WOULD CHANGE THE VIEW",
    "LIMITATIONS",
)

#: Static limitation statements; they are renderer identity, never context.
STATIC_LIMITATIONS: tuple[str, ...] = (
    (
        "This explanation describes recorded deterministic evidence only; it "
        "creates no market facts, prices, indicators, statistics, setups or "
        "trade ideas."
    ),
    (
        "QUALIFIED means qualification rules were satisfied; it is not "
        "profitability, advice or a recommendation. PLANNABLE means a "
        "deterministic proposal exists; it is neither guaranteed nor "
        "recommended."
    ),
    (
        "Nothing here is an order, fill, position or execution; producing this "
        "explanation executed nothing and accepted nothing."
    ),
    (
        "Historical statistics are observational/hypothetical records of "
        "proposed levels; they are never evidence of future performance or a "
        "profitability guarantee."
    ),
    (
        "The decision belongs to Bailey; this explanation presents information "
        "only and decides nothing."
    ),
    (
        "Missing information remains UNKNOWN; nothing was inferred, clamped or "
        "invented through a gap."
    ),
    (
        "Journal notes and externally generated text are untrusted data; they "
        "are never instructions to this engine."
    ),
)

_PATH_STEP = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


class ExplanationRenderer(ABC):
    """Provider-independent rendering interface.

    Implementations receive the deterministic context and its fact manifest
    and return a narrative. They must never receive credentials, and their
    output is audited through :class:`trading_assistant.ai_explanation.models.
    ExplanationResult`.
    """

    renderer_id: str
    renderer_version: str

    @abstractmethod
    def render(
        self, context: ExplanationContext, manifest: FactManifest
    ) -> RenderedNarrative:
        """Render one explanation narrative from grounded facts only."""


class _FactAccess:
    """Payload access that records every manifest fact the renderer cites."""

    def __init__(self, context: ExplanationContext, manifest: FactManifest) -> None:
        self.payload = context.payload
        self.manifest = manifest
        self.referenced: list[str] = []

    def node(self, fact_id: str) -> Any:
        """Resolve a (possibly structured) payload node without recording it."""

        node: Any = self.payload
        for step in _PATH_STEP.findall(fact_id):
            key, index = step
            node = node[int(index)] if index else node[key]
        return node

    def value(self, fact_id: str) -> Any:
        """Resolve one manifest leaf fact and record the reference."""

        if self.manifest.get(fact_id) is None:
            raise KeyError(fact_id)
        node = self.node(fact_id)
        self.referenced.append(fact_id)
        return node

    def text(self, fact_id: str, *, unknown: str = "UNKNOWN") -> str:
        value = self.value(fact_id)
        return unknown if value is None else str(value)


class LocalTemplateRenderer(ExplanationRenderer):
    """Deterministic offline template renderer; the reference implementation."""

    renderer_id = LOCAL_RENDERER_ID
    renderer_version = LOCAL_RENDERER_VERSION

    def render(
        self, context: ExplanationContext, manifest: FactManifest
    ) -> RenderedNarrative:
        facts = _FactAccess(context, manifest)
        payload = context.payload
        focus_ids = self._focused_indexes(payload)
        plan_present = payload["plan"] is not None
        stats_present = payload["statistics"] is not None

        sections = (
            ExplanationSection(
                number=1,
                title=SECTION_TITLES[0],
                text=self._what_engine_sees(facts, payload),
                origin="deterministic",
            ),
            ExplanationSection(
                number=2,
                title=SECTION_TITLES[1],
                text=self._why_it_matters(facts, payload, focus_ids),
                origin="deterministic",
            ),
            ExplanationSection(
                number=3,
                title=SECTION_TITLES[2],
                text=self._evidence_for(facts, payload, focus_ids),
                origin="deterministic",
            ),
            ExplanationSection(
                number=4,
                title=SECTION_TITLES[3],
                text=self._evidence_against(facts, payload, focus_ids),
                origin="deterministic",
            ),
            ExplanationSection(
                number=5,
                title=SECTION_TITLES[4],
                text=self._unknowns(
                    facts, payload, focus_ids, plan_present, stats_present
                ),
                origin="deterministic",
            ),
            ExplanationSection(
                number=6,
                title=SECTION_TITLES[5],
                text=self._setup_state(facts, payload, focus_ids),
                origin="deterministic",
            ),
            ExplanationSection(
                number=7,
                title=SECTION_TITLES[6],
                text=self._proposed_plan(facts, payload),
                origin="deterministic",
            ),
            ExplanationSection(
                number=8,
                title=SECTION_TITLES[7],
                text=self._historical_evidence(facts, payload),
                origin="deterministic",
            ),
            ExplanationSection(
                number=9,
                title=SECTION_TITLES[8],
                text=self._invalidation(facts, payload, focus_ids),
                origin="deterministic",
            ),
            ExplanationSection(
                number=10,
                title=SECTION_TITLES[9],
                text=self._limitations(facts, payload),
                origin="deterministic",
            ),
        )
        headline = self._headline(facts, payload, plan_present)
        referenced = tuple(dict.fromkeys(facts.referenced))
        return RenderedNarrative(
            headline=headline, sections=sections, referenced_fact_ids=referenced
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _focused_indexes(payload: dict[str, Any]) -> tuple[int, ...]:
        setups = payload["qualification"]["setups"]
        focus = payload["setup_focus"]
        if focus is not None:
            return tuple(i for i, setup in enumerate(setups) if setup["id"] == focus)
        return tuple(range(len(setups)))

    def _headline(
        self, facts: _FactAccess, payload: dict[str, Any], plan_present: bool
    ) -> str:
        symbol = facts.text("ctx.instrument.symbol")
        timeframe = facts.text("ctx.instrument.timeframe")
        as_of = facts.text("ctx.as_of")
        state = facts.text("ctx.qualification.state")
        headline = (
            f"Step 9 grounded explanation — {symbol} {timeframe} as of {as_of} — "
            f"setup state: {state}"
        )
        if plan_present:
            plan_state = facts.text("ctx.plan.state")
            headline += f" — plan state: {plan_state}"
        if payload.get("multi_timeframe") is not None:
            decision = facts.text("ctx.multi_timeframe.decision")
            headline += f" — hierarchy: {decision}"
        return headline

    def _what_engine_sees(self, facts: _FactAccess, payload: dict[str, Any]) -> str:
        exchange = facts.text("ctx.instrument.exchange")
        symbol = facts.text("ctx.instrument.symbol")
        timeframe = facts.text("ctx.instrument.timeframe")
        as_of = facts.text("ctx.as_of")
        state = facts.text("ctx.qualification.state")
        status = facts.text("ctx.qualification.status")
        lines = [
            (
                f"As of {as_of}, the deterministic engine recorded for "
                f"{exchange} {symbol} ({timeframe}): aggregate setup state "
                f"{state} (snapshot status: {status})."
            )
        ]
        reasons = payload["qualification"]["reasons"]
        for index in range(len(reasons)):
            reason = facts.text(f"ctx.qualification.reasons[{index}]")
            lines.append(f"Aggregate engine reason: {reason}.")
        structure = payload["market_structure"]
        if structure is None:
            lines.append(
                "Market structure and pattern evidence: unavailable (no "
                "same-as-of Step 3/4 frame was supplied)."
            )
        else:
            direction = facts.text("ctx.market_structure.trend.direction")
            reason = facts.text("ctx.market_structure.trend.reason")
            swings = facts.text("ctx.market_structure.trend.confirmed_swing_count")
            lines.append(
                f"Structural trend: {direction} ({reason}) with {swings} "
                "confirmed swings."
            )
            active_range = structure["active_range"]
            if active_range is None:
                lines.append("Active range: none recorded.")
            else:
                high = facts.text("ctx.market_structure.active_range.range_high")
                low = facts.text("ctx.market_structure.active_range.range_low")
                lines.append(f"Active range recorded: {low} to {high}.")
            zones = structure["zones"]
            if not zones:
                lines.append("Support/resistance zones: none recorded.")
            for index, _zone in enumerate(zones):
                role = facts.text(f"ctx.market_structure.zones[{index}].role")
                low = facts.text(f"ctx.market_structure.zones[{index}].band_low")
                high = facts.text(f"ctx.market_structure.zones[{index}].band_high")
                touches = facts.text(f"ctx.market_structure.zones[{index}].touch_count")
                lines.append(
                    f"Zone ({role}): band {low} to {high}, touch count {touches}."
                )
            vol_available = facts.text("ctx.market_structure.volatility.available")
            if vol_available == "True":
                atr_pct = facts.text(
                    "ctx.market_structure.volatility.atr_percent_of_price",
                    unknown="UNKNOWN",
                )
                lines.append(f"Volatility: ATR available, {atr_pct}% of price.")
            else:
                vol_reason = facts.text("ctx.market_structure.volatility.reason")
                lines.append(f"Volatility: unavailable ({vol_reason}).")
            vol_sufficient = facts.text("ctx.market_structure.volume.sufficient")
            if vol_sufficient == "True":
                relative = facts.text(
                    "ctx.market_structure.volume.relative_volume", unknown="UNKNOWN"
                )
                lines.append(f"Volume: sufficient, relative volume {relative}.")
            else:
                lines.append("Volume: insufficient data recorded.")
            complete = facts.text("ctx.market_structure.completeness.complete")
            missing = facts.text(
                "ctx.market_structure.completeness.missing_candle_count"
            )
            lines.append(
                f"Candle window complete: {complete}; missing candles: {missing}."
            )
            higher = structure["higher_timeframes"]
            for index, _higher in enumerate(higher):
                tf = facts.text(
                    f"ctx.market_structure.higher_timeframes[{index}].timeframe"
                )
                available = facts.text(
                    f"ctx.market_structure.higher_timeframes[{index}].available"
                )
                if available == "True":
                    htf_direction = facts.text(
                        f"ctx.market_structure.higher_timeframes[{index}]"
                        ".trend_direction",
                        unknown="UNKNOWN",
                    )
                    lines.append(
                        f"Higher timeframe {tf}: available, trend {htf_direction}."
                    )
                else:
                    htf_reason = facts.text(
                        f"ctx.market_structure.higher_timeframes[{index}].reason",
                        unknown="UNKNOWN",
                    )
                    lines.append(f"Higher timeframe {tf}: unavailable ({htf_reason}).")
        evidence = payload["pattern_evidence"]
        if evidence is not None:
            status = facts.text("ctx.pattern_evidence.status")
            lines.append(f"Step 4 pattern/liquidity evidence status: {status}.")
            for index, _event in enumerate(evidence["events"]):
                event_type = facts.text(
                    f"ctx.pattern_evidence.events[{index}].event_type"
                )
                event_id = facts.text(f"ctx.pattern_evidence.events[{index}].id")
                known_at = facts.text(f"ctx.pattern_evidence.events[{index}].known_at")
                event_direction = evidence["events"][index].get("direction")
                if event_direction is None:
                    lines.append(
                        f"Recorded event: {event_type} {event_id}, known at {known_at}."
                    )
                else:
                    direction_text = facts.text(
                        f"ctx.pattern_evidence.events[{index}].direction"
                    )
                    lines.append(
                        f"Recorded event: {event_type} {event_id} "
                        f"({direction_text}), known at {known_at}."
                    )
            if not evidence["events"]:
                lines.append("Recorded events: none.")
        lines.extend(self._hierarchy_lines(facts, payload))
        return "\n".join(lines)

    def _hierarchy_lines(
        self, facts: _FactAccess, payload: dict[str, Any]
    ) -> list[str]:
        """Step 13 multi-timeframe ladder lines, only when the snapshot exists.

        Every value is read from the recorded hierarchy payload through the
        fact manifest, exactly like every other section: the renderer never
        computes a market fact, and when no hierarchy snapshot was supplied
        the section stays silent rather than inventing one.
        """

        hierarchy = payload.get("multi_timeframe")
        if hierarchy is None:
            return []
        lines: list[str] = []
        decision = facts.text("ctx.multi_timeframe.decision")
        alignment = facts.text("ctx.multi_timeframe.alignment")
        counter_trend = facts.text("ctx.multi_timeframe.counter_trend")
        decision_time = facts.text("ctx.multi_timeframe.decision_time")
        lines.append(
            f"Multi-timeframe hierarchy at {decision_time}: overall decision "
            f"{decision}, alignment {alignment}"
            + (
                " (counter-trend setup, explicitly flagged; an ordinary "
                "counter-trend setup stays below PLANNABLE)."
                if counter_trend == "True"
                else "."
            )
        )
        status = facts.text("ctx.multi_timeframe.status", unknown="evaluated")
        if status == "incomplete" and decision != "no_setup":
            lines.append(
                "The recorded hierarchy evaluation is incomplete: required "
                "market data is missing or stale, so the hierarchy is waiting "
                "for complete/current data rather than presenting a "
                "trade-ready conclusion."
            )
        context = hierarchy["context"]
        context_tf = facts.text("ctx.multi_timeframe.context.timeframe")
        if context["available"] is False:
            lines.append(
                f"{context_tf} context: unavailable at the decision time."
            )
        else:
            regime = facts.text("ctx.multi_timeframe.context.regime")
            trend = facts.text(
                "ctx.multi_timeframe.context.trend_direction", unknown="UNKNOWN"
            )
            swings = facts.text(
                "ctx.multi_timeframe.context.confirmed_swing_count"
            )
            lines.append(
                f"{context_tf} context: {regime} (trend {trend}, {swings} "
                "confirmed swings)."
            )
        setup = hierarchy["setup"]
        setup_tf = facts.text("ctx.multi_timeframe.setup.timeframe")
        if setup["setup_id"] is None:
            if setup["terminal_reason"] is not None:
                terminal = facts.text("ctx.multi_timeframe.setup.terminal_reason")
                lines.append(f"{setup_tf} setup: none active (a setup ended: {terminal}).")
            else:
                lines.append(f"{setup_tf} setup: none active.")
        else:
            family = facts.text("ctx.multi_timeframe.setup.family")
            direction = facts.text("ctx.multi_timeframe.setup.direction")
            setup_state = facts.text("ctx.multi_timeframe.setup.setup_state")
            band_low = facts.text(
                "ctx.multi_timeframe.setup.reference_band_low", unknown="UNKNOWN"
            )
            band_high = facts.text(
                "ctx.multi_timeframe.setup.reference_band_high", unknown="UNKNOWN"
            )
            lines.append(
                f"{setup_tf} setup: {family} ({direction}), state {setup_state}, "
                f"reference band {band_low} to {band_high}."
            )
        confirmation = hierarchy["confirmation"]
        confirmation_tf = facts.text("ctx.multi_timeframe.confirmation.timeframe")
        confirmation_state = facts.text("ctx.multi_timeframe.confirmation.state")
        confirmation_reason = facts.text(
            "ctx.multi_timeframe.confirmation.reason", unknown="UNKNOWN"
        )
        lines.append(
            f"{confirmation_tf} confirmation: {confirmation_state} "
            f"({confirmation_reason})."
        )
        execution = hierarchy["execution"]
        execution_tf = facts.text("ctx.multi_timeframe.execution.timeframe")
        execution_state = facts.text("ctx.multi_timeframe.execution.state")
        execution_reason = facts.text(
            "ctx.multi_timeframe.execution.reason", unknown="UNKNOWN"
        )
        lines.append(
            f"{execution_tf} execution: {execution_state} ({execution_reason})."
        )
        for index, _reason in enumerate(hierarchy["waiting_for"]):
            waiting = facts.text(f"ctx.multi_timeframe.waiting_for[{index}]")
            lines.append(f"Waiting for: {waiting}.")
        for index, _reason in enumerate(hierarchy["invalidated_if"]):
            invalidated = facts.text(f"ctx.multi_timeframe.invalidated_if[{index}]")
            lines.append(f"Invalidated if: {invalidated}.")
        return lines

    def _why_it_matters(
        self, facts: _FactAccess, payload: dict[str, Any], focus_ids: tuple[int, ...]
    ) -> str:
        state = facts.text("ctx.qualification.state")
        if state == "NO_SETUP":
            lines = [
                (
                    "The qualification engine currently records NO_SETUP for this "
                    "instrument at this instant. There is no setup to plan, and "
                    "this explanation does not manufacture one: only the recorded "
                    "structure and evidence above are described."
                )
            ]
        elif state == "WATCH":
            lines = [
                (
                    "A seed event is being tracked, but required confirmation is "
                    "not complete. WATCH states exactly what is present and what "
                    "must still happen; nothing below upgrades it."
                )
            ]
        elif state == "QUALIFIED":
            lines = [
                (
                    "All required qualification rules are satisfied at this "
                    "instant. QUALIFIED is a statement about recorded rules, not "
                    "about profitability; whether a deterministic plan exists is "
                    "stated separately in section 7."
                )
            ]
        else:  # pragma: no cover - state vocabulary is fixed upstream
            lines = [f"Recorded aggregate state: {state}."]
        for index in focus_ids:
            family = facts.text(f"ctx.qualification.setups[{index}].family")
            direction = facts.text(f"ctx.qualification.setups[{index}].direction")
            setup_state = facts.text(f"ctx.qualification.setups[{index}].state")
            lines.append(
                f"Tracked setup {index + 1} of {len(payload['qualification']['setups'])}: "
                f"family {family}, direction {direction}, recorded state "
                f"{setup_state}."
            )
        return "\n".join(lines)

    def _evidence_for(
        self, facts: _FactAccess, payload: dict[str, Any], focus_ids: tuple[int, ...]
    ) -> str:
        if not payload["qualification"]["setups"]:
            return (
                "None recorded. With no setup in the snapshot there is no "
                "supporting evidence to present, and none is invented."
            )
        lines: list[str] = []
        for index in focus_ids:
            setup = payload["qualification"]["setups"][index]
            passed_any = False
            for rule_index, _rule in enumerate(setup["rules"]):
                outcome = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].outcome"
                )
                if outcome != "passed":
                    continue
                passed_any = True
                rule_id = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].rule_id"
                )
                reason = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].reason"
                )
                required = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].required"
                )
                kind = "required" if required == "True" else "optional"
                lines.append(f"Rule {rule_id} ({kind}) passed: {reason}")
            if not passed_any:
                setup_id = facts.text(f"ctx.qualification.setups[{index}].id")
                lines.append(f"Setup {setup_id}: no passed rules recorded.")
        return "\n".join(lines) if lines else "None recorded."

    def _evidence_against(
        self, facts: _FactAccess, payload: dict[str, Any], focus_ids: tuple[int, ...]
    ) -> str:
        if not payload["qualification"]["setups"]:
            return "None recorded."
        lines: list[str] = []
        for index in focus_ids:
            setup = payload["qualification"]["setups"][index]
            for rule_index, _rule in enumerate(setup["rules"]):
                outcome = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].outcome"
                )
                if outcome != "failed":
                    continue
                rule_id = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].rule_id"
                )
                reason = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].reason"
                )
                veto = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].veto"
                )
                veto_note = " [VETO]" if veto == "True" else ""
                lines.append(f"Rule {rule_id} failed{veto_note}: {reason}")
        if payload["plan"] is not None:
            for rule_index, _rule in enumerate(payload["plan"]["rules"]):
                outcome = facts.text(f"ctx.plan.rules[{rule_index}].outcome")
                if outcome != "failed":
                    continue
                rule_id = facts.text(f"ctx.plan.rules[{rule_index}].rule_id")
                reason = facts.text(f"ctx.plan.rules[{rule_index}].reason")
                lines.append(f"Planning rule {rule_id} failed: {reason}")
        return "\n".join(lines) if lines else "None recorded."

    def _unknowns(
        self,
        facts: _FactAccess,
        payload: dict[str, Any],
        focus_ids: tuple[int, ...],
        plan_present: bool,
        stats_present: bool,
    ) -> str:
        lines: list[str] = []
        for index in focus_ids:
            setup = payload["qualification"]["setups"][index]
            for rule_index, _rule in enumerate(setup["rules"]):
                outcome = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].outcome"
                )
                if outcome != "pending":
                    continue
                rule_id = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].rule_id"
                )
                reason = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].reason"
                )
                lines.append(
                    f"Qualification rule {rule_id} is pending/UNKNOWN: {reason}"
                )
        if plan_present:
            plan = payload["plan"]
            for index, _missing in enumerate(plan["missing_inputs"]):
                missing = facts.text(f"ctx.plan.missing_inputs[{index}]")
                lines.append(f"Planning input missing/UNKNOWN: {missing}")
        for index, _limitation in enumerate(payload["limitations"]):
            limitation = facts.text(f"ctx.limitations[{index}]")
            lines.append(limitation)
        if not lines:
            return (
                "No pending rules, missing inputs or absent sections were "
                "recorded beyond the limitations in section 10."
            )
        return "\n".join(lines)

    def _setup_state(
        self, facts: _FactAccess, payload: dict[str, Any], focus_ids: tuple[int, ...]
    ) -> str:
        state = facts.text("ctx.qualification.state")
        rules_version = facts.text("ctx.qualification.rules_version")
        fingerprint = facts.text("ctx.qualification.config_fingerprint")
        lines = [
            (
                f"Aggregate Step 5 state: {state} (rules {rules_version}, config "
                f"fingerprint {fingerprint})."
            )
        ]
        if not payload["qualification"]["setups"]:
            lines.append(
                "The snapshot contains no setup. NO_SETUP is the complete "
                "statement: nothing is being withheld."
            )
            return "\n".join(lines)
        for index in focus_ids:
            setup = payload["qualification"]["setups"][index]
            setup_id = facts.text(f"ctx.qualification.setups[{index}].id")
            setup_state = facts.text(f"ctx.qualification.setups[{index}].state")
            family = facts.text(f"ctx.qualification.setups[{index}].family")
            direction = facts.text(f"ctx.qualification.setups[{index}].direction")
            created_at = facts.text(f"ctx.qualification.setups[{index}].created_at")
            lines.append(
                f"Setup {setup_id}: state {setup_state}, family {family}, "
                f"direction {direction}, created at {created_at}."
            )
            if setup["terminal_reason"] is not None:
                terminal = facts.text(
                    f"ctx.qualification.setups[{index}].terminal_reason"
                )
                ended_at = facts.text(
                    f"ctx.qualification.setups[{index}].ended_at", unknown="UNKNOWN"
                )
                lines.append(
                    f"This setup is terminal: {terminal} (ended at {ended_at})."
                )
        if state == "QUALIFIED":
            lines.append(
                "QUALIFIED means the recorded rules were satisfied; it never "
                "means profitable, advisable or executable."
            )
        hierarchy = payload.get("multi_timeframe")
        if hierarchy is not None:
            decision = facts.text("ctx.multi_timeframe.decision")
            alignment = facts.text("ctx.multi_timeframe.alignment")
            counter_trend = facts.text("ctx.multi_timeframe.counter_trend")
            context_tf = facts.text("ctx.multi_timeframe.context.timeframe")
            setup_tf = facts.text("ctx.multi_timeframe.setup.timeframe")
            confirmation_tf = facts.text("ctx.multi_timeframe.confirmation.timeframe")
            execution_tf = facts.text("ctx.multi_timeframe.execution.timeframe")
            lines.append(
                f"Multi-timeframe hierarchy ({context_tf} context, {setup_tf} "
                f"setup, {confirmation_tf} confirmation, {execution_tf} "
                f"execution): overall decision {decision}, alignment {alignment}"
                + (
                    "; this is a counter-trend setup, flagged as such, and it "
                    "stays below PLANNABLE: an ordinary setup opposing the "
                    "established 4H structure cannot complete the hierarchy on "
                    "lower-timeframe signals alone."
                    if counter_trend == "True"
                    else "."
                )
            )
        journal = payload["journal"]
        if journal is not None:
            if journal["decision"] is not None:
                decision = facts.text("ctx.journal.decision.decision")
                decided_at = facts.text("ctx.journal.decision.decided_at")
                lines.append(
                    f"Recorded Bailey decision on the journaled proposal: "
                    f"{decision} at {decided_at}. A recorded decision is "
                    "append-only history; it is not an execution, an order "
                    "or advice."
                )
            else:
                lines.append(
                    "No recorded decision was supplied with this explanation; "
                    "the decision remains Bailey's and is never implied."
                )
            if journal["outcome"] is not None:
                outcome_status = facts.text("ctx.journal.outcome.status")
                lines.append(
                    "Latest recorded outcome observation of the proposed "
                    f"levels: {outcome_status}. This observes proposed levels "
                    "only; it describes no executed trade."
                )
        return "\n".join(lines)

    def _proposed_plan(self, facts: _FactAccess, payload: dict[str, Any]) -> str:
        if payload["plan"] is None:
            return (
                "No Step 6 planning result was supplied with this "
                "explanation; planning state is UNKNOWN here."
            )
        plan = payload["plan"]
        plan_state = facts.text("ctx.plan.state")
        plan_id = facts.text("ctx.plan.id")
        as_of = facts.text("ctx.plan.as_of", unknown="UNKNOWN")
        lines = [
            f"Step 6 plan {plan_id}: state {plan_state} at planning as-of {as_of}."
        ]
        if plan["state_detail"] is not None:
            detail = facts.text("ctx.plan.state_detail")
            lines.append(f"State detail: {detail}.")
        for index, _reason in enumerate(plan["reasons"]):
            reason = facts.text(f"ctx.plan.reasons[{index}]")
            lines.append(f"Planning reason code: {reason}.")
        if plan_state == "PLANNABLE":
            entry = facts.text("ctx.plan.entry.value", unknown="UNKNOWN")
            invalidation = facts.text("ctx.plan.invalidation.value", unknown="UNKNOWN")
            stop = facts.text("ctx.plan.stop.value", unknown="UNKNOWN")
            risk = facts.text("ctx.plan.risk_per_unit", unknown="UNKNOWN")
            direction = facts.text("ctx.plan.direction", unknown="UNKNOWN")
            lines.append(
                f"Proposed levels exactly as derived by Step 6 (direction "
                f"{direction}): entry {entry}; thesis invalidation "
                f"{invalidation}; protective stop {stop}; risk per unit "
                f"{risk}."
            )
            for index, _target in enumerate(plan["targets"]):
                level = facts.text(
                    f"ctx.plan.targets[{index}].level.value", unknown="UNKNOWN"
                )
                reward = facts.text(
                    f"ctx.plan.targets[{index}].reward_per_unit", unknown="UNKNOWN"
                )
                r_multiple = facts.text(
                    f"ctx.plan.targets[{index}].r_multiple", unknown="UNKNOWN"
                )
                structural = facts.text(f"ctx.plan.targets[{index}].is_structural")
                kind = "structural" if structural == "True" else "non-structural"
                lines.append(
                    f"Proposed target {index + 1}: level {level} ({kind}); "
                    f"reward per unit {reward}; R multiple {r_multiple}."
                )
            lines.append(
                "These are proposed deterministic levels only. They are not "
                "guaranteed, not recommended, and no order exists anywhere in "
                "this system."
            )
        elif plan_state == "NO_PLAN":
            lines.append(
                "Qualification does not equal an actionable plan: Step 6 "
                "refused to propose levels because a required fact was "
                "missing/UNKNOWN, the source setup was not usable, or no "
                "genuine structural target reached the mandatory "
                "reward-to-risk floor. Nothing was guessed through the gap."
            )
            for index, _missing in enumerate(plan["missing_inputs"]):
                missing = facts.text(f"ctx.plan.missing_inputs[{index}]")
                lines.append(f"Missing planning input: {missing}.")
        elif plan_state == "INVALID":
            lines.append(
                "Step 6 found the derived plan violates a hard planning "
                "invariant. The offending numbers are reported by the plan "
                "rules above; nothing was silently corrected and no proposal "
                "exists."
            )
        return "\n".join(lines)

    def _historical_evidence(self, facts: _FactAccess, payload: dict[str, Any]) -> str:
        if payload["statistics"] is None:
            return (
                "No Step 8 statistics report was supplied with this "
                "explanation; no historical statement is made."
            )
        report = payload["statistics"]["report"]
        report_id = facts.text("ctx.statistics.report.report_id")
        cutoff = facts.text("ctx.statistics.report.as_of")
        window_start = facts.text(
            "ctx.statistics.report.window_start", unknown="open-ended"
        )
        mixed = facts.text("ctx.statistics.report.mixed_versions")
        policy = facts.text("ctx.statistics.report.version_policy")
        lines = [
            (
                f"Step 8 report {report_id}: cutoff {cutoff}, window start "
                f"{window_start}, version policy {policy}, mixed versions "
                f"{mixed}."
            )
        ]
        quality_path = "ctx.statistics.report.data_quality"
        lines.extend(self._data_quality_lines(facts, quality_path))
        minimum_sample = None
        if payload["statistics"]["config"] is not None:
            minimum_sample = facts.text("ctx.statistics.config.minimum_sample_size")
        if report["overall"] is not None:
            lines.append("Overall cohort:")
            lines.extend(
                self._group_lines(
                    facts, "ctx.statistics.report.overall", minimum_sample
                )
            )
        for group_index, _group in enumerate(report["groups"]):
            key_pairs = report["groups"][group_index]["key"]
            rendered_pairs = []
            for pair_index in range(len(key_pairs)):
                base = f"ctx.statistics.report.groups[{group_index}].key[{pair_index}]"
                dim = facts.text(f"{base}[0]")
                val = facts.text(f"{base}[1]", unknown="UNKNOWN")
                rendered_pairs.append(f"{dim}={val}")
            rendered_key = ", ".join(rendered_pairs)
            lines.append(f"Group ({rendered_key}):")
            lines.extend(
                self._group_lines(
                    facts,
                    f"ctx.statistics.report.groups[{group_index}]",
                    minimum_sample,
                )
            )
        lines.append(
            "Every figure above is an observational or hypothetical record of "
            "proposed levels. It is not executed-trade performance, not "
            "evidence of future results, and never a profitability guarantee."
        )
        return "\n".join(lines)

    def _data_quality_lines(self, facts: _FactAccess, base: str) -> list[str]:
        lines: list[str] = []
        fields = (
            ("total_journal_records_considered", "journal records considered"),
            ("setup_records_eligible", "eligible setup records"),
            ("distinct_setup_ids", "distinct setup ids"),
            ("snapshot_records", "snapshot records"),
            ("plannable_plan_records", "PLANNABLE plan records"),
            (
                "non_plannable_or_missing_plan_records",
                "non-PLANNABLE or missing plan records",
            ),
            ("outcome_records_eligible", "eligible outcome records"),
            (
                "outcome_records_without_observation_by_cutoff",
                "outcome records without observation by cutoff",
            ),
            ("outcome_records_determinate", "determinate outcome records"),
            ("ambiguous_count", "AMBIGUOUS outcomes"),
            ("incomplete_unknown_count", "INCOMPLETE_DATA/UNKNOWN outcomes"),
            ("observations_with_any_gap_count", "observations with any data gap"),
            ("entry_not_reached_count", "ENTRY_NOT_REACHED outcomes"),
            (
                "excluded_from_determinate_outcome_analysis",
                "records excluded from determinate outcome analysis",
            ),
        )
        for name, label in fields:
            value = facts.text(f"{base}.{name}")
            lines.append(f"Data quality — {label}: {value}.")
        return lines

    def _group_lines(
        self, facts: _FactAccess, base: str, minimum_sample: str | None
    ) -> list[str]:
        lines: list[str] = []
        total = facts.text(f"{base}.total_journal_records_considered")
        lines.append(f"Total journal records considered: {total}.")
        for metric_path, label in (
            ("setup_qualification_rate", "setup qualification rate"),
            ("entry_reach_rate", "entry reach rate"),
            (
                "stop_touch_rate_after_ordered_entry",
                "stop touch rate after ordered entry",
            ),
        ):
            lines.extend(
                self._rate_lines(facts, f"{base}.{metric_path}", label, minimum_sample)
            )
        targets = facts.node(f"{base}.target_summaries")
        for index in range(len(targets)):
            label_value = facts.text(f"{base}.target_summaries[{index}].label")
            proposed = facts.text(
                f"{base}.target_summaries[{index}].records_with_target_proposed"
            )
            lines.append(
                f"Target {label_value}: records with target proposed {proposed}."
            )
            lines.extend(
                self._rate_lines(
                    facts,
                    f"{base}.target_summaries[{index}].reach_rate",
                    f"target {label_value} reach rate",
                    minimum_sample,
                )
            )
        lines.extend(
            self._distribution_lines(
                facts,
                f"{base}.hypothetical_proposed_plan_outcome_r",
                "hypothetical proposed-plan outcome R (observational; no "
                "execution implied)",
                minimum_sample,
            )
        )
        for metric_path, label in (
            ("observational_mfe_price_move", "observational MFE price move"),
            ("observational_mae_price_move", "observational MAE price move"),
            ("observational_mfe_r", "observational MFE in R"),
            ("observational_mae_r", "observational MAE in R"),
        ):
            lines.extend(
                self._distribution_lines(
                    facts, f"{base}.{metric_path}", label, minimum_sample
                )
            )
        return lines

    def _rate_lines(
        self,
        facts: _FactAccess,
        base: str,
        label: str,
        minimum_sample: str | None,
    ) -> list[str]:
        status = facts.text(f"{base}.status")
        sample_size = facts.text(f"{base}.sample_size")
        considered = facts.text(f"{base}.records_considered")
        excluded = facts.text(f"{base}.excluded_records")
        if status == "INSUFFICIENT_DATA":
            floor_note = (
                f" (reporting floor {minimum_sample})"
                if minimum_sample is not None
                else ""
            )
            lines = [
                (
                    f"{label}: INSUFFICIENT_DATA — evidence is insufficient: "
                    f"sample size {sample_size} is below the reporting floor"
                    f"{floor_note}; the percentage is withheld. Records "
                    f"considered {considered}; excluded {excluded}."
                )
            ]
        else:
            numerator = facts.text(f"{base}.numerator")
            denominator = facts.text(f"{base}.denominator")
            percentage = facts.text(f"{base}.percentage", unknown="withheld")
            lines = [
                (
                    f"{label}: SUFFICIENT_DATA — {numerator} of {denominator} "
                    f"({percentage}%) over sample size {sample_size}. Records "
                    f"considered {considered}; excluded {excluded}."
                )
            ]
        lines.extend(self._exclusion_lines(facts, f"{base}.exclusion_reasons"))
        return lines

    def _exclusion_lines(self, facts: _FactAccess, base: str) -> list[str]:
        reasons = facts.node(base)
        lines = []
        for index, _reason in enumerate(reasons):
            value = facts.text(f"{base}[{index}].value", unknown="UNKNOWN")
            count = facts.text(f"{base}[{index}].count")
            lines.append(f"Excluded records — reason {value}: {count}.")
        return lines

    def _distribution_lines(
        self,
        facts: _FactAccess,
        base: str,
        label: str,
        minimum_sample: str | None,
    ) -> list[str]:
        status = facts.text(f"{base}.status")
        sample_size = facts.text(f"{base}.sample_size")
        if status == "INSUFFICIENT_DATA":
            floor_note = (
                f" (reporting floor {minimum_sample})"
                if minimum_sample is not None
                else ""
            )
            lines = [
                (
                    f"{label}: INSUFFICIENT_DATA — evidence is insufficient: "
                    f"sample size {sample_size} is below the reporting floor"
                    f"{floor_note}; summary statistics are withheld."
                )
            ]
        else:
            average = facts.text(f"{base}.average", unknown="withheld")
            median = facts.text(f"{base}.median", unknown="withheld")
            minimum = facts.text(f"{base}.minimum", unknown="withheld")
            maximum = facts.text(f"{base}.maximum", unknown="withheld")
            positive = facts.text(f"{base}.positive_count")
            negative = facts.text(f"{base}.negative_count")
            zero = facts.text(f"{base}.zero_count")
            lines = [
                (
                    f"{label}: SUFFICIENT_DATA over sample size {sample_size}; "
                    f"average {average}, median {median}, minimum {minimum}, "
                    f"maximum {maximum}; positive {positive}, negative "
                    f"{negative}, zero {zero}."
                )
            ]
        lines.extend(self._exclusion_lines(facts, f"{base}.exclusion_reasons"))
        return lines

    def _invalidation(
        self, facts: _FactAccess, payload: dict[str, Any], focus_ids: tuple[int, ...]
    ) -> str:
        lines: list[str] = []
        if payload["plan"] is not None:
            plan_state = facts.text("ctx.plan.state")
            if plan_state == "PLANNABLE":
                invalidation = facts.text(
                    "ctx.plan.invalidation.value", unknown="UNKNOWN"
                )
                stop = facts.text("ctx.plan.stop.value", unknown="UNKNOWN")
                lines.append(
                    f"The recorded thesis fails if the protective stop "
                    f"{stop} is reached; the Step 6 invalidation level is "
                    f"{invalidation}. These levels are the deterministic "
                    "boundary of the proposal."
                )
            else:
                lines.append(
                    f"No proposal exists (plan state {plan_state}); the view "
                    "changes when the missing/contradictory facts recorded "
                    "in section 5 are resolved."
                )
        for index in focus_ids:
            setup = payload["qualification"]["setups"][index]
            for rule_index, _rule in enumerate(setup["rules"]):
                outcome = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].outcome"
                )
                if outcome != "pending":
                    continue
                rule_id = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].rule_id"
                )
                required = facts.text(
                    f"ctx.qualification.setups[{index}].rules[{rule_index}].required"
                )
                if required == "True":
                    lines.append(
                        f"Qualification rule {rule_id} (required) must still "
                        "resolve; until it does, the setup cannot be treated "
                        "as confirmed."
                    )
                else:
                    lines.append(
                        f"Optional qualification rule {rule_id} is still "
                        "pending; it neither blocks nor confirms the setup, "
                        "and its absence of evidence stays UNKNOWN."
                    )
            if setup["terminal_reason"] is not None:
                terminal = facts.text(
                    f"ctx.qualification.setups[{index}].terminal_reason"
                )
                lines.append(
                    f"This setup already ended ({terminal}); nothing can "
                    "re-qualify it retroactively."
                )
        hierarchy = payload.get("multi_timeframe")
        if hierarchy is not None:
            for index, _reason in enumerate(hierarchy["waiting_for"]):
                waiting = facts.text(f"ctx.multi_timeframe.waiting_for[{index}]")
                lines.append(f"The hierarchy is waiting for: {waiting}.")
            for index, _reason in enumerate(hierarchy["invalidated_if"]):
                invalidated = facts.text(
                    f"ctx.multi_timeframe.invalidated_if[{index}]"
                )
                lines.append(f"The hierarchy view is invalidated if: {invalidated}.")
        if not lines:
            lines.append(
                "No setup or plan is recorded, so there is no thesis to "
                "invalidate; the engine's view changes only when new "
                "deterministic evidence appears in later snapshots."
            )
        return "\n".join(lines)

    def _limitations(self, facts: _FactAccess, payload: dict[str, Any]) -> str:
        lines = list(STATIC_LIMITATIONS)
        for index, _limitation in enumerate(payload["limitations"]):
            lines.append(facts.text(f"ctx.limitations[{index}]"))
        if payload["journal"] is not None and payload["journal"]["untrusted_notes"]:
            lines.append(
                "Journal note(s) are quoted only as untrusted data in the "
                "journal section of the context; they did not influence any "
                "fact, state or number in this explanation."
            )
        return "\n".join(lines)
