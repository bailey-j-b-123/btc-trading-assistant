from trading_assistant.config import Settings


_SETTINGS_ENV_VARS = (
    "TRADING_ASSISTANT_SYMBOL",
    "TRADING_ASSISTANT_BASE_ASSET",
    "TRADING_ASSISTANT_QUOTE_ASSET",
    "TRADING_ASSISTANT_DATABASE_URL",
    "TRADING_ASSISTANT_LOG_LEVEL",
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


def test_instrument_and_storage_can_be_configured_from_environment(monkeypatch):
    monkeypatch.setenv("TRADING_ASSISTANT_SYMBOL", "ETH/USDT")
    monkeypatch.setenv("TRADING_ASSISTANT_BASE_ASSET", "ETH")
    monkeypatch.setenv("TRADING_ASSISTANT_QUOTE_ASSET", "USDT")
    monkeypatch.setenv("TRADING_ASSISTANT_DATABASE_URL", "sqlite:///temporary.sqlite3")

    settings = Settings(_env_file=None)

    assert (settings.symbol, settings.base_asset, settings.quote_asset) == ("ETH/USDT", "ETH", "USDT")
    assert settings.database_url == "sqlite:///temporary.sqlite3"
