"""Immutable Step 9 contracts.

Two strictly separated parts:

* the deterministic explanation context plus its fact manifest — facts only,
  copied verbatim from existing Step 3-8 outputs, never re-derived; and
* the provider-independent narrative contracts — a structured request that a
  future LLM/API provider may receive, and the structured, fact-referencing
  response it must return.

Nothing here is an order, a position, a decision, a profitability claim, or an
execution statement. No field carries credentials of any kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from trading_assistant.market_structure.snapshot import to_jsonable

# ---------------------------------------------------------------------------
# Fact manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fact:
    """One atomic deterministic fact copied from Step 3-8 output.

    ``value`` is already canonical (JSON scalar: str/int/bool/None). Numeric
    values are exact decimal strings, datetimes UTC ISO-8601 strings — the same
    spellings Steps 3-8 use, so the manifest never re-spells a number.
    """

    fact_id: str
    label: str
    value: Any
    kind: Literal["text", "integer", "decimal", "boolean", "null"]


@dataclass(frozen=True, slots=True)
class FactManifest:
    """The deterministic grounding surface of one explanation context.

    Every fact ID is a stable canonical path into the context payload
    (``ctx.instrument.symbol``, ``ctx.plan.entry.value``, ...). External
    explanations may only reference these IDs; unknown IDs fail validation.
    """

    facts: tuple[Fact, ...]
    fingerprint: str
    numeric_tokens: frozenset[str] = field(repr=False)
    _by_id: dict[str, Fact] = field(repr=False)

    def get(self, fact_id: str) -> Fact | None:
        return self._by_id.get(fact_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(fact.fact_id for fact in self.facts)

    def require(self, fact_id: str) -> Fact:
        fact = self._by_id.get(fact_id)
        if fact is None:
            raise KeyError(fact_id)
        return fact

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "facts": to_jsonable(self.facts),
        }


# ---------------------------------------------------------------------------
# Explanation context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExplanationContext:
    """Immutable, canonical, fingerprinted bundle of existing deterministic facts.

    ``payload`` contains only values already established by Steps 3-8, rendered
    with the shared canonical projection (datetimes UTC ISO-8601, Decimals exact
    strings). Missing information is ``None``/absent and stays that way — the
    builder never infers through a gap. The context never holds live upstream
    objects, so rendering cannot mutate them; ``source_*`` fields record which
    upstream artifacts the facts were copied from.
    """

    schema_version: str
    payload: dict[str, Any]
    fingerprint: str
    as_of: datetime
    source_setup_id: str | None
    source_plan_id: str | None
    source_journal_id: str | None
    source_statistics_report_id: str | None
    limitations: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fingerprint": self.fingerprint,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class ExplanationRequest:
    """The only payload an external provider ever receives.

    It contains the canonical context, the manifest listing and the grounding
    contract text — nothing else. There is deliberately no credential, no
    endpoint, no order and no execution field: a provider cannot be handed
    credentials and cannot be asked to act.
    """

    schema_version: str
    context_fingerprint: str
    payload: dict[str, Any]
    facts: tuple[Fact, ...]
    grounding_rules: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "context_fingerprint": self.context_fingerprint,
            "grounding_rules": list(self.grounding_rules),
            "payload": self.payload,
            "facts": to_jsonable(self.facts),
        }


# ---------------------------------------------------------------------------
# Structured provider response contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FactualClaim:
    """One grounded claim: prose whose ``{fact_id}`` placeholders are filled by
    deterministic software with manifest values — never by the provider.

    ``fact_ids`` must all exist in the manifest; unknown IDs fail validation.
    """

    text: str
    fact_ids: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {"text": self.text, "fact_ids": list(self.fact_ids)}


@dataclass(frozen=True, slots=True)
class ExplanationProviderResponse:
    """Structured response contract for any external explanation provider.

    Prose fields are untrusted text; structured grounding happens through
    ``factual_claims`` and the referenced fact IDs. ``setup_state`` and
    ``plan_state``, when present, must equal the context's recorded states.
    """

    summary: str
    setup_state: str | None = None
    plan_state: str | None = None
    evidence_for: tuple[FactualClaim, ...] = ()
    evidence_against: tuple[FactualClaim, ...] = ()
    unknowns: tuple[FactualClaim, ...] = ()
    plan_explanation: str | None = None
    statistics_explanation: str | None = None
    risk_notes: tuple[str, ...] = ()
    factual_claims: tuple[FactualClaim, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "setup_state": self.setup_state,
            "plan_state": self.plan_state,
            "evidence_for": [claim.to_json_dict() for claim in self.evidence_for],
            "evidence_against": [
                claim.to_json_dict() for claim in self.evidence_against
            ],
            "unknowns": [claim.to_json_dict() for claim in self.unknowns],
            "plan_explanation": self.plan_explanation,
            "statistics_explanation": self.statistics_explanation,
            "risk_notes": list(self.risk_notes),
            "factual_claims": [claim.to_json_dict() for claim in self.factual_claims],
        }


# ---------------------------------------------------------------------------
# Rendered explanation and auditable result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExplanationSection:
    """One numbered section of a rendered explanation.

    ``origin`` separates deterministic engine text (``"deterministic"``) from
    externally generated provider text (``"provider"``); the two are never
    merged into one unattributed voice.
    """

    number: int
    title: str
    text: str
    origin: Literal["deterministic", "provider"]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "text": self.text,
            "origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class RenderedNarrative:
    """Renderer output: sections plus the facts actually referenced."""

    headline: str
    sections: tuple[ExplanationSection, ...]
    referenced_fact_ids: tuple[str, ...]

    @property
    def text(self) -> str:
        body = "\n\n".join(
            f"[{section.number}] {section.title}\n{section.text}"
            for section in self.sections
        )
        return f"{self.headline}\n\n{body}"

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "sections": [section.to_json_dict() for section in self.sections],
            "referenced_fact_ids": list(self.referenced_fact_ids),
        }


@dataclass(frozen=True, slots=True)
class ExplanationResult:
    """One auditable explanation.

    Identity is content-derived: the same deterministic context plus the same
    renderer/provider identity and response reproduces ``explanation_id`` and
    ``text`` exactly. ``generated_at`` is wall-clock audit metadata only and is
    deliberately excluded from the identity. Nothing here is a decision, an
    acceptance, an order, or a claim that anything was executed.
    """

    explanation_id: str
    context_fingerprint: str
    manifest_fingerprint: str
    as_of: datetime
    generated_at: datetime
    renderer_id: str
    renderer_version: str
    provenance: Literal["deterministic-local", "external-provider"]
    setup_state: str
    plan_state: str | None
    source_setup_id: str | None
    source_plan_id: str | None
    source_journal_id: str | None
    source_statistics_report_id: str | None
    referenced_fact_ids: tuple[str, ...]
    sections: tuple[ExplanationSection, ...]
    headline: str
    limitations: tuple[str, ...]
    provider_response: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        body = "\n\n".join(
            f"[{section.number}] {section.title}\n{section.text}"
            for section in self.sections
        )
        return f"{self.headline}\n\n{body}"

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "explanation_id": self.explanation_id,
            "context_fingerprint": self.context_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "as_of": to_jsonable(self.as_of),
            "generated_at": to_jsonable(self.generated_at),
            "renderer_id": self.renderer_id,
            "renderer_version": self.renderer_version,
            "provenance": self.provenance,
            "setup_state": self.setup_state,
            "plan_state": self.plan_state,
            "source_setup_id": self.source_setup_id,
            "source_plan_id": self.source_plan_id,
            "source_journal_id": self.source_journal_id,
            "source_statistics_report_id": self.source_statistics_report_id,
            "referenced_fact_ids": list(self.referenced_fact_ids),
            "sections": [section.to_json_dict() for section in self.sections],
            "headline": self.headline,
            "limitations": list(self.limitations),
            "provider_response": self.provider_response,
        }
