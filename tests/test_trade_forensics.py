"""Tests for the read-only trade forensic report.

Every test that changes data works on a copy of a synthetic database built from
the labelled forward-ledger scenario. The real BRAIN database is never needed or
read. The tests prove, in order: the source bytes and row counts do not change,
decisions use nothing after their instant, the snapshot refuses writes, no
network socket is opened, and missing data is reported rather than invented.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import socket
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from forward_fixtures import QUALIFYING_BOUNDARY, bar, labelled_series, make_harness
from sqlalchemy import text

from trading_assistant.trade_forensics.__main__ import (
    EXIT_BAD_REQUEST,
    EXIT_DATABASE,
    EXIT_MOVED,
    EXIT_OK,
    run,
)
from trading_assistant.trade_forensics.snapshot import (
    ReadOnlySnapshot,
    changed_files,
    fingerprint_database,
)

SIGNAL = "2024-01-01T21:00:00Z"
ENTRY = "124"
STOP = "117"
TARGET = "138"

LEDGER_TABLES = (
    "forward_cycles",
    "forward_observations",
    "forward_paper_plans",
    "forward_paper_outcomes",
    "forward_runner_heartbeats",
    "ohlcv_candles",
)


def _build_stop_database(directory: Path) -> Path:
    """The labelled scenario through a real stop: plan at 21:00, stop wick at 22:00."""

    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    harness.run(refresh_market_data=False)
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    harness.step((bar(22, 120, high=126, low=116),), refresh_market_data=False)
    source = Path(harness.engine.url.database)
    harness.engine.dispose()
    target = directory / "brain.sqlite3"
    shutil.copy(source, target)
    wal = source.with_name(source.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        shutil.copy(wal, target.with_name(target.name + "-wal"))
    return target


@pytest.fixture(scope="module")
def base_database(tmp_path_factory) -> Path:
    return _build_stop_database(tmp_path_factory.mktemp("forensic-base"))


@pytest.fixture
def database(base_database, tmp_path) -> Path:
    """A private copy of the synthetic stop database for one test."""

    target = tmp_path / "brain.sqlite3"
    shutil.copy(base_database, target)
    return target


def _argv(database: Path, **overrides: str) -> list[str]:
    values = {
        "--database-url": f"sqlite:///{database}",
        "--signal": SIGNAL,
        "--entry": ENTRY,
        "--stop": STOP,
        "--target": TARGET,
        "--expected-outcome": "STOPPED",
    }
    for key, value in overrides.items():
        values["--" + key.replace("_", "-")] = value
    argv: list[str] = []
    for key, value in values.items():
        argv.extend([key, value])
    return argv


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run(argv, out, err)
    return code, out.getvalue(), err.getvalue()


def _digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_state(database: Path) -> dict[str, str | None]:
    """Digests of the database and its WAL. A zero-byte WAL holds no content and is
    treated as absent: SQLite creates one when a WAL database is first opened."""

    wal = database.with_name(database.name + "-wal")
    wal_digest = _digest(wal) if wal.exists() and wal.stat().st_size else None
    return {"db": _digest(database), "wal": wal_digest}


def _row_counts(database: Path) -> dict[str, int]:
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as conn:
        return {
            table: conn.execute(f"select count(*) from {table}").fetchone()[0]
            for table in LEDGER_TABLES
        }


def _decision_sections(report: str) -> str:
    """Every section that must not depend on anything after the decision."""

    blocks: list[str] = []
    current: list[str] = []
    title = ""
    for line in report.splitlines():
        if line.startswith("## "):
            if current:
                blocks.append(title + "\n" + "\n".join(current))
            title, current = line, []
        else:
            current.append(line)
    if current:
        blocks.append(title + "\n" + "\n".join(current))
    keep = [
        block
        for block in blocks
        if not block.startswith("## Run and source identity")
        and not block.startswith("## Missing or unavailable")
        and "POST-DECISION" not in block.splitlines()[0]
    ]
    return "\n\n".join(keep)


@contextmanager
def _live_writer(database: Path):
    """A writer that keeps committed rows in the WAL, un-checkpointed, while open."""

    conn = sqlite3.connect(database, isolation_level=None)
    try:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        yield conn
    finally:
        conn.close()


def test_read_only_run_leaves_database_and_wal_bytes_and_rows_unchanged(database):
    with _live_writer(database) as writer:
        # A committed 5M candle that exists only in the WAL, before the signal.
        writer.execute(
            "insert into ohlcv_candles values "
            "('binance','BTC/USDT','5m','2024-01-01 20:55:00.000000','118','119','117','118','7')"
        )
        wal = database.with_name(database.name + "-wal")
        assert wal.exists() and wal.stat().st_size > 0, (
            "fixture must keep rows in the WAL"
        )

        before_state = _file_state(database)
        before_rows = _row_counts(database)

        code, out, err = _run(_argv(database))

        assert code == EXIT_OK, err
        # The report read the WAL-only 5M candle through the snapshot.
        assert "| 5m |" in out
        assert "| 1 of 131 |" in out
        assert _file_state(database) == before_state
        assert _row_counts(database) == before_rows


def test_appending_candles_after_the_decision_leaves_decision_sections_byte_identical(
    database,
):
    code, before, err = _run(_argv(database))
    assert code == EXIT_OK, err

    with closing(sqlite3.connect(database, isolation_level=None)) as writer:
        # Extreme, obviously wrong candles after the signal. If any decision read them,
        # the decision text below would change.
        for opened in (
            "2024-01-01 23:00:00.000000",
            "2024-01-02 00:00:00.000000",
            "2024-01-02 01:00:00.000000",
        ):
            writer.execute(
                "insert into ohlcv_candles values "
                f"('binance','BTC/USDT','1h','{opened}','1','900000','1','1','1')"
            )

    code, after, err = _run(_argv(database))
    assert code == EXIT_OK, err
    assert _decision_sections(before) == _decision_sections(after)
    assert "900000" not in _decision_sections(after)


def test_snapshot_authorizer_denies_every_write(database):
    statements = [
        "insert into forward_cycles (cycle_id) values ('x')",
        "delete from ohlcv_candles",
        "update ohlcv_candles set close='1'",
        "drop table ohlcv_candles",
        "create table forensic_probe (a integer)",
        "pragma user_version = 5",
    ]
    state = _file_state(database)
    with ReadOnlySnapshot(database) as snapshot:
        assert snapshot.engine is not None
        for statement in statements:
            with (
                pytest.raises(Exception, match="not authorized"),
                snapshot.engine.begin() as connection,
            ):
                connection.execute(text(statement))
        with snapshot.engine.connect() as connection:
            assert (
                connection.execute(text("select count(*) from ohlcv_candles")).scalar()
                > 0
            )
    assert _file_state(database) == state


def test_no_network_socket_is_used(database, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("trade_forensics must not open a network socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    code, out, err = _run(_argv(database))

    assert code == EXIT_OK, err
    assert "read-only forensic report" in out


def test_missing_candle_is_reported_unavailable_not_invented(database):
    with closing(sqlite3.connect(database, isolation_level=None)) as writer:
        writer.execute(
            "delete from ohlcv_candles where timeframe='1h' and timestamp='2024-01-01 14:00:00.000000'"
        )

    code, out, err = _run(_argv(database))

    assert code == EXIT_OK, err
    breakout_rows = [
        line for line in out.splitlines() if line.startswith("| Breakout |")
    ]
    assert len(breakout_rows) == 1
    cells = [cell.strip() for cell in breakout_rows[0].strip("|").split("|")]
    assert cells[1] == "2024-01-01T14:00:00Z"  # the expected open, not a neighbour's
    assert cells[3:8] == ["UNAVAILABLE"] * 5
    assert "900000" not in out
    assert "table row missing: Breakout" in out


def test_unknown_plan_exits_2_with_nearby_plans_and_changes_nothing(database, tmp_path):
    state = _file_state(database)
    before_files = {p.name for p in tmp_path.iterdir()}

    code, out, err = _run(_argv(database, entry="999"))

    assert code == EXIT_BAD_REQUEST
    assert out == ""
    assert "no stored paper plan matches" in err
    assert "Nearby stored plans" in err
    new_files = {p.name for p in tmp_path.iterdir()} - before_files
    # SQLite may create its own -shm index for a read-only open of a WAL file. Nothing else.
    assert new_files <= {"brain.sqlite3-shm", "brain.sqlite3-wal"}
    assert _file_state(database) == state


def test_slot_and_competing_setup_evidence_is_shown(database):
    code, out, err = _run(_argv(database))

    assert code == EXIT_OK, err
    assert "← frozen" in out
    assert "NO TRADE — BTC paper trade already active." in out
    assert (
        "**Candidates refused a paper trade by an occupied slot at this close:** 1"
        in out
    )
    assert "**Instrument slot free when this paper plan was recorded:** yes" in out


def test_hierarchy_absence_is_reported_as_unavailable(database):
    code, out, err = _run(_argv(database))

    assert code == EXIT_OK, err
    assert (
        "**4H / 15M / 5M hierarchy evaluation known at the paper plan:** UNAVAILABLE"
        in out
    )
    assert "| Decision time (historical) |" not in out


def _insert_hierarchy_row(
    database: Path, *, observation_id: str, decision_time: str, recorded_at: str
) -> None:
    """One stored hierarchy evaluation. Only the timing columns matter to the report."""

    with closing(sqlite3.connect(database, isolation_level=None)) as writer:
        writer.execute(
            """
            insert into forward_hierarchy_observations (
                observation_id, rules_version, exchange, symbol, decision_time,
                hierarchy_fingerprint, hierarchy_json, context_timeframe,
                context_boundary_open, context_boundary_close, context_json,
                setup_timeframe, setup_boundary_open, setup_boundary_close, setup_json,
                confirmation_timeframe, confirmation_boundary_open, confirmation_boundary_close,
                confirmation_json, execution_timeframe, execution_boundary_open,
                execution_boundary_close, execution_json, alignment, counter_trend,
                decision, status, reasons_json, strategy_versions_json, waiting_for_json,
                invalidated_if_json, boundary_json, snapshot_json, recorded_at
            ) values (
                ?, 'test-rules', 'binance', 'BTC/USDT', ?,
                ?, '{}', '4h',
                '2023-12-31 20:00:00.000000', '2024-01-01 00:00:00.000000', '{}',
                '1h', '2024-01-01 00:00:00.000000', '2024-01-01 01:00:00.000000', '{}',
                '15m', '2024-01-01 20:45:00.000000', '2024-01-01 21:00:00.000000', '{}',
                '5m', '2024-01-01 20:55:00.000000', '2024-01-01 21:00:00.000000', '{}',
                'aligned', 0, 'watch', 'evaluated', '[]', '[]', '[]', '[]', '{}', '{}', ?
            )
            """,
            (observation_id, decision_time, observation_id, recorded_at),
        )


def test_hierarchy_row_written_after_the_paper_trade_is_not_used_at_the_time(database):
    """An old decision_time written after the paper trade is not known-at-the-time evidence."""

    code, before, err = _run(_argv(database))
    assert code == EXIT_OK, err
    assert (
        "**4H / 15M / 5M hierarchy evaluation known at the paper plan:** UNAVAILABLE"
        in before
    )

    _insert_hierarchy_row(
        database,
        observation_id="late-hierarchy-row",
        decision_time="2024-01-01 17:00:00.000000",  # historical, before the signal
        recorded_at="2024-01-02 09:00:00.000000",  # written long after the paper trade
    )

    code, after, err = _run(_argv(database))
    assert code == EXIT_OK, err

    # Every pre-decision finding is byte-identical.
    assert _decision_sections(before) == _decision_sections(after)
    section_8 = after[after.index("## 8.") : after.index("## 9.")]
    assert (
        "**4H / 15M / 5M hierarchy evaluation known at the paper plan:** UNAVAILABLE"
        in section_8
    )
    assert (
        "no evaluation with decision_time ≤ signal was stored at or before the plan's recorded_at"
        in section_8
    )
    assert "| Decision time (historical) |" not in section_8
    assert "2024-01-01T17:00:00Z" not in section_8

    # Listed only in the post-decision audit section, with both timestamps.
    section_10 = after[after.index("## 10.") :]
    assert "POST-DECISION" in section_10.splitlines()[0]
    assert (
        "**Hierarchy evaluations with decision_time ≤ signal recorded after the paper plan:** 1"
        in section_10
    )
    assert "2024-01-01T17:00:00Z" in section_10
    assert "2024-01-02T09:00:00Z" in section_10


def test_hierarchy_row_known_at_the_paper_trade_is_shown_with_both_timestamps(database):
    _insert_hierarchy_row(
        database,
        observation_id="known-hierarchy-row",
        decision_time="2024-01-01 17:00:00.000000",
        recorded_at="2024-01-01 21:00:00.000000",  # before the plan's recorded_at (21:00:30)
    )

    code, out, err = _run(_argv(database))

    assert code == EXIT_OK, err
    section_8 = out[out.index("## 8.") : out.index("## 9.")]
    assert "| Decision time (historical) |" in section_8
    assert "| 2024-01-01T17:00:00Z | 2024-01-01T21:00:00Z |" in section_8
    assert (
        "**Hierarchy evaluations known at the paper plan (recorded_at ≤ plan recorded_at):** 1 shown"
        in section_8
    )
    assert "UNAVAILABLE — no evaluation" not in section_8
    assert (
        "**Hierarchy evaluations with decision_time ≤ signal recorded after the paper plan:** none"
        in out
    )


def test_candles_stored_now_are_not_presented_as_available_at_the_time(database):
    code, out, err = _run(_argv(database))

    assert code == EXIT_OK, err
    section_8 = out[out.index("## 8.") : out.index("## 9.")]
    assert (
        "**4H / 15M / 5M candles demonstrably available at the paper plan:** UNAVAILABLE"
        in section_8
    )
    assert "| Stored now, in window |" in section_8
    assert "**Candles read by the base-timeframe cycle at recording:** " in section_8


def test_entry_is_matched_exactly_and_breakout_retest_states_are_reported(database):
    code, out, err = _run(_argv(database))

    assert code == EXIT_OK, err
    assert "**Entry:** 124 [RECORDED]" in out
    assert "**Stop:** 117 [RECORDED]" in out
    assert "2024-01-01T14:00:00Z [RECORDED]" in out  # breakout open
    assert "2024-01-01T17:00:00Z [RECORDED]" in out  # held retest open
    assert "**First recorded QUALIFIED (as_of):** 2024-01-01T21:00:00Z" in out
    assert "**First recorded PLANNABLE (as_of):** 2024-01-01T21:00:00Z" in out


def test_outcome_section_is_labelled_post_decision_and_shows_stop_candle(database):
    code, out, err = _run(_argv(database))

    assert code == EXIT_OK, err
    headings = [line for line in out.splitlines() if line.startswith("## ")]
    post_decision = [line for line in headings if "POST-DECISION" in line]
    assert [line.split(" — ")[0] for line in post_decision] == [
        "## 9. Stop: candle and evidence",
        "## 10. Records written after the paper plan (audit only)",
    ]
    assert out.index("## 9.") > out.index("## 8.")
    assert "**Stop touch candle open (recorded event):** 2024-01-01T22:00:00Z" in out
    assert "low 116 vs stop 117" in out


def test_source_change_during_read_exits_4_and_prints_no_report(database, monkeypatch):
    monkeypatch.setattr(
        ReadOnlySnapshot,
        "verify_source_unchanged",
        lambda self: (False, (str(self.database_path),)),
    )

    code, out, err = _run(_argv(database))

    assert code == EXIT_MOVED
    assert out == ""
    assert "changed while it was being read" in err


def test_missing_database_exits_3_and_is_not_created(tmp_path):
    missing = tmp_path / "absent.sqlite3"

    code, out, err = _run(_argv(missing))

    assert code == EXIT_DATABASE
    assert out == ""
    assert "database file not found" in err
    assert not missing.exists()


def test_database_without_ledger_tables_exits_3_and_is_unchanged(tmp_path):
    empty = tmp_path / "empty.sqlite3"
    with closing(sqlite3.connect(empty)) as conn:
        conn.execute("create table unrelated (a integer)")
        conn.commit()
    state = _file_state(empty)

    code, out, err = _run(_argv(empty))

    assert code == EXIT_DATABASE
    assert out == ""
    assert "forward-ledger tables" in err
    assert _file_state(empty) == state


def test_invalid_price_argument_exits_2(database):
    code, _out, err = _run(_argv(database, entry="not-a-price"))

    assert code == EXIT_BAD_REQUEST
    assert "--entry is not a decimal price" in err


def test_empty_wal_sidecar_is_not_a_change(tmp_path):
    database = tmp_path / "brain.sqlite3"
    database.write_bytes(b"not used by the fingerprint")
    before = fingerprint_database(database)
    database.with_name(database.name + "-wal").write_bytes(b"")
    after = fingerprint_database(database)

    assert changed_files(before, after) == ()
    database.with_name(database.name + "-wal").write_bytes(b"x")
    assert changed_files(before, fingerprint_database(database)) == (
        str(database.with_name(database.name + "-wal")),
    )
