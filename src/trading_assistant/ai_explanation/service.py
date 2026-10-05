"""Step 9 explanation service: deterministic context in, audited narrative out.

The service is stateless and database-free by construction: it receives
already-produced Step 3-8 objects, builds the immutable context and manifest,
renders through the deterministic local renderer (default) or validates and
assembles an external provider response. It never computes market facts,
never writes anywhere, never accepts a setup, and never executes anything.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trading_assistant.ai_explanation.context import build_explanation_context
from trading_assistant.ai_explanation.errors import GroundingViolation
from trading_assistant.ai_explanation.manifest import build_fact_manifest
from trading_assistant.ai_explanation.models import (
    ExplanationContext,
    ExplanationResult,
)
from trading_assistant.ai_explanation.parameters import explanation_fingerprint
from trading_assistant.ai_explanation.provider import (
    ExplanationProvider,
    build_request,
    render_provider_narrative,
    validate_provider_response,
)
from trading_assistant.ai_explanation.renderer import (
    STATIC_LIMITATIONS,
    LocalTemplateRenderer,
)
from trading_assistant.journaling.types import (
    DecisionRecord,
    JournalRecord,
    OutcomeObservation,
)
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
)
from trading_assistant.statistics.config import StatisticsConfig
from trading_assistant.statistics.models import StatisticsReport
from trading_assistant.trade_planning.models import TradePlanResult


class ExplanationService:
    """Grounded explanations over existing deterministic facts.

    No engine, no repository, no network and no credentials: constructing the
    service and calling :meth:`explain` can neither read nor modify any
    database. Inputs are the same Step 3-8 objects the rest of the system
    already produces.
    """

    def build_context(
        self,
        *,
        snapshot: QualificationSnapshot,
        setup_id: str | None = None,
        frame: QualificationFrame | None = None,
        plan: TradePlanResult | None = None,
        journal_record: JournalRecord | None = None,
        latest_decision: DecisionRecord | None = None,
        latest_outcome: OutcomeObservation | None = None,
        statistics_report: StatisticsReport | None = None,
        statistics_config: StatisticsConfig | None = None,
    ) -> ExplanationContext:
        """Build the canonical, fingerprinted fact context for one explanation."""

        return build_explanation_context(
            snapshot=snapshot,
            setup_id=setup_id,
            frame=frame,
            plan=plan,
            journal_record=journal_record,
            latest_decision=latest_decision,
            latest_outcome=latest_outcome,
            statistics_report=statistics_report,
            statistics_config=statistics_config,
        )

    def explain(
        self,
        context: ExplanationContext,
        *,
        provider: ExplanationProvider | None = None,
        generated_at: datetime | None = None,
    ) -> ExplanationResult:
        """Render one audited explanation from a context.

        Without ``provider`` the deterministic local template renderer is
        used. With a provider, the provider receives only the context request
        and its structured response must pass manifest grounding or
        :class:`GroundingViolation` is raised; grounded provider text is then
        appended *behind* the unchanged deterministic sections.
        """

        manifest = build_fact_manifest(context)
        payload = context.payload
        setup_state = payload["qualification"]["state"]
        plan_state = payload["plan"]["state"] if payload["plan"] is not None else None
        stamp = generated_at or datetime.now(UTC)

        if provider is None:
            renderer = LocalTemplateRenderer()
            narrative = renderer.render(context, manifest)
            renderer_id: str = renderer.renderer_id
            renderer_version: str = renderer.renderer_version
            provenance: str = "deterministic-local"
            provider_payload = None
            explanation_id = explanation_fingerprint(
                "explanation",
                context.fingerprint,
                renderer_id,
                renderer_version,
                narrative.text,
            )
        else:
            request = build_request(context, manifest)
            response = provider.produce(request)
            violations = validate_provider_response(response, context, manifest)
            if violations:
                raise GroundingViolation(violations)
            narrative = render_provider_narrative(context, manifest, response)
            renderer_id = provider.provider_id
            renderer_version = provider.provider_version
            provenance = "external-provider"
            provider_payload = response.to_json_dict()
            explanation_id = explanation_fingerprint(
                "explanation",
                context.fingerprint,
                renderer_id,
                renderer_version,
                narrative.text,
            )

        return ExplanationResult(
            explanation_id=explanation_id,
            context_fingerprint=context.fingerprint,
            manifest_fingerprint=manifest.fingerprint,
            as_of=context.as_of,
            generated_at=stamp,
            renderer_id=renderer_id,
            renderer_version=renderer_version,
            provenance=provenance,
            setup_state=setup_state,
            plan_state=plan_state,
            source_setup_id=context.source_setup_id,
            source_plan_id=context.source_plan_id,
            source_journal_id=context.source_journal_id,
            source_statistics_report_id=context.source_statistics_report_id,
            referenced_fact_ids=narrative.referenced_fact_ids,
            sections=narrative.sections,
            headline=narrative.headline,
            limitations=STATIC_LIMITATIONS + tuple(context.limitations),
            provider_response=provider_payload,
        )
