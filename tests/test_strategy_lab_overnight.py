"""Offline local-worker controls; no network, synthetic research only."""

from urllib.error import URLError
import json
import pytest
from trading_assistant.strategy_lab.store import Store
from trading_assistant.strategy_lab.overnight import overnight
from trading_assistant.strategy_lab.report import report


def test_overnight_resumes_after_outage_stops_gracefully_and_requires_no_chat(tmp_path):
    store = Store(tmp_path / "lab")
    calls = []

    def research(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise URLError("offline injected temporary outage")
        return {"finished": True, "completed_jobs": 0}

    result = overnight(
        store, research=research, brain_period=None, max_cycles=2, sleep=lambda _: None
    )
    assert result["cycles"] == 2 and len(calls) == 2
    status = store.status()
    assert len(status["recent_errors"]) == 1
    assert (
        not status["worker_lock_held"] and status["worker_health"] == "no_active_worker"
    )
    assert report(store)["forward_candidates"] == []
    stopped = overnight(store, research=research, stop=lambda: True)
    assert stopped["stage"] == "stopped" and len(calls) == 2
    store.close()


def test_permanent_data_errors_stop_instead_of_infinite_retry_or_fake_health(tmp_path):
    store = Store(tmp_path / "lab")

    def fail(*_, **__):
        raise ValueError("verified malformed public data")

    result = overnight(store, research=fail, brain_period=None, max_cycles=3)
    assert result["stage"] == "blocked_error" and result["cycles"] == 0
    assert (
        json.loads(store.db.execute("SELECT health FROM worker").fetchone()[0])["stage"]
        == "blocked_error"
    )
    assert not store.status()["worker_lock_held"]
    with pytest.raises(ValueError):
        overnight(store, minutes=0)
    store.close()
