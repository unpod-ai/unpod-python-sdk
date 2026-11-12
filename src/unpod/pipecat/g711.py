"""G.711 μ-law ↔ 16-bit PCM (little-endian), via lookup tables.

Kept here rather than imported so the serializer behaves the same on every
Pipecat 1.x: ``pipecat.audio.g711`` only exists from 1.16, and the older
``pipecat.audio.utils`` μ-law helpers are deprecated there. numpy comes with
Pipecat.
"""

from __future__ import annotations

import numpy as np

_BIAS = 0x84


def _build_decode() -> np.ndarray:
    u = ~np.arange(256) & 0xFF
    exponent = (u >> 4) & 0x07
    magnitude = ((((u & 0x0F) << 3) + _BIAS) << exponent) - _BIAS
    return np.where(u & 0x80, -magnitude, magnitude).astype("<i2")


# Segment upper bounds of the 14-bit magnitude (Sun/CCITT reference g711.c).
_SEG_ENDS = np.array([0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF])


def _build_encode() -> np.ndarray:
    # The reference encoder: drop to 14 bits, bias by 0x21, find the segment,
    # keep 4 mantissa bits, invert. Same table as pipecat.audio.g711 (1.16+).
    sample = np.arange(-32768, 32768, dtype=np.int32) >> 2
    magnitude = np.abs(sample) + 0x21
    segment = np.searchsorted(_SEG_ENDS, magnitude)
    code = np.where(
        segment > 7, 0x7F, (segment << 4) | ((magnitude >> (segment + 1)) & 0x0F)
    )
    return (code ^ np.where(sample < 0, 0x7F, 0xFF)).astype(np.uint8)


_DECODE = _build_decode()
_ENCODE = _build_encode()


def ulaw_to_pcm(ulaw: bytes) -> bytes:
    """μ-law (1 byte/sample) → 16-bit little-endian PCM."""
    if not ulaw:
        return b""
    return _DECODE[np.frombuffer(ulaw, dtype=np.uint8)].tobytes()


def pcm_to_ulaw(pcm: bytes) -> bytes:
    """16-bit little-endian PCM → μ-law. A trailing odd byte is dropped."""
    usable = len(pcm) - (len(pcm) % 2)
    if not usable:
        return b""
    samples = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.int32)
    return _ENCODE[samples + 32768].tobytes()


__all__ = ["pcm_to_ulaw", "ulaw_to_pcm"]
