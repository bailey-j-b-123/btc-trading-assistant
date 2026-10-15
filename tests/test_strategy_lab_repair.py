"""Offline type-contract and protected research-stage regression controls."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import json
import pytest
from trading_assistant.strategy_lab import brain
from trading_assistant.strategy_lab.store import Store
from trading_assistant.trade_planning.planner import plan_trade
from trading_assistant.multi_timeframe.models import (
    ContextRegime,
    ExecutionState,
    HierarchyAlignment,
    HierarchyDecision,
)
from test_multi_timeframe import _snapshot, candle
from test_trade_planning import qualified


@pytest.mark.parametrize("bearish", [False, True])
def test_real_structural_plan_string_direction_through_research_adapter(
    tmp_path, monkeypatch, bearish
):
    """Real planner contract; isolated hierarchy fixture, actual simulator/SQLite."""
    q, frame, setup = qualified(bearish)
    plan = plan_trade(snapshot=q, frame=frame, setup_id=setup.id)
    assert plan.actionable and type(plan.direction) is str
    direction = "bearish" if bearish else "bullish"
    assert plan.direction == direction
    at = datetime(2024, 1, 1, 6, tzinfo=UTC)
    h = _snapshot(decision_time=at, setup_kwargs={"direction": direction})
    h = replace(
        h,
        context=replace(
            h.context,
            regime=ContextRegime.BEARISH_STRUCTURE
            if bearish
            else ContextRegime.BULLISH_STRUCTURE,
        ),
        alignment=HierarchyAlignment.ALIGNED,
        decision=HierarchyDecision.PLANNABLE,
        setup=replace(h.setup, setup_id=setup.id),
        execution=replace(h.execution, state=ExecutionState.TRIGGERED),
    )
    calls = []
    service = SimpleNamespace(
        qualification=SimpleNamespace(build_frames=lambda **kw: (frame,)),
        qualification_parameters=None,
    )

    def evaluate(**kw):
        calls.append(kw["decision_time"])
        service.qualification.build_frames(as_of=h.setup.boundary_close)
        return h

    service.evaluate = evaluate
    monkeypatch.setattr(brain, "MultiTimeframeService", lambda *args, **kw: service)
    monkeypatch.setattr(brain, "enumerate_qualifications", lambda *args, **kw: (q,))

    def source(store, start, end, tf):
        c = tuple(
            candle(
                tf,
                at - timedelta(minutes=5) + i * timedelta(minutes=5),
                "88" if bearish else "112",
            )
            for i in range(2)
        )
        return c, {"fixture": tf}

    monkeypatch.setattr(brain, "dataset", source)
    s = Store(tmp_path / "lab")
    try:
        result = brain.compare_brain(
            s, start=at, end=at + timedelta(minutes=5), prepare=False
        )
        assert result["complete"] and len(calls) == 2
        assert (
            s.db.execute("select count(*) from brain_observations").fetchone()[0] == 2
        )
        for p in ("A1", "A2", "A3"):
            measured = result["result"]["performance"][p]
            assert measured["filled_trades"] == (0 if bearish else 1)
            assert result["result"]["counts"][p]["plannable"] == 2
            if bearish:
                assert result["result"]["counts"][p]["unsupported_short_plan"] == 2
        payload = json.loads(
            s.db.execute("select payload from brain_observations limit 1").fetchone()[0]
        )
        assert payload["plan"] == plan.to_json_dict()
        assert payload["policies"]["A1"]["decision"] == h.decision.value
        assert brain.compare_brain(
            s, start=at, end=at + timedelta(minutes=5), prepare=False
        )["deduplicated"]
    finally:
        s.close()


def test_default_modern_scope_excludes_archived_history_and_recent_holdout():
    from trading_assistant.strategy_lab.campaign import ResearchPlan

    p = ResearchPlan()
    assert p.development == ("2023-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00")
    assert p.folds[0][0] == p.development[1]
    assert p.folds[-1][1] == p.holdout[0] == "2025-01-01T00:00:00+00:00"
    assert p.version == "research-campaign-v2-manual-holdout"


def test_permanent_overnight_failure_has_nonzero_cli_exit(
    tmp_path, monkeypatch, capsys
):
    from trading_assistant.strategy_lab import __main__ as cli

    monkeypatch.setattr(cli.os, "nice", lambda _: None)
    monkeypatch.setattr(
        cli,
        "overnight",
        lambda *a, **kw: {
            "stage": "blocked_error",
            "error": "test-only",
            "progress_preserved": True,
        },
    )
    assert (
        cli.main(["--root", str(tmp_path / "lab"), "overnight", "--minutes", "1"]) == 1
    )
    assert json.loads(capsys.readouterr().out)["stage"] == "blocked_error"


def test_worker_exposes_approval_wait_without_granting_access(tmp_path):
    from trading_assistant.strategy_lab.overnight import overnight

    s = Store(tmp_path / "lab")
    stages = []
    real_heartbeat = s.heartbeat

    def heartbeat(stage, **kw):
        stages.append(stage)
        return real_heartbeat(stage, **kw)

    s.heartbeat = heartbeat

    def research(*a, **kw):
        assert "approve_holdout_for" not in kw
        return {"finished": False, "stage": "awaiting_explicit_holdout_approval"}

    try:
        overnight(
            s, brain_period=None, research=research, max_cycles=2, sleep=lambda _: None
        )
        assert "awaiting_explicit_holdout_approval" in stages
        assert s.db.execute("select count(*) from holdout_custody").fetchone()[0] == 0
    finally:
        s.close()


def test_exclusive_dataset_end_does_not_claim_next_month_receipt(tmp_path):
    from test_public_archive import archive
    from trading_assistant.strategy_lab.data import acquire, dataset

    s = Store(tmp_path / "lab")
    try:
        for month in ("2023-12", "2024-01"):
            raw, checksum = archive(month, "1h")
            acquire(
                s,
                [month],
                download=lambda url: (
                    checksum.encode() if url.endswith(".CHECKSUM") else raw
                ),
            )
        _, m = dataset(
            s, datetime(2023, 12, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)
        )
        assert [r["month"] for r in m["source_receipts"]] == ["2023-12"]
    finally:
        s.close()
