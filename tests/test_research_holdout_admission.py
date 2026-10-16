"""Synthetic dates and sentinels only: never open protected historical prices."""

from datetime import UTC, datetime

import pytest
from test_strategy_lab import candles, manifest
from trading_assistant.strategy_lab import data
from trading_assistant.strategy_lab.models import Execution, Strategy
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.store import Store
from dataclasses import replace
from trading_assistant.strategy_lab.holdout import (
    require_period,
    _approved_campaign_period,
)


def no_prices(*args, **kwargs):
    raise AssertionError("reached a price-access boundary before custody check")


@pytest.mark.parametrize("months", [["2025-01"], ["2024-12", "2025-01"]])
def test_acquisition_rejects_entire_mixed_request_before_repository_or_download(
    tmp_path, monkeypatch, months
):
    store = Store(tmp_path / "lab")
    monkeypatch.setattr(data, "repository", no_prices)
    try:
        with pytest.raises(ValueError, match="sealed 2025"):
            data.acquire(store, months, download=no_prices)
        assert not (store.root / "archives").exists()
    finally:
        store.close()


@pytest.mark.parametrize(
    "a,b",
    [
        ("2025-01-01", "2025-02-01"),
        ("2024-12-31", "2025-01-02"),
        ("2025-12-31", "2026-01-02"),
    ],
)
def test_cached_dataset_reads_are_guarded_before_repository_access(
    tmp_path, monkeypatch, a, b
):
    store = Store(tmp_path / "lab")
    monkeypatch.setattr(data, "repository", no_prices)
    try:
        with pytest.raises(ValueError, match="sealed 2025"):
            data.dataset(
                store,
                datetime.fromisoformat(a).replace(tzinfo=UTC),
                datetime.fromisoformat(b).replace(tzinfo=UTC),
            )
    finally:
        store.close()


@pytest.mark.parametrize(
    "stage", ["development", "walk_forward", "holdout", "forward_paper"]
)
def test_stage_label_cannot_admit_protected_historical_candles(tmp_path, stage):
    store = Store(tmp_path / "lab")
    try:
        c = tuple(
            replace(c, timestamp=c.timestamp.replace(year=2025)) for c in candles(3)
        )
        with pytest.raises(ValueError, match="sealed 2025"):
            run_experiment(store, c, manifest(c), Strategy(), Execution(), stage=stage)
        assert store.db.execute("SELECT count(*) FROM experiments").fetchone()[0] == 0
    finally:
        store.close()


def test_internal_approved_scope_is_exact_and_reset_after_interruption(tmp_path):
    a, b = datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC)
    owner, other = Store(tmp_path / "owner"), Store(tmp_path / "other")
    try:
        with pytest.raises(RuntimeError, match="interrupt"):
            with _approved_campaign_period(owner, a, b, "1h", "frozen-candidate"):
                require_period(owner, a, b, "1h")  # archive/cache admission
                require_period(
                    owner, a, b, "1h", candidate="frozen-candidate", stage="holdout"
                )
                for store, start, end, tf, candidate, stage in [
                    (other, a, b, "1h", "frozen-candidate", "holdout"),
                    (
                        owner,
                        a,
                        datetime(2025, 3, 1, tzinfo=UTC),
                        "1h",
                        "frozen-candidate",
                        "holdout",
                    ),
                    (owner, a, b, "4h", "frozen-candidate", "holdout"),
                    (owner, a, b, "1h", "another-candidate", "holdout"),
                    (owner, a, b, "1h", "frozen-candidate", "development"),
                ]:
                    with pytest.raises(ValueError, match="sealed 2025"):
                        require_period(
                            store, start, end, tf, candidate=candidate, stage=stage
                        )
                raise RuntimeError("interrupt")
        with pytest.raises(ValueError, match="sealed 2025"):
            require_period(owner, a, b, "1h")
    finally:
        owner.close()
        other.close()


def test_allowed_period_edges_do_not_touch_repository(tmp_path):
    store = Store(tmp_path / "lab")
    try:
        require_period(
            store,
            datetime(2024, 12, 1, tzinfo=UTC),
            datetime(2025, 1, 1, tzinfo=UTC),
            "1h",
        )
        require_period(
            store,
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 2, 1, tzinfo=UTC),
            "1h",
        )
        with pytest.raises(ValueError, match="explicit UTC"):
            require_period(store, datetime(2024, 1, 1), datetime(2024, 2, 1), "1h")
    finally:
        store.close()


def test_campaign_requires_exact_approval_before_synthetic_2025_download(
    tmp_path, monkeypatch
):
    """Actual detectors/gates/custody; fake archive bytes, NEVER real 2025 prices."""
    from decimal import Decimal as D
    from test_public_archive import archive
    from trading_assistant.strategy_lab import campaign
    from trading_assistant.strategy_lab.validation import ValidationRules

    requested = []

    def geometry(rows):
        for i, row in enumerate(rows):
            phase = i % 100
            excursion = max(0, phase - 20) if phase < 60 else 100 - phase
            price = D("100") + D(excursion) * D("0.3")
            if i >= len(rows) - 100:
                price = D("100")
            row[1:6] = [
                str(price),
                str(price + D("0.1")),
                str(price - D("0.1")),
                str(price),
                "100",
            ]

    def acquire_synthetic(store, months, timeframe, **kwargs):
        def download(url):
            month = url.split("/")[-1].split("BTCUSDT-" + timeframe + "-")[1][:7]
            requested.append(month)
            raw, checksum = archive(month, timeframe, mutate=geometry)
            return checksum.encode() if url.endswith(".CHECKSUM") else raw

        return data.acquire(store, months, timeframe, download=download, **kwargs)

    monkeypatch.setattr(campaign, "acquire", acquire_synthetic)
    plan = campaign.ResearchPlan(
        development=("2024-01-01T00:00:00+00:00", "2024-02-01T00:00:00+00:00"),
        folds=tuple(
            (f"2024-{i:02}-01T00:00:00+00:00", f"2024-{i + 1:02}-01T00:00:00+00:00")
            for i in (2, 3, 4)
        ),
        holdout=("2025-01-01T00:00:00+00:00", "2025-02-01T00:00:00+00:00"),
        search_size=1,
        rules=ValidationRules(
            minimum_development_trades=5,
            minimum_fold_trades=5,
            minimum_holdout_trades=5,
            bootstrap_samples=1000,
        ),
    )
    store = Store(tmp_path / "lab")
    try:
        waiting = campaign.run_campaign(store, plan, max_jobs=256)
        assert waiting["stage"] == "awaiting_explicit_holdout_approval"
        assert "2025-01" not in requested
        with pytest.raises(ValueError, match="does not match"):
            campaign.run_campaign(
                store, plan, max_jobs=256, approve_holdout_for="wrong"
            )
        assert "2025-01" not in requested
        done = campaign.run_campaign(
            store, plan, max_jobs=256, approve_holdout_for=waiting["approval_id"]
        )
        assert done["finished"] and done["completed_jobs"] == 10
        assert "2025-01" in requested
        with pytest.raises(ValueError, match="sealed 2025"):
            data.dataset(
                store,
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 2, 1, tzinfo=UTC),
            )
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        store.close()
