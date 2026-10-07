from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from trading_assistant.database import Base, create_database_engine
from trading_assistant.forward_testing import models as _forward_models  # noqa: F401
from trading_assistant.journaling import models as _journal_models  # noqa: F401
from trading_assistant.market_data import models as _market_data_models  # noqa: F401
from trading_assistant.multi_timeframe import tables as _hierarchy_models  # noqa: F401

FORWARD_TABLES = {
    "forward_cycles",
    "forward_observations",
    "forward_paper_plans",
    "forward_paper_outcomes",
    "forward_runner_heartbeats",
}

HIERARCHY_TABLES = {"forward_hierarchy_observations"}

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


def test_engine_is_lazy_and_reopening_preserves_existing_data(tmp_path):
    database_path = tmp_path / "local.sqlite3"
    database_url = _sqlite_url(database_path)

    engine = create_database_engine(database_url)
    assert not database_path.exists()

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sentinel (value TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO sentinel (value) VALUES ('preserve me')"))
    engine.dispose()

    reopened_engine = create_database_engine(database_url)
    assert database_path.exists()
    with reopened_engine.connect() as connection:
        stored_value = connection.execute(text("SELECT value FROM sentinel")).scalar_one()
    reopened_engine.dispose()

    assert stored_value == "preserve me"


def test_explicit_alembic_upgrade_preserves_existing_rows_and_tracks_revision(tmp_path):
    database_path = tmp_path / "migration.sqlite3"
    database_url = _sqlite_url(database_path)
    config = _alembic_config(database_url)

    # Model a database already initialized with the Step 1 schema baseline.
    command.upgrade(config, "0001_foundation")
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sentinel (value TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO sentinel (value) VALUES ('keep')"))
    engine.dispose()

    command.upgrade(config, "head")

    migrated_engine = create_database_engine(database_url)
    with migrated_engine.connect() as connection:
        stored_value = connection.execute(text("SELECT value FROM sentinel")).scalar_one()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    table_names = set(inspect(migrated_engine).get_table_names())
    migrated_engine.dispose()

    assert stored_value == "keep"
    assert revision == "0006_forward_no_trade_reason"
    assert table_names == {
        "alembic_version",
        "ohlcv_candles",
        "sentinel",
        "journal_records",
        "journal_decisions",
        "journal_outcomes",
        "journal_outcome_events",
    } | FORWARD_TABLES | HIERARCHY_TABLES
    assert set(Base.metadata.tables) == {
        "ohlcv_candles",
        "journal_records",
        "journal_decisions",
        "journal_outcomes",
        "journal_outcome_events",
    } | FORWARD_TABLES | HIERARCHY_TABLES


def test_forward_migration_is_additive_and_never_drops_recorded_observations(tmp_path):
    database_path = tmp_path / "forward_migration.sqlite3"
    database_url = _sqlite_url(database_path)
    config = _alembic_config(database_url)

    # Stop at the Step 11 head, keep some real market data, then migrate.
    command.upgrade(config, "0003_journal")
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ohlcv_candles "
                "(exchange, symbol, timeframe, timestamp, open, high, low, close, volume) "
                "VALUES ('mock-exchange', 'ETH/USDT', '5m', '1970-01-01 00:00:00.000000', "
                "'10', '11', '9', '10.5', '3')"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    migrated_engine = create_database_engine(database_url)
    with migrated_engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        candles = connection.execute(
            text("SELECT COUNT(*) FROM ohlcv_candles")
        ).scalar_one()
    table_names = set(inspect(migrated_engine).get_table_names())

    # The forward ledger's and hierarchy ledger's tables exist alongside the
    # Step 2 archive.
    assert revision == "0006_forward_no_trade_reason"
    assert FORWARD_TABLES <= table_names
    assert HIERARCHY_TABLES <= table_names
    assert "ohlcv_candles" in table_names

    # Downgrading the empty forward/hierarchy ledgers removes only Step 12/13
    # objects and keeps the real market history.
    command.downgrade(config, "0003_journal")
    with migrated_engine.connect() as connection:
        remaining = set(inspect(migrated_engine).get_table_names())
        candles_after = connection.execute(
            text("SELECT COUNT(*) FROM ohlcv_candles")
        ).scalar_one()
        revision_after = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    migrated_engine.dispose()

    assert revision_after == "0003_journal"
    assert candles == candles_after == 1
    assert FORWARD_TABLES.isdisjoint(remaining)
    assert HIERARCHY_TABLES.isdisjoint(remaining)


def test_candle_migration_refuses_to_drop_historical_rows(tmp_path):
    database_path = tmp_path / "protected.sqlite3"
    database_url = _sqlite_url(database_path)
    config = _alembic_config(database_url)
    command.upgrade(config, "head")

    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ohlcv_candles "
                "(exchange, symbol, timeframe, timestamp, open, high, low, close, volume) "
                "VALUES ('mock-exchange', 'ETH/USDT', '5m', '1970-01-01 00:00:00.000000', "
                "'10', '11', '9', '10.5', '3')"
            )
        )

    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        command.downgrade(config, "0001_foundation")

    with engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM ohlcv_candles")).scalar_one()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()

    assert count == 1
    assert revision == "0002_ohlcv_candles"
