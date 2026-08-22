"""The ear: streams mic audio into the Live API and turns tool calls into frames.

Model note: `gemini-3.7-flash` is the stable workhorse but is NOT a Live API
model. Audio streaming goes through a *-live-* model (see config.live_model).
Use 3.7-flash for async enrichment off the hot path if we need it.
"""
import asyncio
from collections.abc import Callable

from google import genai
from google.genai import types

import ops
import tools
from config import CFG

SYSTEM_INSTRUCTION = """\
You are a silent co-presenter. A human is giving a live talk to an audience.
You control the screen behind them. You never speak and never address anyone.

Your only output is tool calls that put things on a shared canvas.

WHEN TO DRAW — this is the whole job:
- Draw when a sentence contains something a slide would have carried: a claim
  worth anchoring, a real number, a system or flow, a comparison, a place.
- Stay silent otherwise. Filler, throat-clearing, transitions, "so", "right",
  "as I was saying" — draw nothing. An empty screen is better than noise.
- At most one tool call per sentence. Never restate what is already on screen.
- Aim for roughly one visual every 15-20 seconds of speech. Fewer, bigger, better.

CONTENT RULES:
- Never invent a number, a name, or a fact the speaker did not say.
- Titles are 3-8 words. Bullets are under 8 words. No full sentences on screen.
- The audience reads this from across a room. Terse wins.

You are told the ids of blocks already on the canvas. Use connect_blocks to
relate a new idea to an existing one when the speaker links them explicitly.
"""


def build_config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["TEXT"],           # we want tool calls, not speech
        system_instruction=types.Content(
            parts=[types.Part(text=SYSTEM_INSTRUCTION)]
        ),
        tools=[types.Tool(function_declarations=tools.DECLARATIONS)],
        input_audio_transcription={},           # gives us the status ticker
        realtime_input_config={
            "automatic_activity_detection": {
                "disabled": False,
                "silence_duration_ms": 400,     # tune: lower = twitchier
                "prefix_padding_ms": 60,
            }
        },
    )


async def run(audio_q: asyncio.Queue, broadcast: Callable[[dict], None]):
    """Own the Live session for the whole talk. `broadcast(frame)` is sync."""
    client = genai.Client(api_key=CFG.api_key)
    cfg = build_config()

    async with client.aio.live.connect(model=CFG.live_model, config=cfg) as session:
        broadcast(ops.status("listening"))
        print(f"[live] connected to {CFG.live_model}")

        async def pump_audio():
            while True:
                chunk = await audio_q.get()
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )

        async def pump_responses():
            async for response in session.receive():
                sc = response.server_content
                if sc and sc.input_transcription and sc.input_transcription.text:
                    broadcast(ops.status("listening",
                                         sc.input_transcription.text))

                if response.tool_call:
                    broadcast(ops.status("drawing"))
                    replies = []
                    for fc in response.tool_call.function_calls:
                        print(f"[live] tool {fc.name} {fc.args}")
                        frames, result = tools.dispatch(fc.name, dict(fc.args or {}))
                        for f in frames:
                            broadcast(f)
                        replies.append(types.FunctionResponse(
                            id=fc.id, name=fc.name, response=result))
                    if replies:
                        await session.send_tool_response(function_responses=replies)
                    broadcast(ops.status("listening"))

        await asyncio.gather(pump_audio(), pump_responses())
