"""The ear: streams mic audio into the Live API and turns tool calls into frames.

Model note: `gemini-3.7-flash` is the stable workhorse but is NOT a Live API
model. Audio streaming goes through a *-live-* model (see config.live_model).
3.7-flash does the async enrichment off the hot path — see memory.py.

CONTEXT MODEL — two layers, deliberately:
  * The Live session is PRIMARY. It holds the verbatim audio, the transcripts and
    the full tool history. Context-window compression keeps it alive through a long
    talk; session resumption reconnects it without losing that history.
  * canvas.py + memory.py are the DURABLE net. They tell the model what is on screen
    (via the manifest riding back in every tool response) and they rebuild its
    understanding if the session dies anyway.

We never re-prompt with raw transcript on the hot path. The manifest is small, and it
travels in the FunctionResponse we already had to send.
"""
import asyncio
import time
from collections.abc import Callable

from google import genai
from google.genai import types

import canvas
import ops
import tools
from config import CFG

HEARTBEAT_S = 25.0        # top up canvas context if no tool has fired this long
SILENCE_GUARD_S = 8.0     # refuse to draw if nothing has been transcribed this long
TRIGGER_TOKENS = 16000    # compress the session before it hits the wall
TARGET_TOKENS = 8000

SYSTEM_INSTRUCTION = """\
You are a silent co-presenter. A human is giving a live talk to an audience.
You control the screen behind them. You never speak and never address anyone.

Your only output is tool calls that put things on a shared canvas.

WHEN TO DRAW — half the job:
- Draw when a sentence carries something a slide would have carried: a claim worth
  anchoring, a real number, a system or flow, a comparison, a place.
- Stay silent otherwise. Filler, throat-clearing, transitions — "so", "right",
  "as I was saying" — draw nothing. An empty screen beats a noisy one.
- At most one tool call per sentence.
- Roughly one NEW visual every 15-20 seconds of speech. Fewer, bigger, better.

THE BOARD EVOLVES — the other half, and the one that makes this feel alive:
Every visual belongs to a topic `key`. Every tool result hands you a CANVAS list of
what is on screen right now, each entry with its key. That list is the truth. Read it
before you draw.
- Speaker adds detail to something already up there → call the SAME tool with the
  SAME key. The block grows in place. Updating is CHEAP. Prefer it.
- Speaker contradicts or corrects something already up there → new key, and set
  `revises` to the old key. Both stay visible, side by side. NEVER silently
  overwrite a number a human said out loud.
- Speaker opens a genuinely new topic → new key.
- Never create a second block about a topic that already has one. Adding is
  EXPENSIVE.
- A key names the SUBJECT, not the sentence: 'pricing', 'latency', 'pipeline'.
  Reuse it exactly, character for character.

CONTENT RULES:
- Never invent a number, a name, or a fact the speaker did not say.
- The audience reads this from ten metres away. Words are expensive; pictures are not.

FORM -- pick the lightest thing that carries the meaning:
1. show_hero (an emoji + 2-5 words) is the DEFAULT. Reach for it first, always.
2. A number said aloud -> show_stat. Several numbers -> show_chart.
3. A relationship or formula -> show_math.
4. A REAL, nameable thing (a Porsche 911, the Eiffel Tower, a blue whale)
   -> show_photo. It searches for an actual photograph.
5. An imagined or non-existent scene -> show_image (generated).
6. A route between two places -> show_route.
7. show_concept (bullets) is the LAST resort, only when a list is genuinely
   the point. Never more than 3 bullets, never a full sentence.

ONE THING AT A TIME. The screen shows a single subject; whatever you draw
replaces what was there. Never try to build up a layout.

END OF TALK: when the speaker says "to sum up", "in summary", "to wrap up"
or similar, call show_summary with 4-9 tiles drawn from what was ACTUALLY
said during the session. That recap is the last thing the room sees.

Prose is failure. If you are about to write a sentence on screen, you have
picked the wrong tool -- choose an emoji, a number, or a picture instead.

Use connect_blocks with block ids from the CANVAS list to relate two ideas when the
speaker explicitly links them. Never guess an id.
"""


