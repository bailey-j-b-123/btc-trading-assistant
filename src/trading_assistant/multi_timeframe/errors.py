"""Error types for the Step 13 multi-timeframe hierarchy."""

from __future__ import annotations


class MultiTimeframeError(Exception):
    """Base class for hierarchy errors."""


class HierarchyNotConfigured(MultiTimeframeError):
    """No market-data source is configured for hierarchy acquisition."""


class HierarchyDataUnavailable(MultiTimeframeError):
    """Required stored market data is missing for one hierarchy layer."""


class HierarchyConflict(MultiTimeframeError):
    """A stored hierarchy row disagrees with the deterministic re-derivation."""


__all__ = [
    "HierarchyConflict",
    "HierarchyDataUnavailable",
    "HierarchyNotConfigured",
    "MultiTimeframeError",
]
