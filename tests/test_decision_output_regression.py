"""Decision-bearing outputs must not change under read-only presentation work.

Expected digests in ``tests/fixtures/decision_digests`` were generated from commit
4656b43 (the pre-optimisation decision path) with ``tests/decision_digests.py``.
Any difference in plans, setups, frames, overlays, chart evidence or the rest of the
decision-bearing dashboard payload at these fixed historical boundaries fails the
test. Provenance labels (signal provenance and the reference-provenance verdict),
and the live-evaluation source note are excluded because those features
intentionally add them without changing trading decisions. Inputs
are SYNTHETIC and deterministic. This proves equivalence to the earlier code on
these inputs; it does not prove live correctness.

The fixtures were regenerated once after that baseline, deliberately and with the
version bump in ``trade-planning`` to ``trade-planning-v3``: every plan identity
and configuration fingerprint now includes the new ``cost_assumptions`` and
``min_cost_adjusted_r_multiple`` inputs, every plan records the new
``economic_viability`` rule (PENDING under the v2-equivalent default parameters),
and the dashboard/forward default (an explicit all-zero cost scenario) now
withholds plans as ``economic_viability_unverifiable`` instead of certifying them.
Those are the intended, versioned differences; anything else still fails here.
Regenerate with ``python tests/decision_digests.py`` after any further deliberate
decision-path change.
"""

from __future__ import annotations

import json
from datetime import timedelta

from decision_digests import (
    DASHBOARD_CANDLES,
    DECISION_CANDLES,
    FIXTURE_DIR,
    boundary_offsets,
    build_state,
    dashboard_digest,
    decision_digest,
)
from test_decision_invariance import EPOCH

from trading_assistant.web.dashboard_service import DashboardService


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def test_dashboard_payloads_match_pre_optimisation_digests(tmp_path):
    expected = _fixture("dashboard_n500.json")
    assert expected["candles"] == DASHBOARD_CANDLES
    service = DashboardService(build_state(tmp_path / "dash", DASHBOARD_CANDLES))
    actual = {
        str(offset): dashboard_digest(service, EPOCH + timedelta(hours=offset))
        for offset in boundary_offsets(DASHBOARD_CANDLES)
    }
    assert actual == expected["boundaries"]


def test_decision_outputs_match_pre_optimisation_digests(tmp_path):
    expected = _fixture("decision_n1000.json")
    assert expected["candles"] == DECISION_CANDLES
    state = build_state(tmp_path / "decision", DECISION_CANDLES)
    actual = {
        str(offset): decision_digest(state, EPOCH + timedelta(hours=offset))
        for offset in boundary_offsets(DECISION_CANDLES)
    }
    assert actual == expected["boundaries"]
    # Coverage guard: the pinned boundaries must include real plans, not only empty states.
    assert sum(item["plans"] for item in expected["boundaries"].values()) > 0
    assert any(item["state"] == "QUALIFIED" for item in expected["boundaries"].values())