def build_config(handle: str | None = None) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        # gemini-3.1-flash-live-preview REJECTS ["TEXT"] with a 1007 close:
        # "requested combination of response modalities (TEXT) is not supported".
        # AUDIO is the only modality it accepts. Tool calls still arrive normally
        # (verified live) -- we simply drop the audio bytes and never play them,
        # so the co-presenter stays silent as designed.
        response_modalities=["AUDIO"],
        system_instruction=types.Content(
            parts=[types.Part(text=SYSTEM_INSTRUCTION)]
        ),
        tools=[types.Tool(function_declarations=tools.DECLARATIONS)],
        input_audio_transcription={},           # gives us the status ticker
        # The model's own VAD works on a real microphone. It does NOT fire on a
        # replayed PCM feed — with automatic detection on, a --pcm run transcribes
        # nothing at all. MANUAL_ACTIVITY=1 drives the boundaries from mics.Floor
        # instead, which is what makes replay rehearsals work.
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=True) if CFG.manual_activity
            else types.AutomaticActivityDetection(
                disabled=False, silence_duration_ms=400, prefix_padding_ms=60)
        ),
        # Keep the primary context layer alive through a long talk instead of
        # letting it hit the window and die.
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=TRIGGER_TOKENS,
            sliding_window=types.SlidingWindow(target_tokens=TARGET_TOKENS),
        ),
        # Reconnect into the SAME conversation rather than a blank one.
        session_resumption=types.SessionResumptionConfig(handle=handle),
    )


