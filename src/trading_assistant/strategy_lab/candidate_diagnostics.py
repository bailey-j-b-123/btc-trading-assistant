"""Opt-in causal research evidence, never a signal producer or trading policy.

Candidates come from the actual qualification replay before setup selection.
Unselected lower-layer assessments are observations, not admitted trades.
Facts are content addressed; observations reference shared hourly basis facts.
All writes join the existing runner's boundary/checkpoint transaction.
"""

from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path

from trading_assistant.costs import FrictionAssumptions
from trading_assistant.market_structure.numeric import tolerance_band
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.multi_timeframe.alignment import evaluate_alignment
from trading_assistant.multi_timeframe.confirmation import evaluate_confirmation
from trading_assistant.multi_timeframe.decision import gate_decision
from trading_assistant.multi_timeframe.engine import _window_candles
from trading_assistant.multi_timeframe.execution import evaluate_execution
from trading_assistant.multi_timeframe.setup import build_setup_snapshot
from trading_assistant.pattern_liquidity.events import FailedBreakout
from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.families import reference_for
from trading_assistant.setup_qualification.models import SetupState
from trading_assistant.trade_planning.economics import assess
from trading_assistant.trade_planning.levels import find_seed, structural_target_catalog
from trading_assistant.trade_planning.parameters import PlanningParameters
from trading_assistant.trade_planning.planner import plan_trade
from .models import canonical, decimal_context, identity

VERSION = "candidate-diagnostics-v1"


def retest_geometry(event):
    """Exact event linkage and candle intersection, including later held states."""
    ref, candle = event.breakout.reference, event.candle
    level = ref.band_high if event.breakout.direction == "bullish" else ref.band_low
    tolerance = tolerance_band(level, event.parameters.retest_tolerance_pct)
    distance = max(ref.band_low - candle.high, candle.low - ref.band_high, D(0))
    physical = candle.low <= ref.band_high and candle.high >= ref.band_low
    tolerated = candle.low <= ref.band_high + tolerance and candle.high >= ref.band_low - tolerance
    return to_jsonable(dict(
        event_id=event.id, breakout_id=event.breakout.id, reference_id=ref.id,
        timeframe=candle.timeframe, candle_open=candle.timestamp,
        known_at=event.known_at, state=event.state, candle_high=candle.high,
        candle_low=candle.low, reference_low=ref.band_low, reference_high=ref.band_high,
        reference_known_at=ref.known_at, tolerance=tolerance, nearest_distance=distance,
        recorded_distance=event.distance_from_band, physical_contact=physical,
        within_tolerance=tolerated,
        contact="physical" if physical else "tolerance_only" if tolerated else "no_current_contact",
        note="A later held/failed state is not the original observed contact.",
    ))


def targets(frame, setup, plan):
    """Uncapped existing catalog; intervening levels are evidence, not a veto."""
    entry = plan.entry.value
    indicative = entry is None
    if indicative:
        entry = frame.patterns.structure.volatility.latest_close
    if entry is None:
        return {"entry": None, "reason": "no_closed_entry_price", "levels": []}
    parameters = PlanningParameters()
    catalog, exclusions = structural_target_catalog(
        frame.patterns, entry, setup.direction, parameters, frame.patterns.as_of
    )
    risk = plan.risk_per_unit
    levels = []
    for index, target in enumerate(catalog):
        gross = abs(target.value - entry)
        economics = {}
        if risk is not None and risk > 0 and plan.stop.value is not None:
            for label, fee, impact in (("base", D(10), D(4)), ("stress", D(20), D(11))):
                costs = FrictionAssumptions(fee_bps=fee, entry_slippage_bps=impact, exit_slippage_bps=impact)
                result = assess(entry=entry, stop=plan.stop.value, target=target.value,
                                direction=setup.direction, costs=costs)
                economics[label] = to_jsonable(result)
        levels.append(dict(
            **to_jsonable(target), rank=index+1, included_by_existing_cap=index < parameters.max_structural_targets,
            gross_r=str(gross/risk) if risk is not None and risk > 0 else None,
            cost_scenarios=economics,
            # The exact obstacles are levels[:intervening_prefix_length].
            # Repeating every prefix makes large catalogs quadratic in size.
            intervening_prefix_length=index,
            obstacle_meaning="causally known nearer structural levels; not predicted barriers or fill probabilities",
        ))
    atr = frame.patterns.structure.volatility.atr
    ordinary = frame.patterns.structure.volatility
    return dict(entry=str(entry), indicative_only=indicative, planning_as_of=frame.patterns.as_of.isoformat(),
                levels=levels, excluded=list(exclusions), cap=parameters.max_structural_targets,
                risk_distance=str(risk) if risk is not None else None,
                risk_bps=str(risk/entry*10000) if risk is not None else None,
                risk_to_atr=str(risk/atr) if risk is not None and atr is not None and atr > 0 else None,
                volatility=to_jsonable(ordinary))


