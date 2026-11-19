"""``UnpodFrameSerializer``: Pipecat frames ↔ the Unpod audio-stream protocol.

Built for Pipecat's ``FastAPIWebsocketTransport``; the constructor takes the
stream id and call id. Notes:

* the audio format comes from the ``start`` event (μ-law or A-law 8k, or L16
  8k/16k/24k),
  instead of being assumed;
* the bot's hangup goes through the Unpod call API
  (``client.telephony.calls.hangup``).

Works across Pipecat 1.x: it does its own G.711 coding and resampling rather
than the base-class helpers (Pipecat 1.16+) or ``pipecat.audio.utils``'s μ-law
functions (deprecated in 1.16).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from pipecat.audio.dtmf.types import KeypadEntry
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InputDTMFFrame,
    InputTransportMessageFrame,
    InterruptionFrame,
    OutputDTMFFrame,
    OutputDTMFUrgentFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.serializers.base_serializer import FrameSerializer

from unpod.pipecat.g711 import alaw_to_pcm, pcm_to_alaw, pcm_to_ulaw, ulaw_to_pcm
from unpod.telephony.stream import (
    ALAW,
    L16,
    MULAW,
    MediaFormat,
    StreamStart,
    clear_audio_message,
    decode_message,
    play_audio_message,
    send_dtmf_message,
)

if TYPE_CHECKING:
    from unpod.client import AsyncClient

logger = logging.getLogger("unpod.pipecat")

#: ``async def hangup(call_id: str) -> None``
HangupFn = Callable[[str], Awaitable[Any]]

#: Call-API statuses that mean "the call is already over", not a failure.
_ALREADY_ENDED = {404, 409, 410}


class UnpodFrameSerializer(FrameSerializer):
    """Serializer for Unpod SuperSBC audio streams.

    Build it from the parsed ``start`` event::

        start = await parse_unpod_websocket(websocket)
        serializer = UnpodFrameSerializer.from_start(start, client=unpod_client)

    With ``auto_hang_up`` (default), an ``EndFrame``/``CancelFrame`` hangs the
    call up through the Unpod call API, so pass an :class:`~unpod.AsyncClient`
    (or a ``hangup`` coroutine). The SBC keeps the call up when the WebSocket
    closes, so a bot that ends without hanging up leaves the caller in silence.
    """

    class InputParams(FrameSerializer.InputParams):
        """Configuration for :class:`UnpodFrameSerializer`.

        Parameters:
            auto_hang_up: Hang the call up on ``EndFrame``/``CancelFrame``.
            hangup_drain_secs: On ``EndFrame``, wait up to this long for the
                audio already sent with ``playAudio`` to finish playing before
                hanging up, so the bot's last words are not cut off. The SBC
                plays in real time, so the wait is what is still queued there.
                ``CancelFrame`` hangs up at once.
            output_format: Format for ``playAudio``. Defaults to the stream's
                input format, which is what the SBC expects back.
        """

        auto_hang_up: bool = True
        hangup_drain_secs: float = 5.0
        output_format: MediaFormat | None = None

    def __init__(
        self,
        stream_id: str,
        call_id: str,
        *,
        media_format: MediaFormat | None = None,
        client: AsyncClient | None = None,
        hangup: HangupFn | None = None,
        params: InputParams | None = None,
    ):
        params = params or UnpodFrameSerializer.InputParams()
        super().__init__(params)
        self._params: UnpodFrameSerializer.InputParams = params

        if not stream_id or not call_id:
            raise ValueError("stream_id and call_id are required")
        if params.auto_hang_up and hangup is None and client is None:
            raise ValueError(
                "auto_hang_up is enabled but neither client= nor hangup= was given; "
                "pass an unpod AsyncClient, or params=InputParams(auto_hang_up=False)"
            )

        self._stream_id = stream_id
        self._call_id = call_id
        self._in_format = (media_format or MediaFormat()).validate_supported()
        self._out_format = (
            params.output_format or self._in_format
        ).validate_supported()
        self._hangup_fn = hangup or (
            _client_hangup(client) if client is not None else None
        )
        self._hangup_attempted = False

        self._pipeline_rate = 0
        self._in_resampler = create_stream_resampler()
        self._out_resampler = create_stream_resampler()
        self._out_rate = 0
        # Monotonic time when the audio sent so far finishes playing at the SBC.
        self._playout_until = 0.0

    @classmethod
    def from_start(
        cls,
        start: StreamStart,
        *,
        client: AsyncClient | None = None,
        hangup: HangupFn | None = None,
        params: InputParams | None = None,
    ) -> UnpodFrameSerializer:
        """Build a serializer from a parsed ``start`` event."""
        return cls(
            start.stream_id,
            start.call_id,
            media_format=start.media_format,
            client=client,
            hangup=hangup,
            params=params,
        )

    @property
    def stream_id(self) -> str:
        return self._stream_id

    @property
    def call_id(self) -> str:
        return self._call_id

    @property
    def input_format(self) -> MediaFormat:
        return self._in_format

    @property
    def output_format(self) -> MediaFormat:
        return self._out_format

    async def setup(self, setup: FrameProcessorSetup):
        """Record the pipeline's input rate; incoming audio is converted to it."""
        await super().setup(setup)
        self._pipeline_rate = setup.audio_in_sample_rate

    # ── pipeline → SBC ──────────────────────────────────────────────────────

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, (EndFrame, CancelFrame)):
            if self._params.auto_hang_up and not self._hangup_attempted:
                self._hangup_attempted = True
                if isinstance(frame, EndFrame):
                    await self._drain_playout()
                await self._hang_up()
            return None
        if isinstance(frame, InterruptionFrame):
            # Drop the interrupted reply's tail still inside the resampler.
            self._out_resampler = create_stream_resampler()
            self._playout_until = 0.0  # clearAudio empties the SBC's queue
            return clear_audio_message(self._stream_id)
        if isinstance(frame, AudioRawFrame):
            payload = await self._encode(frame)
            if not payload:
                return None
            self._track_playout(payload)
            return play_audio_message(self._stream_id, payload, self._out_format)
        if isinstance(frame, (OutputDTMFFrame, OutputDTMFUrgentFrame)):
            # `buttons` only exists from Pipecat ~1.3; 1.0 has `button` alone.
            buttons = getattr(frame, "buttons", None) or (
                [frame.button] if frame.button else []
            )
            digits = "".join(str(b.value) for b in buttons)
            if not digits:
                return None
            try:
                return send_dtmf_message(self._stream_id, digits)
            except ValueError:
                logger.warning("ignoring invalid DTMF digits %r", digits)
                return None
        if isinstance(
            frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)
        ):
            if self.should_ignore_frame(frame):
                return None
            return json.dumps(frame.message)
        return None

    async def _encode(self, frame: AudioRawFrame) -> bytes:
        fmt = self._out_format
        # A stream resampler converts from one rate; a new rate starts a new one.
        if self._out_rate and frame.sample_rate != self._out_rate:
            self._out_resampler = create_stream_resampler()
        self._out_rate = frame.sample_rate
        pcm = await self._out_resampler.resample(
            frame.audio, frame.sample_rate, fmt.sample_rate
        )
        if fmt.encoding == MULAW:
            return pcm_to_ulaw(pcm)
        if fmt.encoding == ALAW:
            return pcm_to_alaw(pcm)
        return pcm

    def _track_playout(self, payload: bytes) -> None:
        fmt = self._out_format
        samples = len(payload) // 2 if fmt.encoding == L16 else len(payload)
        now = time.monotonic()
        self._playout_until = max(self._playout_until, now) + samples / fmt.sample_rate

    async def _drain_playout(self) -> None:
        remaining = self._playout_until - time.monotonic()
        wait = min(remaining, self._params.hangup_drain_secs)
        if wait > 0:
            logger.debug("waiting %.2fs for queued audio before hanging up", wait)
            await asyncio.sleep(wait)

    async def _hang_up(self) -> None:
        if self._hangup_fn is None:
            return
        try:
            await self._hangup_fn(self._call_id)
            logger.debug("hung up Unpod call %s", self._call_id)
        except Exception as exc:  # noqa: BLE001 - a hangup failure must not break pipeline shutdown
            status = getattr(exc, "status_code", None)
            if status in _ALREADY_ENDED:
                logger.debug("Unpod call %s already ended (%s)", self._call_id, status)
            else:
                logger.error("failed to hang up Unpod call %s: %s", self._call_id, exc)

    # ── SBC → pipeline ──────────────────────────────────────────────────────

    async def deserialize(self, data: str | bytes) -> Frame | None:
        message = decode_message(data)
        event = message.get("event")
        if event == "media":
            media = message.get("media") or {}
            # Only the caller's audio feeds the pipeline: with audioTrack="both"
            # the SBC also sends what it plays to the caller (track "outbound").
            if media.get("track", "inbound") != "inbound":
                return None
            return await self._decode_media(media)
        if event == "dtmf":
            digit = (message.get("dtmf") or {}).get("digit")
            if not digit:
                return None
            try:
                return InputDTMFFrame(KeypadEntry(str(digit)))
            except ValueError:
                logger.warning("ignoring invalid DTMF digit %r", digit)
                return None
        if event in ("playedStream", "clearedAudio"):
            # Replies to the bot's own `checkpoint` / `clearAudio`; surfaced as
            # transport messages so a processor can wait on a checkpoint.
            return InputTransportMessageFrame(message=message)
        # `start` is consumed by parse_unpod_websocket; unknown events carry
        # nothing for the pipeline (the SBC ends a stream by closing the socket).
        return None

    async def _decode_media(self, media: dict[str, Any]) -> Frame | None:
        encoded = media.get("payload")
        if not encoded:
            return None
        try:
            raw = base64.b64decode(encoded)
        except (ValueError, TypeError):
            logger.warning("ignoring media frame with invalid base64 payload")
            return None
        rate = self._pipeline_rate or self._in_format.sample_rate
        fmt = self._in_format
        if fmt.encoding == MULAW:
            raw = ulaw_to_pcm(raw)
        elif fmt.encoding == ALAW:
            raw = alaw_to_pcm(raw)
        elif fmt.encoding != L16:  # pragma: no cover - rejected in __init__
            return None
        pcm = await self._in_resampler.resample(raw, fmt.sample_rate, rate)
        if not pcm:
            return None
        return InputAudioRawFrame(audio=pcm, num_channels=1, sample_rate=rate)


def _client_hangup(client: AsyncClient) -> HangupFn:
    async def hangup(call_id: str) -> None:
        await client.telephony.calls.hangup(call_id)

    return hangup


__all__ = ["HangupFn", "UnpodFrameSerializer"]
