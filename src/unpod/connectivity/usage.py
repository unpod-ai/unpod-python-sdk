"""UsageReporter — buffers per-session LLM usage and pushes it to the cloud.

This is the SDK half of the supervoice usage ledger (the merge point): the
worker reports STT/TTS, the SDK reports the LLM tokens that superdialog burns.
The push is keyed by ``session_id`` and carries ONLY counters + provider names —
never billing identity, which the cloud stamps from the trusted session record
at consume time.

Best-effort by construction: when ``UNPOD_USAGE_INGEST_URL`` is unset the
reporter is a no-op, and a push failure is swallowed — usage egress must never
break a live call. Each ``flush()`` sends a DELTA (counters reset after a
successful push) so repeated flushes accumulate correctly via the ingest's
``$inc`` semantics.
"""

from __future__ import annotations

import os

import httpx

_INGEST_PATH = "/v1/internal/sessions/{session_id}/usage"


def _split_model_uri(model: str) -> tuple[str, str]:
    """Split a model URI into ``(provider, model)``.

    Handles litellm-style ``provider/model`` and gateway-prefixed
    ``custom/lk-inference/provider/model`` by taking the last two path
    segments; a bare ``model`` yields an empty provider.
    """
    parts = [p for p in (model or "").split("/") if p]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if parts:
        return "", parts[0]
    return "", ""


class UsageReporter:
    """Accumulates LLM token usage for one session and flushes it to the cloud."""

    def __init__(
        self,
        session_id: str,
        *,
        ingest_url: str | None = None,
        token: str | None = None,
        timeout_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session_id = session_id
        self._ingest_url = (
            ingest_url
            if ingest_url is not None
            else os.getenv("UNPOD_USAGE_INGEST_URL", "")
        ).rstrip("/")
        self._token = (
            token if token is not None else os.getenv("UNPOD_USAGE_INGEST_TOKEN", "")
        )
        self._timeout = timeout_s
        self._transport = transport
        self._prompt = 0
        self._completion = 0
        self._cached = 0
        self._provider = ""
        self._model = ""
        self._seq = 0

    @property
    def configured(self) -> bool:
        """True when an ingest URL and session id are both set."""
        return bool(self._ingest_url and self._session_id)

    def record_llm(
        self,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cached: int = 0,
        model: str = "",
    ) -> None:
        """Accumulate one LLM call's token usage (sync; safe to call per turn)."""
        self._prompt += int(tokens_in or 0)
        self._completion += int(tokens_out or 0)
        self._cached += int(cached or 0)
        if model:
            self._provider, self._model = _split_model_uri(model)

    def _counters(self) -> dict[str, int]:
        counters: dict[str, int] = {}
        if self._prompt:
            counters["llm_prompt_tokens"] = self._prompt
        if self._completion:
            counters["llm_completion_tokens"] = self._completion
        if self._cached:
            counters["llm_cached_tokens"] = self._cached
        return counters

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _new_client(self) -> httpx.AsyncClient:
        kwargs: dict = {"timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def flush(self) -> bool:
        """Push the buffered usage delta to the cloud ingest. Never raises.

        Returns True iff a delta was actually pushed. No-op when unconfigured or
        when nothing has accumulated. Resets the counters on success so the next
        flush sends only new usage.
        """
        if not self.configured:
            return False
        counters = self._counters()
        if not counters:
            return False
        providers: dict[str, str] = {}
        if self._provider:
            providers["llm_provider"] = self._provider
        if self._model:
            providers["llm_model"] = self._model
        body = {
            "source": "sdk",
            "seq": self._seq,
            "counters": counters,
            "providers": providers,
        }
        url = self._ingest_url + _INGEST_PATH.format(session_id=self._session_id)
        try:
            async with self._new_client() as client:
                resp = await client.post(url, json=body, headers=self._headers())
                resp.raise_for_status()
        except Exception:
            # Best-effort: a down/slow ingest must never break the call.
            return False
        self._seq += 1
        self._prompt = self._completion = self._cached = 0
        return True


__all__ = ["UsageReporter"]
