"""Signal provenance for 1H paper candidates and stored hierarchy decisions.

The Step 12 forward engine independently records 1H paper plans. A Step 13
hierarchy evaluation is confirmation evidence only when BRAIN stored the exact
same four-timeframe decision boundary before that paper plan was recorded.
This module is read-only: it joins those immutable records for API presentation
and never changes a plan, outcome, hierarchy observation, or trading rule.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from trading_assistant.forward_testing.models import PaperPlan
from trading_assistant.market_data.timeframes import latest_closed_candle_open_time
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.multi_timeframe.models import HierarchyObservation
from trading_assistant.multi_timeframe.repository import HierarchyLedgerRepository

_EXPECTED_LAYERS = (
    ("context", "4h"),
    ("setup", "1h"),
    ("confirmation", "15m"),
    ("execution", "5m"),
)

_STATUS_LABELS = {
    "confirmed": "FOUR-TIMEFRAME CONFIRMED",
    "not_confirmed": "NOT FOUR-TIMEFRAME CONFIRMED",
    "unavailable": "FOUR-TIMEFRAME CONFIRMATION UNAVAILABLE",
    "incomplete": "FOUR-TIMEFRAME EVIDENCE INCOMPLETE",
    "stale": "FOUR-TIMEFRAME EVIDENCE STALE",
    "conflicting": "FOUR-TIMEFRAME EVIDENCE CONFLICTING",
    "not_applicable": "FOUR-TIMEFRAME STATUS NOT APPLICABLE",
}


class _InvalidStoredEvidence(ValueError):
    """A stored hierarchy row is internally inconsistent or malformed."""


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("signal provenance timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise _InvalidStoredEvidence(f"stored {field} timestamp has no UTC offset")
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value:
        raise _InvalidStoredEvidence(f"stored {field} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _InvalidStoredEvidence(f"stored {field} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise _InvalidStoredEvidence(f"stored {field} timestamp has no UTC offset")
    return parsed.astimezone(UTC)


def _json_object(value: str, *, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _InvalidStoredEvidence(f"stored {field} JSON is invalid") from exc
    if not isinstance(parsed, dict):
        raise _InvalidStoredEvidence(f"stored {field} evidence is not an object")
    return parsed


def _nonnegative_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _InvalidStoredEvidence(f"stored {field} count is missing or invalid")
    return value


def _candidate(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    setup_id: str | None,
    plan_as_of: datetime | None,
    plan_recorded_at: datetime | None,
    paper_plan_id: str | None,
    source: str,
) -> dict[str, Any]:
    tf = timeframe.lower()
    label = "1H PAPER CANDIDATE" if tf == "1h" else f"{tf.upper()} PAPER CANDIDATE"
    return {
        "label": label,
        "source": source,
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": tf,
        "setup_id": setup_id,
        "paper_plan_id": paper_plan_id,
        "plan_as_of": _iso(plan_as_of),
        "recorded_at": _iso(plan_recorded_at),
        "is_recorded_paper_plan": paper_plan_id is not None,
    }


def _confirmation_base(
    *,
    status: str,
    reason: str,
    plan_as_of: datetime | None,
    matching_decision_count: int = 0,
    excluded_late_decision_count: int = 0,
    available_by_plan: bool = False,
    evidence_complete: bool | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "confirmed": status == "confirmed",
        "label": _STATUS_LABELS[status],
        "reason": reason,
        "decision_time": _iso(plan_as_of),
        "recorded_at": None,
        "observation_id": None,
        "hierarchy_fingerprint": None,
        "decision": None,
        "alignment": None,
        "matching_boundary": False,
        "available_by_plan": available_by_plan,
        "evidence_complete": evidence_complete,
        "evidence_timeframes": [timeframe for _role, timeframe in _EXPECTED_LAYERS],
        "matching_decision_count": matching_decision_count,
        "excluded_late_decision_count": excluded_late_decision_count,
        "records": [],
        "excluded_late_records": [],
        "layers": [],
    }


def _stored_record_summary(row: HierarchyObservation) -> dict[str, Any]:
    return {
        "observation_id": row.observation_id,
        "hierarchy_fingerprint": row.hierarchy_fingerprint,
        "decision_time": _iso(row.decision_time),
        "recorded_at": _iso(row.recorded_at),
        "decision": row.decision,
        "status": row.status,
        "alignment": row.alignment,
    }


def _excluded_late_summary(row: HierarchyObservation) -> dict[str, Any]:
    # Deliberately omit the decision and its layer contents. A later-written
    # evaluation is not evidence for the earlier paper candidate.
    return {
        "observation_id": row.observation_id,
        "hierarchy_fingerprint": row.hierarchy_fingerprint,
        "decision_time": _iso(row.decision_time),
        "recorded_at": _iso(row.recorded_at),
    }


def _layer_json(row: HierarchyObservation, role: str) -> dict[str, Any]:
    return _json_object(getattr(row, f"{role}_json"), field=f"{role}_layer")


def _check_layer_boundaries(
    row: HierarchyObservation,
    *,
    decision_time: datetime,
    boundary: dict[str, Any],
    layers: dict[str, dict[str, Any]],
    exchange: str,
    symbol: str,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Check exact per-timeframe boundaries and whether the candles were present.

    Returns ``(layer_metadata, has_stale_data, has_incomplete_data)``. A
    boundary mismatch is corruption/conflict, not a nearby match.
    """

    if _parse_datetime(boundary.get("decision_time"), field="boundary decision") != decision_time:
        raise _InvalidStoredEvidence("boundary decision_time does not match the paper plan")
    boundaries = boundary.get("boundaries")
    if not isinstance(boundaries, list) or len(boundaries) != len(_EXPECTED_LAYERS):
        raise _InvalidStoredEvidence("stored decision does not contain all four layer boundaries")

    by_role: dict[str, dict[str, Any]] = {}
    for item in boundaries:
        if not isinstance(item, dict) or not isinstance(item.get("role"), str):
            raise _InvalidStoredEvidence("stored layer boundary is malformed")
        role = item["role"]
        if role in by_role:
            raise _InvalidStoredEvidence("stored decision repeats a layer role")
        by_role[role] = item

    layer_metadata: list[dict[str, Any]] = []
    has_stale_data = False
    has_incomplete_data = False
    for role, timeframe in _EXPECTED_LAYERS:
        expected_open = latest_closed_candle_open_time(decision_time, timeframe)
        expected_close = expected_open + interval_for_timeframe(timeframe)
        item = by_role.get(role)
        if item is None or item.get("timeframe") != timeframe:
            raise _InvalidStoredEvidence(
                f"stored boundary is missing the {role} {timeframe} layer"
            )
        if _parse_datetime(item.get("decision_time"), field=f"{role} decision") != decision_time:
            raise _InvalidStoredEvidence(f"{role} boundary uses a different decision_time")
        if (
            _parse_datetime(item.get("candle_open_time"), field=f"{role} candle open")
            != expected_open
            or _parse_datetime(item.get("candle_close_time"), field=f"{role} candle close")
            != expected_close
        ):
            raise _InvalidStoredEvidence(f"{role} candle boundary does not match its timeframe")

        layer = layers[role]
        if layer.get("timeframe") != timeframe:
            raise _InvalidStoredEvidence(f"{role} layer JSON has a conflicting timeframe")
        if _parse_datetime(layer.get("decision_time"), field=f"{role} layer decision") != decision_time:
            raise _InvalidStoredEvidence(f"{role} layer JSON uses a different decision_time")
        if (
            _parse_datetime(layer.get("boundary_open"), field=f"{role} layer open")
            != expected_open
            or _parse_datetime(layer.get("boundary_close"), field=f"{role} layer close")
            != expected_close
        ):
            raise _InvalidStoredEvidence(f"{role} layer JSON has a conflicting boundary")

        column_timeframe = getattr(row, f"{role}_timeframe")
        column_open = getattr(row, f"{role}_boundary_open")
        column_close = getattr(row, f"{role}_boundary_close")
        if (
            column_timeframe != timeframe
            or _utc(column_open) != expected_open
            or _utc(column_close) != expected_close
        ):
            raise _InvalidStoredEvidence(f"stored {role} boundary columns do not match the exact boundary")

        if item.get("stale") is True or layer.get("stale") is True:
            has_stale_data = True
        elif item.get("stale") is not False or layer.get("stale") is not False:
            raise _InvalidStoredEvidence(f"{role} stale status is missing")

        candle = item.get("candle")
        if item.get("candle_known") is not True:
            has_incomplete_data = True
        if candle is None:
            has_incomplete_data = True
        elif not isinstance(candle, dict):
            raise _InvalidStoredEvidence(f"stored {role} boundary candle is malformed")
        else:
            if (
                candle.get("exchange") != exchange
                or candle.get("symbol") != symbol
                or candle.get("timeframe") != timeframe
                or _parse_datetime(candle.get("timestamp"), field=f"{role} candle timestamp")
                != expected_open
            ):
                raise _InvalidStoredEvidence(f"stored {role} candle does not match its boundary")

        layer_metadata.append(
            {
                "role": role,
                "timeframe": timeframe,
                "boundary_open": _iso(expected_open),
                "boundary_close": _iso(expected_close),
                "candle_known": item.get("candle_known") is True,
                "stale": layer.get("stale") is True or item.get("stale") is True,
            }
        )

    return layer_metadata, has_stale_data, has_incomplete_data


