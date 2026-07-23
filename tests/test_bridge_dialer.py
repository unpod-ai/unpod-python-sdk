"""Runner-side per-call bridge dialer (v2 dial-out transport)."""

import json

import pytest
import websockets

from unpod.connectivity.bridge_dialer import dial_bridge


def _worker_side_frames(agent_id: str = "ag-call") -> list[str]:
    return [
        json.dumps(
            {
                "event": "hello.ack",
                "protocol_version": 1,
                "negotiated_events": [],
                "negotiated_verbs": [],
                "call_id": "s1",
                "session_id": "s1",
                "job_id": "j1",
                "room_id": "s1",
            }
        ),
        json.dumps(
            {
                "event": "call.started",
                "session_id": "s1",
                "job_id": "j1",
                "room_id": "s1",
                "agent_id": agent_id,
                "metadata": {},
            }
        ),
    ]


@pytest.mark.anyio
async def test_dial_bridge_runs_entrypoint() -> None:
    seen: dict[str, object] = {}
    got_paths: list[str] = []

    async def fake_worker(ws) -> None:
        got_paths.append(ws.request.path)
        raw = await ws.recv()  # runner's hello
        assert json.loads(raw)["event"] == "hello"
        for frame in _worker_side_frames():
            await ws.send(frame)
        # Then keep the socket open until the runner closes.
        try:
            await ws.recv()
        except websockets.ConnectionClosed:
            pass

    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        seen["session_id"] = ctx.session_id
        seen["agent_id"] = ctx.agent_id

    async with websockets.serve(fake_worker, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await dial_bridge(
            f"ws://127.0.0.1:{port}/call/s1",
            call_token="tok-1",
            entrypoint=entrypoint,
            agent_id="ag-runner",
        )

    assert seen["session_id"] == "s1"
    assert seen["agent_id"] == "ag-call"
    assert got_paths and "token=tok-1" in got_paths[0]


@pytest.mark.anyio
async def test_dial_bridge_preserves_existing_query_params() -> None:
    got_paths: list[str] = []

    async def fake_worker(ws) -> None:
        got_paths.append(ws.request.path)
        await ws.recv()
        for frame in _worker_side_frames():
            await ws.send(frame)
        try:
            await ws.recv()
        except websockets.ConnectionClosed:
            pass

    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        pass

    async with websockets.serve(fake_worker, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await dial_bridge(
            f"ws://127.0.0.1:{port}/call/s1?a=b",
            call_token="tok-2",
            entrypoint=entrypoint,
            agent_id="bot",
        )

    assert got_paths and "a=b" in got_paths[0] and "token=tok-2" in got_paths[0]
