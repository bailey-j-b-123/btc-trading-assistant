"""Step 7 versions, canonical identity helpers, notes and outcome parameters.

Every stored journal value is built from these helpers so identities are
deterministic SHA-256 fingerprints of canonical JSON rather than random UUIDs,
matching Steps 4-6. Versions are recorded on every row: changing journal
semantics or observation rules intentionally changes identities instead of
silently reinterpreting history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from trading_assistant.market_structure.snapshot import to_jsonable

#: Version of the journal record/decision contract (identity + stored fields).
JOURNAL_RULES_VERSION = "journal-v1"

#: Version of the decision-append contract (states, correction semantics).
DECISION_RULES_VERSION = "journal-decision-v1"

#: Version of the deterministic candle-based outcome observation contract.
OUTCOME_RULES_VERSION = "journal-outcome-v1"

#: Version of the outcome contract that may use genuine, confirmed stored
#: 1-minute candles **only to order events inside an ambiguous higher-timeframe
#: candle** (Phase 3). The plan's entry, stop, targets, planning timestamp and
#: minimum-1R rules are never altered by this version: lower-timeframe candles
#: never re-plan. When 1-minute evidence is missing, incomplete, inconsistent
#: with the higher-timeframe candle, or still ambiguous at 1-minute
#: granularity, the observation stays an explicitly unscored ``AMBIGUOUS`` —
#: the favourable result is never chosen. Observations evaluated under this
#: version carry it on the row, so v1 and v2 cohorts are never merged
#: silently. Without resolution candles this version behaves exactly like v1.
OUTCOME_RESOLUTION_RULES_VERSION = "journal-outcome-v2"

#: Hard bound for a user-supplied note/reason; notes are metadata, not evidence.
MAX_NOTE_LENGTH = 2000


def fingerprint(*parts: object) -> str:
    """Canonical SHA-256 over JSON-safe journal material, version-prefixed."""

    payload = json.dumps(to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    return sha256((JOURNAL_RULES_VERSION + ":" + payload).encode()).hexdigest()


def canonical_json(value: object) -> str:
    """Exact stored rendering of any journal value (sorted keys, no whitespace)."""

    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"))


def normalize_note(note: str | None) -> str | None:
    """Validate and normalize an optional human note without altering its words.

    Notes are user-supplied metadata only. They are stripped, bounded in length,
    and rejected if they contain NUL or C0 control characters other than tab and
    newline. An empty note is stored as ``None``.
    """

    if note is None:
        return None
    if not isinstance(note, str):
        raise TypeError("note must be a string or None")
    if "\x00" in note:
        raise ValueError("note must not contain NUL characters")
    stripped = note.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_NOTE_LENGTH:
        raise ValueError(f"note must be at most {MAX_NOTE_LENGTH} characters")
    illegal = sorted(
        {char for char in stripped if ord(char) < 32 and char not in "\n\t"}
    )
    if illegal:
        raise ValueError("note must not contain control characters")
    return stripped


@dataclass(frozen=True, slots=True)
class OutcomeParameters:
    """Explicit configuration identity for one outcome observation.

    The touch, ordering, ambiguity and gap rules are code-bound semantics
    identified by ``rules_version``; there are no calibrated knobs here, so two
    runs with the same version, journal inputs, candles and cutoff must produce
    the same observation. The class exists so the version — and any future
    explicitly versioned addition — is recorded and fingerprinted on the row.
    """

    rules_version: str = OUTCOME_RULES_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.rules_version, str) or not self.rules_version.strip():
            raise ValueError("rules_version must be a non-empty string")
        object.__setattr__(self, "rules_version", self.rules_version.strip())

    def fingerprint(self) -> str:
        """Versioned identity of this exact observation configuration."""

        return fingerprint("outcome-parameters", self)
