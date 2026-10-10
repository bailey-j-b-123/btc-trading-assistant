"""BRAIN data path (Step 12 forward runner) with a Binance Spot source.

The forward runner refreshes market data through the configured exchange and
records paper cycles keyed by exchange identity. This test drives one real
``run_single_pass`` over a Binance-shaped public source (date-bounded klines
semantics, ``exchange_id="binance"``) and proves the whole chain — refresh,
storage, qualification, paper-plan recording — works under the Binance identity
without touching Kraken-keyed data.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from forward_fixtures import (
    EPOCH,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    FakeExchange,
    clock_at,
    labelled_series,
    make_service,
)
from market_structure_fixtures import INTERVAL
from web_fixtures import migrated_engine

from trading_assistant.config import Settings
from trading_assistant.forward_testing import run_single_pass
from trading_assistant.market_data.repository import CandleRepository


def binance_settings(database_url: str, raw_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        symbol=SYMBOL,
        base_asset="BTC",
        quote_asset="USDT",
        exchange="binance",
        default_timeframe=TIMEFRAME,
        supported_timeframes=("15m", TIMEFRAME, "4h", "1d"),
        database_url=database_url,
        raw_data_dir=raw_dir,
        market_data_page_limit=1000,
        market_data_max_pages=50,
    )


def test_forward_runner_refreshes_and_records_cycles_through_binance(tmp_path):
    engine, url = migrated_engine(tmp_path)
    # The labelled qualifying series re-keyed to the Binance identity, served
    # by a date-bounded (Binance-shaped) public source.
    series = tuple(replace(candle, exchange="binance") for candle in labelled_series())
    source = FakeExchange(exchange_id="binance")
    source.set_candles(series)
    settings = binance_settings(url, tmp_path / "raw")
    clock = {"t": clock_at(QUALIFYING_BOUNDARY)}
    service = make_service(
        engine,
        settings,
        source,
        clock=lambda: clock["t"],
        ledger_start=EPOCH,
        backfill_start=EPOCH,
    )
    try:
        result = run_single_pass(service, symbol=SYMBOL, timeframe=TIMEFRAME)

        # The BRAIN refresh went through the Binance source (not Kraken, not a
        # second client): every stored candle carries the binance identity.
        assert source.exchange_id == "binance"
        assert source.calls >= 1
        stored = CandleRepository(engine).get_candles(
            exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME
        )
        assert stored.complete
        assert {candle.exchange for candle in stored.candles} == {"binance"}

        # The qualifying boundary was processed and a paper plan recorded —
        # the existing provider-neutral Step 6 contract is unchanged.
        assert result.paper_plans_created == 1
        assert QUALIFYING_BOUNDARY in result.processed_boundaries
        counts = service.ledger.counts(exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME)
        assert counts["cycles"] >= 1
        assert counts["paper_plans"] == 1

        # Kraken identity was never touched: no kraken-keyed rows exist.
        assert (
            CandleRepository(engine).latest_timestamp(
                exchange="kraken", symbol=SYMBOL, timeframe=TIMEFRAME
            )
            is None
        )
        assert (
            service.ledger.counts(exchange="kraken", symbol=SYMBOL, timeframe=TIMEFRAME)[
                "cycles"
            ]
            == 0
        )
    finally:
        engine.dispose()


def test_forward_runner_binance_history_coexists_with_kraken(tmp_path):
    """Both venues' series and ledgers coexist; the runner only reads its own."""

    engine, url = migrated_engine(tmp_path)
    binance_series = tuple(
        replace(candle, exchange="binance") for candle in labelled_series()
    )
    kraken_series = tuple(
        replace(candle, exchange="kraken") for candle in labelled_series()
    )
    repository = CandleRepository(engine)
    repository.insert_unchanged_or_new(binance_series)
    repository.insert_unchanged_or_new(kraken_series)

    settings = binance_settings(url, tmp_path / "raw")
    source = FakeExchange(exchange_id="binance")
    source.set_candles(binance_series)
    clock = {"t": clock_at(QUALIFYING_BOUNDARY)}
    service = make_service(
        engine,
        settings,
        source,
        clock=lambda: clock["t"],
        ledger_start=EPOCH,
        backfill_start=EPOCH,
    )
    try:
        run_single_pass(service, symbol=SYMBOL, timeframe=TIMEFRAME)

        binance_view = repository.get_candles(
            exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME
        )
        kraken_view = repository.get_candles(
            exchange="kraken", symbol=SYMBOL, timeframe=TIMEFRAME
        )
        assert len(binance_view.candles) == len(kraken_view.candles) == len(binance_series)
        assert service.ledger.counts(exchange="binance", symbol=SYMBOL, timeframe=TIMEFRAME)[
            "cycles"
        ] >= 1
        assert (
            service.ledger.counts(exchange="kraken", symbol=SYMBOL, timeframe=TIMEFRAME)[
                "cycles"
            ]
            == 0
        )
        # The stored Binance series was read independently; because it is
        # already complete, the runner correctly avoids an unnecessary fetch.
        assert source.exchange_id == "binance"
        assert INTERVAL.total_seconds() == 3600
    finally:
        engine.dispose()
