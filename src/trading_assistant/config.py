"""Runtime configuration loaded from environment variables and an optional .env."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Finite default timeout for Binance public market-data requests, in
#: milliseconds. CCXT expresses ``timeout`` in milliseconds and applies it to
#: every individual socket operation (connect/read) of one request. It bounds
#: network waits without masking a genuinely stalled request; DNS resolution
#: is bounded separately by the watchdog in ``market_data.exchange``.
DEFAULT_EXCHANGE_TIMEOUT_MS = 10_000

#: Lower bound for the exchange timeout. Below one second a healthy-but-slow
#: request would false-fail, so smaller values are rejected rather than clamped.
MIN_EXCHANGE_TIMEOUT_MS = 1_000

#: Upper bound for the exchange timeout. A larger value only hides a hang for
#: longer (the same philosophy as the SQLite busy-timeout cap), so it is
#: rejected rather than clamped.
MAX_EXCHANGE_TIMEOUT_MS = 60_000


class Settings(BaseSettings):
    """Application settings; every field can be overridden via environment."""

    model_config = SettingsConfigDict(
        env_prefix="TRADING_ASSISTANT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    symbol: str = "BTC/USDT"
    base_asset: str = "BTC"
    quote_asset: str = "USDT"
    database_url: str = "sqlite:///data/trading_assistant.sqlite3"
    #: Finite SQLite busy timeout in milliseconds, applied to every pooled
    #: connection (see ``trading_assistant.database.engine``). It is a secondary
    #: defence for the short windows where two writers overlap; the writer no
    #: longer queues behind dashboard readers because the runtime uses WAL.
    #: Deliberately bounded: a huge timeout hides a lock problem instead of
    #: repairing it.
    sqlite_busy_timeout_ms: int = Field(default=5_000, ge=0, le=60_000)
    log_level: str = "INFO"

    # Fixed public market-data provider. A legacy exchange override is accepted
    # only when it says "binance"; unsupported venues fail validation rather
    # than silently changing the source used by analysis and paper observations.
    exchange: Literal["binance"] = "binance"
    default_timeframe: str = "1h"
    supported_timeframes: tuple[str, ...] = ("5m", "15m", "1h", "4h")
    raw_data_dir: Path = Path("data/raw")
    market_data_page_limit: int = Field(default=1_000, gt=0)
    market_data_max_pages: int = Field(default=10_000, gt=0)
    #: Finite, project-controlled timeout for public exchange (CCXT) requests,
    #: in milliseconds (CCXT timeout semantics: milliseconds, applied by CCXT to
    #: every individual socket connect/read of a request). It covers Binance
    #: public market-data requests, including market metadata and OHLCV,
    #: and never requires API credentials. It bounds each socket operation; DNS
    #: resolution is bounded separately by the watchdog in
    #: ``market_data.exchange``.
    exchange_timeout_ms: int = Field(
        default=DEFAULT_EXCHANGE_TIMEOUT_MS,
        ge=MIN_EXCHANGE_TIMEOUT_MS,
        le=MAX_EXCHANGE_TIMEOUT_MS,
    )

    @model_validator(mode="after")
    def validate_market_data_settings(self) -> "Settings":
        """Reject internally inconsistent timeframe configuration."""

        if not self.exchange.strip():
            raise ValueError("exchange must not be empty")
        if not self.supported_timeframes:
            raise ValueError("supported_timeframes must contain at least one timeframe")
        if len(set(self.supported_timeframes)) != len(self.supported_timeframes):
            raise ValueError("supported_timeframes must not contain duplicates")
        if self.default_timeframe not in self.supported_timeframes:
            raise ValueError("default_timeframe must be included in supported_timeframes")
        return self


def get_settings() -> Settings:
    """Return settings for the current process environment."""

    return Settings()
