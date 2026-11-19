"""Build ``answer_url`` responses: :class:`Response`.

When a call reaches a number whose inbound config has an ``answer_url``
(``client.telephony.inbound``), or a call made with ``calls.create(answer_url=...)``
is answered, SuperSBC asks that URL what to do. Reply with the actions, in
either of the two forms the SBC parses (``src/proxy/answer_url/{actions,xml}.rs``):

    from unpod.telephony.answer import Response

    resp = Response().stream("wss://bot.example.com/ws")
    return JSONResponse(resp.to_json())                       # {"actions": [...]}
    return Response(resp.to_xml(), media_type="application/xml")  # XML

Supported actions (what SuperSBC parses): ``stream``, ``speak``, ``play``,
``wait``, ``record``, ``get_digits``, ``get_input`` (DTMF), ``dial`` and
``hangup``. Any other verb makes the SBC reject the whole reply.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from xml.sax.saxutils import escape, quoteattr

from unpod.telephony.stream import (
    DEFAULT_ENCODING,
    DEFAULT_SAMPLE_RATE,
    MediaFormat,
    stream_options,
)


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
        keep_call_alive: bool = True,
        audio_track: Literal["inbound", "outbound", "both"] = "inbound",
        status_callback_url: str | None = None,
        status_callback_method: Literal["GET", "POST"] | None = None,
        stream_timeout: int | None = None,
        extra_headers: str | Mapping[str, str] | None = None,
        noise_cancellation: bool | None = None,
        noise_cancellation_level: int | None = None,
    ) -> Response:
        """Stream the call's audio to ``url`` (``ws://`` or ``wss://``).

        The SBC dials the URL and speaks the Unpod stream protocol
        (:mod:`unpod.telephony.stream`). Stream lifecycle events
        (``started`` / ``stopped`` / ``failed`` / ``degraded``) go to
        ``status_callback_url`` only: without it no stream events are sent.

        ``keep_call_alive=True`` (the default) holds the call on the stream
        until the bot closes it. With ``False`` the SBC runs the stream in the
        background and moves on to the next action at once; with no next
        action it hangs up ("End Of XML Instructions").
        ``audio_track`` picks what the bot hears; a bidirectional stream must
        use ``"inbound"`` (the caller).

        ``status_callback_method`` is ``POST`` (default) or ``GET``.
        ``stream_timeout`` ends the stream after that many seconds.
        ``extra_headers`` (``"k=v,k2=v2"`` or a dict; ``[A-Za-z0-9_-]`` only)
        reach the bot in the ``start`` event and the stream events.
        ``noise_cancellation`` / ``noise_cancellation_level`` (60-100) ask the
        SBC to clean the caller audio before streaming it.
        """
        MediaFormat(encoding=content_type, sampleRate=sample_rate).validate_supported()
        action: dict[str, Any] = {
            "type": "stream",
            "url": url,
            "keep_call_alive": keep_call_alive,
            "status_callback_url": status_callback_url or None,
            "noise_cancellation": noise_cancellation,
        }
        action.update(
            stream_options(
                url,
                bidirectional=bidirectional,
                audio_track=audio_track,
                # The SBC reads the rate from the content type only
                # (``audio/x-mulaw;rate=8000``).
                content_type=f"{content_type};rate={sample_rate}",
                stream_timeout=stream_timeout,
                status_callback_method=status_callback_method,
                extra_headers=extra_headers,
                noise_cancellation_level=noise_cancellation_level,
            )
        )
        action = {k: v for k, v in action.items() if v is not None}
        self._actions.append(action)
        return self

    def speak(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
        loop: bool | int | None = None,
    ) -> Response:
        """Say ``text`` with the SBC's TTS.

        ``loop``: ``True`` repeats until the call moves on, ``False`` says it
        once. The SBC has no repeat count; ``0`` / ``1`` are accepted as the
        ``<Speak loop>`` aliases for ``True`` / ``False``.
        """
        self._actions.append(
            _drop_none(
                {
                    "type": "speak",
                    "text": text,
                    "voice": voice,
                    "language": language,
                    "loop": _loop_flag(loop),
                }
            )
        )
        return self

    def play(self, url: str, *, loop: bool | int | None = None) -> Response:
        """Play an MP3/WAV from an https URL. ``loop`` as in :meth:`speak`."""
        self._actions.append(
            _drop_none({"type": "play", "url": url, "loop": _loop_flag(loop)})
        )
        return self

    def wait(self, seconds: int) -> Response:
        """Pause for ``seconds``."""
        self._actions.append({"type": "wait", "seconds": seconds})
        return self

    def record(
        self,
        *,
        max_length: int | None = None,
        callback_url: str | None = None,
        record_session: bool | None = None,
        action: str | None = None,
        method: Literal["GET", "POST"] | None = None,
        callback_method: Literal["GET", "POST"] | None = None,
    ) -> Response:
        """Record the call (``<Record>``).

        By default the recording blocks the action list until ``max_length``
        seconds (SBC default 300). ``record_session=True`` records the whole
        session in the background and moves on at once; only then do
        ``action`` / ``method`` (requested once recording starts) and
        ``callback_method`` apply, so passing them without it raises.
        """
        if not record_session and (action or method or callback_method):
            raise ValueError(
                "action, method and callback_method need record_session=True"
            )
        if max_length is not None and max_length < 1:
            raise ValueError("max_length must be at least 1 second")
        _check_method(method, "method")
        _check_method(callback_method, "callback_method")
        self._actions.append(
            _drop_none(
                {
                    "type": "record",
                    "max_duration_secs": max_length,
                    "callback_url": callback_url,
                    "record_session": record_session or None,
                    "action": action,
                    "action_method": method,
                    "callback_method": callback_method,
                }
            )
        )
        return self

    def get_digits(
        self,
        action: str | None = None,
        *,
        method: Literal["GET", "POST"] | None = None,
        timeout: int | None = None,
        digit_timeout: int | None = None,
        finish_on_key: str | None = None,
        num_digits: int | None = None,
        retries: int | None = None,
        valid_digits: str | None = None,
        redirect: bool | None = None,
        say: str | None = None,
        play: str | None = None,
    ) -> Response:
        """Collect DTMF (``<GetDigits>``), then request ``action`` with ``Digits``.

        ``say`` (text-to-speech) and/or ``play`` (an https audio URL) are the
        prompt, played first and cut off by the first key. ``None`` fields take
        the SBC defaults: ``timeout`` 5 s, ``digit_timeout`` 2 s,
        ``finish_on_key`` ``#`` (``""`` disables it), ``num_digits`` 99,
        ``retries`` 1, ``valid_digits`` ``1234567890*#``, ``redirect`` true
        (the action URL's reply replaces the remaining actions). Parse the
        ``action`` request with :func:`unpod.telephony.parse_digits_request`.
        """
        _check_method(method, "method")
        _check_finish_on_key(finish_on_key)
        self._actions.append(
            _drop_none(
                {
                    "type": "get_digits",
                    "action": action,
                    "method": method,
                    "timeout": timeout,
                    "digit_timeout": digit_timeout,
                    "finish_on_key": finish_on_key,
                    "num_digits": num_digits,
                    "retries": retries,
                    "valid_digits": valid_digits,
                    "redirect": redirect,
                    "prompts": _prompts(say, play),
                }
            )
        )
        return self

    def get_input(
        self,
        action: str | None = None,
        *,
        method: Literal["GET", "POST"] | None = None,
        input_type: Literal["dtmf", "dtmf speech"] | None = None,
        execution_timeout: int | None = None,
        digit_end_timeout: int | None = None,
        finish_on_key: str | None = None,
        num_digits: int | None = None,
        redirect: bool | None = None,
        say: str | None = None,
        play: str | None = None,
    ) -> Response:
        """Collect input (``<GetInput>``), then request ``action``.

        Only DTMF is collected today: SuperSBC does not run speech
        recognition, so ``input_type`` must include ``dtmf`` (SBC default
        ``dtmf speech``). ``execution_timeout`` defaults to 15 s;
        ``digit_end_timeout`` ``None`` is the SBC's ``auto``. ``say`` / ``play``
        are the prompt, as in :meth:`get_digits`.
        """
        if input_type is not None and "dtmf" not in input_type.split():
            raise ValueError('input_type must include "dtmf" (speech is not supported)')
        _check_method(method, "method")
        _check_finish_on_key(finish_on_key)
        self._actions.append(
            _drop_none(
                {
                    "type": "get_input",
                    "action": action,
                    "method": method,
                    "input_type": input_type,
                    "execution_timeout": execution_timeout,
                    "digit_end_timeout": digit_end_timeout,
                    "finish_on_key": finish_on_key,
                    "num_digits": num_digits,
                    "redirect": redirect,
                    "prompts": _prompts(say, play),
                }
            )
        )
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
        """``<Response>`` XML."""
        body = "".join(_xml_action(a) for a in self._actions)
        return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>{body}</Response>'


def _loop_flag(loop: bool | int | None) -> bool | None:
    """The SBC's ``loop`` is a bool (``PlayAction`` / ``SpeakAction``); ``0``
    means loop and ``1`` play once, as in ``<Play loop="0">``."""
    if loop is None or isinstance(loop, bool):
        return loop
    if loop == 0:
        return True
    if loop == 1:
        return False
    raise ValueError(
        "loop is True/0 (repeat until the call moves on) or False/1 (once); "
        "the SBC has no repeat count"
    )


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None and v != []}


def _check_method(value: str | None, name: str) -> None:
    if value not in (None, "GET", "POST"):
        raise ValueError(f'{name} must be "GET" or "POST"')


def _check_finish_on_key(value: str | None) -> None:
    """SBC rule: one of ``0-9 * #``, or ``""`` / ``none`` to disable it."""
    if (
        value is None
        or value in ("", "none")
        or (len(value) == 1 and value in "0123456789*#")
    ):
        return
    raise ValueError('finish_on_key must be one of 0-9, *, #, or "" to disable it')


def _prompts(say: str | None, play: str | None) -> list[dict[str, Any]]:
    """The nested prompt of ``get_digits`` / ``get_input`` (``PromptAction``)."""
    prompts: list[dict[str, Any]] = []
    if say:
        prompts.append({"type": "speak", "text": say})
    if play:
        prompts.append({"type": "play", "url": play})
    return prompts


def _attrs(pairs: list[tuple[str, Any]]) -> str:
    out = []
    for name, value in pairs:
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        out.append(f" {name}={quoteattr(str(value))}")
    return "".join(out)


_Verb = Literal[
    "stream",
    "speak",
    "play",
    "wait",
    "record",
    "get_digits",
    "get_input",
    "dial",
    "hangup",
]


def _xml_action(a: dict[str, Any]) -> str:
    kind: _Verb = a["type"]
    if kind == "stream":
        attrs = _attrs(
            [
                ("bidirectional", a["bidirectional"]),
                ("keepCallAlive", a.get("keep_call_alive")),
                ("audioTrack", a.get("audio_track")),
                ("contentType", a["content_type"]),
                ("streamTimeout", a.get("stream_timeout")),
                ("extraHeaders", a.get("extra_headers")),
                ("statusCallbackUrl", a.get("status_callback_url")),
                ("statusCallbackMethod", a.get("status_callback_method")),
                ("noiseCancellation", a.get("noise_cancellation")),
                ("noiseCancellationLevel", a.get("noise_cancellation_level")),
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
    if kind == "record":
        attrs = _attrs(
            [
                ("maxLength", a.get("max_duration_secs")),
                ("callbackUrl", a.get("callback_url")),
                ("recordSession", a.get("record_session")),
                ("action", a.get("action")),
                ("method", a.get("action_method")),
                ("callbackMethod", a.get("callback_method")),
            ]
        )
        return f"<Record{attrs}/>"
    if kind in ("get_digits", "get_input"):
        names = [
            ("action", "action"),
            ("method", "method"),
            ("timeout", "timeout"),
            ("digitTimeout", "digit_timeout"),
            ("inputType", "input_type"),
            ("executionTimeout", "execution_timeout"),
            ("digitEndTimeout", "digit_end_timeout"),
            ("finishOnKey", "finish_on_key"),
            ("numDigits", "num_digits"),
            ("retries", "retries"),
            ("validDigits", "valid_digits"),
            ("redirect", "redirect"),
        ]
        attrs = _attrs([(xml, a.get(key)) for xml, key in names])
        inner = "".join(_xml_action(p) for p in a.get("prompts", []))
        tag = "GetDigits" if kind == "get_digits" else "GetInput"
        return f"<{tag}{attrs}>{inner}</{tag}>"
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
