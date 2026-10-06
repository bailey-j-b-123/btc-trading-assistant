"""Tests for the dashboard startup preflight checks (Step 10 CLI support)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from trading_assistant.web.preflight import (
    PreflightError,
    run_preflight_checks,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _migrate(url: str, revision: str = "head") -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, revision)


def test_missing_database_file_reports_the_fix_and_creates_nothing(tmp_path) -> None:
    target = tmp_path / "missing.sqlite3"
    url = f"sqlite:///{target}"
    with pytest.raises(PreflightError, match="alembic upgrade head"):
        run_preflight_checks(url)
    assert not target.exists()


def test_unstamped_database_file_reports_the_fix(tmp_path) -> None:
    target = tmp_path / "empty.sqlite3"
    sqlite3.connect(target).close()
    with pytest.raises(PreflightError, match="alembic upgrade head"):
        run_preflight_checks(f"sqlite:///{target}")


def test_partially_migrated_database_reports_the_fix(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'partial.sqlite3'}"
    _migrate(url, "0001_foundation")
    with pytest.raises(PreflightError, match="alembic upgrade head"):
        run_preflight_checks(url)


def test_migrated_database_without_candles_passes_with_warning(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'nocandles.sqlite3'}"
    _migrate(url, "head")
    report = run_preflight_checks(url)
    assert report.schema_revision is not None
    assert report.candle_count == 0
    assert len(report.warnings) == 1
    assert "download_history" in report.warnings[0]


def test_migrated_database_with_candles_passes_quietly(tmp_path) -> None:
    from web_fixtures import (
        insert_candles,
        migrated_engine,
        qualifying_candles,
    )

    engine, url = migrated_engine(tmp_path, name="candles.sqlite3")
    try:
        insert_candles(engine, qualifying_candles())
    finally:
        engine.dispose()
    report = run_preflight_checks(url)
    assert report.candle_count == len(qualifying_candles())
    assert report.warnings == ()


def test_cli_entry_refuses_to_start_on_missing_database(
    tmp_path, monkeypatch, capsys
) -> None:
    import trading_assistant.web.__main__ as cli

    target = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("TRADING_ASSISTANT_DATABASE_URL", f"sqlite:///{target}")
    monkeypatch.setattr(
        "sys.argv", ["trading_assistant.web", "--port", "8099"]
    )
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert "alembic upgrade head" in capsys.readouterr().err
    assert not target.exists()


def test_cli_entry_starts_server_after_preflight_passes(
    tmp_path, monkeypatch
) -> None:
    import trading_assistant.web.__main__ as cli
    from web_fixtures import migrated_engine

    engine, url = migrated_engine(tmp_path, name="ok.sqlite3")
    engine.dispose()
    started: dict = {}

    def _fake_run(*args, **kwargs):
        started["args"] = args
        started["kwargs"] = kwargs

    monkeypatch.setenv("TRADING_ASSISTANT_DATABASE_URL", url)
    monkeypatch.setattr("sys.argv", ["trading_assistant.web", "--port", "8099"])
    monkeypatch.setattr("uvicorn.run", _fake_run)
    cli.main()
    assert started["kwargs"]["port"] == 8099
