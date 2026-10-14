"""Serve an existing disposable synthetic DB for offline browser integration tests.

Usage: .venv/bin/python scripts/browser-audit/offline_server.py --db .cache/brain-repair/demo.sqlite3 --port 8123

Refuses databases outside the repository's ignored .cache directory. No seeding,
runner, migration or exchange requests. Market/evidence APIs are real; only the
unavailable display-only quote/socket and preview framing are test adaptations.
Never use this wrapper for production.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from fastapi.responses import JSONResponse, Response

from trading_assistant.config import Settings
from trading_assistant.web.app import STATIC_DIR, create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    database = args.db.resolve()
    if not database.is_relative_to(root / ".cache") or not database.is_file():
        parser.error("--db must be an existing disposable synthetic database inside .cache")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database}")
    app = create_app(settings=settings)

    @app.middleware("http")
    async def offline_only(request, call_next):
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"error": "offline browser harness is read-only"}, status_code=405)
        if request.url.path == "/api/market/live-price":
            return JSONResponse({
                "status": "UNAVAILABLE", "price": None, "fetched_at": None,
                "exchange": "binance", "source": "OFFLINE SYNTHETIC TEST — no exchange request",
                "display_only": True,
            })
        if request.url.path == "/__offline.js":
            # Keep forming-display's own unavailable path; never invent a quote/candle.
            return Response("window.WebSocket = undefined;", media_type="text/javascript")
        if request.url.path == "/":
            html = (STATIC_DIR / "index.html").read_text()
            html = html.replace("<head>", '<head><script src="/__offline.js"></script>')
            html = html.replace("<title>", "<title>OFFLINE SYNTHETIC TEST — ")
            response = Response(html, media_type="text/html")
        else:
            response = await call_next(request)
        # This disposable wrapper may be framed by the sandbox preview. Production
        # security policy stays untouched in app.py, and APIs remain same-origin.
        csp = response.headers.get("content-security-policy")
        if csp:
            response.headers["content-security-policy"] = csp.replace("frame-ancestors 'none'; ", "")
        return response

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
