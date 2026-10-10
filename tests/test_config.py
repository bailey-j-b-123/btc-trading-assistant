from pathlib import Path

import pytest

from trading_assistant.config import Settings

_SETTINGS_ENV_VARS = (
    "TRADING_ASSISTANT_SYMBOL",
    "TRADING_ASSISTANT_BASE_ASSET",
    "TRADING_ASSISTANT_QUOTE_ASSET",
    "TRADING_ASSISTANT_DATABASE_URL",
    "TRADING_ASSISTANT_LOG_LEVEL",
    "TRADING_ASSISTANT_EXCHANGE",
    "TRADING_ASSISTANT_DEFAULT_TIMEFRAME",
    "TRADING_ASSISTANT_SUPPORTED_TIMEFRAMES",
    "TRADING_ASSISTANT_RAW_DATA_DIR",
    "TRADING_ASSISTANT_MARKET_DATA_PAGE_LIMIT",
    "TRADING_ASSISTANT_MARKET_DATA_MAX_PAGES",
    "TRADING_ASSISTANT_EXCHANGE_TIMEOUT_MS",
)


def test_settings_use_documented_defaults(monkeypatch):
    for variable in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)

    settings = Settings(_env_file=None)

    assert settings.symbol == "BTC/USDT"
    assert settings.base_asset == "BTC"
    assert settings.quote_asset == "USDT"
    assert settings.database_url == "sqlite:///data/trading_assistant.sqlite3"
    assert settings.log_level == "INFO"
    assert settings.exchange == "binance"
    assert settings.default_timeframe == "1h"
    # ``1m`` is outcome-ordering evidence only (journal-outcome-v2); it is not
    # a planning timeframe. The forward runner refuses it as a base timeframe.
    assert settings.supported_timeframes == ("1m", "5m", "15m", "1h", "4h")
    assert settings.raw_data_dir == Path("data/raw")
    assert settings.market_data_page_limit == 1_000
    assert settings.exchange_timeout_ms == 10_000


def test_instrument_storage_and_market_data_can_be_configured_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_ASSISTANT_SYMBOL", "ETH/USDT")
    monkeypatch.setenv("TRADING_ASSISTANT_BASE_ASSET", "ETH")
    monkeypatch.setenv("TRADING_ASSISTANT_QUOTE_ASSET", "USDT")
    monkeypatch.setenv("TRADING_ASSISTANT_DATABASE_URL", "sqlite:///temporary.sqlite3")
    monkeypatch.setenv("TRADING_ASSISTANT_EXCHANGE", "binance")
    monkeypatch.setenv("TRADING_ASSISTANT_DEFAULT_TIMEFRAME", "30m")
    monkeypatch.setenv("TRADING_ASSISTANT_SUPPORTED_TIMEFRAMES", '["5m", "30m"]')
    monkeypatch.setenv("TRADING_ASSISTANT_RAW_DATA_DIR", str(tmp_path / "exchange-raw"))

    settings = Settings(_env_file=None)

    assert (settings.symbol, settings.base_asset, settings.quote_asset) == ("ETH/USDT", "ETH", "USDT")
    assert settings.database_url == "sqlite:///temporary.sqlite3"
    assert settings.exchange == "binance"
    assert settings.default_timeframe == "30m"
    assert settings.supported_timeframes == ("5m", "30m")
    assert settings.raw_data_dir == tmp_path / "exchange-raw"


@pytest.mark.parametrize("exchange", ["kraken", "coinbase", ""])
def test_runtime_configuration_rejects_non_binance_exchange_ids(monkeypatch, exchange):
    monkeypatch.setenv("TRADING_ASSISTANT_EXCHANGE", exchange)
    with pytest.raises(ValueError, match="binance"):
        Settings(_env_file=None)
