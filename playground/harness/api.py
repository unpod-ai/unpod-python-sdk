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
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from playground.agents.catalog import CATALOG, get_agent, resolve_agent
from playground.agents.flows import flow_registry
from playground.harness.control import ControlError, SessionRegistry
from playground.harness.events import EventBus
from playground.harness.runner import build_runner
from unpod._base_url import ws_base

_HERE = Path(__file__).parent
_UI_DIST = _HERE.parent / "web" / "dist"

# ── Django token manager ──────────────────────────────────────────────────────
_django_token: str | None = os.getenv("DJANGO_API_TOKEN") or None


async def _django_login() -> str | None:
    """Login to Django and cache the access token."""
    global _django_token
    url = os.getenv("DJANGO_API_URL")
    email = os.getenv("DJANGO_EMAIL")
    password = os.getenv("DJANGO_PASSWORD")
    if not url or not email or not password:
        return _django_token
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{url}/api/v1/auth/login/",
                json={"email": email, "password": password},
            )
            resp.raise_for_status()
            data = resp.json()
            token = (
                data.get("access")
                or data.get("token")
                or (data.get("data") or {}).get("access")
                or (data.get("data") or {}).get("token")
            )
            if token:
                _django_token = token
                logger.info("[django] token refreshed")
    except Exception as exc:
        logger.warning(f"[django] login failed: {type(exc).__name__}: {exc}")
    return _django_token


async def _django_get(path: str) -> httpx.Response | None:
    """GET with auto-relogin on 401."""
    global _django_token
    url = os.getenv("DJANGO_API_URL")
    if not url:
        return None
    if not _django_token:
        await _django_login()
    if not _django_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{url}{path}",
                headers={"Authorization": f"Bearer {_django_token}"},
            )
            if resp.status_code == 401:
                await _django_login()
                if not _django_token:
                    return None
                resp = await client.get(
                    f"{url}{path}",
                    headers={"Authorization": f"Bearer {_django_token}"},
                )
            return resp
    except Exception as exc:
        logger.warning(f"[django] GET {path} failed: {exc}")
        return None


def _audio_ws_url(supervoice_url: str) -> str:
    """Return the browser audio WebSocket URL for a supervoice ws base."""
    return supervoice_url.rstrip("/") + "/ws/audio"


def _http_base_url(supervoice_url: str) -> str:
    """Convert ws(s):// → http(s):// base URL."""
    base = supervoice_url.rstrip("/")
    if base.startswith("wss://"):
        return "https://" + base[len("wss://") :]
    if base.startswith("ws://"):
        return "http://" + base[len("ws://") :]
    return base


