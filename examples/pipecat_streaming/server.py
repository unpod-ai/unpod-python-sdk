"""Answer server + stream endpoint for a Pipecat bot on Unpod calls.

Routes:
    POST/GET /answer   SuperSBC asks what to do with a call → stream it to /ws
    WS       /ws       SuperSBC streams the call's audio; the bot answers
    POST     /start    place an outbound call that streams to the bot

Setup, once per number (inbound):
    await client.telephony.inbound.set_did(
        "+911234567890", answer_url="https://<your-host>/answer")

Env:
    UNPOD_PLATFORM_TOKEN, UNPOD_ORG_HANDLE   SDK auth (org needs the call API on)
    UNPOD_WEBHOOK_SECRET                     client.telephony.webhook_secret
    UNPOD_FROM_NUMBER                        caller ID for outbound calls
    START_API_KEY                            required as X-Api-Key on POST /start
    PUBLIC_WS_BASE                           e.g. wss://bot.example.com when TLS ends
                                             at a proxy/tunnel (checked, not Host)
    OPENAI_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY

Run:
    pip install "unpod[pipecat]" "pipecat-ai[websocket,silero,deepgram,openai,cartesia]" uvicorn
    python server.py   # behind https/wss, e.g. ngrok http 7860
"""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager

import uvicorn
from bot import run_bot
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import Response

from unpod import AsyncClient
from unpod.pipecat import parse_unpod_websocket, verify_stream_request
from unpod.telephony.answer import Response as Answer
from unpod.telephony.stream import NonceCache

WEBHOOK_SECRET = os.environ["UNPOD_WEBHOOK_SECRET"]
PUBLIC_WS_BASE = os.environ.get("PUBLIC_WS_BASE") or None
# Remembers accepted signatures so a captured upgrade cannot be replayed.
NONCES = NonceCache()
# /start places calls billed to the org: never expose it without a key.
START_API_KEY = os.environ.get("START_API_KEY", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncClient() as client:
        app.state.unpod = client
        yield


app = FastAPI(lifespan=lifespan)


def _ws_url(request: Request, direction: str) -> str:
    host = request.headers.get("host")
    if not host:
        raise HTTPException(400, "no Host header")
    return f"wss://{host}/ws?direction={direction}"


@app.api_route("/answer", methods=["GET", "POST"])
async def answer(request: Request, direction: str = "inbound") -> Response:
    """Tell the SBC to stream this call to our bot. Replies with XML.

    A POST answer request carries ``X-Webhook-Signature`` when the org has a
    webhook secret (verify it with ``unpod.telephony.parse_call_event``); a
    GET has no body to sign. This route only returns the stream URL, and /ws
    checks its own signature, so the example leaves it open.
    """
    xml = Answer().stream(_ws_url(request, direction)).to_xml()
    return Response(content=xml, media_type="application/xml")


@app.websocket("/ws")
async def stream(websocket: WebSocket, direction: str = "inbound"):
    # Only Unpod's SBC holds the org's webhook secret.
    if not verify_stream_request(
        websocket.headers,
        WEBHOOK_SECRET,
        url=str(websocket.url),
        public_base=PUBLIC_WS_BASE,
        nonce_cache=NONCES,
    ):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    start = await parse_unpod_websocket(websocket)
    await run_bot(websocket, start, app.state.unpod, greet=direction == "inbound")


@app.post("/start")
async def start_outbound(request: Request) -> dict:
    """Dial ``{"phone_number": "+1..."}``; once answered, the call streams to /ws.

    Needs ``X-Api-Key: $START_API_KEY``: anyone who can reach an open /start
    can place calls billed to the org.
    """
    key = request.headers.get("x-api-key", "")
    if not START_API_KEY or not hmac.compare_digest(key, START_API_KEY):
        raise HTTPException(401, "missing or wrong X-Api-Key")
    body = await request.json()
    to = body.get("phone_number")
    if not to:
        raise HTTPException(400, "phone_number is required")
    host = request.headers["host"]
    call = await app.state.unpod.telephony.calls.create(
        os.environ["UNPOD_FROM_NUMBER"],
        to,
        answer_url=f"https://{host}/answer?direction=outbound",
    )
    return {"call_id": call.call_id, "state": call.state}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
