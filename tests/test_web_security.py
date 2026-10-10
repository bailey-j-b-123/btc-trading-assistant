"""Step 10 security boundary: no execution surface, no credentials, safe
rendering contracts, and defensive HTTP behaviour.
"""

import re
from pathlib import Path

import pytest
from web_fixtures import (
    insert_candles,
    make_settings,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
)

STATIC_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "trading_assistant" / "web" / "static"
)

FORBIDDEN_ROUTE_WORDS = (
    "order",
    "execute",
    "execution",
    "position",
    "balance",
    "leverage",
    "liquidat",
    "withdraw",
    "transfer",
    "apikey",
    "api_key",
    "credential",
)


@pytest.fixture
def app(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    from trading_assistant.web import create_app

    application = create_app(
        engine=engine, settings=settings, clock=lambda: qualified_clock()
    )
    yield application
    engine.dispose()


def _flatten_routes(routes):
    """Walk FastAPI's lazily included routers into concrete APIRoute objects."""

    flat = []
    for route in routes:
        router = getattr(route, "original_router", None)
        if router is not None:
            flat.extend(_flatten_routes(router.routes))
        elif getattr(route, "routes", None) is not None and not hasattr(route, "path"):
            flat.extend(_flatten_routes(route.routes))
        else:
            flat.append(route)
    return flat


def test_no_execution_or_credential_endpoints_exist(app):
    flat = _flatten_routes(app.routes)
    routes = {route.path for route in flat if hasattr(route, "path")}
    assert routes, "the app must expose routes"
    for path in routes:
        lowered = path.lower()
        for word in FORBIDDEN_ROUTE_WORDS:
            assert word not in lowered, f"forbidden concept {word!r} in route {path}"
    # The whole state-changing surface is exactly the journal decision /
    # observation endpoints and the dashboard decision composite.
    mutating = {
        route.path
        for route in flat
        if getattr(route, "methods", None)
        and {"POST", "PUT", "PATCH", "DELETE"} & route.methods
    }
    assert mutating == {
        "/api/dashboard/decisions",
        "/api/journal/records/{journal_id}/decisions",
        "/api/journal/records/{journal_id}/observations",
    }


def test_mutating_endpoints_only_write_journal_rows(app):
    """Accepting a proposal can never reach an exchange: the code path ends in
    JournalService (append-only SQLite rows)."""

    import trading_assistant.web.dashboard_service as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "ccxt" not in source
    assert "place_order" not in source
    assert "create_order" not in source


def test_settings_endpoint_has_no_unsafe_controls(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    payload = client.get("/api/settings").json()
    unavailable = set(payload["unavailable_by_design"])
    assert "exchange_api_credentials" in unavailable
    assert "order_execution" in unavailable
    assert "leverage" in unavailable
    assert "account_balances" in unavailable
    assert "strategy_optimisation" in unavailable
    configured = payload["configured_defaults"]
    assert "api_key" not in configured and "secret" not in configured


def test_security_headers_on_every_response(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    for path in (
        "/",
        "/api/meta",
        "/api/dashboard",
        "/api/settings",
        "/static/styles.css",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        csp = response.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp, path
        assert "connect-src 'self' wss://stream.binance.com:9443" in csp, path
        assert "kraken" not in csp.lower(), path
        assert "'unsafe-inline'" not in csp, path
        assert "'unsafe-eval'" not in csp, path
        assert response.headers.get("x-content-type-options") == "nosniff", path


def test_index_html_has_no_inline_scripts_or_handlers(app):
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "<script" in html  # the module entry point exists...
    for match in re.findall(r"<script[^>]*>", html):
        assert "src=" in match, (
            "only external script files are allowed (CSP has no inline allowance)"
        )
    assert not re.search(r"\son\w+\s*=", html), "inline event handlers are forbidden"
    assert "eval(" not in html


def test_frontend_js_never_uses_innerhtml_or_eval():
    js_files = list(STATIC_DIR.glob("js/**/*.js"))
    assert js_files, "frontend modules must exist"
    for path in js_files:
        source = path.read_text(encoding="utf-8")
        assert "eval(" not in source, path
        assert ".innerHTML" not in source, path
        assert "document.write" not in source, path


def test_errors_are_json_not_html(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/api/journal/records/missing-record-id")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "error" in response.json()

    bad = client.get("/api/dashboard", params={"as_of": "not-a-date"})
    assert bad.status_code == 422
    assert bad.headers["content-type"].startswith("application/json")


def test_unsupported_methods_rejected(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.delete("/api/journal/records/anything")
    assert response.status_code == 405  # journal rows are append-only; no DELETE


def test_vendor_license_is_shipped():
    vendor = STATIC_DIR / "vendor"
    assert (vendor / "lightweight-charts.standalone.production.js").exists()
    license_text = (vendor / "lightweight-charts.LICENSE").read_text(encoding="utf-8")
    assert "Apache" in license_text or "License" in license_text
