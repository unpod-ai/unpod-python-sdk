"""Browser voice agent — full platform setup + connectivity example.

Demonstrates the complete flow against a local supervoice dev server:
  1. List voice profiles
  2. Create a Speech Pipe
  3. Sync numbers from LiveKit trunks
  4. Attach a number to the Speech Pipe
  5. Initiate an outbound call  (optional — see make_call below)
  6. Run an AgentRunner to handle inbound + outbound calls

Two terminals:

    Terminal 1 — start supervoice:
        cd supervoice && uv run uvicorn supervoice.main:app --port 8000 --reload

    Terminal 2 — run this script:
        uv run python examples/browser_agent.py [--setup] [--call +91XXXXXXXXXX]

Required env vars:
    UNPOD_API_KEY=sk_...                              (from POST /platform/v1/api-keys)
    UNPOD_SERVICE_BASE_URL=http://127.0.0.1:8000/platform  (default for local dev)
    ANTHROPIC_API_KEY or OPENAI_API_KEY
"""

from __future__ import annotations

import asyncio
import os
import sys

from unpod import AgentRunner, AsyncClient, CallContext

from superdialog import LLMAgent


def _pick_llm() -> str:
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic/claude-haiku-4-5-20251001"
    if os.getenv("OPENAI_API_KEY"):
        return "openai/gpt-4o-mini"
    raise RuntimeError("Set ANTHROPIC_API_KEY or OPENAI_API_KEY")


# ---------------------------------------------------------------------------
# Management setup — run once to provision resources
# ---------------------------------------------------------------------------


async def setup() -> None:
    """Create Speech Pipe, sync numbers, and attach the first available number."""
    client = AsyncClient()

    # 1. List voice profiles
    profiles = await client.voice_profiles.list()
    if not profiles:
        print(
            "No voice profiles found — check that supervoice seeded profiles on startup."
        )
        return
    vp = profiles[0]
    print(f"Using voice profile: {vp.name} ({vp.profile_id})")

    # 2. Create (or find existing) Speech Pipe
    pipes = await client.pipes.list()
    pipe = next((p for p in pipes if p.name == "browser-agent"), None)
    if pipe is None:
        pipe = await client.pipes.create(
            name="browser-agent",
            voice_profile=vp.name,
            system_prompt=(
                "You are a helpful voice assistant. "
                "Keep every answer under three sentences — responses are spoken aloud."
            ),
            first_speaker="agent",
            agent_id="my-agent",
        )
        print(f"Created Speech Pipe: {pipe.pipe_id}")
    else:
        print(f"Found existing Speech Pipe: {pipe.pipe_id}")

    # 3. Sync numbers from LiveKit trunks
    synced = await client.numbers.sync()
    print(f"Synced {len(synced)} numbers from LiveKit trunks")

    # 4. Attach first available number
    if pipe.number_id:
        print(f"Speech Pipe already has number: {pipe.number}")
    else:
        available = await client.numbers.list(status="available")
        if not available:
            print("No available numbers to attach — sync a trunk first.")
            return
        number = await client.numbers.attach(available[0].number_id, pipe.pipe_id)
        print(f"Attached number: {number.number} → Speech Pipe {pipe.pipe_id}")


# ---------------------------------------------------------------------------
# Outbound call — trigger a call to a specific number
# ---------------------------------------------------------------------------


async def make_call(to_number: str) -> None:
    """Initiate an outbound call to the given E.164 number."""
    client = AsyncClient()

    pipes = await client.pipes.list()
    pipe = next((p for p in pipes if p.name == "browser-agent"), None)
    if pipe is None:
        print("Run --setup first to create the Speech Pipe.")
        return
    if not pipe.number_id:
        print("Speech Pipe has no attached number — run --setup first.")
        return

    call = await client.calls.create(
        pipe_id=pipe.pipe_id,
        to_number=to_number,
    )
    print(f"Outbound call {call.call_id} → {to_number} (status: {call.status})")


# ---------------------------------------------------------------------------
# AgentRunner — handles inbound and outbound calls
# ---------------------------------------------------------------------------


async def entrypoint(ctx: CallContext) -> None:
    """Called once per call. This is the only code you write per call."""
    ctx.session.dialog_machine = LLMAgent(
        llm=_pick_llm(),
        system_prompt=(
            "You are a helpful voice assistant. "
            "Keep every answer under three sentences — responses are spoken aloud."
        ),
    )
    await ctx.session.run()


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--setup" in args:
        asyncio.run(setup())
    elif "--call" in args:
        idx = args.index("--call")
        if idx + 1 >= len(args):
            print("Usage: browser_agent.py --call +91XXXXXXXXXX")
            sys.exit(1)
        asyncio.run(make_call(args[idx + 1]))
    else:
        # Default: start the AgentRunner
        AgentRunner(
            entrypoint=entrypoint,
            agent_id="my-agent",
            # base_url and api_key read from UNPOD_SERVICE_BASE_URL / UNPOD_API_KEY
        ).start()
