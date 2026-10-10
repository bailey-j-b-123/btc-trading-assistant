"""Read-only chronological qualification replay over Step 3/4 outputs.

Never reconstruct historical context from a latest snapshot. Each frame must be
an actual as-of snapshot at a base-timeframe close. Missing frames terminate
live candidates, rather than guessing the path between two observations.
"""

from collections.abc import Iterable
from dataclasses import fields, is_dataclass, replace
from datetime import datetime

from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    require_utc_datetime,
)
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.analysis import TimeframeStructureAnalysis
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.pattern_liquidity.events import Breakout, FailedBreakout, Sweep
from trading_assistant.setup_qualification.families import (
    Seed,
    direction_for,
    family_for,
    reference_for,
)
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
    RuleOutcome,
    SetupFamily,
    SetupResult,
    SetupState,
)
from trading_assistant.setup_qualification.parameters import (
    RULES_VERSION,
    QualificationParameters,
    fingerprint,
)
from trading_assistant.setup_qualification.rules import evaluate_rules, rule


def _validate_available(
    value: object,
    now: datetime,
    instrument: tuple[str, str, str],
    _seen: set[int] | None = None,
) -> None:
    """Reject inconsistent hand-built inputs, including nested future references.

    Each shared dataclass or tuple node is checked once per call: the checks
    depend only on (node, now, instrument), so revisiting an already-validated
    object cannot change the outcome. ``_seen`` holds ids of nodes that are
    alive for the whole call (they are reachable from the root).
    """
    if _seen is None:
        _seen = set()
    if (is_dataclass(value) and not isinstance(value, type)) or isinstance(value, tuple):
        if id(value) in _seen:
            return
        _seen.add(id(value))
    if isinstance(value, Candle):
        if (value.exchange, value.symbol, value.timeframe) != instrument:
            raise ValueError("source candle instrument does not match frame")
        if value.timestamp + interval_for_timeframe(value.timeframe) > now:
            raise ValueError("source candle was not closed by frame as_of")
    if isinstance(value, datetime):
        # Datetimes nested in tuples (touch/source timestamp sequences) must
        # meet the same no-future rule as direct dataclass fields.
        if require_utc_datetime(value, field_name="source timestamp") > now:
            raise ValueError("source timestamp is after frame as_of")
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            item = getattr(value, field.name)
            if (
                isinstance(item, datetime)
                and require_utc_datetime(item, field_name=field.name) > now
            ):
                raise ValueError(f"source {field.name} is after frame as_of")
            _validate_available(item, now, instrument, _seen)
    elif isinstance(value, tuple):
        for item in value:
            _validate_available(item, now, instrument, _seen)


def _validate_context_timestamps(context: TimeframeStructureAnalysis) -> None:
    # Never accept future/stale metrics disguised by a current outer as_of.
    if (
        context.volume.latest_candle_timestamp != context.window_end_timestamp
        or context.volatility.latest_candle_timestamp != context.window_end_timestamp
        or context.trend.as_of != context.as_of
        or context.range.as_of != context.as_of
        or context.swings.as_of != context.as_of
        or context.levels.as_of != context.as_of
    ):
        raise ValueError("context metrics must share their source window/as_of")


def _validate_frame(frame: QualificationFrame, p: QualificationParameters) -> None:
    s = frame.patterns
    now = require_utc_datetime(s.as_of, field_name="frame.as_of")
    interval = interval_for_timeframe(s.timeframe)
    expected = latest_closed_candle_open_time(now, s.timeframe)
    if expected + interval != now:
        raise ValueError("qualification frames must be at base candle-close boundaries")
    if (
        s.structure.as_of != now
        or s.completeness.expected_latest_closed_open_time != expected
    ):
        raise ValueError("structure/completeness must describe the frame as_of")
    _validate_available(s, now, (s.exchange, s.symbol, s.timeframe))
    _validate_context_timestamps(s.structure)
    if (
        s.structure.window_end_timestamp is not None
        and s.structure.window_end_timestamp > expected
    ):
        raise ValueError("structure contains an unclosed candle")
    seen = set()
    for tf in p.higher_timeframes:
        if interval_for_timeframe(tf) <= interval:
            raise ValueError("higher timeframes must be longer than the base timeframe")
    for higher in frame.higher_timeframes:
        if higher.timeframe in seen or higher.timeframe not in p.higher_timeframes:
            raise ValueError("higher-timeframe inputs must be unique and configured")
        seen.add(higher.timeframe)
        expected_higher = latest_closed_candle_open_time(now, higher.timeframe)
        if higher.completeness.expected_latest_closed_open_time != expected_higher:
            raise ValueError("higher-timeframe completeness is not as-of aligned")
        analysis = higher.analysis
        if analysis:
            _validate_context_timestamps(analysis)
            if analysis.as_of != now or analysis.parameters != s.structure.parameters:
                raise ValueError(
                    "higher-timeframe analysis must share as_of and structure parameters"
                )
            if (
                analysis.window_end_timestamp is not None
                and analysis.window_end_timestamp > expected_higher
            ):
                raise ValueError("higher-timeframe candle was not closed")
        _validate_available(higher, now, (s.exchange, s.symbol, higher.timeframe))


