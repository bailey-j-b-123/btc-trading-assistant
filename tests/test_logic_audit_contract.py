"""Read-only custody and unmodified target policy controls."""

from hashlib import sha256
from datetime import timedelta
from decimal import Decimal as D
from trading_assistant.pattern_liquidity.events import Reference
import pytest
from test_trade_planning import qualified
from trading_assistant.trade_planning.levels import (
    structural_target_catalog,
    structural_target_candidates,
)
from trading_assistant.trade_planning.parameters import PlanningParameters
from trading_assistant.strategy_lab.logic_audit import readonly_lab, run_audit
from trading_assistant.strategy_lab.store import Store


def test_full_catalog_retains_more_than_two_without_changing_nearest_cap(monkeypatch):
    q, frame, setup = qualified(False)
    p = PlanningParameters()
    entry = frame.patterns.structure.volatility.latest_close
    # Explicit synthetic catalog fixture: three prices, one exact duplicate,
    # one unavailable future fact. The real qualified fixture has only ONE
    # trade-side level, so it cannot exercise a two-target cap by itself.
    refs = tuple(
        Reference(label, "swing_high", entry + D(i), entry + D(i), q.as_of, ())
        for i, label in ((1, "a"), (2, "b"), (3, "c"), (1, "duplicate"))
    )
    refs += (
        Reference(
            "future",
            "swing_high",
            entry + D(".5"),
            entry + D(".5"),
            q.as_of + timedelta(hours=1),
            (),
        ),
    )
    monkeypatch.setattr(
        "trading_assistant.trade_planning.levels.references", lambda *args: refs
    )
    all_levels, excluded = structural_target_catalog(
        frame.patterns, entry, setup.direction, p, q.as_of
    )
    selected, reasons = structural_target_candidates(
        frame.patterns, entry, setup.direction, p, q.as_of
    )
    assert selected == all_levels[:2] and len(all_levels) == 3
    assert len({c.value for c in all_levels}) == len(all_levels)
    assert all(c.confirmed_at <= q.as_of for c in all_levels)
    assert reasons[: len(excluded)] == excluded
    assert any("duplicate" in reason for reason in excluded)
    assert any("confirmed after" in reason for reason in excluded)
    assert any("beyond max_structural_targets 2" in reason for reason in reasons)


def test_audit_refuses_source_as_destination_without_mutating_it(tmp_path):
    root = tmp_path / "lab"
    s = Store(root)
    s.close()
    database = root / "research.sqlite3"
    before = sha256(database.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="never write"):
        run_audit(root, root)
    assert sha256(database.read_bytes()).hexdigest() == before


def test_unmarked_and_redirected_source_is_not_opened(tmp_path):
    root = tmp_path / "lab"
    s = Store(root)
    s.close()
    redirect = tmp_path / "redirect"
    redirect.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="ordinary"):
        readonly_lab(redirect)
    (root / "LAB-ONLY.txt").write_text("not-a-lab")
    with pytest.raises(ValueError, match="marked"):
        readonly_lab(root)
