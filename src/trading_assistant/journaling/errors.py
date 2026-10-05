"""Errors raised by the Step 7 journaling layer.

Argument misuse and documented refusals raise ``ValueError``/``TypeError``
(consistent with Steps 5-6). Operational failures raise ``JournalError``:
a stored immutable value that disagrees with the requested one, or a missing
journal record. Refusals never mutate history; conflicts never overwrite it.
"""


class JournalError(RuntimeError):
    """Base class for operational journal failures."""


class JournalConflict(JournalError):
    """A stored immutable journal value disagrees with the requested value."""


class JournalNotFound(JournalError):
    """A requested journal record, decision, or outcome is not stored."""
