"""Runtime configuration loaded from environment variables and an optional .env."""

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    exchange: str = "kraken"
    default_timeframe: str = "1h"
    supported_timeframes: tuple[str, ...] = ("5m", "15m", "1h", "4h", "1d")
    raw_data_dir: Path = Path("data/raw")
    market_data_page_limit: int = Field(default=720, gt=0)
    market_data_max_pages: int = Field(default=10_000, gt=0)

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
