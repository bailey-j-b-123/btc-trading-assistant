from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from trading_assistant.database import Base, create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


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

    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sentinel (value TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO sentinel (value) VALUES ('keep')"))
    engine.dispose()

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")

    migrated_engine = create_database_engine(database_url)
    with migrated_engine.connect() as connection:
        stored_value = connection.execute(text("SELECT value FROM sentinel")).scalar_one()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    table_names = set(inspect(migrated_engine).get_table_names())
    migrated_engine.dispose()

    assert stored_value == "keep"
    assert revision == "0001_foundation"
    assert table_names == {"alembic_version", "sentinel"}
    assert not Base.metadata.tables
