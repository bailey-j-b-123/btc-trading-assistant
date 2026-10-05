"""Step 9 — grounded AI explanation layer over deterministic Steps 3-8.

Software calculates -> rules qualify -> statistics validate -> AI explains ->
Bailey decides -> everything gets recorded. This package owns only "AI
explains": it converts facts already established by the deterministic engine
into clear, auditable explanations. It never becomes a source of market
truth: it calculates nothing new, invents nothing through a gap, decides
nothing, executes nothing, and its external narrative surface is grounded to
a deterministic fact manifest.

The default renderer is fully deterministic and offline; external providers
are optional, provider-independent, credential-free, and validated against
the manifest before any of their text is used.
"""

from trading_assistant.ai_explanation.context import build_explanation_context
from trading_assistant.ai_explanation.errors import (
    ContextBuildError,
    ExplanationError,
    GroundingViolation,
)
from trading_assistant.ai_explanation.manifest import build_fact_manifest
from trading_assistant.ai_explanation.models import (
    ExplanationContext,
    ExplanationProviderResponse,
    ExplanationRequest,
    ExplanationResult,
    ExplanationSection,
    Fact,
    FactManifest,
    FactualClaim,
    RenderedNarrative,
)
from trading_assistant.ai_explanation.parameters import (
    EXPLANATION_CONTEXT_SCHEMA_VERSION,
    EXPLANATION_RULES_VERSION,
    GROUNDING_RULES,
    LOCAL_RENDERER_ID,
    LOCAL_RENDERER_VERSION,
    canonical_explanation_json,
    explanation_fingerprint,
)
from trading_assistant.ai_explanation.provider import (
    FORBIDDEN_PATTERNS,
    ExplanationProvider,
    build_request,
    render_provider_narrative,
    require_grounded,
    validate_provider_response,
)
from trading_assistant.ai_explanation.renderer import (
    SECTION_TITLES,
    STATIC_LIMITATIONS,
    ExplanationRenderer,
    LocalTemplateRenderer,
)
from trading_assistant.ai_explanation.service import ExplanationService

__all__ = [
    "EXPLANATION_CONTEXT_SCHEMA_VERSION",
    "EXPLANATION_RULES_VERSION",
    "FORBIDDEN_PATTERNS",
    "GROUNDING_RULES",
    "LOCAL_RENDERER_ID",
    "LOCAL_RENDERER_VERSION",
    "SECTION_TITLES",
    "STATIC_LIMITATIONS",
    "ContextBuildError",
    "ExplanationContext",
    "ExplanationError",
    "ExplanationProvider",
    "ExplanationProviderResponse",
    "ExplanationRenderer",
    "ExplanationRequest",
    "ExplanationResult",
    "ExplanationSection",
    "ExplanationService",
    "Fact",
    "FactManifest",
    "FactualClaim",
    "GroundingViolation",
    "LocalTemplateRenderer",
    "RenderedNarrative",
    "build_explanation_context",
    "build_fact_manifest",
    "build_request",
    "canonical_explanation_json",
    "explanation_fingerprint",
    "render_provider_narrative",
    "require_grounded",
    "validate_provider_response",
]
