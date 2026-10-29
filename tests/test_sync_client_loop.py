"""The blocking ``Client`` survives more than one call over a real connection.

Each sync call used to run in a fresh ``asyncio.run`` loop while the pooled
httpx connection stayed bound to the first one, so the second call died with
``RuntimeError: Event loop is closed`` (seen live on ``flows.list`` after
``journeys.ping``).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from unpod import Client
from unpod.management._auth import TokenAuth


class _Ok(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, so the pool reuses the socket

    def do_GET(self) -> None:
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def test_two_sync_calls_share_one_connection_without_closing_the_loop():
    server = HTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/platform"
        client = Client(base_url=base, auth=TokenAuth(token="t", org_handle="o"))
        assert client.journeys.ping() == {"ok": True}
        assert client.journeys.ping() == {"ok": True}
        client.close()
    finally:
        server.shutdown()
