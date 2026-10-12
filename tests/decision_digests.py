"""Digests of complete decision outputs at fixed historical boundaries.

Used by ``test_decision_output_regression.py`` to pin the decision path before and
after performance work. Inputs are the deterministic SYNTHETIC series from
``test_decision_invariance.synthetic_series``; nothing here reads an exchange.

Two digests per boundary:

* ``dashboard``: SHA-256 of the dashboard's decision-bearing JSON (``plan``,
  ``scenario``, ``planning``, ``looking_for``, ``setups``/``qualification``,
  ``overlays``, ``chart_evidence``, ``market_state`` and the rest). The wall-clock
  ``generated_at`` stamp and signal-provenance presentation block are excluded.
  Live-evaluation source markers and their explicit display prefixes are
  normalized away because they distinguish read-only UI context from stored
  confirmation; they do not change the underlying hierarchy decision.
* ``decision``: SHA-256 of every replay frame, every enumerated qualification
  snapshot, and every trade plan for a QUALIFIED setup at the boundary, using the
  objects' full ``repr``. Plan and frame content is therefore pinned even where
  the dashboard does not surface it.

Run as a script to (re)write the fixtures, e.g. from a baseline checkout::

    PYTHONPATH=<checkout>/src python tests/decision_digests.py <fixtures_dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from test_decision_invariance import EPOCH, synthetic_series
from web_fixtures import insert_candles, make_settings, migrated_engine

from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.parameters import QualificationParameters
from trading_assistant.setup_qualification.service import bounded_replay_start
from trading_assistant.trade_planning.planner import plan_trade
from trading_assistant.web.dashboard_service import DashboardService
from trading_assistant.web.state import AppState

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "decision_digests"
DASHBOARD_CANDLES = 500
DECISION_CANDLES = 1000


def boundary_offsets(candles: int) -> list[int]:
    """Four fixed boundaries: mid-history, three-quarter, 100 closes before the end, and the end."""
    return [candles // 2, (3 * candles) // 4, candles - 100, candles]


def build_state(directory: Path, candles: int) -> AppState:
    directory.mkdir(parents=True, exist_ok=True)
    engine, url = migrated_engine(directory, name="decision.sqlite3")
    insert_candles(engine, synthetic_series(candles))
    return AppState(
        engine,
        settings=make_settings(url),
        clock=lambda: EPOCH + timedelta(hours=candles),
    )


def _strip_audit(value):
    if isinstance(value, dict):
        live_evaluation = value.get("evaluation_source") == "live_read_only"
        stripped = {}
        presentation_keys = {
            "generated_at",
            "signal_provenance",
            "evaluation_source",
            "decision_kind",
            "stored_decision_for_paper_confirmation",
            "evaluation_note",
            # The reference-provenance verdict is a read-only statement about the
            # stored record's depth. It is excluded like the other provenance
            # labels, while ``plan``/``planning`` stay pinned: if the gate ever
            # withheld a plan at these boundaries, this digest changes and the
            # regression test says so.
            "reference_provenance",
        }
        for key, item in value.items():
            if key in presentation_keys:
                continue
            if live_evaluation and key == "overall" and isinstance(item, str):
                if item == "LIVE EVALUATION COMPLETE — NOT STORED CONFIRMATION":
                    item = "PLAN READY — HIERARCHY COMPLETE"
                elif item.startswith("LIVE EVALUATION · "):
                    item = item.removeprefix("LIVE EVALUATION · ")
            elif live_evaluation and key == "decision_label" and isinstance(item, str):
                if item == "Live hierarchy evaluation complete":
                    item = "Plan ready — hierarchy complete"
                elif item.startswith("Live evaluation: "):
                    item = item.removeprefix("Live evaluation: ")
            stripped[key] = _strip_audit(item)
        return stripped
    if isinstance(value, list):
        return [_strip_audit(v) for v in value]
    return value


def dashboard_digest(service: DashboardService, as_of) -> dict[str, object]:
    payload = service.dashboard(symbol="BTC/USDT", timeframe="1h", as_of=as_of)
    text = json.dumps(_strip_audit(payload), sort_keys=True, default=str)
    assert " at 0x" not in text, "non-deterministic repr in dashboard payload"
    return {"sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text)}


def decision_digest(state: AppState, as_of) -> dict[str, object]:
    parameters = QualificationParameters()
    frames = state.qualification.build_frames(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=as_of,
        parameters=parameters,
        start_at=bounded_replay_start(as_of=as_of, timeframe="1h", parameters=parameters),
    )
    snapshots = enumerate_qualifications(frames, as_of=as_of, parameters=parameters)
    last = snapshots[-1]
    plans = []
    for setup in last.setups:
        if setup.state.value == "QUALIFIED":
            plans.append(repr(plan_trade(snapshot=last, frame=frames[-1], setup_id=setup.id)))
    sections = {
        "frames": [repr(frame) for frame in frames],
        "snapshots": [repr(snapshot) for snapshot in snapshots],
        "plans": plans,
    }
    digest = hashlib.sha256()
    for name in ("frames", "snapshots", "plans"):
        for item in sections[name]:
            assert " at 0x" not in item, "non-deterministic repr in decision output"
            digest.update(name.encode() + b"\x00" + item.encode() + b"\x00")
    return {
        "sha256": digest.hexdigest(),
        "frames": len(frames),
        "setups": len(last.setups),
        "qualified": sum(1 for s in last.setups if s.state.value == "QUALIFIED"),
        "plans": len(plans),
        "state": last.state.value,
    }


def dashboard_fixture(directory: Path) -> dict[str, object]:
    state = build_state(directory, DASHBOARD_CANDLES)
    service = DashboardService(state)
    boundaries = {}
    for offset in boundary_offsets(DASHBOARD_CANDLES):
        boundaries[str(offset)] = dashboard_digest(service, EPOCH + timedelta(hours=offset))
    return {"candles": DASHBOARD_CANDLES, "boundaries": boundaries}


def decision_fixture(directory: Path) -> dict[str, object]:
    state = build_state(directory, DECISION_CANDLES)
    boundaries = {}
    for offset in boundary_offsets(DECISION_CANDLES):
        boundaries[str(offset)] = decision_digest(state, EPOCH + timedelta(hours=offset))
    return {"candles": DECISION_CANDLES, "boundaries": boundaries}


def main(argv: list[str]) -> None:
    target = Path(argv[1]) if len(argv) > 1 else FIXTURE_DIR
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        dash = dashboard_fixture(Path(tmp) / "dash")
        (target / "dashboard_n500.json").write_text(json.dumps(dash, indent=1, sort_keys=True) + "\n")
    with tempfile.TemporaryDirectory() as tmp:
        decision = decision_fixture(Path(tmp) / "decision")
        (target / "decision_n1000.json").write_text(
            json.dumps(decision, indent=1, sort_keys=True) + "\n"
        )
    print("wrote fixtures to", target)


if __name__ == "__main__":
    main(sys.argv)
