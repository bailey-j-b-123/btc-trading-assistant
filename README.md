# Evidence-Driven Cryptocurrency Trading Assistant

A foundation for an evidence-driven cryptocurrency analysis assistant. The intended purpose is to organize reliable market evidence, future setup analysis, trade planning, and journaling for human review.

**This is not an automated trading bot. This stage is foundation only. It contains no market-data downloads, indicators, trading strategies, pattern detection, setup qualification, trade planning engine, backtesting, AI/LLM features, or user interface. It does not place or execute trades.**

Numerical market facts added in future stages must come from deterministic code and source data, not be invented by an LLM. Missing or unavailable values should remain unknown rather than be guessed.

## Current architecture

```text
src/trading_assistant/
├── config.py                 Environment-backed runtime configuration
├── database/                 SQLAlchemy declarative base and engine factory
├── logging_config.py         Structured JSON-lines logging
├── market_data/              Reserved; no data client implemented
├── market_structure/         Reserved; no analysis implemented
├── pattern_liquidity/        Reserved; no detector implemented
├── setup_qualification/      Reserved; no qualification logic implemented
├── trade_planning/           Reserved; no planning logic implemented
├── journaling/               Reserved; no journal functionality implemented
├── statistics/               Reserved; no statistical analysis implemented
└── ai_explanation/           Reserved; no AI/LLM feature implemented

migrations/                   Alembic environment and initial empty baseline
 data/raw/                    Protected location for future downloaded source data
 data/processed/              Protected location for future generated datasets
backups/                      Protected local backup location
```

Local persistence uses SQLite through SQLAlchemy. Alembic is the sole schema/version mechanism. The initial migration establishes an Alembic revision baseline and creates no market, setup, or journal tables. The application does not call `create_all()` and does not run migrations implicitly.

The instrument is configured via `TRADING_ASSISTANT_SYMBOL`, `TRADING_ASSISTANT_BASE_ASSET`, and `TRADING_ASSISTANT_QUOTE_ASSET`; defaults are `BTC/USDT`, `BTC`, and `USDT`. The default database URL is `sqlite:///data/trading_assistant.sqlite3`.

## Installation

Requires Python 3.11 or newer.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

For local overrides, copy `.env.example` to `.env` and edit it. `.env`, SQLite files, downloaded/generated data, and backup contents are ignored by Git. Never add real credentials to `.env.example` or commit them.

Structured logging can be enabled by application code with:

```python
from trading_assistant.logging_config import configure_logging

configure_logging()
```

Log records are emitted as JSON lines. The default level is `INFO`; it can be changed with `TRADING_ASSISTANT_LOG_LEVEL`.

## Initialize or migrate the database

Database creation and schema changes are explicit. From the project root, run:

```bash
alembic upgrade head
```

This creates the default local SQLite database if it does not exist and applies checked-in migrations. To use a different database URL, set `TRADING_ASSISTANT_DATABASE_URL` in the environment or local `.env` before running Alembic.

For future schema changes, create a migration, review its operations carefully, and then apply it explicitly:

```bash
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

Back up local data before schema changes. Application startup and engine construction never delete or recreate an existing database; no reset function is provided. Do not treat a downgrade or a hand-edited migration as a data reset mechanism.

## Run tests

```bash
python -m pytest
```

The suite uses temporary SQLite databases and does not initialize or modify project-persistent data.
