"""One local worker; finite search, bounded bursts, durable graceful stopping."""

import time
from urllib.error import HTTPError, URLError
from .campaign import run_campaign, schema
from .paper import poll_paper, FeedNotCurrent
from .store import exclusive
from .brain import compare_brain
from datetime import datetime


def transient(exc):
    if isinstance(exc, HTTPError):
        return exc.code in {408, 429, 500, 502, 503, 504}
    return isinstance(exc, (URLError, TimeoutError, ConnectionError, FeedNotCurrent))


@exclusive
def overnight(
    store,
    plan=None,
    *,
    minutes=480,
    max_jobs=2,
    burst_seconds=60,
    idle_seconds=60,
    duty_cycle=0.5,
    max_cycles=None,
    stop=lambda: False,
    research=run_campaign,
    paper=poll_paper,
    brain=compare_brain,
    brain_period=("2024-01-01T00:00:00+00:00", "2024-02-01T00:00:00+00:00"),
    sleep=time.sleep,
    clock=time.monotonic,
):
    if (
        not 0 < minutes <= 1440
        or not 1 <= burst_seconds <= 300
        or not 1 <= idle_seconds <= 300
        or not 0 < duty_cycle <= 1
    ):
        raise ValueError("bounded runtime, burst, idle and CPU duty cycle required")
    if (
        type(max_jobs) is not int
        or not 1 <= max_jobs <= 256
        or max_cycles is not None
        and (type(max_cycles) is not int or max_cycles < 1)
    ):
        raise ValueError("invalid work budget")
    schema(store)
    started = clock()
    cycles = 0
    failures = 0
    last = None

    def expired():
        return stop() or clock() - started >= minutes * 60

    def pause(seconds):
        # A one-second slice makes stop responsiveness independent of idle period.
        remaining = seconds
        while remaining > 0 and not expired():
            delay = min(1, remaining)
            sleep(delay)
            remaining -= delay

    try:
        while not expired() and (max_cycles is None or cycles < max_cycles):
            cycle_started = clock()
            store.heartbeat("overnight_research", cycle=cycles)
            try:
                if brain_period is not None:
                    brain(
                        store,
                        start=datetime.fromisoformat(brain_period[0]),
                        end=datetime.fromisoformat(brain_period[1]),
                        max_boundaries=24,
                        max_seconds=min(
                            burst_seconds, max(1, minutes * 60 - (clock() - started))
                        ),
                        stop=expired,
                    )
                if expired():
                    break
                last = research(
                    store,
                    plan,
                    max_jobs=max_jobs,
                    max_seconds=min(
                        burst_seconds, max(1, minutes * 60 - (clock() - started))
                    ),
                    stop=expired,
                )
                if (
                    not expired()
                    and store.db.execute("SELECT count(*) FROM promotions").fetchone()[
                        0
                    ]
                ):
                    store.heartbeat("forward_paper_poll")
                    paper(store)
                failures = 0
            except Exception as exc:
                retry = transient(exc)
                store.error("overnight", exc, retryable=retry)
                if not retry:
                    store.heartbeat("blocked_error", detail=str(exc)[:500])
                    return {
                        "stage": "blocked_error",
                        "cycles": cycles,
                        "error": str(exc),
                        "progress_preserved": True,
                    }
                failures += 1
                store.heartbeat("temporary_public_data_outage", retry=failures)
                pause(min(300, 5 * 2 ** min(failures, 6)))
            cycles += 1
            if expired() or max_cycles is not None and cycles >= max_cycles:
                break
            active_seconds = clock() - cycle_started
            stage = (
                "search_exhausted_waiting_for_forward"
                if last and last.get("finished")
                else "bounded_research_pause"
            )
            store.heartbeat(stage, cycle=cycles)
            pause(max(idle_seconds, active_seconds * (1 / duty_cycle - 1)))
        return {
            "stage": "stopped" if stop() else "runtime_budget_complete",
            "cycles": cycles,
            "last_campaign": last,
            "progress_preserved": True,
        }
    finally:
        # Preserve explicit blocked_error diagnostics, but never claim active health.
        row = store.db.execute("SELECT health FROM worker").fetchone()
        import json

        if not row or json.loads(row[0])["stage"] != "blocked_error":
            store.heartbeat("stopped" if stop() else "runtime_budget_complete")
