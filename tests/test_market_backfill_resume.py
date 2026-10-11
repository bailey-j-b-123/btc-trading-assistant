"""Regression tests for gap-chunk resume, stored-row isolation and checkpoint safety.

Synthetic checks only: every exchange call goes to the deterministic ``FakeSource`` from
``test_market_backfill``. They prove the backfill's bookkeeping, not what Binance returns.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from test_market_backfill import (
    AS_OF,
    EPOCH,
    SYMBOL,
    FakeSource,
    _database,
    _rows,
    _service,
    _stored_count,
)
from trading_assistant.market_data.backfill import run_backfill


def _run(service, *, as_of=AS_OF, timeframes=("1h",), **kwargs):
    return run_backfill(
        service,
        symbol=SYMBOL,
        timeframes=timeframes,
        start=EPOCH,
        as_of=as_of,
        chunk_candles=100,
        **kwargs,
    )


def _checkpoint(tmp_path: Path) -> Path:
    return tmp_path / "raw" / "backfill-checkpoints" / "binance-BTC-USDT-1h.json"


def _hour(index: int) -> str:
    return (EPOCH + timedelta(hours=index)).isoformat()


# --- gap chunks settle: complete chunk, and a missing candle at start / middle / end ----------


def test_complete_chunk_is_skipped_on_rerun_with_no_request_and_no_gap(tmp_path):
    engine = _database(tmp_path)
    source = FakeSource(_rows(300))
    service = _service(engine, tmp_path, source)
    _run(service)
    calls = source.calls
    item = _run(service).timeframes[0]
    assert item.chunks_already_complete == 3 and item.chunks_downloaded == 0
    assert item.chunks_remaining == 0 and item.gaps == []
    assert source.calls == calls


@pytest.mark.parametrize("missing_index", [0, 50, 99], ids=["start-of-chunk", "middle", "end-of-chunk"])
def test_gap_chunk_settles_so_rerun_does_not_refetch_it(tmp_path, missing_index):
    engine = _database(tmp_path)
    source = FakeSource(_rows(300, skip={missing_index}))
    service = _service(engine, tmp_path, source)
    first = _run(service).timeframes[0]
    assert first.chunks_downloaded == 3 and first.chunks_remaining == 0
    assert _stored_count(engine) == 299, "no candle is invented for the missing hour"
    calls = source.calls

    second = _run(service).timeframes[0]
    assert source.calls == calls, "a settled gap chunk must not be requested again"
    assert second.chunks_already_complete == 3 and second.chunks_downloaded == 0
    assert second.chunks_remaining == 0 and second.stopped_at_chunk_limit is False
    assert [gap["missing"] for gap in second.gaps] == [1], "the gap stays reported on every run"
    assert second.gaps[0]["start"] == _hour(missing_index)


def test_gapped_chunks_do_not_consume_the_whole_budget_on_every_run(tmp_path):
    """Regression: gap chunks at the front used to re-download forever and starve later chunks."""

    engine = _database(tmp_path)
    service = _service(engine, tmp_path, FakeSource(_rows(300, skip={50, 150})))
    _run(service, max_chunks=2)
    report = _run(service, max_chunks=2).timeframes[0]
    assert report.chunks_downloaded == 1, "only the chunk that was never fetched is downloaded"
    assert report.chunks_remaining == 0 and report.stopped_at_chunk_limit is False
    assert _stored_count(engine) == 298


def test_gap_at_newest_closed_candle_is_not_settled_while_it_is_recent(tmp_path):
    engine = _database(tmp_path)
    source = FakeSource(_rows(300, skip={299}))
    service = _service(engine, tmp_path, source)
    first = _run(service).timeframes[0]
    assert first.chunks_remaining == 1, "the newest chunk is within the settle margin"
    calls = source.calls
    second = _run(service).timeframes[0]
    # One or two requests: pagination asks once more when the final candle of a chunk is absent.
    assert source.calls > calls, "a recent gap is re-verified on the next run"
    assert second.chunks_remaining == 1


def test_recent_gap_settles_once_it_is_older_than_the_margin(tmp_path):
    engine = _database(tmp_path)
    source = FakeSource(_rows(300, skip={299}))
    service = _service(engine, tmp_path, source)
    _run(service)  # as_of: newest closed open 299:00, gap in the newest chunk
    later = AS_OF + timedelta(hours=2)  # newest closed 301:00, so the 299 gap is now aged
    _run(service, as_of=later)
    calls = source.calls
    item = _run(service, as_of=later).timeframes[0]
    assert item.chunks_downloaded == 1, "only the still-recent 300..301 chunk is re-checked"
    assert source.calls == calls + 1


def test_recheck_gaps_refetches_a_settled_gap_so_a_later_candle_is_inserted_once(tmp_path):
    engine = _database(tmp_path)
    service_first = _service(engine, tmp_path, FakeSource(_rows(300, skip={50})))
    _run(service_first)
    assert _stored_count(engine) == 299

    healed = FakeSource(_rows(300))  # the exchange now publishes the candle at 50:00
    recheck = _run(_service(engine, tmp_path, healed), recheck_settled_gaps=True).timeframes[0]
    assert recheck.inserted == 1 and recheck.already_present == 99
    assert _stored_count(engine) == 300

    calls = healed.calls
    _run(_service(engine, tmp_path, healed))
    assert healed.calls == calls, "after healing, the chunk is complete and not requested"


def test_settled_gap_survives_a_later_failure_and_is_not_refetched_on_resume(tmp_path):
    engine = _database(tmp_path)
    flaky = FakeSource(_rows(300, skip={50}), fail_on_call=2)  # chunk 0 ok, chunk 1 fails
    first = _run(_service(engine, tmp_path, flaky)).timeframes[0]
    assert first.error and "ConnectionError" in first.error
    healthy = FakeSource(_rows(300, skip={50}))
    resumed = _run(_service(engine, tmp_path, healthy)).timeframes[0]
    assert resumed.chunks_already_complete == 1, "the settled gap chunk is not requested again"
    assert resumed.chunks_downloaded == 2 and healthy.calls == 2
    assert _stored_count(engine) == 299


# --- stored-row validation never aborts the whole run -----------------------------------------


def _corrupt_stored_row(engine, offset: int) -> str:
    sql = text
    with engine.begin() as connection:
        timestamp = connection.execute(
            sql("SELECT timestamp FROM ohlcv_candles WHERE timeframe='1h' ORDER BY timestamp LIMIT 1 OFFSET :o"),
            {"o": offset},
        ).scalar_one()
        connection.execute(
            sql("UPDATE ohlcv_candles SET high='1.0' WHERE timeframe='1h' AND timestamp=:t"),
            {"t": timestamp},
        )
    return str(timestamp)


def _stored_high(engine, timestamp: str) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT high FROM ohlcv_candles WHERE timeframe='1h' AND timestamp=:t"), {"t": timestamp}
        ).scalar_one()


def test_corrupt_stored_row_is_reported_by_dry_run_without_raising_or_requests(tmp_path):
    engine = _database(tmp_path)
    service = _service(engine, tmp_path, FakeSource(_rows(300)))
    _run(service)
    timestamp = _corrupt_stored_row(engine, offset=250)
    source = FakeSource(_rows(300))
    report = _run(_service(engine, tmp_path, source), dry_run=True)
    item = report.timeframes[0]
    assert report.ok is False and item.error and "CandleValidationError" in item.error
    assert source.calls == 0
    assert _stored_high(engine, timestamp) == "1.0", "the bad row is reported, never rewritten"


def test_corrupt_stored_row_is_reported_by_a_real_run_without_rewriting_it(tmp_path):
    engine = _database(tmp_path)
    _run(_service(engine, tmp_path, FakeSource(_rows(300))))
    timestamp = _corrupt_stored_row(engine, offset=250)
    report = _run(_service(engine, tmp_path, FakeSource(_rows(300))))
    assert report.ok is False and "CandleValidationError" in (report.timeframes[0].error or "")
    assert _stored_high(engine, timestamp) == "1.0"


def test_a_corrupt_timeframe_does_not_stop_the_other_timeframes_being_reported(tmp_path):
    engine = _database(tmp_path)
    _run(_service(engine, tmp_path, FakeSource(_rows(300))))
    _corrupt_stored_row(engine, offset=250)
    report = _run(
        _service(engine, tmp_path, FakeSource(_rows(300))), timeframes=("1h", "4h"), dry_run=True
    )
    assert [item.timeframe for item in report.timeframes] == ["1h", "4h"]
    assert report.timeframes[0].error is not None


# --- checkpoint safety ------------------------------------------------------------------------


def test_dry_run_reads_the_checkpoint_but_never_writes_it(tmp_path):
    engine = _database(tmp_path)
    _run(_service(engine, tmp_path, FakeSource(_rows(300, skip={50}))))
    before = _checkpoint(tmp_path).read_text(encoding="utf-8")
    source = FakeSource(_rows(300, skip={50}))
    report = _run(_service(engine, tmp_path, source), dry_run=True).timeframes[0]
    assert report.chunks_remaining == 0 and source.calls == 0
    assert _checkpoint(tmp_path).read_text(encoding="utf-8") == before


def test_dry_run_on_a_fresh_database_creates_no_checkpoint_directory(tmp_path):
    engine = _database(tmp_path)
    _run(_service(engine, tmp_path, FakeSource(_rows(300))), dry_run=True)
    assert not (tmp_path / "raw" / "backfill-checkpoints").exists()


def test_checkpoint_is_written_atomically_with_no_temporary_file_left_behind(tmp_path):
    engine = _database(tmp_path)
    _run(_service(engine, tmp_path, FakeSource(_rows(300, skip={50}))))
    payload = json.loads(_checkpoint(tmp_path).read_text(encoding="utf-8"))
    assert payload["exchange"] == "binance" and payload["timeframe"] == "1h"
    assert len(payload["settled"]) == 1
    leftovers = list(_checkpoint(tmp_path).parent.glob("*.tmp"))
    assert leftovers == []


def test_unreadable_checkpoint_is_reported_moved_aside_and_rebuilt_safely(tmp_path):
    engine = _database(tmp_path)
    _run(_service(engine, tmp_path, FakeSource(_rows(300, skip={50}))))
    path = _checkpoint(tmp_path)
    path.write_text("{not json", encoding="utf-8")

    dry = _run(_service(engine, tmp_path, FakeSource(_rows(300, skip={50}))), dry_run=True).timeframes[0]
    assert dry.checkpoint_warning and "unreadable" in dry.checkpoint_warning
    assert path.read_text(encoding="utf-8") == "{not json", "a dry run never moves files"

    report = _run(_service(engine, tmp_path, FakeSource(_rows(300, skip={50})))).timeframes[0]
    assert report.error is None and report.chunks_downloaded == 1, "the gap chunk is verified again"
    assert list(path.parent.glob(path.name + ".unreadable-*")), "the unreadable file is kept, not deleted"
    assert json.loads(path.read_text(encoding="utf-8"))["settled"]


def test_cli_recheck_flag_is_off_by_default_and_opt_in():
    from trading_assistant.ingestion.__main__ import build_parser

    parser = build_parser()
    base = ["backfill", "--start", "2024-01-01", "--dry-run"]
    assert parser.parse_args(base).recheck_gaps is False
    assert parser.parse_args([*base, "--recheck-gaps"]).recheck_gaps is True
