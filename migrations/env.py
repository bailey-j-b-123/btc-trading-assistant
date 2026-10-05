"""Alembic environment using application settings and SQLAlchemy metadata."""

from alembic import context
from sqlalchemy.engine import Connection

from trading_assistant.config import get_settings
from trading_assistant.database import Base, create_database_engine
from trading_assistant.journaling import models as _journal_models  # noqa: F401
from trading_assistant.market_data import models as _market_data_models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    """Use a test-provided URL when present, otherwise the application setting."""

    configured_url = config.attributes.get("database_url")
    url = str(configured_url) if configured_url is not None else get_settings().database_url
    # ConfigParser treats percent signs as interpolation markers.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return url


def run_migrations_offline() -> None:
    """Run migrations without opening a database connection."""

    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations only when explicitly invoked through Alembic."""

    _database_url()
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _run_migrations(supplied_connection)
        return

    engine = create_database_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with engine.connect() as connection:
            _run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
