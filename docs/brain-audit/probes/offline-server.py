"""Audit-only read-only browser adapter; NEVER a deployment configuration.

Synthetic DB only, no quote/exchange requests or writes. Production API evidence
and assets otherwise unchanged. Based on Agent 2's inspected offline wrapper;
framing adaptation is limited to this disposable harness.
"""
import argparse
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=Path.cwd())
parser.add_argument('--db', type=Path, required=True)
parser.add_argument('--port', type=int, default=8138)
args = parser.parse_args()
root = args.root.resolve()
database = args.db.resolve()
if not database.is_file() or '.cache/brain-audit/' not in str(database):
    parser.error('An existing disposable .cache/brain-audit/ database is required')
sys.path.insert(0, str(root / 'src'))
import uvicorn
from fastapi.responses import JSONResponse, Response
from trading_assistant.config import Settings
from trading_assistant.web.app import STATIC_DIR, create_app
from datetime import datetime, UTC
settings = Settings(_env_file=None, database_url=f'sqlite:///{database}')
# Matches latest synthetic seed close; fixed so results do not drift with wall time.
app = create_app(settings=settings, clock=lambda: datetime(2026, 10, 14, 8, tzinfo=UTC))

@app.middleware('http')
async def offline_only(request, call_next):
    if request.method not in {'GET', 'HEAD'}:
        return JSONResponse({'error': 'read-only audit harness'}, status_code=405)
    if request.url.path == '/api/market/live-price':
        return JSONResponse({'status': 'UNAVAILABLE', 'price': None, 'fetched_at': None,
            'exchange': 'binance', 'source': 'OFFLINE SYNTHETIC AUDIT', 'display_only': True})
    if request.url.path == '/__offline.js':
        return Response('window.WebSocket = undefined;', media_type='text/javascript')
    if request.url.path == '/':
        html = (STATIC_DIR / 'index.html').read_text().replace('<head>', '<head><script src="/__offline.js"></script>')
        html = html.replace('<title>', '<title>OFFLINE SYNTHETIC AUDIT — ')
        response = Response(html, media_type='text/html')
    else:
        response = await call_next(request)
    csp = response.headers.get('content-security-policy')
    if csp:
        response.headers['content-security-policy'] = csp.replace("frame-ancestors 'none'; ", '')
    return response

uvicorn.run(app, host='0.0.0.0', port=args.port)
