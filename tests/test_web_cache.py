"""Interface caching: refresh-safe headers, no query hacks, no service worker."""

from datetime import datetime, timezone
from pathlib import Path

from web_fixtures import make_client, make_settings, migrated_engine

STATIC_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "trading_assistant"
    / "web"
    / "static"
)


def _client(tmp_path):
    engine, url = migrated_engine(tmp_path)
    client = make_client(
        engine, make_settings(url), clock=datetime(2024, 1, 2, 1, tzinfo=timezone.utc)
    )
    return engine, client


def test_shell_and_assets_require_revalidation(tmp_path):
    engine, client = _client(tmp_path)
    try:
        for path in ("/", "/static/js/main.js", "/static/styles.css"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.headers["Cache-Control"] == "no-cache", path
    finally:
        engine.dispose()


def test_unchanged_assets_answer_304_on_revalidation(tmp_path):
    engine, client = _client(tmp_path)
    try:
        first = client.get("/static/js/main.js")
        etag = first.headers.get("ETag") or first.headers.get("etag")
        assert etag, "static responses must carry a validator for 304 revalidation"
        second = client.get("/static/js/main.js", headers={"If-None-Match": etag})
        assert second.status_code == 304
    finally:
        engine.dispose()


def test_api_responses_carry_no_interface_cache_header(tmp_path):
    engine, client = _client(tmp_path)
    try:
        response = client.get("/api/meta")
        assert response.status_code == 200
        assert "Cache-Control" not in response.headers
    finally:
        engine.dispose()


def test_no_query_version_hacks_or_service_worker_in_the_interface():
    html = (STATIC_DIR / "index.html").read_text()
    assert "?v=" not in html
    assert "serviceWorker" not in html
    for asset in list(STATIC_DIR.rglob("*.js")) + list(STATIC_DIR.rglob("*.css")):
        text = asset.read_text()
        assert "serviceWorker" not in text, asset.name
        assert "navigator.serviceWorker" not in text, asset.name
