"""Errors raised by the Step 12 forward-testing ledger and runner."""


class ForwardError(RuntimeError):
    """Base class for forward-ledger and forward-runner failures."""


class ForwardConflict(ForwardError):
    """The same deterministic identity was re-recorded with different content."""


class ForwardNotFound(ForwardError):
    """A referenced forward row is not stored."""


class ForwardDataUnavailable(ForwardError):
    """Public market data could not be fetched; no conclusion may be produced."""


class ForwardNotConfigured(ForwardError):
    """The forward runner was asked to run without a usable market-data source."""


__all__ = [
    "ForwardConflict",
    "ForwardDataUnavailable",
    "ForwardError",
    "ForwardNotConfigured",
    "ForwardNotFound",
]
