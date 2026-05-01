#!/usr/bin/env python3
"""ICS Feed Server — HTTP server that serves .ics calendar feed.

Usage:
    python3 ics_server.py

Serves:
    /events.ics — All events from database
    /events.ics?topic=бизнес — Filtered by topic
    /events.ics?source=ВШЭ — Filtered by source
    /health — Health check
"""

import asyncio
import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

from database import EventDatabase
from calendar_service import generate_ics

logger = logging.getLogger(__name__)

PORT = int(os.getenv("ICS_PORT", "8081"))
HOST = os.getenv("ICS_HOST", "0.0.0.0")

db = EventDatabase()

_ics_loop: asyncio.AbstractEventLoop = None
_ics_loop_ready = threading.Event()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Get or create the ICS thread's event loop."""
    global _ics_loop
    if _ics_loop is None or _ics_loop.is_closed():
        _ics_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_ics_loop)
    return _ics_loop


def _run_coro(coro):
    """Run a coroutine on the ICS thread's event loop synchronously."""
    loop = _ensure_loop()
    return loop.run_until_complete(coro)


class ICSHandler(BaseHTTPRequestHandler):
    """HTTP handler for ICS feed."""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/events.ics":
            self._handle_events(params)
        elif parsed.path == "/health":
            self._handle_health()
        elif parsed.path == "/":
            self._handle_index()
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _handle_events(self, params: dict):
        """Generate and serve ICS feed."""
        topic = params.get("topic", [None])[0]
        source = params.get("source", [None])[0]
        limit = int(params.get("limit", ["200"])[0])

        if topic:
            events = _run_coro(db.get_upcoming_events(limit=limit, topic=topic))
            cal_title = f"Event Finder — {topic}"
        elif source:
            events = _run_coro(db.get_upcoming_events(limit=limit, source=source))
            cal_title = f"Event Finder — {source}"
        else:
            events = _run_coro(db.get_upcoming_events(limit=limit))
            cal_title = "Event Finder — Все события"

        ics_content = generate_ics(events, title=cal_title)

        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="events.ics"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(ics_content.encode("utf-8"))

        event_count = len(events)
        logger.info(f"ICS feed served: {event_count} events (topic={topic}, source={source})")

    def _handle_health(self):
        """Health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        total = _run_coro(db.get_total_count())
        new = _run_coro(db.get_new_count())
        import json
        response = json.dumps({
            "status": "ok",
            "total_events": total,
            "new_events": new,
            "timestamp": datetime.now().isoformat(),
        })
        self.wfile.write(response.encode())

    def _handle_index(self):
        """Serve HTML info page."""
        total = _run_coro(db.get_total_count())
        new = _run_coro(db.get_new_count())
        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Event Finder Calendar</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; }}
.btn {{ display: inline-block; padding: 12px 24px; background: #667eea; color: white; text-decoration: none; border-radius: 8px; margin: 8px 0; }}
.btn:hover {{ background: #5a6fd6; }}
code {{ background: #f0f0f0; padding: 4px 8px; border-radius: 4px; }}
.info {{ background: #f8f9fa; padding: 16px; border-radius: 8px; margin: 16px 0; }}
</style>
</head>
<body>
<h1>📅 Event Finder Calendar</h1>
<p>Автоматический календарь мероприятий: вузы, компании, KudaGo, Timepad</p>

<div class="info">
<h3>Подписаться на календарь:</h3>
<a class="btn" href="/events.ics">📥 events.ics</a>
<p>Или скопируйте ссылку и добавьте в календарь:</p>
<code>http://YOUR_DOMAIN:8081/events.ics</code>
</div>

<div class="info">
<h3>Фильтры:</h3>
<p><a href="/events.ics?topic=бизнес">💼 Бизнес</a> |
<a href="/events.ics?topic=экономика">📈 Экономика</a> |
<a href="/events.ics?topic=психология">🧠 Психология</a> |
<a href="/events.ics?topic=история">📜 История</a> |
<a href="/events.ics?topic=технологии">💻 Технологии</a> |
<a href="/events.ics?topic=образование">🎓 Образование</a> |
<a href="/events.ics?topic=наука">🔬 Наука</a></p>
</div>

<div class="info">
<h3>Как добавить в календарь:</h3>
<ul>
<li><b>Google Calendar:</b> Настройки → Добавить календарь → По URL → вставьте ссылку</li>
<li><b>Apple Calendar:</b> Файл → Новая подписка на календарь → вставьте ссылку</li>
<li><b>Outlook:</b> Добавить календарь → Из интернета → вставьте ссылку</li>
</ul>
</div>

<p style="color: #888; font-size: 14px;">
Всего событий: <b>{total}</b> |
Новых: <b>{new}</b>
</p>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        logger.info(f"ICS: {format % args}")


def create_server():
    """Create (but do not start) the ICS feed HTTPServer."""
    _ensure_loop()
    server = HTTPServer((HOST, PORT), ICSHandler)
    logger.info(f"ICS Feed Server created on http://{HOST}:{PORT}")
    return server


def run_server():
    """Run the ICS feed server."""
    server = create_server()
    logger.info(f"ICS Feed Server started on http://{HOST}:{PORT}")
    logger.info(f"Events feed: http://{HOST}:{PORT}/events.ics")
    logger.info(f"Health check: http://{HOST}:{PORT}/health")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("ICS server stopping")
        server.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_server()
