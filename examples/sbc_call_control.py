"""SBC call control through the SDK only (preview), via the unpod call-API proxy.

Dials party A, adds party B, plays a prompt, then ends the call. Use only
``local_`` / ``qa_`` test numbers and trunks: QA and Prod share one SBC.

Usage:
    python examples/sbc_call_control.py <from> <to_a> <to_b>

Required env vars (or a .env file in the working directory):
    UNPOD_BASE_URL=https://unpod.ai
    UNPOD_PLATFORM_TOKEN   (API key from Settings -> Security -> API keys)
    UNPOD_ORG_HANDLE       (the org needs the call API switched on)

<from> must be one of your org's numbers. Guide: docs/08-telephony.md
"""

from __future__ import annotations

import asyncio
import sys

from unpod import AsyncClient
from unpod.telephony import TelephonyAPIError


async def main(from_: str, to_a: str, to_b: str) -> None:
    async with AsyncClient() as client:
        calls = client.telephony.calls
        call = await calls.create(from_, to_a, role="agent", ring_timeout=30)
        print("call", call.call_id, call.state)
        try:
            leg = await calls.add_participant(call.call_id, from_, to_b, role="customer")
            print("added leg", leg.leg_id)
            await asyncio.sleep(5)
            played = await calls.play_audio(call.call_id, url="https://example.com/prompt.mp3")
            print("playing", played.play_id)
            await asyncio.sleep(5)
        except TelephonyAPIError as exc:
            print("call API error:", exc.status_code, exc.code, exc.message)
        finally:
            await calls.end(call.call_id)
            print("ended")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    asyncio.run(main(*sys.argv[1:4]))