async def run(audio_q: asyncio.Queue, broadcast: Callable[[dict], None],
              memory=None, session_state: dict | None = None, brain=None):
    """Own the Live session for the whole talk. `broadcast(frame)` is sync.

    `session_state` is a dict owned by the caller and reused across reconnects; we
    keep the resumption handle in it so a dropped session comes back with its
    history instead of amnesia.
    """
    state = session_state if session_state is not None else {}
    client = genai.Client(api_key=CFG.api_key)
    cfg = build_config(state.get("handle"))
    last_tool = time.time()
    # Nothing has been heard yet. Any tool call before the first transcript is the
    # model inventing, so start this in the past rather than at "now".
    last_heard = 0.0
    suppressed = 0

    async with client.aio.live.connect(model=CFG.live_model, config=cfg) as session:
        broadcast(ops.status("listening"))
        resumed = " (resumed)" if state.get("handle") else ""
        print(f"[live] connected to {CFG.live_model}{resumed}")

        # If the board already has blocks, this is a reconnect: the canvas outlived
        # the session. Hand the model the record before it hears another word, or it
        # will happily duplicate everything already on screen.
        if canvas.BLOCKS:
            brief = canvas.manifest_text()
            if memory is not None:
                brief = f"{memory.resume_brief()}\n\n{brief}"
            await session.send_client_content(
                turns=[types.Content(role="user", parts=[types.Part(text=brief)])],
                turn_complete=False,
            )
            print(f"[live] re-briefed on {len(canvas.BLOCKS)} live blocks")

        async def pump_audio():
            # The queue carries tagged items now, because phone mics put speaker
            # changes and turn boundaries on the same queue as audio and the
            # ordering between them matters. See audio.py.
            while True:
                kind, payload = await audio_q.get()
                if kind == "audio":
                    await session.send_realtime_input(
                        audio=types.Blob(data=payload,
                                         mime_type="audio/pcm;rate=16000")
                    )
                elif kind == "activity" and CFG.manual_activity:
                    # Off by default: the model's own VAD works on a real mic.
                    # Turn it on if a replayed or noisy feed stops being heard.
                    if payload == "start":
                        await session.send_realtime_input(
                            activity_start=types.ActivityStart())
                    else:
                        await session.send_realtime_input(
                            activity_end=types.ActivityEnd())
                elif kind == "speaker" and CFG.announce_speakers:
                    # send_client_content, NOT send_realtime_input(text=...):
                    # realtime text counts as a user turn and would make the model
                    # answer. turn_complete=False makes it context only, so it can
                    # attribute the next thing it draws without being prompted.
                    await session.send_client_content(
                        turns=[types.Content(role="user", parts=[types.Part(
                            text=f"[{payload} is now the one speaking]")])],
                        turn_complete=False,
                    )

        def _mark_tool():
            nonlocal last_tool
            last_tool = time.time()

        def nonlocal_heard(t: float) -> None:
            nonlocal last_heard
            last_heard = t

        def nonlocal_suppressed() -> None:
            nonlocal suppressed
            suppressed += 1

        async def pump_heartbeat():
            """Top up canvas context when the model has been quiet a while.

            Cheap (a few hundred tokens) and it stops the model drifting away from
            what is actually on screen during a long stretch of no tool calls.
            """
            while True:
                await asyncio.sleep(5)
                if not canvas.BLOCKS or time.time() - last_tool < HEARTBEAT_S:
                    continue
                await session.send_client_content(
                    turns=[types.Content(role="user", parts=[
                        types.Part(text=canvas.manifest_text())])],
                    turn_complete=False,
                )
                _mark_tool()

        async def pump_responses():
            async for response in session.receive():
                sc = response.server_content
                if sc and sc.input_transcription and sc.input_transcription.text:
                    nonlocal_heard(time.time())
                    text = sc.input_transcription.text
                    broadcast(ops.status("listening", text))
                    if brain is not None:
                        brain.feed(text)             # only with --brain
                    if memory is not None:
                        memory.add_utterance(text)   # feeds the durable layer only

                # Keep the resumption handle fresh so a drop is recoverable.
                if response.session_resumption_update:
                    upd = response.session_resumption_update
                    if upd.resumable and upd.new_handle:
                        state["handle"] = upd.new_handle

                if response.go_away:
                    print(f"[live] server going away in {response.go_away.time_left}; "
                          f"will resume on the stored handle")

                if response.tool_call:
                    _mark_tool()
                    # Structural "never invent": the model sometimes calls a tool
                    # with nothing transcribed at all — an empty room produced
                    # "Nice, Agreed 👍". A prompt cannot guarantee this; a guard
                    # can. If we have not heard words recently, we do not draw.
                    quiet_for = time.time() - last_heard
                    if quiet_for > SILENCE_GUARD_S:
                        nonlocal_suppressed()
                        names = ", ".join(fc.name for fc
                                          in response.tool_call.function_calls)
                        print(f"[live] suppressed {names}: nothing heard for "
                              f"{quiet_for:.0f}s (total {suppressed})")
                        await session.send_tool_response(function_responses=[
                            types.FunctionResponse(
                                id=fc.id, name=fc.name,
                                response={"error": "nobody is speaking; draw "
                                                   "nothing and stay silent"})
                            for fc in response.tool_call.function_calls])
                        continue
                    broadcast(ops.status("drawing"))
                    replies = []
                    for fc in response.tool_call.function_calls:
                        frames, result = tools.dispatch(fc.name, dict(fc.args or {}))
                        print(f"[live] tool {fc.name} "
                              f"key={ (fc.args or {}).get('key') } "
                              f"-> {result.get('action') or result.get('skipped') or result.get('error')}")
                        for f in frames:
                            broadcast(f)
                        replies.append(types.FunctionResponse(
                            id=fc.id, name=fc.name, response=result))
                    if replies:
                        await session.send_tool_response(function_responses=replies)
                    broadcast(ops.status("listening"))

        await asyncio.gather(pump_audio(), pump_responses(), pump_heartbeat())
