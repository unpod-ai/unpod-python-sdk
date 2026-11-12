"""Read the stream handshake off a freshly accepted WebSocket."""

from __future__ import annotations

from typing import Any, Protocol

from unpod.telephony.stream import StreamStart, decode_message, parse_start

#: How many frames to read looking for ``start`` before giving up. The SBC
#: sends ``start`` first; the slack tolerates a leading ``connected`` frame.
MAX_HANDSHAKE_FRAMES = 3


class _TextWebSocket(Protocol):
    async def receive_text(self) -> str: ...


async def parse_unpod_websocket(
    websocket: _TextWebSocket | Any, *, max_frames: int = MAX_HANDSHAKE_FRAMES
) -> StreamStart:
    """Consume frames up to and including ``start`` and return it parsed.

    Call it after ``websocket.accept()`` and before building the transport,
    the way Pipecat's ``parse_telephony_websocket`` is used. The ``start``
    frame is consumed here, so the transport's receive loop begins with the
    caller's audio.

    Raises ``ValueError`` when no ``start`` arrives within ``max_frames``.
    A disconnect raises whatever the WebSocket library raises.
    """
    for _ in range(max_frames):
        message = decode_message(await websocket.receive_text())
        if message.get("event") == "start":
            return parse_start(message)
    raise ValueError(f"no 'start' event in the first {max_frames} stream frames")


__all__ = ["MAX_HANDSHAKE_FRAMES", "parse_unpod_websocket"]