class CandidateDiagnostics:
    """A versioned sidecar scoped to one new research cohort; no backfill API."""

    def __init__(self, store, run, definition):
        self.store, self.run = store, run
        self.definition = dict(version=VERSION, brain_run=run, source=definition,
                               diagnostic_source_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
        self.id = identity(self.definition)
        self.cache = {}
        store.db.executescript("""
          CREATE TABLE IF NOT EXISTS candidate_diagnostic_runs(
            id TEXT PRIMARY KEY,brain_run TEXT NOT NULL,definition TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS candidate_diagnostic_facts(
            diagnostic TEXT NOT NULL REFERENCES candidate_diagnostic_runs(id),
            id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(diagnostic,id));
          CREATE TABLE IF NOT EXISTS candidate_diagnostic_observations(
            diagnostic TEXT NOT NULL REFERENCES candidate_diagnostic_runs(id),
            setup TEXT NOT NULL,boundary TEXT NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(diagnostic,setup,boundary));
          CREATE TABLE IF NOT EXISTS candidate_diagnostic_coverage(
            diagnostic TEXT NOT NULL REFERENCES candidate_diagnostic_runs(id),
            boundary TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(diagnostic,boundary));
        """)
        store.db.execute("INSERT OR IGNORE INTO candidate_diagnostic_runs VALUES(?,?,?)",
                         (self.id, run, canonical(self.definition)))

    @decimal_context
    def capture(self, *, snapshot, frames, sources, service, before, after, produced):
        """Return staged records; caller commits these with the existing boundary."""
        at = snapshot.decision_time
        boundary = snapshot.setup.boundary_close
        facts, observations = {}, []
        if not frames or frames[-1].patterns.as_of != boundary or not snapshot.setup.available:
            return facts, observations, dict(available=False, reason="qualification_source_unavailable",
                                            as_of=at.isoformat(), candidates=None)
        if boundary not in self.cache:
            self.cache.clear()
            q = enumerate_qualifications(frames, as_of=boundary, parameters=service.qualification_parameters)[-1]
            frame = frames[-1]
            bases = {}
            terminal_seen = {r[0] for r in self.store.db.execute(
                "SELECT DISTINCT json_extract(payload,'$.setup.id') FROM candidate_diagnostic_facts "
                "WHERE diagnostic=? AND kind='candidate_basis' "
                "AND json_extract(payload,'$.setup.terminal_reason') IS NOT NULL", (self.id,)
            )}
            for candidate in q.setups:
                if candidate.terminal_reason and candidate.id in terminal_seen:
                    continue
                if candidate.created_at > boundary:
                    raise ValueError("future candidate in diagnostics")
                seed = find_seed(frame.patterns, candidate.seed_event_id)
                if seed is None:
                    raise ValueError("candidate source seed missing")
                ref = reference_for(seed)
                breakout_id = seed.breakout.id if isinstance(seed, FailedBreakout) else seed.id
                retests = [retest_geometry(e) for e in frame.patterns.retests
                           if e.breakout.id == breakout_id and e.known_at <= boundary]
                plan = plan_trade(snapshot=q, frame=frame, setup_id=candidate.id)
                basis = to_jsonable(dict(
                    setup=candidate, instrument=(q.exchange, q.symbol, q.timeframe),
                    config=q.config_fingerprint, rules_version=q.rules_version,
                    captured_at=boundary, seed=seed, reference=ref,
                    retests=retests, planning=plan.to_json_dict(),
                    target_diagnostics=(targets(frame, candidate, plan) if not candidate.terminal_reason else dict(
                        entry=None, levels=[], risk_distance=None, risk_bps=None, risk_to_atr=None,
                        reason="terminal candidate; no current planning or retrospective target reconstruction")),
                    market_structure=frame.patterns.structure.trend,
                    source_status=q.status,
                    scope="actual qualification candidates before deterministic hierarchy selection",
                    plan_scope="diagnostic unchanged planner assessment, not an admitted order",
                ))
                # The seed includes only immutable recorded source evidence.
                bid = identity((VERSION, "basis", basis))
                bases[candidate.id] = (candidate, bid, basis)
            self.cache[boundary] = (q, frame, bases)
        q, frame, bases = self.cache[boundary]
        for candidate, bid, basis in bases.values():
            facts[bid] = ("candidate_basis", basis)
            if candidate.terminal_reason:
                # Preserve terminal facts once per setup/hour; no future lower
                # candles are evaluated as though a terminal setup were active.
                continue
            setup = build_setup_snapshot(q, selected=candidate, ended=None, frame=frame,
                                         boundary=snapshot.boundary.boundary_for(q.timeframe),
                                         window_status=snapshot.setup.snapshot_status)
            cb = snapshot.boundary.boundary_for(service.hierarchy.confirmation.timeframe)
            eb = snapshot.boundary.boundary_for(service.hierarchy.execution.timeframe)
            cc, cm = _window_candles(sources[cb.timeframe], timeframe=cb.timeframe,
                                    window_start=candidate.created_at, decision_time=at)
            ec, em = _window_candles(sources[eb.timeframe], timeframe=eb.timeframe,
                                    window_start=candidate.created_at, decision_time=at)
            confirmation = evaluate_confirmation(setup, cc, boundary=cb, missing_candle_count=cm,
                                                  structure_parameters=service.structure_parameters)
            execution = evaluate_execution(setup, confirmation, ec, boundary=eb, missing_candle_count=em)
            alignment = evaluate_alignment(snapshot.context, setup)
            verdict = gate_decision(context=snapshot.context, setup=setup, confirmation=confirmation,
                                    execution=execution, alignment=alignment)
            selected = candidate.id == snapshot.setup.setup_id
            if selected and (confirmation != snapshot.confirmation or execution != snapshot.execution
                             or verdict[0] != snapshot.decision or alignment != snapshot.alignment):
                raise ValueError("diagnostics disagree with actual selected hierarchy")
            observations.append(to_jsonable(dict(
                setup_id=candidate.id, basis_id=bid, as_of=at, selected=selected,
                context=snapshot.context, confirmation=confirmation, execution=execution,
                alignment=alignment, decision=verdict[0], reasons=verdict[1], status=verdict[2],
                waiting_for=verdict[4], invalidated_if=verdict[5],
                diagnostic_only=not selected,
                slots={p: dict(position_before=bool(s["position"]), pending_before=bool(s["pending"]),
                               position_after_tracking=bool(after[p]["position"]),
                               filled_count_before=s["filled"], filled_count_after=after[p]["filled"])
                       for p, s in before.items()},
            )))
        for policy, event in produced:
            if event["kind"] == "equity_mark":
                continue
            payload = dict(policy=policy, at=at.isoformat(), event=event)
            # A closing trade is linked by its exact pending/position signal,
            # not by a price/time similarity join.
            pending = before[policy].get("pending") or {}
            position = before[policy].get("position") or {}
            evidence = event.get("evidence") or event.get("signal", {}).get("evidence") or {}
            if not evidence:
                source = next((s for s in (pending, position)
                               if s.get("signal_id") == event.get("signal_id")), {})
                evidence = source.get("evidence", {})
            payload["setup_id"] = evidence.get("setup_id")
            facts[identity((VERSION, "actual_event", payload))] = ("actual_execution_event", payload)
        coverage = dict(available=True, as_of=at.isoformat(), qualification_as_of=boundary.isoformat(),
                        candidates=len(q.setups), active=sum(not s.terminal_reason for s in q.setups),
                        qualified=sum(s.state is SetupState.QUALIFIED for s in q.setups),
                        qualification_status=q.status, replay_start=frames[0].patterns.as_of.isoformat(),
                        limitation="Bounded source history; no claim to recover seeds before the replay window.")
        return facts, observations, coverage

    def commit(self, staged, boundary):
        if not self.store.db.in_transaction:
            raise RuntimeError("diagnostics must join the boundary transaction")
        facts, observations, coverage = staged
        for key, (kind, payload) in facts.items():
            self.store.db.execute("INSERT OR IGNORE INTO candidate_diagnostic_facts VALUES(?,?,?,?)",
                                  (self.id, key, kind, canonical(payload)))
        for payload in observations:
            self.store.db.execute("INSERT INTO candidate_diagnostic_observations VALUES(?,?,?,?)",
                                  (self.id, payload["setup_id"], boundary, canonical(payload)))
        self.store.db.execute("INSERT INTO candidate_diagnostic_coverage VALUES(?,?,?)",
                              (self.id, boundary, canonical(coverage)))
