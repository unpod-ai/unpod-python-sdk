"""A Pipecat voice bot on an Unpod call: Deepgram → OpenAI → Cartesia.

The same pipeline as Pipecat's ``plivo-chatbot`` example. Only the transport
setup differs: ``UnpodFrameSerializer`` instead of ``PlivoFrameSerializer``.
"""

from __future__ import annotations

import os

from fastapi import WebSocket
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.workers.runner import WorkerRunner

from unpod import AsyncClient
from unpod.pipecat import StreamStart, UnpodFrameSerializer

# Unpod streams μ-law at 8 kHz; running the pipeline at 8 kHz skips resampling.
SAMPLE_RATE = 8000


async def run_bot(
    websocket: WebSocket, start: StreamStart, unpod: AsyncClient, *, greet: bool
) -> None:
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            # Hangs the call up through the Unpod call API when the bot ends.
            serializer=UnpodFrameSerializer.from_start(start, client=unpod),
        ),
    )

    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(
            system_instruction=(
                "You are a friendly assistant on a phone call. Your replies are spoken aloud, "
                "so keep them short and avoid special characters."
            ),
        ),
    )
    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
    tts = CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        settings=CartesiaTTSService.Settings(
            voice="71a7ad14-091c-4e8e-a314-022ece01c121"
        ),
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE
        ),
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        if greet:  # inbound: the bot speaks first; outbound: wait for "hello?"
            context.add_message(
                {"role": "developer", "content": "Greet the caller briefly."}
            )
            await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await runner.cancel()

    await runner.run()
