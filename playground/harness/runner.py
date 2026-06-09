"""AgentRunner wiring — builds the per-call entrypoint for a catalog agent.

The entrypoint is the only per-call code: it builds the dialog machine from the
catalog, registers the live session for control, wires session lifecycle hooks
onto the side-channel :class:`EventBus`, then runs the call. Events are emitted
under supervoice-named types only (``ready``, ``user_turn``, ``agent_turn``,
``flow_node_changed``, ``error``, ``disconnected``).
"""

from __future__ import annotations

import os
from typing import Any

from playground.agents.catalog import AgentSpec
from playground.harness.control import SessionRegistry
from playground.harness.events import EventBus
from unpod import AgentRunner, CallContext


def pick_llm() -> str:
    """Resolve an LLM URI from the environment (OpenAI preferred, then Claude)."""
    if os.getenv("OPENAI_API_KEY"):
        return "openai/gpt-4.1-mini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic/claude-haiku-4-5-20251001"
    raise RuntimeError("No LLM API key found. Set OPENAI_API_KEY or ANTHROPIC_API_KEY.")


def _publish_flow_node(bus: EventBus, ctx: CallContext) -> None:
    """Emit the current dialog node + slots, if the machine exposes state."""
    adapter = ctx.session.dialog_machine
    if adapter is None:
        return
    try:
        state = adapter.state
    except (AttributeError, NotImplementedError):
        return
    if isinstance(state, dict):
        bus.publish("flow_node_changed", **state)


def build_runner(
    spec: AgentSpec,
    bus: EventBus,
    registry: SessionRegistry,
    *,
    base_url: str,
    api_key: str,
    llm: str | None = None,
) -> AgentRunner:
    """Build an :class:`AgentRunner` for ``spec`` wired to the side-channel."""
    model = llm or pick_llm()

    async def entrypoint(ctx: CallContext) -> None:
        machine = spec.build(model)

        # DialogMachine: generate + speak the opening turn before the session loop.
        # LLMAgent has no start(), so this branch is skipped for plain LLM agents.
        if hasattr(machine, "start"):
            try:
                turn = await machine.start()
                if turn and turn.text:
                    await ctx.session.say(turn.text)
                    bus.publish("agent_turn", text=turn.text)
            except Exception:
                pass

        ctx.session.dialog_machine = machine
        registry.register(ctx.call_id, ctx.session)

        @ctx.session.on("call_start")
        async def _on_start() -> None:
            bus.publish("ready", agent=spec.name, call_id=ctx.call_id)
            _publish_flow_node(bus, ctx)  # initial node, for the flow sidebar

        @ctx.session.on("user_turn")
        async def _on_user(text: str) -> None:
            bus.publish("user_turn", text=text)

        @ctx.session.on("agent_turn")
        async def _on_agent(text: str) -> None:
            bus.publish("agent_turn", text=text)
            _publish_flow_node(bus, ctx)

        @ctx.session.on("llm_call")
        async def _on_llm_call(**data: Any) -> None:
            bus.publish("llm_call", **data)

        @ctx.session.on("turn_complete")
        async def _on_turn_complete(**data: Any) -> None:
            bus.publish("turn_complete", **data)

        @ctx.session.on("error")
        async def _on_error(
            code: str, message: str, severity: str, source: str
        ) -> None:
            bus.publish(
                "error",
                code=code,
                message=message,
                severity=severity,
                source=source,
            )

        @ctx.session.on("metric")
        async def _on_metric(metric: Any) -> None:
            bus.publish("metric", **metric.model_dump(exclude={"event"}))

        @ctx.session.on("interruption")
        async def _on_interruption() -> None:
            bus.publish("interruption")

        @ctx.session.on("call_end")
        async def _on_end(reason: str) -> None:
            bus.publish("disconnected", reason=reason)

        try:
            await ctx.session.run()
        finally:
            registry.unregister(ctx.call_id)

    return AgentRunner(
        entrypoint=entrypoint,
        agent_id=spec.agent_id,
        base_url=base_url,
        api_key=api_key,
    )
