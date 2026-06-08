"""Playground backend — FastAPI control plane the web UI calls.

Routes:
  GET  /playground/agents               → catalog
  GET  /playground/config               → config (voices, flows, active LLM)
  POST /playground/sessions             → proxy supervoice POST /connect
  POST /playground/sessions/{id}/control→ live control (set_llm / switch_flow)
  WS   /playground/events               → side-channel: agent events → browser
  GET  /health                          → liveness

The agent (an SDK ``AgentRunner``) runs in-process as an asyncio task and
registers with the remote supervoice referenced by ``SUPERVOICE_URL``. The
browser talks audio directly to supervoice's ``/ws/audio``; transcript / flow /
metrics ride this server's ``/playground/events`` side-channel.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from ..agents.catalog import CATALOG, get_agent
from .control import ControlError, SessionRegistry
from .events import EventBus
from .runner import build_runner
from .voice_profiles import VOICE_PROFILES

_HERE = Path(__file__).parent
_UI_DIST = _HERE.parent / "web" / "dist"


def _audio_ws_url(supervoice_url: str) -> str:
    """Return the browser audio WebSocket URL for a supervoice ws base."""
    return supervoice_url.rstrip("/") + "/ws/audio"


def _connect_http_url(supervoice_url: str) -> str:
    """Return the HTTP(S) /connect URL for a supervoice ws(s) base."""
    base = supervoice_url.rstrip("/")
    if base.startswith("wss://"):
        base = "https://" + base[len("wss://") :]
    elif base.startswith("ws://"):
        base = "http://" + base[len("ws://") :]
    return base + "/connect"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the in-process AgentRunner for the configured agent."""
    agent_name = os.getenv("AGENT_NAME", "faq_bot")
    spec = get_agent(agent_name)
    if spec is None:
        raise RuntimeError(f"unknown AGENT_NAME {agent_name!r}; see catalog")

    runner = build_runner(
        spec,
        app.state.bus,
        app.state.registry,
        base_url=app.state.supervoice_url,
        api_key=os.getenv("UNPOD_API_KEY", "dev-key"),
    )
    app.state.agent_spec = spec

    async def _run() -> None:
        await asyncio.sleep(1)  # let uvicorn bind first
        try:
            await runner.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[playground] agent exited: {exc}")

    task = asyncio.create_task(_run())
    logger.info(f"[playground] agent={spec.agent_id} → {app.state.supervoice_url}")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def build_app() -> FastAPI:
    """Build the playground FastAPI app."""
    app = FastAPI(title="unpod-playground", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.bus = EventBus()
    app.state.registry = SessionRegistry()
    app.state.supervoice_url = os.getenv("SUPERVOICE_URL", "ws://127.0.0.1:9000")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/playground/agents")
    async def list_agents() -> dict[str, Any]:
        return {
            "agents": [
                {
                    "name": spec.name,
                    "agent_id": spec.agent_id,
                    "description": spec.description,
                }
                for spec in CATALOG.values()
            ]
        }

    @app.get("/playground/config")
    async def get_config() -> dict[str, Any]:
        llm = "claude-3-haiku" if os.getenv("ANTHROPIC_API_KEY") else "gpt-4o-mini"
        return {
            "voice_profiles": [
                {"id": vp.id, "name": f"{vp.name} ({vp.stt} + {vp.tts})"}
                for vp in VOICE_PROFILES
            ],
            "flows": [
                {
                    "id": spec.agent_id,
                    "name": spec.name,
                    "description": spec.description,
                }
                for spec in CATALOG.values()
            ],
            "active_llm": os.getenv("ACTIVE_LLM", llm),
        }

    @app.post("/playground/sessions")
    async def create_session(
        agent: str | None = None,
        voice_profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Proxy supervoice /connect, return a transport descriptor."""
        spec = get_agent(agent) if agent else app.state.agent_spec
        if spec is None:
            return {"error": f"unknown agent {agent!r}"}
        target = _connect_http_url(app.state.supervoice_url)
        params: dict[str, str] = {"agent_id": spec.agent_id}
        if voice_profile_id:
            params["voice_profile_id"] = voice_profile_id
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(target, params=params)
            resp.raise_for_status()
        data = dict(resp.json())
        data["ws_url"] = _audio_ws_url(app.state.supervoice_url)
        data["transport"] = "ws"
        data["agent"] = spec.name
        return data

    @app.post("/playground/sessions/{session_id}/control")
    async def control(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        action = body.get("action", "")
        params = body.get("params", {})
        try:
            app.state.registry.apply(session_id, action, params)
        except ControlError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    @app.websocket("/playground/events")
    async def events(ws: WebSocket) -> None:
        """Side-channel: stream agent-process events to the browser."""
        await ws.accept()
        bus: EventBus = app.state.bus
        with bus.subscribe() as queue:
            try:
                while True:
                    message = await queue.get()
                    await ws.send_json(message)
            except WebSocketDisconnect:
                pass

    if _UI_DIST.exists():
        app.mount("/", StaticFiles(directory=_UI_DIST, html=True), name="ui")
    else:
        logger.warning("[playground] web/dist not found — run the Vite build")

    return app
