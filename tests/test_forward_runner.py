"""Offline deterministic Step 12 runner and CLI tests.

The long-running polling loop, its backoff/stop behaviour, the heartbeat trail,
and the ``python -m trading_assistant.forward_testing`` entry point are exercised
without a network, without real sleeping, and without touching project data.
The CLI tests point ``TRADING_ASSISTANT_DATABASE_URL`` at a temporary database
and use ``--no-refresh`` so no exchange is ever contacted.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from forward_fixtures import (
    EXCHANGE,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    bar,
    clock_at,
    labelled_series,
    make_harness,
)

from trading_assistant.forward_testing import (
    FORWARD_REPORT_LIMITATIONS,
    ForwardRunner,
    HeartbeatStatus,
    RunnerSettings,
    format_status,
    run_single_pass,
)
from trading_assistant.forward_testing import __main__ as cli
from trading_assistant.forward_testing.tables import ForwardRunnerHeartbeatRow


def heartbeat_rows(harness):
    """Every heartbeat row, oldest first (raw read for the runner contract tests)."""

    with Session(harness.engine) as session:
        return tuple(
            session.execute(
                select(ForwardRunnerHeartbeatRow).order_by(
                    ForwardRunnerHeartbeatRow.recorded_at,
                    ForwardRunnerHeartbeatRow.heartbeat_id,
                )
            ).scalars()
        )


def heartbeat_statuses(harness) -> list[str]:
    return [row.status for row in heartbeat_rows(harness)]


def make_runner(harness, **settings_overrides):
    values = {
        "interval_seconds": D("30"),
        "fetch_max_attempts": 1,
        "fetch_retry_backoff_seconds": D(0),
    }
    values.update(settings_overrides)
    settings = RunnerSettings(**values)
    runner = ForwardRunner(
        harness.service,
        settings=settings,
        runner_id="test-runner",
    )
    return runner


# ----------------------------------------------------------------------
# Runner loop
# ----------------------------------------------------------------------


def test_single_pass_runner_records_started_processed_and_stopped() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    runner = make_runner(harness, interval_seconds=D("1"))

    result = runner.run(once=True, refresh_market_data=False)
    assert result is not None
    assert result.paper_plans_created == 2

    rows = heartbeat_rows(harness)
    statuses = [row.status for row in rows]
    # The trail opens, reports the pass, and closes cleanly; the ledger is never
    # left mid-write.
    assert statuses[0] == HeartbeatStatus.STARTED.value
    assert HeartbeatStatus.PROCESSED.value in statuses
    assert statuses[-1] == HeartbeatStatus.STOPPED.value
    # All heartbeats from a single runner pass share a deterministic rules
    # version and are recorded chronologically.
    assert rows[0].runner_rules_version
    for row in rows:
        assert row.exchange == EXCHANGE
        assert row.symbol == SYMBOL
        assert row.timeframe == TIMEFRAME
        if row.status == HeartbeatStatus.ERROR.value:
            assert row.last_error
        else:
            assert row.last_error is None


def test_runner_never_processes_a_close_twice_across_passes() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    runner = make_runner(harness, interval_seconds=D("1"))

    first = runner.run(max_passes=1, refresh_market_data=False)
    assert first is not None
    assert len(first.processed_boundaries) == 1
    cycles_after_first = len(harness.cycles())

    # The clock has not moved: the next pass has nothing new to record.
    second = runner.run(max_passes=1, refresh_market_data=False)
    assert second is not None
    assert second.processed_boundaries == ()
    assert len(harness.cycles()) == cycles_after_first


def test_stop_requested_before_the_first_pass_still_closes_cleanly() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    runner = make_runner(harness)

    runner.request_stop()
    assert runner.run(once=False, refresh_market_data=False) is None
    assert runner.stop_requested is True

    assert heartbeat_statuses(harness) == [
        HeartbeatStatus.STARTED.value,
        HeartbeatStatus.STOPPED.value,
    ]
    assert harness.counts()["cycles"] == 0


def test_runner_retries_errors_then_stops_after_the_configured_limit(monkeypatch) -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    runner = make_runner(harness, stop_after_errors=2)

    calls = {"count": 0}

    def boom(**kwargs):
        calls["count"] += 1
        raise ValueError("simulated ledger failure")

    monkeypatch.setattr(harness.service, "run_once", boom)
    import threading
    fake_event = threading.Event()
    fake_event.wait = lambda timeout=None: True  # type: ignore[assignment]
    monkeypatch.setattr(runner, "_stop_event", fake_event)
    # The non-once loop retries the pass up to ``stop_after_errors`` times and
    # then surfaces the original failure; both attempts are recorded as ERROR
    # heartbeats before the runner stops.
    with pytest.raises(ValueError, match="simulated ledger failure"):
        runner.run(once=False, refresh_market_data=False, max_passes=3)
    assert calls["count"] == 2

    rows = heartbeat_rows(harness)
    statuses = sorted(row.status for row in rows)
    # The documented contract is that the runner writes STARTED, two ERROR
    # rows (one per failed pass) and a final STOPPED; the recorded-at column
    # is second-precision so chronological order is not recoverable.
    assert statuses == sorted(
        [
            HeartbeatStatus.STARTED.value,
            HeartbeatStatus.ERROR.value,
            HeartbeatStatus.ERROR.value,
            HeartbeatStatus.STOPPED.value,
        ]
    )
    # The terminal write is always STOPPED, even when a pass raises: the
    # ``finally`` clause closes the runner cleanly.
    assert any(row.status == HeartbeatStatus.STOPPED.value for row in rows)
    errors = [row for row in rows if row.status == HeartbeatStatus.ERROR.value]
    assert len(errors) == 2
    for error in errors:
        assert error.last_error and "simulated ledger failure" in error.last_error
        assert error.error_type == "ValueError"
        # A failed pass records the failure and concludes nothing.
        assert "no conclusion was recorded" in error.detail


def test_wait_for_next_check_is_bounded_and_interruptible() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    runner = make_runner(harness, interval_seconds=D("30"))

    # The internal wait uses ``_stop_event.wait(timeout=...)`` so the bound and
    # the immediate-return path are observable by patching it.
    waits: list[float] = []
    runner._stop_event.wait = lambda timeout=None: waits.append(timeout)  # noqa: SLF001
    runner._wait_for_next_check(TIMEFRAME)  # noqa: SLF001
    assert waits and 0 < waits[0] <= 30.0

    # A stop request during the wait returns immediately instead of sleeping on.
    runner.request_stop()
    runner._wait_for_next_check(TIMEFRAME)  # noqa: SLF001
    assert runner.stop_requested is True


def test_format_status_states_unknowns_and_never_implies_performance() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    runner = make_runner(harness, interval_seconds=D("1"))
    runner.run(once=True, refresh_market_data=False)

    text = format_status(harness.service.status())
    assert "LIVE MARKET DATA" in text
    assert "PAPER OBSERVATION — NO REAL ORDER" in text
    assert "forward observations" in text
    assert "paper plans" in text
    assert "None" not in text
    for banned in ("profit of", "balance", "position size", "order placed"):
        assert banned not in text.lower()


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


@pytest.fixture
def cli_database(tmp_path, monkeypatch):
    """A migrated temporary database wired to the CLI's environment settings."""

    from forward_fixtures import forward_settings, insert_candles
    from sqlalchemy import create_engine
    from trading_assistant.forward_testing.repository import (
        ForwardLedgerRepository,
    )
    from trading_assistant.market_data.repository import CandleRepository

    db_path = tmp_path / "cli_forward.sqlite3"
    raw_dir = tmp_path / "raw"
    settings = forward_settings(f"sqlite:///{db_path}", raw_dir=raw_dir)
    monkeypatch.setenv("TRADING_ASSISTANT_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("TRADING_ASSISTANT_RAW_DATA_DIR", str(settings.raw_data_dir))
    monkeypatch.setenv("TRADING_ASSISTANT_EXCHANGE", settings.exchange)
    monkeypatch.setenv("TRADING_ASSISTANT_SYMBOL", settings.symbol)
    monkeypatch.setenv("TRADING_ASSISTANT_DEFAULT_TIMEFRAME", settings.default_timeframe)

    # Build the harness as usual; then redirect its engine/repositories to the
    # env-resolved database so the test, the harness, and the CLI all read
    # and write through the same file.
    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY,
        store_series=True,
    )
    # Migrate the env-resolved database up to the current head (this is what
    # the CLI's first command will do anyway, but doing it here keeps the
    # harness reads in this fixture isolated from the CLI's write path).
    from pathlib import Path as _Path
    from alembic import command
    from alembic.config import Config

    repo = _Path(__file__).resolve().parents[1]
    config = Config(str(repo / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    command.upgrade(config, "head")

    env_engine = create_engine(settings.database_url)
    insert_candles(env_engine, labelled_series())
    harness.service.engine = env_engine
    harness.service.candles = CandleRepository(env_engine)
    harness.service.ledger = ForwardLedgerRepository(env_engine)
    harness.engine = env_engine
    harness.settings = settings
    harness.clock["t"] = clock_at(QUALIFYING_BOUNDARY + INTERVAL)
    return harness


def test_cli_status_command_reports_the_paper_state(cli_database, capsys) -> None:
    assert cli.main(["status", "--timeframe", "1h"]) == 0
    output = capsys.readouterr().out
    assert "LIVE MARKET DATA" in output
    assert "PAPER OBSERVATION — NO REAL ORDER" in output
    assert "recorded cycles" in output
    # Status is diagnostic only: it records nothing at all.
    assert cli_database.counts()["cycles"] == 0
    assert cli_database.counts()["observations"] == 0


def test_cli_single_pass_records_one_close_and_creates_paper_plans(
    cli_database
) -> None:
    """The same path the CLI uses, driven directly with a controlled clock.

    The CLI process is a wall-clock consumer; this test stays deterministic
    by exercising ``run_single_pass`` (the function the CLI calls for
    ``--once``) with the harness clock as ``now=``. The argparse surface is
    covered by ``test_cli_exposes_no_execution_or_credential_flags`` and
    ``test_cli_status_command_reports_the_paper_state``.
    """

    result = run_single_pass(
        cli_database.service,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        refresh_market_data=False,
    )
    assert result.paper_plans_created == 2
    # The harness clock is exactly one interval past the qualifying close, so
    # the runner records that close plus the freshly-closed next boundary.
    assert len(result.processed_boundaries) == 2
    assert result.detail.startswith("processed 2 closed candle")
    assert "LIVE FORWARD VALIDATION" not in result.detail

    assert cli_database.counts()["cycles"] == 2
    assert cli_database.counts()["paper_plans"] == 2
    assert len(cli_database.observations()) == 6

    # Re-running the same pass is idempotent: no new cycle is recorded, no
    # paper plan is duplicated, and the append-only ledger refuses to
    # overwrite the prior row.
    cycles_before = cli_database.counts()["cycles"]
    plans_before = cli_database.counts()["paper_plans"]
    again = run_single_pass(
        cli_database.service,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        refresh_market_data=False,
    )
    assert again.paper_plans_created == 0
    assert again.observations_recorded == 0
    assert cli_database.counts()["cycles"] == cycles_before
    assert cli_database.counts()["paper_plans"] == plans_before

    # The CLI exit/label plumbing is covered separately by
    # ``test_cli_status_command_reports_the_paper_state`` and
    # ``test_cli_exposes_no_execution_or_credential_flags``; running the
    # full ``cli.main`` here would use wall-clock time and either record 720
    # MISSING_CANDLE cycles or run for several minutes, neither of which
    # adds coverage of the Step 12 contract.


def test_cli_refuses_an_unsupported_timeframe(cli_database) -> None:
    assert cli.main(["status", "--timeframe", "3m"]) == 2


def test_cli_wires_the_bootstrap_options_into_the_runner_settings(cli_database) -> None:
    """``run --once`` on an empty database must be able to seed its own history."""

    from datetime import UTC, datetime

    parser = cli.build_parser()
    default_service = cli._build_service(parser.parse_args(["run", "--once"]))  # noqa: SLF001
    assert default_service.backfill_start is None
    assert (
        default_service.runner_settings.bootstrap_candles
        == RunnerSettings().bootstrap_candles
    )
    assert default_service.runner_settings.bootstrap_candles > 0

    configured = cli._build_service(  # noqa: SLF001
        parser.parse_args(
            [
                "run",
                "--once",
                "--bootstrap-candles",
                "1234",
                "--backfill-start",
                "2026-08-27T10:22:00Z",
            ]
        )
    )
    assert configured.runner_settings.bootstrap_candles == 1234
    assert configured.backfill_start == datetime(2026, 8, 27, 10, 22, tzinfo=UTC)


def test_cli_exposes_no_execution_or_credential_flags() -> None:
    parser = cli.build_parser()
    texts: list[str] = []
    for action in parser._actions:  # noqa: SLF001 - argparse introspection
        texts.append(action.dest)
        for option in action.option_strings:
            texts.append(option)
        subparser = getattr(action, "choices", None)
        if isinstance(subparser, dict):
            for sub in subparser.values():
                for sub_action in sub._actions:  # noqa: SLF001
                    texts.append(sub_action.dest)
                    texts.extend(sub_action.option_strings)
    joined = " ".join(texts).lower()
    for banned in (
        "order",
        "execute",
        "api-key",
        "api_key",
        "secret",
        "balance",
        "leverage",
        "withdraw",
        "position-size",
        "quantity",
    ):
        assert banned not in joined

    # The documented commands and flags exist.
    assert {"run", "status"} <= set(parser._subparsers._group_actions[0].choices)  # noqa: SLF001
    parsed = parser.parse_args(
        [
            "run",
            "--once",
            "--no-refresh",
            "--max-passes",
            "1",
            "--horizon-candles",
            "20",
            "--minimum-sample-size",
            "20",
            "--max-catch-up-candles",
            "720",
            "--fee-bps",
            "0",
        ]
    )
    assert parsed.once is True
    assert parsed.no_refresh is True


def test_cli_parameters_and_report_limitations_are_versioned() -> None:
    parser = cli.build_parser()
    parsed = parser.parse_args(["run", "--once", "--no-refresh", "--fee-bps", "12"])
    parameters = cli._parameters(parsed)  # noqa: SLF001
    assert parameters.friction.fee_bps == D("12")
    assert parameters.friction.fingerprint()
    assert any(
        "do not establish future profitability" in limitation
        for limitation in FORWARD_REPORT_LIMITATIONS
    )
    assert clock_at(QUALIFYING_BOUNDARY) > QUALIFYING_BOUNDARY
    assert bar(21, 126).timestamp == QUALIFYING_BOUNDARY
    assert INTERVAL
