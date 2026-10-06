"""Run the dashboard locally: ``python -m trading_assistant.web``.

The database must already be migrated (``alembic upgrade head``). The server
binds to localhost by default; deployment as a private web app should add
authentication in front of it first.
"""

from __future__ import annotations

import argparse

import uvicorn

from trading_assistant.logging_config import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the trading-assistant dashboard"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8040)
    args = parser.parse_args()
    configure_logging()
    uvicorn.run(
        "trading_assistant.web.app_factory:create_app",
        factory=True,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