def _terminal_reason(
    seed: Seed, frame: QualificationFrame, p: QualificationParameters, replay_gap: bool
) -> str | None:
    s = frame.patterns
    interval = interval_for_timeframe(s.timeframe)
    ref = reference_for(seed)
    definition = family_for(seed)
    if replay_gap:
        return "missing_replay_frames"
    if s.structure.window_end_timestamp != s.as_of - interval:
        return "missing_current_candle"
    if any(g.end >= seed.known_at for g in s.completeness.gaps):
        return "source_candle_gap"
    # Age zero is the seed close. Exactly max_bars remains eligible.
    if s.as_of - seed.known_at > getattr(p, definition.expiry_parameter) * interval:
        return "maximum_bars_elapsed"
    if isinstance(seed, Breakout):
        if any(e.breakout.id == seed.id for e in s.failed_breakouts):
            return "failed_breakout"
        if any(e.breakout.id == seed.id and e.state == "failed" for e in s.retests):
            return "failed_retest"
    close = s.structure.volatility.latest_close
    if close is not None:
        if (direction_for(seed) == "bullish" and close < ref.band_low) or (
            direction_for(seed) == "bearish" and close > ref.band_high
        ):
            return "opposite_close_through_reference"
        if (
            definition.family == SetupFamily.RANGE_REVERSAL
            and ref.range
            and not ref.range.range_low <= close <= ref.range.range_high
        ):
            return "close_outside_frozen_range"
    return None