def _has_supportive_evidence(
    layer: dict[str, Any],
    *,
    categories: set[str],
    timeframe: str,
    decision_time: datetime,
) -> bool:
    evidence = layer.get("evidence")
    if not isinstance(evidence, list):
        return False
    for item in evidence:
        if (
            not isinstance(item, dict)
            or item.get("status") != "supportive"
            or item.get("category") not in categories
            or item.get("timeframe") != timeframe
        ):
            continue
        try:
            observed_at = _parse_datetime(
                item.get("observed_at"), field=f"{timeframe} supporting evidence"
            )
        except _InvalidStoredEvidence:
            continue
        if observed_at <= decision_time:
            return True
    return False


def _analyze_observation(
    row: HierarchyObservation,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    setup_id: str | None,
    family: str | None,
    direction: str | None,
    plan_as_of: datetime,
) -> dict[str, Any]:
    """Validate one exact stored hierarchy row; it never reads current candles."""

    if (
        row.exchange != exchange
        or row.symbol != symbol
        or _utc(row.decision_time) != plan_as_of
    ):
        raise _InvalidStoredEvidence("stored hierarchy row identity does not match the paper plan")

    hierarchy = _json_object(row.hierarchy_json, field="hierarchy")
    snapshot = _json_object(row.snapshot_json, field="snapshot")
    boundary = _json_object(row.boundary_json, field="decision boundary")
    layers = {role: _layer_json(row, role) for role, _timeframe in _EXPECTED_LAYERS}

    if hierarchy != snapshot.get("hierarchy"):
        raise _InvalidStoredEvidence("snapshot and stored hierarchy configuration disagree")
    if hierarchy.get("fingerprint") != row.hierarchy_fingerprint:
        raise _InvalidStoredEvidence("hierarchy fingerprint does not match its stored configuration")
    if boundary != snapshot.get("boundary"):
        raise _InvalidStoredEvidence("snapshot and stored decision boundaries disagree")
    for role, layer in layers.items():
        if layer != snapshot.get(role):
            raise _InvalidStoredEvidence(f"snapshot and stored {role} evidence disagree")

    for field, expected in (
        ("exchange", exchange),
        ("symbol", symbol),
        ("decision_time", plan_as_of),
    ):
        stored = snapshot.get(field)
        if field == "decision_time":
            if _parse_datetime(stored, field="snapshot decision") != expected:
                raise _InvalidStoredEvidence("snapshot decision_time does not match the paper plan")
        elif stored != expected:
            raise _InvalidStoredEvidence(f"snapshot {field} does not match the paper plan")
    if (
        snapshot.get("decision") != row.decision
        or snapshot.get("status") != row.status
        or snapshot.get("alignment") != row.alignment
        or snapshot.get("counter_trend") is not bool(row.counter_trend)
    ):
        raise _InvalidStoredEvidence("snapshot and stored hierarchy decision fields disagree")

    steps = hierarchy.get("steps")
    expected_steps = [
        {"role": role, "timeframe": expected_timeframe}
        for role, expected_timeframe in _EXPECTED_LAYERS
    ]
    if steps != expected_steps:
        return {
            "status": "not_confirmed",
            "confirmed": False,
            "reason": "The stored hierarchy does not use the 4H / 1H / 15M / 5M role sequence.",
            "decision": row.decision,
            "alignment": row.alignment,
            "record": _stored_record_summary(row),
            "layers": [],
            "signature": ("not_standard_hierarchy", row.decision, row.status),
        }

    layers_metadata, boundary_stale, boundary_incomplete = _check_layer_boundaries(
        row,
        decision_time=plan_as_of,
        boundary=boundary,
        layers=layers,
        exchange=exchange,
        symbol=symbol,
    )

    stale = boundary_stale or any(layer.get("stale") is True for layer in layers.values())
    incomplete = boundary_incomplete or row.status == "incomplete"
    if not layers["context"].get("available", False):
        incomplete = True
    if not layers["setup"].get("available", False):
        incomplete = True
    if layers["setup"].get("snapshot_status") != "evaluated":
        incomplete = True
    if layers["context"].get("missing_candle_count") != 0:
        incomplete = True
    for role in ("confirmation", "execution"):
        missing = _nonnegative_integer(
            layers[role].get("missing_candle_count"),
            field=f"{role} missing candle",
        )
        if missing:
            incomplete = True

    if stale:
        status = "stale"
        reason = "At least one stored timeframe was stale at the hierarchy decision boundary."
    elif incomplete:
        status = "incomplete"
        reason = "The stored hierarchy evaluation or one of its four timeframe windows was incomplete."
    else:
        status = "not_confirmed"
        reason = (
            f"The matching stored hierarchy decision was {row.decision}; "
            "it did not prove a complete four-timeframe confirmation."
        )

    setup = layers["setup"]
    confirmation = layers["confirmation"]
    execution = layers["execution"]
    signature = (
        status,
        row.decision,
        row.status,
        row.alignment,
        row.hierarchy_fingerprint,
        bool(row.counter_trend),
        setup.get("setup_id"),
        setup.get("family"),
        setup.get("direction"),
        setup.get("setup_state"),
        confirmation.get("state"),
        execution.get("state"),
    )

    if status == "stale" or status == "incomplete":
        return {
            "status": status,
            "confirmed": False,
            "reason": reason,
            "decision": row.decision,
            "alignment": row.alignment,
            "record": _stored_record_summary(row),
            "layers": layers_metadata,
            "signature": signature,
        }

    if (
        setup.get("setup_id") != setup_id
        or setup.get("family") != family
        or setup.get("direction") != direction
    ):
        return {
            "status": "conflicting",
            "confirmed": False,
            "reason": "The hierarchy selected a different 1H setup than the paper candidate.",
            "decision": row.decision,
            "alignment": row.alignment,
            "record": _stored_record_summary(row),
            "layers": layers_metadata,
            "signature": ("conflicting_setup", signature),
        }

    if row.decision != "plannable":
        return {
            "status": "not_confirmed",
            "confirmed": False,
            "reason": reason,
            "decision": row.decision,
            "alignment": row.alignment,
            "record": _stored_record_summary(row),
            "layers": layers_metadata,
            "signature": signature,
        }

    supportive_confirmation = _has_supportive_evidence(
        confirmation,
        categories={"level_acceptance"},
        timeframe="15m",
        decision_time=plan_as_of,
    )
    supportive_entry = _has_supportive_evidence(
        execution,
        categories={"entry_zone"},
        timeframe="5m",
        decision_time=plan_as_of,
    )
    supportive_trigger = execution.get("state") != "triggered" or _has_supportive_evidence(
        execution,
        categories={"trigger"},
        timeframe="5m",
        decision_time=plan_as_of,
    )
    supportive_execution = supportive_entry and supportive_trigger
    if (
        row.status != "evaluated"
        or row.counter_trend
        or row.alignment not in {"aligned", "neutral"}
        or setup.get("setup_state") != "QUALIFIED"
        or confirmation.get("state") != "confirming"
        or execution.get("state") not in {"armed", "triggered"}
        or not supportive_confirmation
        or not supportive_execution
    ):
        return {
            "status": "conflicting",
            "confirmed": False,
            "reason": "The stored PLANNABLE verdict conflicts with its layer states or supporting evidence.",
            "decision": row.decision,
            "alignment": row.alignment,
            "record": _stored_record_summary(row),
            "layers": layers_metadata,
            "signature": ("conflicting_plannable_evidence", signature),
        }

    return {
        "status": "confirmed",
        "confirmed": True,
        "reason": "A complete stored 4H / 1H / 15M / 5M PLANNABLE decision matched this setup and was recorded before the paper plan.",
        "decision": row.decision,
        "alignment": row.alignment,
        "record": _stored_record_summary(row),
        "layers": layers_metadata,
        "signature": ("confirmed", signature),
    }


