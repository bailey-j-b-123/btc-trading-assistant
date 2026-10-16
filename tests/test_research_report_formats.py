"""Temporary synthetic metadata; never evidence of trading performance."""

from trading_assistant.strategy_lab.models import identity
from trading_assistant.strategy_lab.report import report
from trading_assistant.strategy_lab.store import Store


def completed(store, definition):
    key, _ = store.experiment(definition, {"index": -1})
    store.checkpoint(
        key,
        {"index": 0},
        [],
        result={
            "status": "rejected",
            "closed_trades": 0,
            "sample_size": 0,
            "net_return": "0",
            "open_positions": 0,
        },
    )
    return key


def test_one_report_handles_original_novel_alpha_and_unknown_without_rewriting(
    tmp_path,
):
    store = Store(tmp_path / "lab")
    dataset = {
        "start": "2023-04-01T00:00:00+00:00",
        "end_exclusive": "2024-01-01T00:00:00+00:00",
        "test_only": True,
    }
    original = {
        "strategy": {"family": "sma_cross"},
        "stage": "development",
        "dataset": dataset,
        "dataset_fingerprint": "recorded-dataset",
        "execution": {"fee_bps": "10"},
    }
    novel = {
        "hypothesis": {"name": "trend_pullback"},
        "stage": "exploratory_reused_chronological",
        "period": "development",
        "dataset": dataset,
        "cost": "base",
        "exit_policy": "stop_target_only",
    }
    alpha = {
        "hypothesis": {"name": "utc_open_close_continuation"},
        "period": "reused_validation_1",
        "dataset": dataset,
        "cost": "stress",
        "exit_policy": "strategy_default",
    }
    unknown = {"future_schema": {"name": "unknown"}, "test_only": True}
    ids = [completed(store, d) for d in (original, novel, alpha, unknown)]
    saved = [
        tuple(r) for r in store.db.execute("select * from experiments order by id")
    ]
    try:
        result = report(store)
        rows = {r["experiment"]: r for r in result["historical"]}
        assert set(rows) == set(ids) and not result["profitability_claim"]
        assert rows[ids[0]]["strategy"] == original["strategy"]
        assert (
            rows[ids[0]]["stage"] == "development"
            and rows[ids[0]]["dataset_fingerprint"] == "recorded-dataset"
        )
        assert rows[ids[0]]["dataset_fingerprint_provenance"] == "recorded"
        assert (
            rows[ids[1]]["strategy"] == novel["hypothesis"]
            and rows[ids[1]]["format"] == "hypothesis"
        )
        assert rows[ids[1]]["dataset_fingerprint"] == identity(dataset)
        assert (
            rows[ids[1]]["dataset_fingerprint_provenance"]
            == "derived_from_recorded_manifest"
        )
        assert rows[ids[2]]["stage"] is None  # never invent a missing validation stage
        assert (
            rows[ids[2]]["period"] == "reused_validation_1"
            and rows[ids[2]]["cost"] == "stress"
        )
        assert rows[ids[1]]["exit_policy"] == "stop_target_only"
        assert (
            rows[ids[3]]["strategy"] is None
            and rows[ids[3]]["dataset_fingerprint"] is None
        )
        assert rows[ids[3]]["definition"] == unknown and rows[ids[3]]["warnings"]
        assert [
            tuple(r) for r in store.db.execute("select * from experiments order by id")
        ] == saved
    finally:
        store.close()
