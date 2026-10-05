"""Provider-independent external explanation interface and grounding checks.

A future LLM/API provider plugs in by implementing :class:`ExplanationProvider`
and returning the structured :class:`ExplanationProviderResponse`. The provider
receives *only* the deterministic explanation context and fact manifest —
never credentials, never an execution surface — and its response is validated
against the manifest before any of it is used.

Validation is structural and deterministic, not free-text parsing:

* structured states must equal the recorded states;
* every factual claim must reference manifest fact IDs that exist;
* UNKNOWN facts cannot be claimed as known and known facts cannot be
  relabelled unknown;
* plan/statistics prose is rejected when the context has no plan/statistics;
* provider prose is scanned for numeric tokens that do not appear anywhere in
  the manifest (defence in depth on top of the structured contract);
* forbidden assurance/execution language is rejected.

Deterministic software — never the provider — inserts the authoritative values
into the final rendered claims.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from trading_assistant.ai_explanation.manifest import _NUMERIC_TOKEN
from trading_assistant.ai_explanation.models import (
    ExplanationContext,
    ExplanationProviderResponse,
    ExplanationRequest,
    ExplanationSection,
    FactManifest,
    FactualClaim,
    RenderedNarrative,
)
from trading_assistant.ai_explanation.parameters import GROUNDING_RULES
from trading_assistant.ai_explanation.renderer import LocalTemplateRenderer

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Forbidden language classes. Deterministic deny-list applied to every
#: provider prose field. The local renderer can never produce these.
FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "guaranteed_profitability": re.compile(
        r"\bguarantee[ds]?\b|\bguranteed\b|\bsure\s+thing\b", re.IGNORECASE
    ),
    "will_win": re.compile(
        r"\bwill\s+(?:win|succeed|be\s+profitable)\b|\bcan(?:not|'?t)\s+lose\b"
        r"|\bnever\s+loses?\b|\balways\s+profit\w*\b|\bsure\s+win\w*\b"
        r"|\brisk[-\s]?free\b|\briskless\b",
        re.IGNORECASE,
    ),
    "advice_or_recommendation": re.compile(
        r"\brecommend\w*\b|\badvisable\b|\byou\s+should\s+(?:buy|sell|enter|take|open)\b",
        re.IGNORECASE,
    ),
    "execution_claim": re.compile(
        r"\b(?:was|were|been|is|are|got)\s+executed\b"
        r"|\bexecuted\s+(?:the|a|an|this|your)\s+(?:trade|order|plan|entry)\b"
        r"|\b(?:placed|filled)\s+(?:the|a|an|your)\s+order\b"
        r"|\border\s+was\s+(?:placed|filled|executed)\b"
        r"|\bposition\s+was\s+opened\b|\bwe\s+(?:bought|sold|entered)\b",
        re.IGNORECASE,
    ),
    "autonomous_decision": re.compile(
        r"\bauto(?:matic(?:ally)?)?[\s-]+accept\w*\b|\bauto[\s-]+trad\w*\b",
        re.IGNORECASE,
    ),
}


class ExplanationProvider(ABC):
    """Contract for any external explanation source (future LLM/API).

    Implementations must be pure functions of the request: no network access
    is required by Step 9 itself, no credential may be required, and output
    must follow the structured response contract exactly.
    """

    provider_id: str
    provider_version: str

    @abstractmethod
    def produce(self, request: ExplanationRequest) -> ExplanationProviderResponse:
        """Produce a structured, fact-referencing explanation response."""


def build_request(
    context: ExplanationContext, manifest: FactManifest
) -> ExplanationRequest:
    """The only payload an external provider receives; no credentials, no actions."""

    return ExplanationRequest(
        schema_version=context.schema_version,
        context_fingerprint=context.fingerprint,
        payload=context.payload,
        facts=manifest.facts,
        grounding_rules=GROUNDING_RULES,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_provider_response(
    response: Any,
    context: ExplanationContext,
    manifest: FactManifest,
) -> tuple[str, ...]:
    """Deterministically verify one provider response against the context.

    Returns every violation found (empty tuple means grounded). The response
    is rejected whole on any violation; nothing is repaired or reinterpreted.
    """

    if not isinstance(response, ExplanationProviderResponse):
        return (
            (
                "invalid_response_type: expected ExplanationProviderResponse, got "
                f"{type(response).__name__}"
            ),
        )

    violations: list[str] = []
    payload = context.payload

    recorded_setup_state = payload["qualification"]["state"]
    recorded_plan_state = (
        payload["plan"]["state"] if payload["plan"] is not None else None
    )

    if not isinstance(response.summary, str) or not response.summary.strip():
        violations.append("missing_summary")

    if response.setup_state is not None and (
        response.setup_state != recorded_setup_state
    ):
        violations.append(
            "setup_state_mismatch: provider said "
            f"{response.setup_state!r}, recorded state is "
            f"{recorded_setup_state!r}; states cannot be altered by prose"
        )
    if response.plan_state is not None:
        if recorded_plan_state is None:
            violations.append(
                "plan_state_invented: provider asserted plan state "
                f"{response.plan_state!r} but no Step 6 plan exists in the "
                "context"
            )
        elif response.plan_state != recorded_plan_state:
            violations.append(
                "plan_state_mismatch: provider said "
                f"{response.plan_state!r}, recorded plan state is "
                f"{recorded_plan_state!r}; plan states cannot be altered by "
                "prose"
            )

    if response.plan_explanation is not None and payload["plan"] is None:
        violations.append(
            "plan_explanation_invented: provider explained a plan that does "
            "not exist in the context"
        )
    if response.statistics_explanation is not None and payload["statistics"] is None:
        violations.append(
            "statistics_invented: provider produced statistics explanation "
            "but no Step 8 report exists in the context"
        )

    for field_name, claims in (
        ("evidence_for", response.evidence_for),
        ("evidence_against", response.evidence_against),
        ("unknowns", response.unknowns),
        ("factual_claims", response.factual_claims),
    ):
        for index, claim in enumerate(claims):
            violations.extend(
                _validate_claim(
                    claim,
                    manifest,
                    where=f"{field_name}[{index}]",
                    nulls_policy=(
                        "forbid-null"
                        if field_name in ("evidence_for", "evidence_against")
                        else "require-null"
                        if field_name == "unknowns"
                        else "neutral"
                    ),
                )
            )

    prose_fields: list[tuple[str, str]] = []
    if isinstance(response.summary, str):
        prose_fields.append(("summary", response.summary))
    for name in ("plan_explanation", "statistics_explanation"):
        value = getattr(response, name)
        if isinstance(value, str):
            prose_fields.append((name, value))
    for index, note in enumerate(response.risk_notes):
        if isinstance(note, str):
            prose_fields.append((f"risk_notes[{index}]", note))
    for field_name, claims in (
        ("evidence_for", response.evidence_for),
        ("evidence_against", response.evidence_against),
        ("unknowns", response.unknowns),
        ("factual_claims", response.factual_claims),
    ):
        for index, claim in enumerate(claims):
            if isinstance(claim, FactualClaim):
                prose_fields.append((f"{field_name}[{index}].text", claim.text))

    ungrounded: set[str] = set()
    for field_name, text in prose_fields:
        if _CONTROL_CHARS.search(text):
            violations.append(f"control_characters_in_text: {field_name}")
        stripped = _PLACEHOLDER.sub(" ", text)
        for token in _NUMERIC_TOKEN.findall(stripped):
            if token not in manifest.numeric_tokens:
                ungrounded.add(token)
                violations.append(
                    f"ungrounded_numeric_token: {token!r} in {field_name} "
                    "does not appear in the fact manifest"
                )
        for pattern_name in sorted(FORBIDDEN_PATTERNS):
            if FORBIDDEN_PATTERNS[pattern_name].search(text):
                violations.append(f"forbidden_language: {pattern_name} in {field_name}")

    return tuple(violations)


def _validate_claim(
    claim: Any,
    manifest: FactManifest,
    *,
    where: str,
    nulls_policy: str,
) -> list[str]:
    violations: list[str] = []
    if not isinstance(claim, FactualClaim):
        return [f"invalid_claim_type: {where} must be a FactualClaim"]
    if not isinstance(claim.text, str) or not claim.text.strip():
        violations.append(f"empty_claim_text: {where}")
    if not claim.fact_ids:
        violations.append(
            f"claim_without_fact_reference: {where} must reference at least "
            "one manifest fact ID"
        )
    for fact_id in claim.fact_ids:
        fact = manifest.get(fact_id)
        if fact is None:
            violations.append(f"unknown_fact_reference: {where} -> {fact_id}")
            continue
        if nulls_policy == "forbid-null" and fact.kind == "null":
            violations.append(
                f"unknown_claimed_as_known: {where} -> {fact_id} is "
                "UNKNOWN/null in the context and cannot be cited as evidence"
            )
        if nulls_policy == "require-null" and fact.kind != "null":
            violations.append(
                f"known_claimed_as_unknown: {where} -> {fact_id} has a known "
                "value and cannot be listed as unknown"
            )
    placeholders = _PLACEHOLDER.findall(claim.text)
    claimed = set(claim.fact_ids)
    for placeholder in placeholders:
        if manifest.get(placeholder) is None:
            violations.append(
                f"unknown_placeholder: {where} references {placeholder!r}, "
                "which is not a manifest fact ID"
            )
            continue
        if placeholder not in claimed:
            violations.append(
                f"placeholder_not_declared: {where} uses {placeholder!r} but "
                "does not declare it in fact_ids"
            )
    return violations


def require_grounded(
    response: Any,
    context: ExplanationContext,
    manifest: FactManifest,
) -> None:
    """Raise :class:`GroundingViolation` unless the response is grounded."""

    from trading_assistant.ai_explanation.errors import GroundingViolation

    violations = validate_provider_response(response, context, manifest)
    if violations:
        raise GroundingViolation(violations)


# ---------------------------------------------------------------------------
# Assembly: provider prose behind deterministic authority
# ---------------------------------------------------------------------------


def _fill_placeholders(text: str, values: dict[str, str]) -> str:
    """Replace ``{fact_id}`` placeholders with deterministic manifest values.

    Fact IDs contain dots, so ``str.format`` (attribute-lookup semantics) is
    never used; substitution is exact and leaves all other text untouched.
    """

    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], text)


def _claim_values(
    claim: FactualClaim,
    manifest: FactManifest,
    referenced: list[str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    for fact_id in claim.fact_ids:
        fact = manifest.require(fact_id)
        values[fact_id] = "UNKNOWN" if fact.value is None else str(fact.value)
        referenced.append(fact_id)
    return values


def render_provider_narrative(
    context: ExplanationContext,
    manifest: FactManifest,
    response: ExplanationProviderResponse,
) -> RenderedNarrative:
    """Assemble the final narrative for a *validated* provider response.

    Sections 1-10 remain the exact deterministic local rendering — failed
    rules, vetoes and refusals therefore can never be hidden by a provider —
    and the provider's grounded output is appended with explicit provenance.
    Numeric values inside claims are inserted by this deterministic code from
    the manifest, never copied from provider text.
    """

    base = LocalTemplateRenderer().render(context, manifest)
    referenced = list(base.referenced_fact_ids)

    claim_lines: list[str] = []
    for index, claim in enumerate(response.factual_claims):
        values = _claim_values(claim, manifest, referenced)
        claim_lines.append(f"{index + 1}. " + _fill_placeholders(claim.text, values))
    claims_text = (
        "\n".join(claim_lines)
        if claim_lines
        else "The provider supplied no factual claims."
    )

    narrative_parts: list[str] = []
    if response.summary:
        narrative_parts.append(f"Provider summary: {response.summary}")
    for label, claims in (
        ("Evidence for (provider view)", response.evidence_for),
        ("Evidence against (provider view)", response.evidence_against),
        ("Unknowns (provider view)", response.unknowns),
    ):
        rendered = []
        for claim in claims:
            values = _claim_values(claim, manifest, referenced)
            rendered.append("- " + _fill_placeholders(claim.text, values))
        narrative_parts.append(
            f"{label}:\n" + ("\n".join(rendered) if rendered else "- none stated")
        )
    if response.plan_explanation is not None:
        narrative_parts.append(
            f"Plan explanation (provider view): {response.plan_explanation}"
        )
    if response.statistics_explanation is not None:
        narrative_parts.append(
            f"Statistics explanation (provider view): {response.statistics_explanation}"
        )
    if response.risk_notes:
        narrative_parts.append(
            "Risk notes (provider view):\n"
            + "\n".join(f"- {note}" for note in response.risk_notes)
        )
    narrative_parts.append(
        "Provider text is untrusted output shown for review; the numbered "
        "deterministic sections above remain authoritative."
    )

    sections = tuple(base.sections) + (
        ExplanationSection(
            number=11,
            title="GROUNDED PROVIDER CLAIMS (values inserted by deterministic software)",
            text=claims_text,
            origin="provider",
        ),
        ExplanationSection(
            number=12,
            title="PROVIDER NARRATIVE (untrusted, grounded to the manifest)",
            text="\n\n".join(narrative_parts),
            origin="provider",
        ),
    )
    return RenderedNarrative(
        headline=base.headline,
        sections=sections,
        referenced_fact_ids=tuple(dict.fromkeys(referenced)),
    )