def enumerate_qualifications(
    frames: Iterable[QualificationFrame],
    *,
    as_of: datetime,
    parameters: QualificationParameters | None = None,
    known_since: datetime | None = None,
) -> tuple[QualificationSnapshot, ...]:
    """Replay in input order; filter output only AFTER lifecycle warm-up.

    Inputs after as_of are ignored before validation or fingerprinting. A frame
    exactly at as_of is required. Start at/before desired seed confirmations;
    old catalog events never create retrospective candidates. Gaps between
    frames are explicit terminal invalidations, not invented intermediate states.
    """
    now = require_utc_datetime(as_of, field_name="as_of")
    if known_since is not None:
        known_since = require_utc_datetime(known_since, field_name="known_since")
        if known_since > now:
            raise ValueError("known_since must not be after as_of")
    p = parameters or QualificationParameters()
    history = []
    candidates: dict[str, tuple[Seed, SetupResult]] = {}
    source_events = {}
    instrument = None
    config = None
    previous_time = None
    for frame in frames:
        s = frame.patterns
        require_utc_datetime(s.as_of, field_name="frame.as_of")
        if s.as_of > now:
            continue
        _validate_frame(frame, p)
        current_instrument = (s.exchange, s.symbol, s.timeframe)
        if instrument is not None and current_instrument != instrument:
            raise ValueError("replay cannot mix instruments or base timeframes")
        instrument = current_instrument
        current_config = fingerprint(p, s.parameters, s.structure.parameters)
        if config is not None and config != current_config:
            raise ValueError("source parameters cannot change within a replay")
        config = current_config
        if previous_time is not None and s.as_of <= previous_time:
            raise ValueError("frames must be strictly chronological and unique")
        interval = interval_for_timeframe(s.timeframe)
        replay_gap = previous_time is not None and s.as_of - previous_time != interval
        previous_time = s.as_of
        events = s.events()
        current_events = {event.id: event for event in events}
        if len(current_events) != len(events):
            raise ValueError("duplicate source event IDs")
        if any(
            current_events.get(key) != value for key, value in source_events.items()
        ):
            raise ValueError(
                "source event catalog must be append-only and facts immutable"
            )
        source_events = current_events
        timeframes = (s.timeframe, *p.higher_timeframes)
        fresh = s.structure.window_end_timestamp == s.as_of - interval
        for event in events:
            if (
                not isinstance(event, (Breakout, FailedBreakout, Sweep))
                or event.known_at != s.as_of
                or not fresh
            ):
                continue
            setup_id = fingerprint(
                instrument, config, family_for(event).family, event.id
            )
            candidates.setdefault(
                setup_id,
                (
                    event,
                    SetupResult(
                        setup_id,
                        family_for(event).family,
                        direction_for(event),
                        SetupState.WATCH,
                        event.id,
                        reference_for(event).id,
                        event.known_at,
                        s.as_of,
                        timeframes,
                        (),
                    ),
                ),
            )
        for setup_id, (seed, prior) in tuple(candidates.items()):
            if prior.terminal_reason:
                # Terminal evidence remains the evidence at ended_at, not relabeled facts.
                candidates[setup_id] = (seed, replace(prior, as_of=s.as_of))
                continue
            reason = _terminal_reason(
                seed, frame, p, bool(replay_gap and seed.known_at < s.as_of)
            )
            rules = evaluate_rules(seed, frame, p)
            lifecycle = rule(
                "lifecycle",
                reason is None,
                (
                    f"{reason}; age_bars={(s.as_of - seed.known_at) // interval}; "
                    f"max_bars={getattr(p, family_for(seed).expiry_parameter)}; "
                    f"window_end={s.structure.window_end_timestamp}; "
                    f"gaps={tuple((g.start, g.end) for g in s.completeness.gaps)}"
                    if reason
                    else "candidate is contiguous, unexpired and not invalidated"
                ),
                timeframe=s.timeframe,
                source=seed.id,
                confirmed=s.as_of,
                category="lifecycle",
            )
            rules = (*rules, lifecycle)
            state = (
                SetupState.NO_SETUP
                if reason
                else SetupState.QUALIFIED
                if all(
                    (not r.required or r.outcome == RuleOutcome.PASS) and not r.veto
                    for r in rules
                )
                else SetupState.WATCH
            )
            candidates[setup_id] = (
                seed,
                replace(
                    prior,
                    state=state,
                    as_of=s.as_of,
                    rules=rules,
                    terminal_reason=reason,
                    ended_at=s.as_of if reason else None,
                ),
            )
        setups = tuple(
            sorted(
                (result for _, result in candidates.values()),
                key=lambda r: (r.created_at, r.id),
            )
        )
        state = (
            SetupState.QUALIFIED
            if any(r.state == SetupState.QUALIFIED for r in setups)
            else SetupState.WATCH
            if any(r.state == SetupState.WATCH for r in setups)
            else SetupState.NO_SETUP
        )
        reasons = []
        if not setups:
            reasons.append("no_seed_confirmed_in_replayed_frames")
        elif state == SetupState.NO_SETUP:
            reasons.append("all_candidates_invalidated_or_expired")
        elif state == SetupState.WATCH:
            reasons.append("candidates_have_failed_or_pending_rules_or_vetoes")
        else:
            reasons.append(
                "at_least_one_candidate_satisfies_all_required_rules_without_veto"
            )
        if replay_gap:
            reasons.append("missing_replay_frames")
        if not fresh:
            reasons.append("missing_current_candle")
        if not s.completeness.complete:
            reasons.append("source_history_incomplete")
        snapshot = QualificationSnapshot(
            s.exchange,
            s.symbol,
            s.timeframe,
            s.as_of,
            state,
            setups,
            tuple(reasons),
            config,
            RULES_VERSION,
            timeframes,
            "incomplete"
            if replay_gap or not fresh or not s.completeness.complete
            else "evaluated",
        )
        if known_since is None or s.as_of >= known_since:
            history.append(snapshot)
    if previous_time != now:
        raise ValueError(
            "replay requires a frame exactly at as_of; no stale-context extrapolation"
        )
    return tuple(history)


def qualify(
    frames: Iterable[QualificationFrame],
    *,
    as_of: datetime,
    parameters: QualificationParameters | None = None,
) -> QualificationSnapshot:
    """The final immutable snapshot from the same chronological replay contract."""
    return enumerate_qualifications(frames, as_of=as_of, parameters=parameters)[-1]