def _connect_http_url(supervoice_url: str) -> str:
    """Return the HTTP(S) /connect URL for a supervoice ws(s) base."""
    return _http_base_url(supervoice_url) + "/connect"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the in-process AgentRunner for the configured agent."""
    agent_name = os.getenv("AGENT_NAME", "flows")
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
    # Speech backend resolution (mirrors examples/browser_playground/run.py):
    # explicit SUPERVOICE_URL wins → else the hosted wss://<UNPOD_BASE_URL>
    # (so a fresh clone needs no local supervoice) → else the local dev service.
    app.state.supervoice_url = (
        os.getenv("SUPERVOICE_URL") or ws_base() or "ws://127.0.0.1:9000"
    )

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

    async def _fetch_django_voice_profiles() -> list[dict[str, Any]]:
        if not os.getenv("DJANGO_API_URL"):
            return []
        try:
            resp = await _django_get("/api/v1/core/voice-profiles/")
            if resp is None or resp.status_code != 200:
                return []
            raw = resp.json()
            profiles = raw.get("data", raw) if isinstance(raw, dict) else raw
            return [
                {
                    "id": p["agent_profile_id"],
                    "name": p.get("name", p["agent_profile_id"]),
                    "stt": p.get("transcriber", {}).get("name", ""),
                    "tts": p.get("voice", {}).get("name", ""),
                    "description": p.get("description", ""),
                }
                for p in profiles
            ]
        except Exception as exc:
            logger.warning(f"[playground] django voice-profiles fetch failed: {exc}")
            return []

    @app.get("/playground/config")
    async def get_config() -> dict[str, Any]:
        llm = "claude-3-haiku" if os.getenv("ANTHROPIC_API_KEY") else "gpt-4.1-mini"
        voice_profiles: list[dict[str, Any]] = await _fetch_django_voice_profiles()
        if not voice_profiles:
            try:
                base = _http_base_url(app.state.supervoice_url)
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(f"{base}/voice-profiles")
                    if resp.status_code == 200:
                        voice_profiles = [
                            {
                                "id": p["profile_id"],
                                "name": p.get("name", p["profile_id"]),
                                "stt": p.get("stt_provider", ""),
                                "tts": p.get("tts_provider", ""),
                                "description": p.get("description", ""),
                            }
                            for p in resp.json()
                        ]
            except Exception as exc:
                logger.warning(f"[playground] voice-profiles fetch failed: {exc}")
        return {
            "voice_profiles": voice_profiles,
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

    @app.get("/playground/flows")
    async def list_flows() -> dict[str, Any]:
        """Return the conversation flows the worker can switch between."""
        infos = flow_registry()
        return {
            "flows": [
                {
                    "id": f.id,
                    "label": f.label,
                    "nodes": f.nodes,
                    "initial_node": f.initial_node,
                    "description": f.description,
                }
                for f in infos
            ],
            "active": infos[0].id if infos else None,
        }

    @app.post("/playground/sessions")
    async def create_session(
        agent: str | None = None,
        voice_profile_id: str | None = None,
        flow: str | None = None,
    ) -> dict[str, Any]:
        """Proxy supervoice /connect, return a transport descriptor."""
        spec = resolve_agent(agent) if agent else app.state.agent_spec
        if spec is None:
            return {"error": f"unknown agent {agent!r}"}
        target = _connect_http_url(app.state.supervoice_url)
        params: dict[str, str] = {"agent_id": spec.agent_id}
        if voice_profile_id:
            params["voice_profile_id"] = voice_profile_id
        if flow:
            params["flow"] = flow
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(target, params=params)
            resp.raise_for_status()
        data = dict(resp.json())
        # Carry agent_id (and voice profile / flow) on the audio socket so the
        # dev speech service routes the call to this agent's worker and starts
        # it on the selected flow.
        query: dict[str, str] = {"agent_id": spec.agent_id}
        if voice_profile_id:
            query["voice_profile_id"] = voice_profile_id
        if flow:
            query["flow"] = flow
        data["ws_url"] = f"{_audio_ws_url(app.state.supervoice_url)}?{urlencode(query)}"
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
        # Reflect the post-switch node to the UI immediately (switch_flow resets
        # the machine to the new flow's initial node — no turn fires on its own).
        if action == "switch_flow":
            try:
                adapter = app.state.registry.resolve(session_id).dialog_machine
                state = getattr(adapter, "state", None)
                if isinstance(state, dict):
                    app.state.bus.publish("flow_node_changed", **state)
            except Exception:  # best-effort echo; never fail the control call
                pass
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

    @app.websocket("/{_path:path}")
    async def reject_unknown_ws(ws: WebSocket) -> None:
        """Cleanly close any websocket that matches no real WS route.

        The SPA is served by a ``StaticFiles`` mount at ``/`` (below), which
        asserts an HTTP scope. A websocket on any unrouted path would otherwise
        fall through to that mount and crash the ASGI app with ``AssertionError``.
        Real WS routes (``/playground/events``) are declared above and still win;
        this only catches strays (e.g. a misdirected audio socket — audio is
        meant to hit supervoice's ``/ws/audio`` directly, not the harness).
        """
        logger.debug(f"[playground] closing unmatched websocket: {ws.url.path}")
        await ws.close(code=1000)

    if _UI_DIST.exists():
        app.mount("/", StaticFiles(directory=_UI_DIST, html=True), name="ui")
    else:
        logger.warning("[playground] web/dist not found — run the Vite build")

    return app
