"""URL helpers — derive supervoice audio/connect URLs from SUPERVOICE_URL.

SUPERVOICE_URL is a websocket base (ws:// or wss://) used as the AgentRunner
base_url. The browser audio WS and the /connect HTTP endpoint are derived from it
so the playground works against a remote supervoice without editing supervoice.
"""

from __future__ import annotations


def audio_ws_url(supervoice_url: str) -> str:
    """Return the browser audio WebSocket URL for the given supervoice base."""
    return supervoice_url.rstrip("/") + "/ws/audio"


def connect_http_url(supervoice_url: str) -> str:
    """Return the HTTP(S) /connect URL for the given supervoice ws(s) base."""
    base = supervoice_url.rstrip("/")
    if base.startswith("wss://"):
        base = "https://" + base[len("wss://") :]
    elif base.startswith("ws://"):
        base = "http://" + base[len("ws://") :]
    return base + "/connect"
