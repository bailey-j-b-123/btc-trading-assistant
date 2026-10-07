"""Step 13 multi-timeframe runner: the long-running closed-candle polling loop.

The runner is deliberately thin, exactly like the Step 12 forward runner. All
evaluation, recording, and idempotency live in
:class:`~trading_assistant.multi_timeframe.service.MultiTimeframeService`; this
module only decides *when* to look, how to back off on errors, how to log, and
how to stop cleanly. It places no orders, needs no private exchange API, and
keeps no state beyond the append-only hierarchy ledger.

The failure contract matches Step 12:

* a failed pass releases the database before anything else is written, so an
  error can never be produced by a transaction the failed pass left behind;
* a SQLite lock/busy failure stops the runner immediately instead of
  retrying: retrying on top of a held lock only multiplies blocked writes;
* the pass is the only authority on the ledger: nothing here can replace or
  hide the exception a pass raised.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable

from trading_assistant.database.engine import (
    describe_sqlite_configuration,
    is_sqlite_lock_error,
)
from trading_assistant.forward_testing.parameters import RunnerSettings
from trading_assistant.multi_timeframe.service import (
    MultiTimeframeRunResult,
    MultiTimeframeService,
    RunnerStatus,
)

logger = logging.getLogger(__name__)


class MultiTimeframeRunner:
    """Poll for newly closed execution candles and evaluate the hierarchy once each."""

    def __init__(
        self,
        service: MultiTimeframeService,
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
        self._consecutive_errors = 0

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self, *_args: object) -> None:
        """Ask the loop to finish the current pass and shut down cleanly."""

        self._stop_event.set()

    def install_signal_handlers(self) -> None:
        """Stop cleanly on SIGINT/SIGTERM; the ledger is always left consistent."""

        for name in ("SIGINT", "SIGTERM"):
            handler_signal = getattr(signal, name, None)
            if handler_signal is None:
                continue
            try:
                signal.signal(handler_signal, self.request_stop)
            except ValueError:
                logger.debug("signal %s could not be installed", name)

    def run(
        self,
        *,
        symbol: str | None = None,
        once: bool = False,
        refresh_market_data: bool = True,
        max_passes: int | None = None,
    ) -> MultiTimeframeRunResult | None:
        """Run the hierarchy runner until stopped (or for one pass in ``once`` mode)."""

        self._log_database_runtime()
        passes = 0
        last_result: MultiTimeframeRunResult | None = None
        while not self.stop_requested:
            passes += 1
            if max_passes is not None and passes > max_passes:
                break
            try:
                result = self.service.run_once(
                    symbol=symbol,
                    refresh_market_data=refresh_market_data,
                )
            except Exception as exc:  # noqa: BLE001 - the pass is the authority
                self._release_database(after=exc)
                if is_sqlite_lock_error(exc):
                    # A locked database is not a transient market-data
                    # failure: stop after a single attempt instead of
                    # multiplying blocked writes on top of the held lock.
                    logger.error(
                        "Multi-timeframe runner stopped on a database lock",
                        extra={
                            "fields": {
                                "runner_id": self.runner_id,
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                        },
                    )
                    raise
                self._consecutive_errors += 1
                logger.exception(
                    "Multi-timeframe runner pass failed",
                    extra={
                        "fields": {
                            "runner_id": self.runner_id,
                            "pass": passes,
                            "consecutive_errors": self._consecutive_errors,
                            "error_type": type(exc).__name__,
                        }
                    },
                )
                if self._consecutive_errors >= self.settings.stop_after_errors:
                    logger.error(
                        "Multi-timeframe runner stopping after repeated errors",
                        extra={
                            "fields": {
                                "runner_id": self.runner_id,
                                "consecutive_errors": self._consecutive_errors,
                                }
                        },
                    )
                    raise
                if once:
                    raise
                self._wait_for_next_check()
                continue
            last_result = result
            self._consecutive_errors = 0
            if once:
                return result
            if result.status is RunnerStatus.IDLE:
                self._wait_for_next_check()
        return last_result

    def _release_database(self, *, after: BaseException | None) -> None:
        """Drop pooled connections so a failed pass leaves no lock behind."""

        try:
            self.service.release_database_connections()
        except Exception:  # noqa: BLE001 - never mask the original failure
            logger.warning(
                "Multi-timeframe runner could not release database connections",
                extra={
                    "fields": {
                        "runner_id": self.runner_id,
                        "original_error_type": (
                            None if after is None else type(after).__name__
                        ),
                    }
                },
            )

    def _log_database_runtime(self) -> None:
        try:
            configuration = describe_sqlite_configuration(self.service.engine)
        except Exception:  # noqa: BLE001 - diagnostics must never stop the runner
            return
        if configuration:
            logger.info(
                "Multi-timeframe runner database runtime",
                extra={"fields": configuration},
            )

    def _wait_for_next_check(self) -> None:
        seconds = float(self.settings.interval_seconds)
        deadline = time.monotonic() + seconds
        while not self.stop_requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._sleep(min(remaining, 1.0))


def run_single_pass(
    service: MultiTimeframeService,
    *,
    symbol: str | None = None,
    refresh_market_data: bool = True,
) -> MultiTimeframeRunResult:
    """Run exactly one hierarchy pass (the testable one-shot entry point)."""

    return service.run_once(symbol=symbol, refresh_market_data=refresh_market_data)


def run_forever(
    service: MultiTimeframeService,
    *,
    symbol: str | None = None,
    refresh_market_data: bool = True,
    runner_id: str | None = None,
) -> MultiTimeframeRunResult | None:
    """Run the hierarchy runner until interrupted."""

    runner = MultiTimeframeRunner(service, runner_id=runner_id)
    runner.install_signal_handlers()
    return runner.run(
        symbol=symbol,
        once=False,
        refresh_market_data=refresh_market_data,
    )


__all__ = [
    "MultiTimeframeRunner",
    "run_forever",
    "run_single_pass",
]
