"""Browser playground server — static UI + /connect proxy + in-process agent.

Single command (`task playground`):
  GET  /              → pipe-ui (pipe-ui/dist)
  GET  /health        → {"status": "ok"}
  POST /connect       → proxies remote supervoice /connect, rewrites ws_url
                        to the configured SUPERVOICE_URL host.

The developer agent (agent.py) runs as an asyncio task and registers with the
remote supervoice. Set EXTERNAL_AGENT=1 to run your own agent script instead.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

_HERE = Path(__file__).parent
load_dotenv(_HERE / ".env")

import _urls  # noqa: E402  (local helper, after dotenv)
from unpod._base_url import ws_base  # noqa: E402  (after dotenv)

_PORT = int(os.getenv("PLAYGROUND_PORT", "9100"))
_SUPERVOICE_URL = os.getenv("SUPERVOICE_URL") or ws_base() or "ws://127.0.0.1:9000"
_UI_DIST = _HERE / "pipe-ui" / "dist"


def _check_keys() -> None:
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("Missing LLM key: set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env")
        sys.exit(1)


async def _run_agent_task() -> None:
    from agent import run_agent

    await asyncio.sleep(1)  # let uvicorn bind first
    try:
        await run_agent()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"[playground] agent exited: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if os.getenv("EXTERNAL_AGENT", "0") == "1":
        logger.info("[playground] EXTERNAL_AGENT=1 — run your own agent script")
        yield
        return
    task = asyncio.create_task(_run_agent_task())
    logger.info(f"[playground] up — open http://localhost:{_PORT}")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def build_app() -> FastAPI:
    app = FastAPI(title="unpod-browser-playground", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/connect")
    async def connect(agent_id: str = "browser-playground") -> dict[str, Any]:
        """Proxy supervoice /connect, rewrite ws_url to the configured host."""
        target = _urls.connect_http_url(_SUPERVOICE_URL)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(target, params={"agent_id": agent_id})
            resp.raise_for_status()
        data = dict(resp.json())
        data["ws_url"] = _urls.audio_ws_url(_SUPERVOICE_URL)
        return data

    if _UI_DIST.exists():
        app.mount("/", StaticFiles(directory=_UI_DIST, html=True), name="ui")
    else:
        logger.warning(
            "[playground] pipe-ui/dist not found — run 'task playground-ui-build'"
        )

    return app


def _open_browser() -> None:
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{_PORT}")).start()


if __name__ == "__main__":
    _check_keys()
    _open_browser()
    uvicorn.run(build_app(), host="127.0.0.1", port=_PORT, log_level="info")
