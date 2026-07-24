"""Offline smoke test of the full management flow — no backend required.

Exercises the real SDK code path end to end against a fake in-memory
Control Plane wired at the HTTP transport layer:

    voice_profiles.list  →  pipes.create  →  calls.create  →  calls.get

Every model is validated by pydantic exactly as it would be against live
supervoice; only the network is faked. Use this to prove request-shaping,
response-parsing, and the flow ordering without Mongo/Redis/Prefect.

Run:
    uv run python examples/flow_smoke.py
"""

from __future__ import annotations

import asyncio
import json

import httpx

from unpod import AsyncClient

# ── Fake Control Plane ────────────────────────────────────────────────────────
# Maps (METHOD, path) -> response body. Bodies use the bare {"data": ...}
# envelope that the supervoice /v1 plane returns.

_PROFILE = {
    "id": "vp_1",
    "name": "aria-en",
    "languages": ["en"],
    "tts_provider": "cartesia",
    "tts_voice": "aria",
    "latency_ms": 320,
}
_PIPE = {
    "pipe_id": "pipe_1",
    "name": "smoke-pipe",
    "voice_profile": "aria-en",
    "agent_id": "smoke-agent",
    "number_id": None,
    "recording": True,
}
_CALL = {
    "call_id": "call_1",
    "pipe_id": "pipe_1",
    "from_number": "+10000000000",
    "to_number": "+19995550001",
    "status": "pending",
    "session_id": "sess_1",
}


def _handler(request: httpx.Request) -> httpx.Response:
    key = (request.method, request.url.path)
    routes = {
        ("GET", "/v1/voice-profiles"): {"data": [_PROFILE]},
        ("POST", "/v1/pipes"): {"data": _PIPE},
        ("GET", "/v1/pipes"): {"data": [_PIPE]},
        ("POST", "/v1/calls"): {"data": _CALL},
        ("GET", "/v1/calls/call_1"): {"data": {**_CALL, "status": "completed", "duration_s": 12}},
    }
    body = routes.get(key)
    if body is None:
        return httpx.Response(404, json={"error": f"no route {key}"})
    print(f"  → {request.method} {request.url.path}  "
          f"body={request.content.decode() or '-'}")
    return httpx.Response(200, json=body)


async def main() -> None:
    client = AsyncClient(api_key="unpod_sk_smoke", base_url="https://fake.test")
    # Splice the fake transport into the shared httpx client.
    client._http._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="https://fake.test",
        transport=httpx.MockTransport(_handler),
    )

    print("[1] list voice profiles")
    profiles = await client.voice_profiles.list(language="en")
    profile = profiles[0]
    print(f"    picked {profile.name} ({profile.tts_provider}/{profile.tts_voice})\n")

    print("[2] create speech pipe")
    pipe = await client.pipes.create(
        name="smoke-pipe", voice_profile=profile.name,
        agent_id="smoke-agent", recording=True,
    )
    print(f"    pipe_id={pipe.pipe_id}\n")

    print("[3] dispatch call")
    call = await client.calls.create(
        pipe_id=pipe.pipe_id, to_number="+19995550001",
        instructions="Greet warmly.",
    )
    print(f"    call_id={call.call_id} status={call.status}\n")

    print("[4] poll call status")
    done = await client.calls.get(call.call_id)
    print(f"    status={done.status} duration={done.duration_s}s\n")

    await client.close()
    print("FLOW OK — profile → pipe → call → status all validated.")


if __name__ == "__main__":
    asyncio.run(main())