def signal_provenance(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    setup_id: str | None,
    family: str | None,
    direction: str | None,
    plan_as_of: datetime | None,
    plan_recorded_at: datetime | None,
    paper_plan_id: str | None,
    hierarchy_ledger: HierarchyLedgerRepository | None,
    candidate_source: str,
) -> dict[str, Any]:
    """Build a read-only, exact-boundary provenance block for an API plan."""

    candidate = _candidate(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        setup_id=setup_id,
        plan_as_of=plan_as_of,
        plan_recorded_at=plan_recorded_at,
        paper_plan_id=paper_plan_id,
        source=candidate_source,
    )
    tf = timeframe.lower()
    if tf != "1h":
        confirmation = _confirmation_base(
            status="not_applicable",
            reason="Four-timeframe confirmation in this view is defined for the independent 1H paper engine only.",
            plan_as_of=plan_as_of,
        )
        return {"paper_candidate": candidate, "four_timeframe_confirmation": confirmation}

    if plan_as_of is None:
        confirmation = _confirmation_base(
            status="unavailable",
            reason="The paper candidate has no recorded decision boundary to match.",
            plan_as_of=None,
        )
        return {"paper_candidate": candidate, "four_timeframe_confirmation": confirmation}
    boundary_time = _utc(plan_as_of)
    valid_plan_boundary = (
        latest_closed_candle_open_time(boundary_time, "1h")
        + interval_for_timeframe("1h")
        == boundary_time
    )
    if not valid_plan_boundary:
        confirmation = _confirmation_base(
            status="unavailable",
            reason="The paper candidate timestamp is not a valid 1H candle-close decision boundary.",
            plan_as_of=boundary_time,
        )
        return {"paper_candidate": candidate, "four_timeframe_confirmation": confirmation}

    if paper_plan_id is None or plan_recorded_at is None:
        confirmation = _confirmation_base(
            status="unavailable",
            reason=(
                "No stored 1H paper-plan row matches this dashboard candidate; "
                "the live hierarchy evaluation is not used as confirmation."
            ),
            plan_as_of=boundary_time,
        )
        return {"paper_candidate": candidate, "four_timeframe_confirmation": confirmation}

    plan_written_at = _utc(plan_recorded_at)
    if hierarchy_ledger is None:
        confirmation = _confirmation_base(
            status="unavailable",
            reason="The stored hierarchy ledger is unavailable, so confirmation cannot be verified.",
            plan_as_of=boundary_time,
        )
        return {"paper_candidate": candidate, "four_timeframe_confirmation": confirmation}

    rows = hierarchy_ledger.observations_at_boundary(
        exchange=exchange,
        symbol=symbol,
        decision_time=boundary_time,
    )
    if not rows:
        confirmation = _confirmation_base(
            status="unavailable",
            reason="No stored hierarchy decision exists at the paper plan's exact 1H boundary.",
            plan_as_of=boundary_time,
        )
        return {"paper_candidate": candidate, "four_timeframe_confirmation": confirmation}

    eligible = [row for row in rows if _utc(row.recorded_at) <= plan_written_at]
    late = [row for row in rows if _utc(row.recorded_at) > plan_written_at]
    if not eligible:
        confirmation = _confirmation_base(
            status="unavailable",
            reason=(
                "Matching-boundary hierarchy record(s) were written after the paper plan; "
                "POST-DECISION evidence is excluded."
            ),
            plan_as_of=boundary_time,
            excluded_late_decision_count=len(late),
        )
        confirmation["excluded_late_records"] = [_excluded_late_summary(row) for row in late]
        confirmation["matching_boundary"] = True
        return {"paper_candidate": candidate, "four_timeframe_confirmation": confirmation}

    results: list[dict[str, Any]] = []
    for row in eligible:
        try:
            result = _analyze_observation(
                row,
                exchange=exchange,
                symbol=symbol,
                timeframe=tf,
                setup_id=setup_id,
                family=family,
                direction=direction,
                plan_as_of=boundary_time,
            )
        except _InvalidStoredEvidence as exc:
            result = {
                "status": "conflicting",
                "confirmed": False,
                "reason": str(exc),
                "decision": row.decision,
                "alignment": row.alignment,
                "record": _stored_record_summary(row),
                "layers": [],
                "signature": ("invalid_stored_evidence", str(exc)),
            }
        results.append(result)

    statuses = {result["status"] for result in results}
    signatures = {result["signature"] for result in results}
    if len(statuses) > 1 or len(signatures) > 1:
        status = "conflicting"
        reason = "Multiple stored hierarchy decisions at this boundary disagree or contain conflicting evidence."
        selected: dict[str, Any] = {
            "status": status,
            "confirmed": False,
            "reason": reason,
            "decision": None,
            "alignment": None,
            "record": None,
            "layers": [],
        }
    else:
        selected = results[0]
        status = selected["status"]
        reason = selected["reason"]

    confirmation = _confirmation_base(
        status=status,
        reason=reason,
        plan_as_of=boundary_time,
        matching_decision_count=len(eligible),
        excluded_late_decision_count=len(late),
        available_by_plan=True,
        evidence_complete=(status not in {"incomplete", "stale", "unavailable"}),
    )
    confirmation.update(
        {
            "matching_boundary": True,
            "records": [result["record"] for result in results],
            "excluded_late_records": [_excluded_late_summary(row) for row in late],
            "layers": selected.get("layers", []),
            "decision": selected.get("decision"),
            "alignment": selected.get("alignment"),
        }
    )
    record = selected.get("record")
    if isinstance(record, dict):
        confirmation.update(
            {
                "recorded_at": record.get("recorded_at"),
                "observation_id": record.get("observation_id"),
                "hierarchy_fingerprint": record.get("hierarchy_fingerprint"),
            }
        )
    return {"paper_candidate": candidate, "four_timeframe_confirmation": confirmation}


