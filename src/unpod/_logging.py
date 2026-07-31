"""Logging for the SDK — stdlib ``logging``, namespaced under ``unpod``.

A library must not configure logging for the application that imports it, so
every logger here is a child of the ``unpod`` logger and the package attaches a
single :class:`logging.NullHandler` to it. Nothing is emitted until the host app
configures logging (``logging.basicConfig(...)``) or calls
:func:`enable_logging` for a one-line dev setup.

Levels used across the SDK:

* ``INFO``  — lifecycle a developer wants to see on a healthy run: control link
  up/down, registration, job accepted, call start/end.
* ``WARNING`` — recoverable trouble: a dial attempt failed and will retry, a
  handshake was refused, a control drop that will reconnect.
* ``ERROR`` — a call or the runner is lost: dial retries exhausted, entrypoint
  raised, fatal auth rejection.
* ``DEBUG`` — per-heartbeat / per-frame noise.

Every message carries the correlating id (``worker_id``, ``job_id``,
``session_id``) because the orchestrator, the worker and the runner are three
processes and the ids are the only way to line their logs up.
"""

from __future__ import annotations

import logging
import sys
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_ROOT_NAME = "unpod"

# One NullHandler on the package logger: keeps "No handlers could be found"
# quiet without dictating format or destination to the host application.
logging.getLogger(_ROOT_NAME).addHandler(logging.NullHandler())

_SENSITIVE_PARAMS = frozenset({"token", "signature", "api_key", "call_token"})


def get_logger(name: str) -> logging.Logger:
    """Return the ``unpod.<name>`` logger (e.g. ``unpod.runner``)."""
    return logging.getLogger(f"{_ROOT_NAME}.{name}")


def enable_logging(
    level: int | str = logging.INFO, stream: Any = None
) -> logging.Logger:
    """Attach a plain stream handler to the ``unpod`` logger. Dev convenience.

    Idempotent: calling it twice does not double-log. Does NOT touch the root
    logger, so it cannot disturb the host app's own configuration.
    """
    logger = logging.getLogger(_ROOT_NAME)
    logger.setLevel(level)
    for existing in logger.handlers:
        if getattr(existing, "_unpod_handler", False):
            existing.setLevel(level)
            return logger
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
    )
    handler.setLevel(level)
    handler._unpod_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    # The NullHandler stays; propagation off so the host's root config does not
    # duplicate what this handler already prints.
    logger.propagate = False
    return logger


def redact_url(url: str) -> str:
    """Return ``url`` with secret query values replaced by ``***``.

    Bridge URLs carry the per-call token as a query param. Logging the raw URL
    would leak a live call credential into log aggregation, so redact while
    keeping the host/path (the part you actually need to debug routing).
    """
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        pairs = [
            (k, "***" if k.lower() in _SENSITIVE_PARAMS else v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        # safe="*" keeps the mask readable as ``token=***`` rather than
        # percent-encoding it into ``%2A%2A%2A``.
        return urlunparse(parsed._replace(query=urlencode(pairs, safe="*")))
    except Exception:  # noqa: BLE001 — logging helper must never raise
        return "<unparseable-url>"


def close_code_of(exc: BaseException) -> int | None:
    """Best-effort WebSocket close code from a ``websockets`` exception.

    Close codes are the difference between "wrong credentials" and "network
    blip", so they belong in the log line rather than a generic message.
    """
    for attr in ("rcvd", "sent"):
        frame = getattr(exc, attr, None)
        code = getattr(frame, "code", None)
        if isinstance(code, int):
            return code
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


__all__ = ["close_code_of", "enable_logging", "get_logger", "redact_url"]
