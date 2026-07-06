"""Observability — Langfuse tracing + session hook emission per turn.

Activated when LANGFUSE_SECRET_KEY env var is present. All methods are
no-ops on the Langfuse side (but hooks still fire) when env var is absent.
"""

from __future__ import annotations

import os
import time
from typing import Any, Awaitable, Callable


class ObservabilityManager:
    """Per-call observer: Langfuse spans + session hook firing."""

    def __init__(
        self,
        session_id: str,
        user_id: str | None = None,
        fire_hook: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._session_id = session_id
        self._user_id = user_id
        self._fire_hook = fire_hook
        self._enabled = bool(os.getenv("LANGFUSE_SECRET_KEY"))
        self._langfuse: Any = None
        self._current_turn_id: int | None = None
        self._current_span: Any = None
        self._turn_start_t: float | None = None
        self._current_user_text: str = ""
        self._current_agent_text: str = ""
        self._turn_llm_calls: int = 0
        self._turn_llm_total_ms: float = 0.0

        if self._enabled:
            self._langfuse = self._init_langfuse()

    def _init_langfuse(self) -> Any:
        try:
            from langfuse import get_client  # type: ignore[import]

            return get_client()
        except Exception:
            return None

    def start_turn(self, turn_id: int, user_text: str) -> None:
        """Open a Langfuse span for this turn."""
        self._current_turn_id = turn_id
        self._turn_start_t = time.monotonic()
        self._current_user_text = user_text
        self._current_agent_text = ""
        self._turn_llm_calls = 0
        self._turn_llm_total_ms = 0.0

        if self._langfuse is None:
            return
        try:
            self._current_span = self._langfuse.start_as_current_observation(
                as_type="span",
                name=f"voice_turn_{turn_id}",
                session_id=self._session_id,
                user_id=self._user_id,
                input={"user_text": user_text},
            )
            self._current_span.__enter__()
        except Exception:
            self._current_span = None

    async def record_llm_call(self, data: Any) -> None:
        """Create a Langfuse generation child span and fire 'llm_call' hook."""
        if self._current_turn_id is None:
            return
        self._turn_llm_calls += 1
        self._turn_llm_total_ms += data.latency_ms

        if self._langfuse is not None and self._current_span is not None:
            try:
                with self._langfuse.start_as_current_observation(
                    as_type="generation",
                    name=f"{data.call_type}:{data.node_id}",
                    model=data.model,
                    input=data.prompt_messages,
                    output=data.response_json,
                    usage={
                        "input_tokens": data.tokens_in,
                        "output_tokens": data.tokens_out,
                    },
                ):
                    pass
            except Exception:
                pass

        if self._fire_hook is not None:
            await self._fire_hook(
                "llm_call",
                turn_id=self._current_turn_id,
                node_id=data.node_id,
                model=data.model,
                call_type=data.call_type,
                latency_ms=data.latency_ms,
                tokens_in=data.tokens_in,
                tokens_out=data.tokens_out,
                prompt_messages=data.prompt_messages,
                response_json=data.response_json,
                edge_id=data.edge_id,
            )

    def end_turn(self, agent_text: str, from_node: str, to_node: str) -> None:
        """Close the Langfuse span with output fields."""
        self._current_agent_text = agent_text
        if self._current_span is not None:
            try:
                self._current_span.__exit__(None, None, None)
            except Exception:
                pass
            self._current_span = None

    async def record_pipeline_scores(
        self,
        turn_id: int,
        ttfa_ms: float | None,
        asr_ms: float | None,
        llm_ttft_ms: float | None,
        tts_ttfb_ms: float | None,
        from_node: str | None,
        to_node: str | None,
        llm_call_count: int,
        llm_total_ms: float | None,
    ) -> None:
        """Attach pipeline timing scores and fire 'turn_complete' hook."""
        if self._fire_hook is not None:
            await self._fire_hook(
                "turn_complete",
                turn_id=turn_id,
                ttfa_ms=ttfa_ms,
                asr_ms=asr_ms,
                llm_ttft_ms=llm_ttft_ms,
                tts_ttfb_ms=tts_ttfb_ms,
                stt_ms=asr_ms,
                tts_ms=tts_ttfb_ms,
                from_node=from_node,
                to_node=to_node,
                llm_call_count=llm_call_count,
                llm_total_ms=llm_total_ms,
                user_text=self._current_user_text,
                agent_text=self._current_agent_text,
            )
