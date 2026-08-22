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
KEEPALIVE_GAP_S = 1.2     # if no real audio for this long, send silence instead
TRIGGER_TOKENS = 16000    # compress the session before it hits the wall
TARGET_TOKENS = 8000

import taste

# The ear only transcribes in --local mode, but when it is driving the canvas it must
# use exactly the same taste as the brain. See taste.py for why this is shared.
SYSTEM_INSTRUCTION = taste.drawing_prompt()


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
    last_audio = time.time()
    # 20 ms of true digital silence at 16 kHz, mono, 16-bit.
    SILENT_FRAME = b"\x00" * (2 * CFG.sample_rate * CFG.chunk_ms // 1000)

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
                    mark_audio()
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

        def mark_audio() -> None:
            nonlocal last_audio
            last_audio = time.time()

        async def pump_silence():
            """Keep the audio stream continuous while nobody is speaking.

            The floor stops forwarding frames the moment a speaker pauses — that is
            what stops room noise reaching the model. But it also leaves the socket
            idle, and Gemini kills an idle Live session on a keepalive timeout: the
            board would work while you talked, then die when you stopped. Observed
            twice as "session died: keepalive ping timeout".

            Sending real silence keeps the stream alive without feeding the model
            anything to hallucinate from — zeros transcribe as nothing, whereas the
            room noise we are gating out does not.
            """
            while True:
                await asyncio.sleep(0.4)
                if time.time() - last_audio < KEEPALIVE_GAP_S:
                    continue
                mark_audio()
                await session.send_realtime_input(
                    audio=types.Blob(data=SILENT_FRAME,
                                     mime_type="audio/pcm;rate=16000"))

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
                    # With the brain on, IT decides what to draw. Letting the ear
                    # also call tools would double-draw every sentence.
                    if brain is not None and brain.enabled:
                        await session.send_tool_response(function_responses=[
                            types.FunctionResponse(
                                id=fc.id, name=fc.name,
                                response={"error": "another component owns the "
                                                   "canvas; draw nothing"})
                            for fc in response.tool_call.function_calls])
                        continue
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

        await asyncio.gather(pump_audio(), pump_responses(), pump_heartbeat(),
                             pump_silence())
