"""Step 12 forward runner: the long-running closed-candle polling loop.

The runner is deliberately thin. All analysis, recording, and idempotency live in
:class:`~trading_assistant.forward_testing.service.ForwardTestService`; this
module only decides *when* to look, how to back off on errors, how to log, and
how to stop cleanly. It places no orders, needs no private exchange API, and
keeps no state beyond the append-only ledger and heartbeats.

Usage is documented in the README::

    python -m trading_assistant.forward_testing run            # continuous
    python -m trading_assistant.forward_testing run --once     # one pass
    python -m trading_assistant.forward_testing status         # print state
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from trading_assistant.database.engine import (
    describe_sqlite_configuration,
    is_sqlite_lock_error,
)
from trading_assistant.forward_testing.errors import ForwardError
from trading_assistant.forward_testing.models import ForwardHeartbeat
from trading_assistant.forward_testing.parameters import (
    HeartbeatStatus,
    RunnerSettings,
)
from trading_assistant.forward_testing.service import (
    ForwardRunResult,
    ForwardTestService,
)
from trading_assistant.market_data.errors import is_transient_network_error
from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    require_utc_datetime,
)
from trading_assistant.market_structure.candles import interval_for_timeframe

logger = logging.getLogger(__name__)


class ForwardRunner:
    """Poll for newly closed base candles and process each one exactly once."""

    def __init__(
        self,
        service: ForwardTestService,
        *,
        settings: RunnerSettings | None = None,
        sleep: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
        runner_id: str | None = None,
    ) -> None:
        self.service = service
        self.settings = settings if settings is not None else service.runner_settings
        self._sleep = sleep if sleep is not None else time.sleep
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self.runner_id = runner_id

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self, *_args: object) -> None:
        """Ask the loop to finish the current pass and shut down cleanly."""

        self._stop_event.set()
        # Also interrupt any in-pass retry-backoff sleep so shutdown is not
        # delayed by it. A network request already in flight is bounded by the
        # exchange watchdog, so shutdown waits at most one network deadline.
        service_stop = getattr(self.service, "request_stop", None)
        if callable(service_stop):
            service_stop()

    def install_signal_handlers(self) -> None:
        """Stop cleanly on SIGINT/SIGTERM; the ledger is always left consistent."""

        for name in ("SIGINT", "SIGTERM"):
            handler_signal = getattr(signal, name, None)
            if handler_signal is None:
                continue
            try:
                signal.signal(handler_signal, self.request_stop)
            except ValueError:
                # Not on the main thread (for example inside a test runner):
                # the explicit stop_event remains available.
                logger.debug("signal %s could not be installed", name)

    def run(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        once: bool = False,
        refresh_market_data: bool = True,
        max_passes: int | None = None,
    ) -> ForwardRunResult | None:
        """Run the forward tester until stopped (or for one pass in ``once`` mode).

        Each pass is independent and idempotent, so a restart, a crash, or a
        second runner process can only ever re-record identical rows.

        Failure handling contract (both directions matter):

        * the pass is the only authority on the ledger: a heartbeat write is
          diagnostic metadata and must never replace, hide, or outlive the
          exception a pass raised;
        * a failed pass releases the database before anything else is written,
          so an error message can never be produced by a transaction the failed
          pass left behind;
        * a SQLite lock/busy failure stops the runner immediately instead of
          retrying: retrying on top of a held lock only multiplies blocked
          writes (each waiting the whole busy timeout) and can turn one database
          failure into a storm of them.

        Every failure is classified before any retry decision:

        * a *transient external network failure* (connection timeout, DNS
          failure, connection reset, the exchange being temporarily
          unavailable, or a request exceeding its finite network deadline) is
          recoverable indefinitely: it is logged and recorded, it never counts
          toward ``stop_after_errors``, and it is retried after a bounded
          exponential backoff - so a temporary internet outage can never kill
          the long-running process, and never produces a tight retry loop;
        * a *local/structural failure* (invalid configuration, schema or
          programming error, deterministic invariant violation, or a
          non-transient market-data failure a completed pass recorded) counts
          toward ``stop_after_errors`` and stops the runner at the limit:
          permanent errors are never retried as if they were network outages;
        * a SQLite lock failure stops the runner immediately (unchanged).

        Every pass is fully bounded: market-data requests run under the finite
        exchange timeout plus its watchdog, database waits run under the finite
        busy timeout, and every sleep is interruptible. The runner therefore
        never sits indefinitely inside a network operation, and Ctrl+C/SIGTERM
        takes effect at most one in-flight network deadline after the request.
        """

        _, resolved_symbol, resolved_timeframe = self.service.resolve_instrument(
            symbol=symbol, timeframe=timeframe
        )
        # Diagnostic only: a heartbeat that cannot be written must not stop the
        # runner before it has attempted the ledger work it exists to do, and it
        # must never mask the error that the pass itself will report.
        self._record_lifecycle_event(
            status=HeartbeatStatus.STARTED,
            detail=(
                "forward runner started: closed-candle polling only, public market "
                "data only, no order capability"
            ),
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
        )
        self._log_database_runtime()
        logger.info(
            "Forward runner started",
            extra={
                "fields": {
                    "exchange": self.service.settings.exchange,
                    "symbol": resolved_symbol,
                    "timeframe": resolved_timeframe,
                    "poll_interval_seconds": str(self.settings.interval_seconds),
                    "once": once,
                    "refresh_market_data": refresh_market_data,
                }
            },
        )
        passes = 0
        consecutive_errors = 0
        transient_failures = 0
        last_result: ForwardRunResult | None = None
        try:
            while not self.stop_requested:
                passes += 1
                pass_started = time.monotonic()
                pass_exception: Exception | None = None
                try:
                    last_result = self.service.run_once(
                        symbol=resolved_symbol,
                        timeframe=resolved_timeframe,
                        refresh_market_data=refresh_market_data,
                        runner_id=self.runner_id,
                    )
                except Exception as exc:  # noqa: BLE001 - reported as a heartbeat
                    pass_exception = exc
                pass_duration = time.monotonic() - pass_started
                retry_wait: float | None = None

                if pass_exception is None:
                    assert last_result is not None
                    if last_result.market_data_error is None:
                        # A fully successful pass resets both counters.
                        consecutive_errors = 0
                        if transient_failures:
                            logger.info(
                                "Forward runner recovered after %d transient "
                                "network failure(s); normal passes resume",
                                transient_failures,
                                extra={
                                    "fields": {
                                        "stage": "PROCESSING",
                                        "recovered_transient_failures": str(
                                            transient_failures
                                        ),
                                    }
                                },
                            )
                        transient_failures = 0
                        logger.info(
                            "Forward runner pass completed",
                            extra={
                                "fields": {
                                    "stage": (
                                        "PROCESSING"
                                        if last_result.processed_boundaries
                                        else (
                                            "IDLE"
                                            if last_result.status
                                            is HeartbeatStatus.IDLE
                                            else "NO_DATA"
                                        )
                                    ),
                                    "status": last_result.status.value,
                                    "cycles": len(last_result.processed_boundaries),
                                    "observations": last_result.observations_recorded,
                                    "paper_plans": last_result.paper_plans_created,
                                    "outcomes": last_result.outcomes_recorded,
                                    "pending_boundaries": last_result.pending_boundaries,
                                    "data_health": last_result.data_health.value,
                                    "detail": last_result.detail,
                                    "pass_duration_seconds": f"{pass_duration:.3f}",
                                }
                            },
                        )
                    elif last_result.market_data_error_transient:
                        # A completed pass whose market-data refresh failed
                        # transiently: the error is recorded, no new market
                        # conclusion was produced, and the runner backs off
                        # before retrying automatically. Transient failures
                        # never count toward stop_after_errors, so a temporary
                        # internet outage cannot kill this process.
                        transient_failures += 1
                        delay = self.settings.transient_backoff_seconds(
                            transient_failures
                        )
                        retry_wait = float(delay)
                        logger.warning(
                            "Forward runner pass completed with a transient "
                            "market-data failure; no new market conclusion was "
                            "recorded; next retry in %s seconds",
                            delay,
                            extra={
                                "fields": {
                                    "stage": "RETRY_WAIT",
                                    "status": last_result.status.value,
                                    "transient_failures": str(transient_failures),
                                    "error_type": (
                                        last_result.market_data_error_type
                                    ),
                                    "backoff_seconds": str(delay),
                                    "next_retry_at": (
                                        datetime.now(UTC)
                                        + timedelta(seconds=float(delay))
                                    ).isoformat(),
                                    "data_health": last_result.data_health.value,
                                }
                            },
                        )
                    else:
                        # A completed pass that recorded a non-transient
                        # (local/structural) market-data failure counts toward
                        # the existing stop-after-errors budget: permanent
                        # errors are not retried as if they were outages.
                        consecutive_errors += 1
                        logger.error(
                            "Forward runner pass completed with a non-transient "
                            "market-data failure (%s); counted toward "
                            "stop_after_errors (%d/%d)",
                            last_result.market_data_error_type,
                            consecutive_errors,
                            self.settings.stop_after_errors,
                            extra={
                                "fields": {
                                    "stage": "ERROR",
                                    "status": last_result.status.value,
                                    "error_type": (
                                        last_result.market_data_error_type
                                    ),
                                    "consecutive_errors": str(consecutive_errors),
                                    "stop_after_errors": str(
                                        self.settings.stop_after_errors
                                    ),
                                    "data_health": last_result.data_health.value,
                                }
                            },
                        )
                        if consecutive_errors >= self.settings.stop_after_errors:
                            logger.error(
                                "Forward runner stopping after %d consecutive "
                                "non-transient market-data failures; every "
                                "failure is recorded in the heartbeat trail",
                                consecutive_errors,
                                extra={
                                    "fields": {
                                        "stage": "STOPPED",
                                        "consecutive_errors": str(
                                            consecutive_errors
                                        ),
                                    }
                                },
                            )
                            raise ForwardError(
                                f"forward runner stopping after {consecutive_errors} "
                                "consecutive non-transient market-data failures "
                                f"(stop_after_errors="
                                f"{self.settings.stop_after_errors}); last recorded "
                                f"error: {last_result.market_data_error_type}: "
                                f"{last_result.market_data_error}"
                            )
                elif is_transient_network_error(pass_exception):
                    # A transient external failure raised out of the pass:
                    # recoverable indefinitely with bounded backoff; never
                    # counted toward stop_after_errors.
                    transient_failures += 1
                    delay = self.settings.transient_backoff_seconds(
                        transient_failures
                    )
                    retry_wait = float(delay)
                    logger.error(
                        "Forward runner pass failed with a transient network "
                        "error; no conclusion was recorded; next retry in %s "
                        "seconds",
                        delay,
                        exc_info=pass_exception,
                        extra={
                            "fields": {
                                "stage": "RETRY_WAIT",
                                "transient_failures": str(transient_failures),
                                "error_type": type(pass_exception).__name__,
                                "backoff_seconds": str(delay),
                                "next_retry_at": (
                                    datetime.now(UTC)
                                    + timedelta(seconds=float(delay))
                                ).isoformat(),
                            }
                        },
                    )
                    # Release whatever the failed pass left open *before*
                    # writing anything: the error report must never run on top
                    # of a half-open write transaction or a connection that
                    # still holds a SQLite lock.
                    self._release_database(after=pass_exception)
                    self._record_lifecycle_event(
                        status=HeartbeatStatus.ERROR,
                        detail=(
                            f"forward runner pass failed with a transient network "
                            f"error ({type(pass_exception).__name__}); no conclusion "
                            "was recorded for the failed pass; retrying with "
                            "bounded backoff"
                        ),
                        symbol=resolved_symbol,
                        timeframe=resolved_timeframe,
                        last_error=str(pass_exception),
                        error_type=type(pass_exception).__name__,
                    )
                    if once:
                        raise pass_exception
                else:
                    consecutive_errors += 1
                    logger.error(
                        "Forward runner pass failed: %s",
                        pass_exception,
                        exc_info=pass_exception,
                    )
                    # Release whatever the failed pass left open *before* writing
                    # anything: the error report must never run on top of a
                    # half-open write transaction or a connection that still
                    # holds a SQLite lock.
                    self._release_database(after=pass_exception)
                    self._record_lifecycle_event(
                        status=HeartbeatStatus.ERROR,
                        detail=(
                            f"forward runner pass failed ({type(pass_exception).__name__}); no "
                            "conclusion was recorded for the failed pass"
                        ),
                        symbol=resolved_symbol,
                        timeframe=resolved_timeframe,
                        last_error=str(pass_exception),
                        error_type=type(pass_exception).__name__,
                    )
                    locked = is_sqlite_lock_error(pass_exception)
                    if locked:
                        logger.error(
                            "Forward runner stopping after a SQLite lock failure; "
                            "another connection holds a lock on the database and "
                            "retrying would only add more blocked writes",
                            extra={
                                "fields": {
                                    "stage": "ERROR",
                                    "exchange": self.service.settings.exchange,
                                    "symbol": resolved_symbol,
                                    "timeframe": resolved_timeframe,
                                    "error_type": type(pass_exception).__name__,
                                }
                            },
                        )
                    if (
                        once
                        or locked
                        or consecutive_errors >= self.settings.stop_after_errors
                    ):
                        raise pass_exception
                if once or (max_passes is not None and passes >= max_passes):
                    break
                if retry_wait is None:
                    self._wait_for_next_check(resolved_timeframe)
                else:
                    self._wait_before_retry(retry_wait)
        except KeyboardInterrupt:
            self.request_stop()
        finally:
            # Close pooled connections on the way out so the STOPPED heartbeat -
            # and the process itself - never queues behind a connection the last
            # pass left behind. This releases connections only; no stored row is
            # ever touched.
            self._release_database(after=None)
            self._record_lifecycle_event(
                status=HeartbeatStatus.STOPPED,
                detail="forward runner stopped cleanly; recorded history is intact",
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
            )
            logger.info(
                "Forward runner stopped",
                extra={"fields": {"stage": "STOPPED"}},
            )
        return last_result
    # ------------------------------------------------------------------
    # Failure and lifecycle helpers
    # ------------------------------------------------------------------

    def _record_lifecycle_event(
        self,
        *,
        status: HeartbeatStatus,
        detail: str,
        symbol: str,
        timeframe: str,
        last_error: str | None = None,
        error_type: str | None = None,
    ) -> ForwardHeartbeat | None:
        """Record one lifecycle heartbeat without ever raising.

        A heartbeat is diagnostic: it records that the runner started, failed, or
        stopped. It is therefore never allowed to raise - a database failure
        while reporting a database failure would replace the original root
        exception with a second, less informative one and could turn a single
        lock failure into repeated ones. The returned value is ``None`` exactly
        when the heartbeat could not be stored, which is logged with its full
        traceback so the failure stays visible.
        """

        try:
            return self.service.record_runner_event(
                status=status,
                detail=detail,
                symbol=symbol,
                timeframe=timeframe,
                last_error=last_error,
                error_type=error_type,
                runner_id=self.runner_id,
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic write, never fatal
            logger.error(
                "Forward runner could not record a %s heartbeat (%s: %s); the "
                "ledger result/exception of the pass is unaffected and no "
                "heartbeat failure is raised in its place",
                status.value,
                type(exc).__name__,
                exc,
                extra={
                    "fields": {
                        "heartbeat_status": status.value,
                        "error_type": type(exc).__name__,
                        "original_error": last_error,
                    }
                },
                exc_info=True,
            )
            return None

    def _release_database(self, *, after: BaseException | None) -> None:
        """Close pooled database connections; never touches stored rows.

        Called after a failed pass (with the exception for logging) and on
        shutdown. Releasing the pool is what guarantees a failed pass cannot
        hand a connection with an open transaction - or a held SQLite lock - to
        the next write attempt.
        """

        try:
            self.service.release_database_connections()
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask a failure
            logger.error(
                "Forward runner could not release pooled database connections "
                "(%s: %s)",
                type(exc).__name__,
                exc,
                extra={
                    "fields": {
                        "error_type": type(exc).__name__,
                        "original_error": None if after is None else str(after),
                    }
                },
                exc_info=True,
            )

    def _log_database_runtime(self) -> None:
        """Log the effective SQLite runtime configuration once per run."""

        try:
            configuration = describe_sqlite_configuration(self.service.engine)
        except Exception as exc:  # noqa: BLE001 - diagnostics must not stop the run
            logger.warning(
                "Forward runner could not read the database runtime configuration "
                "(%s: %s)",
                type(exc).__name__,
                exc,
            )
            return
        if not configuration:
            return
        logger.info(
            "Forward runner database runtime",
            extra={"fields": dict(configuration)},
        )

    def _wait_for_next_check(self, timeframe: str) -> None:
        """Sleep until the next close boundary, capped by the poll interval."""

        interval = interval_for_timeframe(timeframe)
        now = require_utc_datetime(self.service._clock())  # noqa: SLF001 - owned clock
        next_boundary = latest_closed_candle_open_time(now, timeframe) + interval
        seconds_to_boundary = max(
            1.0, (next_boundary - now).total_seconds() + 1.0
        )
        wait = min(float(self.settings.interval_seconds), seconds_to_boundary)
        self._stop_event.wait(timeout=wait)

    def _wait_before_retry(self, delay: float) -> None:
        """Bounded, interruptible wait before the next retry after a transient failure.

        The wait is the bounded exponential backoff from
        ``RunnerSettings.transient_backoff_seconds``; it uses the stop event, so
        Ctrl+C/SIGTERM interrupts it immediately instead of sleeping on.
        """

        logger.info(
            "Forward runner waiting before the next retry",
            extra={
                "fields": {
                    "stage": "RETRY_WAIT",
                    "wait_seconds": f"{max(0.0, delay):.3f}",
                }
            },
        )
        self._stop_event.wait(timeout=max(0.0, delay))


def run_forever(
    service: ForwardTestService,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    install_signals: bool = True,
) -> ForwardRunResult | None:
    """Convenience wrapper used by the CLI."""

    runner = ForwardRunner(service)
    if install_signals:
        runner.install_signal_handlers()
    return runner.run(symbol=symbol, timeframe=timeframe, once=False)


def run_single_pass(
    service: ForwardTestService,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    refresh_market_data: bool = True,
) -> ForwardRunResult:
    """Process every pending closed candle exactly once and return the result."""

    result = service.run_once(
        symbol=symbol,
        timeframe=timeframe,
        refresh_market_data=refresh_market_data,
    )
    return result


def format_status(status: dict) -> str:
    """Render the status payload as a short human-readable report."""

    market = status["market_data"]
    runner = status["runner"]
    sample = status["sample"]
    current = status["current_state"]
    lines = [
        f"{status['market_data_label']}: {status['exchange']}/{status['symbol']} "
        f"{status['timeframe']}",
        f"  data health            : {market['data_health']} ({market['data_health_detail']})",
        f"  latest stored candle   : {market['latest_stored_candle_open'] or 'UNKNOWN'}",
        "  runner status          : "
        + ("never run" if runner is None else f"{runner['status']} at {runner['recorded_at']}"),
        "  current setup state    : "
        + (
            str(current["setup_state"])
            if current["available"] and current["setup_state"] is not None
            else "UNKNOWN (no recorded setup state at the latest close)"
        ),
        f"  recorded cycles        : {sample['cycles']}",
        f"  forward observations   : {sample['observations']}",
        f"  paper plans            : {sample['paper_plans']}",
        f"  unresolved paper plans : {status['unresolved_paper_plan_count']}",
        f"  pending catch-up       : {sample['pending_catch_up_boundaries']} boundary(ies)",
        "PAPER OBSERVATION — NO REAL ORDER",
    ]
    return "\n".join(lines)


__all__ = [
    "ForwardRunner",
    "format_status",
    "run_forever",
    "run_single_pass",
]


def _now_for_log() -> datetime:  # pragma: no cover - trivial helper
    from datetime import UTC

    return datetime.now(UTC)
