"""Runtime configuration loaded from environment variables and an optional .env."""

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
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Return settings for the current process environment."""

    return Settings()
