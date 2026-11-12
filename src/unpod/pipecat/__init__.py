"""Connect a Pipecat bot to Unpod calls over an audio stream.

Your ``answer_url`` returns a ``stream`` action (see
:mod:`unpod.telephony.answer`); Unpod's SBC then opens a WebSocket to your bot
and streams the call's audio both ways. On the bot side::

    from unpod import AsyncClient
    from unpod.pipecat import UnpodFrameSerializer, parse_unpod_websocket, verify_stream_request

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        if not verify_stream_request(websocket.headers, WEBHOOK_SECRET):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        start = await parse_unpod_websocket(websocket)
        serializer = UnpodFrameSerializer.from_start(start, client=unpod)
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True, audio_out_enabled=True,
                add_wav_header=False, serializer=serializer),
        )

Requires the ``pipecat`` extra: ``pip install "unpod[pipecat]"``. The
protocol helpers (:mod:`unpod.telephony.stream`) import without Pipecat.
"""

from __future__ import annotations

from unpod.pipecat.handshake import parse_unpod_websocket
from unpod.telephony.stream import (
    L16,
    MULAW,
    MediaFormat,
    StreamStart,
    parse_start,
    verify_stream_request,
)

try:
    from unpod.pipecat.serializer import UnpodFrameSerializer
except ImportError as exc:  # pipecat-ai not installed
    _IMPORT_ERROR = exc

    def __getattr__(name: str):
        if name == "UnpodFrameSerializer":
            raise ImportError(
                'UnpodFrameSerializer needs Pipecat: pip install "unpod[pipecat]"'
            ) from _IMPORT_ERROR
        raise AttributeError(name)


__all__ = [
    "L16",
    "MULAW",
    "MediaFormat",
    "StreamStart",
    "UnpodFrameSerializer",
    "parse_start",
    "parse_unpod_websocket",
    "verify_stream_request",
]
