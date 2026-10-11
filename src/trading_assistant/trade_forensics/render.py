"""Markdown rendering for :class:`TradeReport`. Pure string formatting, no I/O."""

from __future__ import annotations

from trading_assistant.trade_forensics.models import Fact, Section, Table, TradeReport

_STATUS_LEGEND = (
    "Status tags: **[RECORDED]** stored ledger or Step 5/6 JSON value · "
    "**[STORED]** stored OHLCV candle at an exact open time · "
    "**[COMPUTED]** arithmetic or comparison of the values shown · "
    "**UNAVAILABLE** not in the database; nothing was estimated or filled."
)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _table(table: Table) -> list[str]:
    if not table.headers:
        return []
    lines = [
        "| " + " | ".join(_cell(h) for h in table.headers) + " |",
        "|" + "|".join("---" for _ in table.headers) + "|",
    ]
    for row in table.rows:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return lines + [""]


def _fact(fact: Fact) -> str:
    text = f"- **{fact.label}:** {fact.display()}"
    if fact.status != "UNAVAILABLE" and fact.note:
        text += f" — {fact.note}"
    if fact.source and fact.status != "UNAVAILABLE":
        text += f" _(source: {fact.source})_"
    return text


def _section(section: Section) -> list[str]:
    title = f"## {section.title}"
    if section.post_decision:
        title += " — POST-DECISION"
    lines = [title, ""]
    for fact in section.facts:
        lines.append(_fact(fact))
    if section.facts:
        lines.append("")
    for table in section.tables:
        lines.extend(_table(table))
    for note in section.notes:
        lines.extend([note, ""])
    return lines


def render_markdown(report: TradeReport) -> str:
    lines: list[str] = [
        "# BRAIN first paper trade: read-only forensic report",
        "",
        (
            "This report explains records BRAIN already stored. It reads the database through a "
            "read-only snapshot and changes nothing. It is not a trading recommendation, a fill, "
            "or a P&L statement."
        ),
        "",
        _STATUS_LEGEND,
        "",
        "## Run and source identity",
        "",
    ]
    for fact in report.header:
        lines.append(_fact(fact))
    lines.append("")
    lines.extend(["## Missing or unavailable", ""])
    if report.missing:
        lines.extend(f"- {item}" for item in report.missing)
    else:
        lines.append("- Nothing requested is missing.")
    lines.append("")
    for section in report.sections:
        lines.extend(_section(section))
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_markdown"]