def paper_plan_signal_provenance(
    plan: PaperPlan,
    *,
    hierarchy_ledger: HierarchyLedgerRepository | None,
) -> dict[str, Any]:
    """Provenance for one immutable forward-ledger paper plan."""

    return signal_provenance(
        exchange=plan.exchange,
        symbol=plan.symbol,
        timeframe=plan.timeframe,
        setup_id=plan.setup_id,
        family=str(_value(plan.family)),
        direction=str(_value(plan.direction)),
        plan_as_of=plan.plan_as_of,
        plan_recorded_at=plan.recorded_at,
        paper_plan_id=plan.paper_plan_id,
        hierarchy_ledger=hierarchy_ledger,
        candidate_source=(
            "independent_1h_forward_engine"
            if plan.timeframe.lower() == "1h"
            else f"independent_{plan.timeframe.lower()}_forward_engine"
        ),
    )


def dashboard_candidate_signal_provenance(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    setup_id: str | None,
    family: str | None,
    direction: str | None,
    plan_as_of: datetime,
    hierarchy_ledger: HierarchyLedgerRepository | None,
) -> dict[str, Any]:
    """Provenance for a dashboard candidate not linked to a stored paper plan."""

    return signal_provenance(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        setup_id=setup_id,
        family=family,
        direction=direction,
        plan_as_of=plan_as_of,
        plan_recorded_at=None,
        paper_plan_id=None,
        hierarchy_ledger=hierarchy_ledger,
        candidate_source="dashboard_step6_candidate",
    )


__all__ = [
    "dashboard_candidate_signal_provenance",
    "paper_plan_signal_provenance",
    "signal_provenance",
]
