"""Completion of Track B must not hide unfinished BRAIN hierarchy research."""

import json
from trading_assistant.strategy_lab.store import Store
from trading_assistant.strategy_lab.overnight import overnight


def test_worker_reports_remaining_brain_work_after_candidate_grid_exhausts(tmp_path):
    store = Store(tmp_path / "lab")
    stages = []

    def pause(_):
        stages.append(
            json.loads(store.db.execute("SELECT health FROM worker").fetchone()[0])[
                "stage"
            ]
        )

    result = overnight(
        store,
        max_cycles=2,
        idle_seconds=1,
        sleep=pause,
        brain=lambda *_, **__: {"complete": False, "processed": 24},
        research=lambda *_, **__: {"finished": True},
    )
    assert stages == ["brain_policy_comparison_in_progress"]
    assert result["last_brain_comparison"]["complete"] is False
    store.close()
