from __future__ import annotations

from dataclasses import replace

import pytest
from market_structure_fixtures import candle_at
from web_fixtures import migrated_engine

from scripts import seed_synthetic_demo
from trading_assistant.database import create_database_engine
from trading_assistant.market_data.repository import CandleRepository


@pytest.mark.parametrize("exchange", ["binance", "kraken"])
def test_synthetic_demo_refuses_to_write_into_existing_btc_usdt_history(
    tmp_path, monkeypatch, capsys, exchange
):
    engine, database_url = migrated_engine(tmp_path, f"existing-{exchange}.sqlite3")
    existing = replace(
        candle_at(
            0,
            open_="10",
            high="11",
            low="9",
            close="10.5",
            timeframe="5m",
        ),
        exchange=exchange,
        symbol="BTC/USDT",
    )
    CandleRepository(engine).insert_unchanged_or_new((existing,))
    monkeypatch.setattr(seed_synthetic_demo, "create_database_engine", lambda: engine)

    seed_synthetic_demo.main()

    assert "refusing to seed" in capsys.readouterr().out
    # main disposed the patched engine; reopen the file and verify that only
    # the original candle remains, with no synthetic demo timeframe or row.
    check_engine = create_database_engine(database_url)
    repository = CandleRepository(check_engine)
    try:
        existing_rows = repository.get_candles(
            exchange=exchange, symbol="BTC/USDT", timeframe="5m"
        )
        assert existing_rows.candles == (existing,)
        assert repository.get_candles(
            exchange="binance", symbol="BTC/USDT", timeframe="1h"
        ).candles == ()
    finally:
        check_engine.dispose()
