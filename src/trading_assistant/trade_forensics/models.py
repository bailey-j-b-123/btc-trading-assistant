"""Immutable report contracts for the read-only trade forensic report.

Every value in the report is a :class:`Fact` with exactly one status:

``RECORDED``
    Read from a stored ledger row or a stored JSON field (Steps 5–12).
``STORED``
    Read from a stored OHLCV candle at an exact open time.
``COMPUTED``
    Arithmetic or a comparison on RECORDED/STORED values, shown with its inputs.
``UNAVAILABLE``
    Not present in the database. The report says what is missing and why; the
    value is never estimated, filled from a neighbouring candle, or inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FactStatus = Literal["RECORDED", "STORED", "COMPUTED", "UNAVAILABLE"]


@dataclass(frozen=True, slots=True)
class Fact:
    label: str
    value: str | None
    status: FactStatus
    source: str = ""
    note: str = ""

    @classmethod
    def recorded(
        cls, label: str, value: str | None, source: str = "", note: str = ""
    ) -> Fact:
        if value is None:
            return cls.unavailable(
                label, source, note or "not recorded in the database"
            )
        return cls(
            label=label, value=value, status="RECORDED", source=source, note=note
        )

    @classmethod
    def stored(cls, label: str, value: str, source: str = "", note: str = "") -> Fact:
        return cls(label=label, value=value, status="STORED", source=source, note=note)

    @classmethod
    def computed(cls, label: str, value: str, source: str = "", note: str = "") -> Fact:
        return cls(
            label=label, value=value, status="COMPUTED", source=source, note=note
        )

    @classmethod
    def unavailable(cls, label: str, source: str = "", note: str = "") -> Fact:
        return cls(
            label=label,
            value=None,
            status="UNAVAILABLE",
            source=source,
            note=note or "not available in the database",
        )

    def display(self) -> str:
        if self.status == "UNAVAILABLE":
            return f"UNAVAILABLE — {self.note}"
        return f"{self.value} [{self.status}]"


@dataclass(frozen=True, slots=True)
class Table:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class Section:
    key: str
    title: str
    facts: tuple[Fact, ...] = ()
    tables: tuple[Table, ...] = ()
    notes: tuple[str, ...] = ()
    #: True for sections that use candles or rows after the signal instant.
    #: Decision sections (everything else) must not depend on those.
    post_decision: bool = False


@dataclass(frozen=True, slots=True)
class TradeReport:
    header: tuple[Fact, ...]
    sections: tuple[Section, ...]
    missing: tuple[str, ...] = field(default=())

    def section(self, key: str) -> Section:
        for section in self.sections:
            if section.key == key:
                return section
        raise KeyError(key)

    def decision_sections(self) -> tuple[Section, ...]:
        return tuple(s for s in self.sections if not s.post_decision)
