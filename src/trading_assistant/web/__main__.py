"""Run the dashboard locally: ``python -m trading_assistant.web``.

The database must already be migrated (``alembic upgrade head``). A read-only
preflight check verifies that before the server starts and reports the exact
fix when setup is incomplete, instead of a low-level traceback.

The server binds to localhost by default; deployment as a private web app
should add authentication in front of it first.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from trading_assistant.config import get_settings
from trading_assistant.logging_config import configure_logging
from trading_assistant.web.preflight import PreflightError, run_preflight_checks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the trading-assistant dashboard"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8040)
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="skip the read-only database/migration check before startup",
    )
    args = parser.parse_args()
    configure_logging()
    if not args.skip_preflight:
        try:
            report = run_preflight_checks(get_settings().database_url)
        except PreflightError as exc:
            print(f"cannot start dashboard: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    uvicorn.run(
        "trading_assistant.web.app_factory:create_app",
        factory=True,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
