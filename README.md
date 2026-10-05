# Evidence-Driven Cryptocurrency Trading Assistant

A foundation for an evidence-driven cryptocurrency analysis assistant. The intended purpose is to organize reliable market evidence and future analysis for human review.

**This is not an automated trading bot. It contains no indicators, trading strategies, market-structure or pattern detection, setup qualification, trade planning engine, backtesting, AI/LLM features, alerts, or user interface. It does not make trading decisions or place/execute trades.** Numerical market facts are derived from source data and deterministic code; missing candles remain missing rather than being guessed or synthesized.

## Current architecture

```text
src/trading_assistant/
├── config.py                 Environment-backed runtime configuration
├── database/                 SQLAlchemy base and SQLite engine factory
├── logging_config.py         Structured JSON-lines logging
├── market_data/
│   ├── exchange.py           Credential-free CCXT public OHLCV adapter
│   ├── models.py             UTC candle model and exact decimal storage types
│   ├── raw_storage.py        Append-only raw CCXT response archive
│   ├── repository.py         Idempotent inserts and ordered retrieval
│   ├── service.py            Pagination, closed-candle filtering, orchestration
│   ├── timeframes.py         Fixed-interval and UTC conversion helpers
│   ├── types.py              Immutable candle/results types
│   └── validation.py         Parsing, validation, and gap reports
├── market_structure/         Reserved; no analysis implemented
├── pattern_liquidity/        Reserved; no detector implemented
├── setup_qualification/      Reserved; no qualification logic implemented
├── trade_planning/           Reserved; no planning logic implemented
├── journaling/               Reserved; no journal functionality implemented
├── statistics/               Reserved; no statistical analysis implemented
└── ai_explanation/           Reserved; no AI/LLM feature implemented

migrations/                   Explicit Alembic schema migrations
data/raw/                      Protected raw-response archive
data/processed/                Protected location for future derived datasets
backups/                       Protected local backup location
```

## Installation and configuration

Requires Python 3.11 or newer.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

`.env` is optional and ignored by Git. Never put credentials in `.env.example` or commit them. Public OHLCV requests use CCXT without API credentials or trading permissions.

The instrument remains configurable with `TRADING_ASSISTANT_SYMBOL`, `TRADING_ASSISTANT_BASE_ASSET`, and `TRADING_ASSISTANT_QUOTE_ASSET` (defaults: `BTC/USDT`, `BTC`, `USDT`). The default local database URL remains `sqlite:///data/trading_assistant.sqlite3`. The default exchange is `kraken`, configurable with `TRADING_ASSISTANT_EXCHANGE`. Initial configured timeframes are `5m`, `15m`, `1h`, `4h`, and `1d`; `TRADING_ASSISTANT_SUPPORTED_TIMEFRAMES` and `TRADING_ASSISTANT_DEFAULT_TIMEFRAME` can be changed. Fixed-duration seconds/minutes/hours/days/weeks are supported by the timeframe parser, so the configured set can be extended without treating the initial list as exhaustive. The raw archive root is configurable with `TRADING_ASSISTANT_RAW_DATA_DIR` and defaults to `data/raw/`.

## Market-data behavior

### Downloading and incremental updates

Run `alembic upgrade head` before using the database-backed service. For an initial historical download, pass an explicit timezone-aware UTC start time. A later `update_history` call starts after the latest stored candle; without existing history, it requires an explicit start time. Pagination uses CCXT's `since` and configured page limit, advancing by the requested timeframe. Empty or truncated exchange responses are reported through gaps; pagination stalls and exchange/network errors fail clearly.

Example from the project root after installation and migration:

```python
from datetime import UTC, datetime

from trading_assistant.database import create_database_engine
from trading_assistant.market_data import create_market_data_service

engine = create_database_engine()
with create_market_data_service(engine) as market_data:
    initial = market_data.download_history(
        start_time=datetime(2024, 1, 1, tzinfo=UTC),
        timeframe="1h",
    )
    print(initial.complete, initial.inserted_count, initial.missing_candle_count)

    update = market_data.update_history(timeframe="1h")
    print(update.complete, update.inserted_count)

engine.dispose()
```

`download_history` and `update_history` return counts, raw-file paths, and any gaps. To retry a reported gap, request that aligned historical range explicitly; unchanged candles are skipped and only absent identities are inserted. Stored candles can be retrieved with `get_candles(exchange=..., symbol=..., timeframe=..., start_time=..., end_time=...)`; the returned `CandleQueryResult.candles` are chronologically ordered, while `.gaps`, `.missing_candle_count`, and `.complete` make incomplete ranges explicit. Optional time bounds are inclusive. Without explicit bounds, retrieval can assess only gaps between stored endpoints; completeness cannot be inferred beyond the range available from the configured exchange.

### Raw source and processed/database storage

Each page returned by CCXT is archived separately under `data/raw/<exchange>/<symbol>/<timeframe>/`. Filenames and JSON metadata identify the exchange, symbol, timeframe, request cursor, and retrieval time. The archive includes the CCXT OHLCV page and, when exposed by CCXT, its HTTP response text. Files are created exclusively: an existing raw file is never overwritten or silently removed. Raw files remain separate from the processed candle table and are ignored by Git.

Validated closed candles are stored in SQLite via SQLAlchemy. The candle identity is `(exchange, symbol, timeframe, UTC open time)`, enforced by the database primary key. Price and volume values are parsed as `Decimal` and persisted as base-10 text rather than SQLite floating-point values. Repeated identical candles are skipped; if the exchange returns different values for a previously stored key, the update fails and the existing history is left unchanged.

### Validation and closed candles

Only candles whose full timeframe interval has ended at the service's UTC `as_of` time are eligible for historical storage. The current/forming candle is archived in the source response but excluded from the processed/database records. Validation reports malformed/missing values, duplicate or unaligned timestamps, out-of-order rows, invalid OHLC relationships, negative prices/volume, and missing/gapped candle intervals. Invalid rows fail the update before database writes. Gaps make the result `complete=False` and are logged/reported; **missing candles are never fabricated, interpolated, or presented as complete**. Previously stored history remains untouched after exchange, validation, or database conflicts/failures.

Configure structured JSON-lines logging with:

```python
from trading_assistant.logging_config import configure_logging

configure_logging()
```

The default log level is `INFO`; use `TRADING_ASSISTANT_LOG_LEVEL` to override it. Logs include request, validation, gap, and storage counts but never raw payloads or credentials.

## Database initialization and migrations

Database creation and schema changes remain explicit. From the project root:

```bash
alembic upgrade head
```

The Step 1 foundation revision is unchanged. The new OHLCV migration adds only the candle table. It does not delete or recreate a database or alter other tables. Its downgrade refuses to drop the candle table if historical candles exist; use a reviewed forward migration for schema corrections. Back up local data before schema changes. There is no reset function, and application startup does not run migrations implicitly.

## Tests

The full test suite uses mocked exchange responses and temporary SQLite/raw-data locations; it does not contact an exchange or modify project-persistent data.

```bash
python -m pytest
python -m pytest tests/test_market_data.py
```
