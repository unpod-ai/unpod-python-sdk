"""Build ``answer_url`` responses: :class:`Response`.

When a call reaches a number whose inbound config has an ``answer_url``
(``client.telephony.inbound``), or a call made with ``calls.create(answer_url=...)``
is answered, SuperSBC asks that URL what to do. Reply with the actions, in
either of the two forms the SBC parses (``src/proxy/answer_url/{actions,xml}.rs``):

    from unpod.telephony.answer import Response

    resp = Response().stream("wss://bot.example.com/ws")
    return JSONResponse(resp.to_json())                       # {"actions": [...]}
    return Response(resp.to_xml(), media_type="application/xml")  # Plivo-style XML

The XML form is Plivo-compatible, so an existing Plivo answer server keeps
working; this builder replaces ``plivo.plivoxml``.

``stream`` needs the SuperSBC ``stream`` verb (design 2026-11-11, inbound
first). Until that ships the SBC rejects it as an unknown action.
"""

from __future__ import annotations

from typing import Any, Literal
from xml.sax.saxutils import escape, quoteattr

from unpod.telephony.stream import DEFAULT_ENCODING, DEFAULT_SAMPLE_RATE, MediaFormat

_WS_SCHEMES = ("ws://", "wss://")


class Response:
    """An ordered list of answer actions. Every method returns ``self``."""

    def __init__(self) -> None:
        self._actions: list[dict[str, Any]] = []

    @property
    def actions(self) -> list[dict[str, Any]]:
        """The actions as SBC JSON objects (a copy)."""
        return [dict(a) for a in self._actions]

    def stream(
        self,
        url: str,
        *,
        bidirectional: bool = True,
        content_type: str = DEFAULT_ENCODING,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        status_callback_url: str | None = None,
    ) -> Response:
        """Stream the call's audio to ``url`` (``ws://`` or ``wss://``).

        The SBC dials the URL and speaks the Unpod stream protocol
        (:mod:`unpod.telephony.stream`). Stream lifecycle events
        (``StreamStarted``/``StreamStopped``/``StreamFailed``) go to
        ``status_callback_url``, or to the ``answer_url`` when unset.
        """
        if not url.startswith(_WS_SCHEMES):
            raise ValueError("stream url must start with ws:// or wss://")
        MediaFormat(encoding=content_type, sampleRate=sample_rate).validate_supported()
        action: dict[str, Any] = {
            "type": "stream",
            "url": url,
            "bidirectional": bidirectional,
            "content_type": content_type,
            "sample_rate": sample_rate,
        }
        if status_callback_url:
            action["status_callback_url"] = status_callback_url
        self._actions.append(action)
        return self

    def speak(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
        loop: int | None = None,
    ) -> Response:
        """Say ``text`` with the SBC's TTS."""
        self._actions.append(
            _drop_none(
                {
                    "type": "speak",
                    "text": text,
                    "voice": voice,
                    "language": language,
                    "loop": loop,
                }
            )
        )
        return self

    def play(self, url: str, *, loop: int | None = None) -> Response:
        """Play an MP3/WAV from an https URL."""
        self._actions.append(_drop_none({"type": "play", "url": url, "loop": loop}))
        return self

    def wait(self, seconds: int) -> Response:
        """Pause for ``seconds``."""
        self._actions.append({"type": "wait", "seconds": seconds})
        return self

    def dial(
        self,
        number: str,
        *,
        caller_id: str | None = None,
        ring_timeout: int | None = None,
        record: bool | None = None,
        status_callback_url: str | None = None,
    ) -> Response:
        """Connect the caller to ``number`` (e.g. hand off to a human)."""
        self._actions.append(
            _drop_none(
                {
                    "type": "dial",
                    "number": number,
                    "caller_id": caller_id,
                    "ring_timeout": ring_timeout,
                    "record": record,
                    "status_callback_url": status_callback_url,
                }
            )
        )
        return self

    def hangup(self, *, reason: str | None = None, code: int | None = None) -> Response:
        """End the call."""
        self._actions.append(
            _drop_none({"type": "hangup", "reason": reason, "code": code})
        )
        return self

    # ── rendering ───────────────────────────────────────────────────────────

    def to_json(self) -> dict[str, list[dict[str, Any]]]:
        """``{"actions": [...]}``: the SBC's JSON answer body."""
        return {"actions": self.actions}

    def to_xml(self) -> str:
        """Plivo-style ``<Response>`` XML."""
        body = "".join(_xml_action(a) for a in self._actions)
        return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>{body}</Response>'


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _attrs(pairs: list[tuple[str, Any]]) -> str:
    out = []
    for name, value in pairs:
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        out.append(f" {name}={quoteattr(str(value))}")
    return "".join(out)


_Verb = Literal["stream", "speak", "play", "wait", "dial", "hangup"]


def _xml_action(a: dict[str, Any]) -> str:
    kind: _Verb = a["type"]
    if kind == "stream":
        attrs = _attrs(
            [
                ("bidirectional", a["bidirectional"]),
                # Plivo's own shape: the rate rides on the content type.
                ("contentType", f"{a['content_type']};rate={a['sample_rate']}"),
                ("statusCallbackUrl", a.get("status_callback_url")),
            ]
        )
        return f"<Stream{attrs}>{escape(a['url'])}</Stream>"
    if kind == "speak":
        attrs = _attrs(
            [
                ("voice", a.get("voice")),
                ("language", a.get("language")),
                ("loop", a.get("loop")),
            ]
        )
        return f"<Speak{attrs}>{escape(a['text'])}</Speak>"
    if kind == "play":
        return f"<Play{_attrs([('loop', a.get('loop'))])}>{escape(a['url'])}</Play>"
    if kind == "wait":
        return f"<Wait{_attrs([('length', a['seconds'])])}/>"
    if kind == "dial":
        attrs = _attrs(
            [
                ("callerId", a.get("caller_id")),
                ("timeout", a.get("ring_timeout")),
                ("record", a.get("record")),
                ("callbackUrl", a.get("status_callback_url")),
            ]
        )
        return f"<Dial{attrs}><Number>{escape(a['number'])}</Number></Dial>"
    if kind == "hangup":
        return (
            f"<Hangup{_attrs([('reason', a.get('reason')), ('code', a.get('code'))])}/>"
        )
    raise ValueError(f"unknown action {kind!r}")  # pragma: no cover


__all__ = ["Response"]
