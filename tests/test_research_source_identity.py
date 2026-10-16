"""Only temporary source copies; never mutate a checkout or stored cohort."""

import json
from pathlib import Path

import pytest
from trading_assistant.strategy_lab import models
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.store import Store
from test_strategy_lab import candles, manifest


@pytest.fixture
def source_copy(tmp_path, monkeypatch):
    original = Path(models.__file__).parents[1]
    copied = tmp_path / "source" / "trading_assistant"
    for source in original.rglob("*.py"):
        target = copied / source.relative_to(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    monkeypatch.setattr(models, "__file__", str(copied / "strategy_lab/models.py"))
    models.research_code_fingerprint.cache_clear()
    try:
        yield copied
    finally:
        models.research_code_fingerprint.cache_clear()


@pytest.mark.parametrize(
    "relative",
    [
        "strategy_lab/runner.py",
        "strategy_lab/data.py",
        "strategy_lab/store.py",
        "strategy_lab/paper.py",
        "strategy_lab/campaign.py",
        "strategy_lab/overnight.py",
        "strategy_lab/novel.py",
        "strategy_lab/alpha.py",
        "strategy_lab/logic_audit.py",
        "strategy_lab/__main__.py",
        "market_data/public_archive.py",
        "market_data/validation.py",
        "market_data/repository.py",
        "config.py",
        "costs.py",
        "trade_planning/planner.py",
        "strategy_lab/simulation.py",
    ],
)
def test_material_core_source_bytes_change_research_identity(source_copy, relative):
    before = models.research_code_fingerprint()
    file = source_copy / relative
    file.write_bytes(file.read_bytes() + b"\n# synthetic source mutation\n")
    models.research_code_fingerprint.cache_clear()  # equivalent to a new process
    assert models.research_code_fingerprint() != before


def test_report_and_dashboard_edits_do_not_invalidate_research(source_copy):
    before = models.research_code_fingerprint()
    for relative in (
        "strategy_lab/report.py",
        "web/chart_evidence.py",
        "ai_explanation/renderer.py",
    ):
        file = source_copy / relative
        file.write_bytes(file.read_bytes() + b"\n# presentation only\n")
    models.research_code_fingerprint.cache_clear()
    assert models.research_code_fingerprint() == before


def test_runner_change_creates_new_cohort_without_resuming_or_rewriting_old(
    source_copy, tmp_path
):
    c = candles(140)
    store = Store(tmp_path / "lab")
    try:
        s, e = models.Strategy(), models.Execution()
        old = run_experiment(store, c, manifest(c), s, e, max_boundaries=10)
        saved = tuple(
            store.db.execute(
                "SELECT * FROM experiments WHERE id=?", (old["id"],)
            ).fetchone()
        )
        file = source_copy / "strategy_lab/runner.py"
        raw = file.read_text()
        assert 'stage="development"' in raw
        file.write_text(raw.replace('stage="development"', 'stage="walk_forward"'))
        models.research_code_fingerprint.cache_clear()
        new = run_experiment(store, c, manifest(c), s, e, max_boundaries=5)
        assert old["id"] != new["id"] and new["next_index"] == 5
        assert (
            tuple(
                store.db.execute(
                    "SELECT * FROM experiments WHERE id=?", (old["id"],)
                ).fetchone()
            )
            == saved
        )
        assert (
            json.loads(
                store.db.execute(
                    "SELECT state FROM experiments WHERE id=?", (old["id"],)
                ).fetchone()[0]
            )["index"]
            == 9
        )
        assert store.db.execute("SELECT count(*) FROM experiments").fetchone()[0] == 2
    finally:
        store.close()
