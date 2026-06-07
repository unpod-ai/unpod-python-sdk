"""Developer agent — registers with a remote supervoice, drives superdialog.

The only code a developer writes: an `entrypoint(ctx)` plus an `AgentRunner`.
Speech infra (STT/TTS/bridge) is provided by the remote supervoice referenced
by SUPERVOICE_URL — never imported here.
"""

from __future__ import annotations

import logging
import os
import re

from loguru import logger


# Intercept superdialog's standard-library logging so [SMART-SKIP-DEBUG] appears
class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


logging.basicConfig(handlers=[_InterceptHandler()], level=logging.DEBUG, force=True)
# Only show INFO+ from superdialog to reduce noise
logging.getLogger("superdialog").setLevel(logging.INFO)
from unpod import AgentRunner, CallContext  # noqa: E402
from unpod._base_url import ws_base  # noqa: E402
from unpod._protocol import AgentTextDeltaEvent, AgentTextEndEvent  # noqa: E402

from superdialog import DialogMachine, LLMAgent  # noqa: E402
from superdialog.flow import load_flow  # noqa: E402

# Devanagari Unicode block — presence = Hindi speech
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

SUPERVOICE_URL = os.getenv("SUPERVOICE_URL") or ws_base() or "ws://127.0.0.1:9000"
AGENT_ID = os.getenv("AGENT_ID", "browser-playground")

_SYSTEM_PROMPT = """You are a helpful voice assistant in a dev test playground.
Keep your answers concise — spoken responses should be under 3 sentences.
Be conversational and natural."""


def _pick_llm() -> str:
    if model := os.getenv("SUPERDIALOG_LLM"):
        return model
    if os.getenv("OPENAI_API_KEY"):
        return "openai/gpt-4.1-mini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic/claude-haiku-4-5-20251001"
    raise RuntimeError(
        "No LLM API key found. Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env"
    )


async def _send_greeting(ctx: CallContext, text: str) -> None:
    """Stream a greeting to the bridge word-by-word (DialogMachine only)."""
    pieces = text.split(" ")
    for idx, piece in enumerate(pieces):
        chunk = piece if idx == len(pieces) - 1 else f"{piece} "
        await ctx.session._bridge.send_verb(AgentTextDeltaEvent(text=chunk))
    await ctx.session._bridge.send_verb(AgentTextEndEvent())


async def entrypoint(ctx: CallContext) -> None:
    """Called once per call — the only code you write per call."""
    logger.info(f"[playground] call started: call_id={ctx.call_id}")

    flow_path = os.getenv("FLOW_JSON_PATH")
    model = _pick_llm()

    if flow_path and os.path.exists(flow_path):
        logger.info(f"[playground] loading flow from {flow_path}")
        agent: DialogMachine | LLMAgent = DialogMachine(
            flow=load_flow(flow_path), llm=model
        )
        try:
            greeting = await agent.start()
            if greeting and greeting.text:
                logger.info(f"[playground] greeting: {greeting.text!r}")
                await _send_greeting(ctx, greeting.text)
        except Exception as exc:  # greeting is non-fatal
            logger.exception(f"[playground] greeting error: {exc}")
    else:
        logger.info("[playground] using LLMAgent with default system prompt")
        agent = LLMAgent(llm=model, system_prompt=_SYSTEM_PROMPT)

    ctx.session.dialog_machine = agent

    @ctx.session.on("user_turn")
    async def _detect_language(text: str) -> None:
        """Auto-detect Hindi vs English from transcript and update machine language flag."""
        detected = "hi" if _DEVANAGARI_RE.search(text) else "en"
        try:
            # Access DialogStateMachine.set_language() through adapter chain
            dm = getattr(agent, "_machine", None)
            if dm is not None and hasattr(dm, "set_language"):
                dm.set_language(detected)
                logger.debug("[playground] language detected → {}", detected)
        except Exception:
            pass

    await ctx.session.run()
    logger.info(f"[playground] call ended: call_id={ctx.call_id}")


def build_runner() -> AgentRunner:
    return AgentRunner(
        entrypoint=entrypoint,
        agent_id=AGENT_ID,
        base_url=SUPERVOICE_URL,
        api_key=os.getenv("UNPOD_API_KEY", "dev-key"),
    )


async def run_agent() -> None:
    """Connect to the remote supervoice and handle calls indefinitely."""
    logger.info(
        f"[playground] agent connecting: agent_id={AGENT_ID} base_url={SUPERVOICE_URL}"
    )
    await build_runner().run()
