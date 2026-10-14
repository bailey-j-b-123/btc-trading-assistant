"""Offline heartbeat evidence and read-only status integration regressions."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from forward_fixtures import QUALIFYING_BOUNDARY, labelled_series, make_harness
from trading_assistant.config import Settings
from trading_assistant.forward_testing.parameters import HeartbeatStatus
from trading_assistant.forward_testing.runner_health import RunnerHealthPolicy, assess_runner_health

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)

def heartbeat(age=0, status="IDLE", error=None):
    return SimpleNamespace(recorded_at=NOW-timedelta(seconds=age), status=status, last_error=error)

@pytest.mark.parametrize("age,state,confirmed", [
    (0,"CONFIRMED_ALIVE",True), (360,"CONFIRMED_ALIVE",True),
    (361,"STALE_UNCONFIRMED",False), (1800,"STALE_UNCONFIRMED",False),
    (-1,"CLOCK_INCONSISTENT",False), (-3,"CLOCK_INCONSISTENT",False),
    (-0.000001,"CLOCK_INCONSISTENT",False),
])
def test_age_boundaries_and_any_future_timestamp(age, state, confirmed):
    result = assess_runner_health(heartbeat(age), now=NOW)
    assert result["state"] == state
    assert result["alive_confirmed"] is confirmed
    assert result["age_seconds"] == age
    assert result["tolerance_source"] == "policy_floor"

@pytest.mark.parametrize("status,error,state", [
    ("ERROR",None,"FAILED"), ("IDLE","failure","FAILED"),
    ("STOPPED",None,"STOPPED"), ("NO_DATA",None,"UNCONFIRMED"),
    ("unknown",None,"UNCONFIRMED"),
])
def test_recorded_nonhealthy_states_are_not_certified(status,error,state):
    result=assess_runner_health(heartbeat(1800,status,error),now=NOW)
    assert result["state"] == state
    assert result["alive_confirmed"] is False

def test_no_heartbeat_is_never_run():
    result=assess_runner_health(None,now=NOW)
    assert result["state"] == "NEVER_RUN"
    assert result["alive_confirmed"] is False
    assert result["age_seconds"] is None

def test_observed_slow_cadence_and_operator_override_are_explicit():
    rows=tuple(heartbeat(1500+i*600) for i in range(12))
    result=assess_runner_health(rows[0],now=NOW,recent_heartbeats=rows)
    assert result["alive_confirmed"] is True
    assert result["cadence_seconds"] == 600
    assert result["tolerance_seconds"] == 1800
    assert result["tolerance_source"] == "observed_heartbeats"
    override=assess_runner_health(rows[0],now=NOW,recent_heartbeats=rows,tolerance_seconds=1000)
    assert override["state"] == "STALE_UNCONFIRMED"
    assert override["tolerance_source"] == "operator_setting"

def test_sparse_or_outlier_gaps_do_not_buy_a_looser_policy():
    sparse=(heartbeat(0),heartbeat(3600),heartbeat(7200))
    result=assess_runner_health(sparse[0],now=NOW,recent_heartbeats=sparse)
    assert result["cadence_source"] == "insufficient_evidence"
    assert result["tolerance_seconds"] == 360
    rows=tuple(heartbeat(age) for age in (0,0,60,120,180,999999))
    result=assess_runner_health(rows[0],now=NOW,recent_heartbeats=rows)
    assert result["cadence_seconds"] == 60
    assert result["tolerance_seconds"] == 360

def test_policy_observation_is_bounded_and_inputs_validated():
    visited=[]
    def rows():
        for i in range(100):
            visited.append(i)
            yield heartbeat(i*60)
    assess_runner_health(heartbeat(),now=NOW,recent_heartbeats=rows())
    assert len(visited) == 12
    with pytest.raises(ValueError):
        RunnerHealthPolicy(sample_size=2,minimum_cadence_gaps=3)
    with pytest.raises(ValueError):
        assess_runner_health(heartbeat(),now=NOW,tolerance_seconds=True)
    with pytest.raises(ValueError):
        Settings(_env_file=None,runner_liveness_tolerance_seconds=0)

def test_current_market_does_not_hide_stale_runner_and_status_writes_nothing():
    harness=make_harness(series=labelled_series(),ledger_start=QUALIFYING_BOUNDARY)
    try:
        harness.advance_to(QUALIFYING_BOUNDARY)
        harness.run(refresh_market_data=False)
        counts=harness.counts()
        status=harness.service.status(now=QUALIFYING_BOUNDARY+timedelta(minutes=30))
        assert status["market_data"]["data_health"] == "CURRENT"
        assert status["sample"]["pending_catch_up_boundaries"] == 0
        assert status["runner_liveness"]["state"] == "STALE_UNCONFIRMED"
        assert "not proof" in status["runner_liveness"]["detail"]
        from web_fixtures import make_client
        client=make_client(harness.engine,harness.settings,clock=QUALIFYING_BOUNDARY+timedelta(minutes=30))
        response=client.get("/api/forward")
        assert response.status_code == 200
        assert response.json()["status"]["runner_liveness"]["alive_confirmed"] is False
        harness.service.settings=harness.settings.model_copy(update={"runner_liveness_tolerance_seconds":7200})
        assert harness.service.status(now=QUALIFYING_BOUNDARY+timedelta(minutes=30))["runner_liveness"]["alive_confirmed"] is True
        assert harness.counts() == counts
        with pytest.raises(ValueError):
            harness.service.ledger.recent_heartbeats(exchange="binance",symbol="BTC/USDT",timeframe="1h",limit=0)
    finally:
        harness.engine.dispose()

def test_latest_insertion_ties_and_fresh_recovery_are_honest():
    harness=make_harness(series=labelled_series(),ledger_start=QUALIFYING_BOUNDARY)
    try:
        harness.advance_to(QUALIFYING_BOUNDARY)
        harness.run(refresh_market_data=False)
        harness.service.record_runner_event(status=HeartbeatStatus.STOPPED,detail="stopped")
        assert harness.service.status()["runner_liveness"]["state"] == "STOPPED"
        harness.service.record_runner_event(status=HeartbeatStatus.IDLE,detail="fresh report")
        status=harness.service.status()
        assert status["runner"]["status"] == "IDLE"
        assert status["runner_liveness"]["alive_confirmed"] is True
        assert harness.service.ledger.recent_heartbeats(exchange="binance",symbol="ETH/USDT",timeframe="1h") == ()
    finally:
        harness.engine.dispose()
