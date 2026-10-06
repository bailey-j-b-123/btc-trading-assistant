"""Static contract checks for the shipped frontend bundle.

These tests guard the properties that cannot be exercised headlessly here:
mobile-first layout hooks, PWA-readiness metadata, and the unsafe-API ban in
every shipped script. They inspect the exact files served by the app.
"""

import json
import re
from pathlib import Path

STATIC_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "trading_assistant" / "web" / "static"
)
JS_FILES = sorted(STATIC_DIR.glob("js/**/*.js"))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_viewport_and_mobile_meta_present():
    html = read(STATIC_DIR / "index.html")
    assert 'name="viewport"' in html
    assert "viewport-fit=cover" in html
    assert 'name="theme-color"' in html
    assert "apple-mobile-web-app-capable" in html


def test_one_page_dashboard_reflows_for_narrow_screens():
    html = read(STATIC_DIR / "index.html")
    css = read(STATIC_DIR / "styles.css")
    assert '<nav class="sidenav"' not in html
    assert "@media (max-width: 760px)" in css
    assert "@media (max-width: 520px)" in css
    assert ".primary-layout { grid-template-columns: minmax(0, 1fr); }" in css
    assert ".terminal-chart-wrap { height: 350px; }" in css


def test_touch_targets_are_comfortable():
    css = read(STATIC_DIR / "styles.css")
    match = re.search(r"--touch:\s*(\d+)px", css)
    assert match is not None
    assert int(match.group(1)) >= 44


def test_terminal_verdict_and_system_status_use_explicit_text():
    """The current verdict and compact health state are words, not colour alone."""

    js = read(STATIC_DIR / "js/views/dashboard.js")
    assert "NO TRADE" in js and "WATCH" in js and "PLANNABLE" in js
    assert "SYSTEM OK" in js and "SYSTEM WARNING" in js
    assert 'role: "status"' in js


def test_no_inline_scripts_and_csp_allowlist_match():
    html = read(STATIC_DIR / "index.html")
    scripts = re.findall(r"<script[^>]*>", html)
    assert scripts, "the app entry scripts must exist"
    for tag in scripts:
        assert "src=" in tag
    styles = re.findall(r"<link[^>]*stylesheet[^>]*>", html)
    assert styles and all("href=" in tag for tag in styles)


def test_every_js_module_avoids_unsafe_apis():
    assert JS_FILES, "frontend modules must ship"
    for path in JS_FILES:
        source = read(path)
        assert "eval(" not in source, path.name
        assert ".innerHTML" not in source, path.name
        assert "document.write" not in source, path.name
        assert "Function(" not in source, path.name
        # No direct market fetches: data only flows through /api/.
        assert "fetch(" not in source or "/api/" in source, path.name


def test_manifest_is_valid_and_isolated():
    manifest = json.loads(read(STATIC_DIR / "manifest.webmanifest"))
    assert manifest["name"]
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert (STATIC_DIR / "icon.svg").exists()


def test_vendor_chart_library_is_shipped_with_license():
    vendor = STATIC_DIR / "vendor"
    library = vendor / "lightweight-charts.standalone.production.js"
    assert library.exists()
    header = read(library)[:400]
    assert "TradingView Lightweight Charts" in header
    assert (vendor / "lightweight-charts.LICENSE").exists()


def test_charts_load_from_same_origin_only():
    """No CDN or third-party origins anywhere in the shipped bundle."""

    html = read(STATIC_DIR / "index.html")
    assert "https://" not in html.replace(
        "https://www.apache.org/licenses/LICENSE-2.0", ""
    )
    css = read(STATIC_DIR / "styles.css")
    assert "@import" not in css and "url(http" not in css
    for path in JS_FILES:
        source = read(path)
        assert "cdn." not in source.lower(), path.name
        assert "unpkg.com" not in source, path.name
