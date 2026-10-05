"""Typed Step 9 errors.

The explanation layer fails loudly and never silently reinterprets anything:
context misuse raises :class:`ContextBuildError`, and any externally generated
explanation that does not stay grounded in the supplied deterministic context
raises :class:`GroundingViolation` listing every detected violation.
"""

from __future__ import annotations


class ExplanationError(Exception):
    """Base class for every Step 9 explanation-layer error."""


class ContextBuildError(ExplanationError, ValueError):
    """Caller supplied inconsistent or unusable Step 3-8 objects.

    This is argument/consistency misuse (mismatched instruments, future-dated
    inputs, a plan attached to the wrong setup), not a domain gap: domain gaps
    stay ``None``/UNKNOWN inside the context instead of raising.
    """


class GroundingViolation(ExplanationError):
    """An externally generated explanation violated the grounding contract.

    ``violations`` carries every deterministic violation found (never just the
    first), so one bad provider response is fully auditable. The explanation is
    rejected whole: no partial acceptance, no repair, no guess through a gap.
    """

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = tuple(violations)
        super().__init__("provider explanation rejected: " + "; ".join(self.violations))
