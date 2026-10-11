"""Command line: ``python -m trading_assistant.trade_forensics``.

Read-only. Prints a Markdown report to stdout. Errors go to stderr.

Exit codes
    0  report produced (missing data is reported inside it, not treated as failure)
    2  invalid arguments, no matching paper plan, or an ambiguous match
    3  database missing, not SQLite, or without the forward-ledger tables
    4  the database or its WAL changed while it was being read; no report is printed
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.config import get_settings
from trading_assistant.trade_forensics.analysis import (
    LedgerNotMigrated,
    PaperPlanAmbiguous,
    PaperPlanNotFound,
    PlanQuery,
    build_report,
    schema_revision,
)
from trading_assistant.trade_forensics.models import Fact
from trading_assistant.trade_forensics.render import render_markdown
from trading_assistant.trade_forensics.snapshot import (
    DatabaseUnavailable,
    FileFingerprint,
    ReadOnlySnapshot,
)

EXIT_OK = 0
EXIT_BAD_REQUEST = 2
EXIT_DATABASE = 3
EXIT_MOVED = 4

#: The recorded first BTC/USDT paper trade (ENTRY_QUALITY_AUDIT.md §6). These are
#: defaults for matching only; the report reads every value from the database.
RECORDED_SIGNAL = "2026-10-11T17:00:00Z"
RECORDED_ENTRY = "83916.01"
RECORDED_STOP = "83606.01"
RECORDED_TARGET = "84379.31"


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m trading_assistant.trade_forensics",
        description=(
            "Read-only forensic report for one recorded BRAIN paper trade. "
            "Opens the SQLite database without write access and never modifies it."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="SQLite URL of the BRAIN database (default: settings database_url)",
    )
    parser.add_argument(
        "--exchange", default="binance", help="exchange (default: binance)"
    )
    parser.add_argument(
        "--symbol", default=settings.symbol, help="symbol (default: BTC/USDT)"
    )
    parser.add_argument(
        "--timeframe",
        default="1h",
        help="base timeframe of the paper plan (default: 1h)",
    )
    parser.add_argument(
        "--signal",
        default=RECORDED_SIGNAL,
        help="planning instant, UTC ISO-8601 (default: 2026-10-11T17:00:00Z)",
    )
    parser.add_argument(
        "--entry",
        default=RECORDED_ENTRY,
        help="recorded entry price (default: 83916.01)",
    )
    parser.add_argument(
        "--stop", default=RECORDED_STOP, help="recorded stop price (default: 83606.01)"
    )
    parser.add_argument(
        "--target",
        default=RECORDED_TARGET,
        help="recorded target price (default: 84379.31)",
    )
    parser.add_argument(
        "--paper-plan-id", default=None, help="choose one paper plan when several match"
    )
    parser.add_argument(
        "--expected-outcome",
        default="STOPPED",
        help="outcome the report checks the latest version against; empty string to skip (default: STOPPED)",
    )
    return parser


def _sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if (
        url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
    ):
        raise ValueError("this read-only tool reads a SQLite database file only")
    return Path(url.database).expanduser()


def _parse_signal(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"--signal is not an ISO-8601 instant: {text!r}") from exc
    if value.tzinfo is None:
        raise ValueError("--signal must include a UTC offset or Z")
    return value.astimezone(UTC)


def _parse_price(name: str, text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{name} is not a decimal price: {text!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a positive finite price: {text!r}")
    return value


def _fingerprint_facts(
    prefix: str, fingerprints: Sequence[FileFingerprint]
) -> list[Fact]:
    facts: list[Fact] = []
    for fingerprint in fingerprints:
        name = Path(fingerprint.path).name
        if not fingerprint.exists:
            value = "absent"
        else:
            value = f"{fingerprint.size} bytes, sha256 {fingerprint.sha256}"
        facts.append(Fact.computed(f"{prefix}: {name}", value, "file bytes", note=""))
    return facts


def run(argv: Sequence[str] | None, stdout: TextIO, stderr: TextIO) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        signal = _parse_signal(args.signal)
        entry = _parse_price("--entry", args.entry)
        stop = _parse_price("--stop", args.stop)
        target = _parse_price("--target", args.target)
        database_path = _sqlite_path(args.database_url)
    except ValueError as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_BAD_REQUEST

    query = PlanQuery(
        exchange=args.exchange,
        symbol=args.symbol,
        timeframe=args.timeframe,
        signal_time=signal,
        entry=entry,
        stop=stop,
        target=target,
        paper_plan_id=args.paper_plan_id,
        expected_outcome=args.expected_outcome or None,
    )

    try:
        with ReadOnlySnapshot(database_path) as snapshot:
            assert snapshot.engine is not None
            revision = schema_revision(snapshot.engine)
            report_header_seed = [
                Fact.computed(
                    "Database file (path)",
                    str(snapshot.database_path),
                    "--database-url",
                ),
                Fact.recorded("Alembic schema revision", revision, "alembic_version"),
                Fact.computed(
                    "Generated at (UTC)",
                    datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "wall clock",
                ),
                Fact.computed(
                    "Access mode",
                    "read-only snapshot: source opened mode=ro with a write-denying authorizer, copied once into memory",
                    "trade_forensics.snapshot",
                ),
            ]
            report = build_report(
                snapshot.engine, query, header=tuple(report_header_seed)
            )
            before = snapshot.before
        unchanged, changed = snapshot.verify_source_unchanged()
    except PaperPlanNotFound as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_BAD_REQUEST
    except PaperPlanAmbiguous as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_BAD_REQUEST
    except (
        DatabaseUnavailable,
        LedgerNotMigrated,
        sqlite3.Error,
        SQLAlchemyError,
    ) as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_DATABASE

    if not unchanged:
        print(
            "error: the database or its WAL changed while it was being read "
            f"({', '.join(changed)}); no report was printed. Re-run when the runner is idle.",
            file=stderr,
        )
        return EXIT_MOVED

    header = tuple(
        list(report.header)
        + _fingerprint_facts("Source file before read", before)
        + [
            Fact.computed(
                "Source files unchanged after read",
                "yes",
                "fingerprints compared after the snapshot closed",
            )
        ]
    )
    report = replace(report, header=header)
    stdout.write(render_markdown(report))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
