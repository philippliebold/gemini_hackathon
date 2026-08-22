"""The real thing: mics -> Gemini Live -> WebSocket.

    python backend/main.py                  # local Whisper ear + Gemini brain (default)
    python backend/main.py --live           # Gemini Live API as the ear instead
    python backend/main.py --devices        # list input devices, then exit
    python backend/main.py --device 2       # pick the Mac's input
    python backend/main.py --phones-only    # only phone mics, ignore the Mac's
    python backend/main.py --no-phones      # only the Mac's mic
    python backend/main.py --pcm talk.pcm   # replay a recording (add MANUAL_ACTIVITY=1)
    python backend/main.py --brain          # 3.7-flash decides instead of the ear
    python backend/main.py --local --no-llm # fully offline: Whisper ear + no API

Up to four phones can join at the printed https:// URL. See MICS.md.
"""
import argparse
import asyncio
import sys

import audio
import brain as brain_mod
import local_brain as local_brain_mod
import ears_local
import gemini_live
import memory as memory_mod
import mic_server
import mics
import ops
import server
import speaker as speaker_mod
import tools
from config import CFG


async def local_pump(q: asyncio.Queue, ear, brain) -> None:
    """Drain the audio queue into the local ear instead of the Live session."""
    import runtime
    while True:
        kind, payload = await q.get()
        if not runtime.listening():
            continue                       # stopped: drop it, do not buffer
        if kind == "audio":
            ear.feed(payload)
        elif kind == "speaker" and brain is not None:
            brain.set_speaker(payload)
        # activity events are meaningless locally: we own the boundaries


async def loop_watchdog(threshold_s: float = 0.25) -> None:
    """Report when the event loop stalls.

    A blocked loop and a bad network look identical from the outside: both end as
    "keepalive ping timeout", because a late PONG and an unanswered PING are the
    same symptom. This tells them apart — if there are no lag lines and the session
    still drops, the network is the problem, not us.
    """
    import time as _t
    while True:
        t0 = _t.perf_counter()
        await asyncio.sleep(0.5)
        lag = _t.perf_counter() - t0 - 0.5
        if lag > threshold_s:
            print(f"[loop] stalled {lag*1000:.0f} ms — this can kill the Live session")


def qr_svg(url: str) -> str | None:
    """Render the join URL as an SVG on the backend, so the screen needs no QR
    library and works with no network."""
    try:
        import io
        import qrcode
        import qrcode.image.svg
        buf = io.BytesIO()
        qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage,
                    box_size=10, border=2).save(buf)
        svg = buf.getvalue().decode()
        svg = svg[svg.index("<svg"):]                    # drop the XML declaration
        return svg.replace('width="', 'data-w="', 1).replace('height="', 'data-h="', 1)
    except Exception as e:                               # noqa: BLE001
        print(f"[mic] no QR ({e}); the URL still works")
        return None


async def roster_ticker(floor: mics.Floor) -> None:
    """One line showing who is connected and who has the floor, and the same state
    pushed to the screen so the room can see it without reading a terminal."""
    last = ""
    while True:
        await asyncio.sleep(1.5)
        line = floor.summary()
        if line != last:
            if floor.mics:
                print(f"[mic] {line}")
            server.push_mics()
            last = line


async def main(args):
    if not CFG.api_key:
        sys.exit("GEMINI_API_KEY is not set. cp .env.example .env and fill it in.")

    # let slow tools (image generation) push their second frame
    tools.BROADCAST = server.broadcast

    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    floor = mics.Floor()

    mem = memory_mod.TopicMemory()
    # Off by default: the Live session calls the tools itself on a real mic.
    # --brain moves the decision to gemini-3.7-flash instead, which is the escape
    # hatch if live tool calling turns out flaky on stage. See brain.py.
    # Always built, never enabled by default: the Live session calls the tools
    # itself on a real mic. The screen can hand the decision to gemini-3.7-flash
    # mid-talk if the ear turns out flaky.
    # --no-llm swaps in a brain that needs no API at all: pattern matching over
    # the transcript. Worse judgement than the model, but Wikimedia photos and
    # Maps routes need no Gemini quota, so a capped account still gives a talk.
    # `mem` is not optional in practice: without it the brain judges every line
    # with no idea what the talk has already established, which is exactly what it
    # needs in order to stay quiet about a topic already covered. It was omitted
    # here, so THE RECORD block in brain._consider() was dead code all along.
    brain = (local_brain_mod.LocalBrain(server.broadcast) if args.no_llm
             else brain_mod.Brain(server.broadcast, mem))
    # Survives every reconnect: holds the Live API resumption handle so a dropped
    # session comes back into the same conversation rather than a blank one.
    session_state: dict = {}

    async def ear():
        attempt = 0
        while True:
            try:
                await gemini_live.run(q, server.broadcast, mem, session_state,
                                      brain)
                attempt = 0                 # clean exit: reset the backoff
            except Exception as e:  # noqa: BLE001 - reconnect, never die on stage
                # Backoff, but start small: a keepalive drop mid-sentence used to
                # cost a flat 2 s of dead stage, and the session resumes with its
                # context intact so retrying fast is cheap.
                delay = min(2.0, 0.3 * (2 ** attempt))
                attempt = min(attempt + 1, 3)
                print(f"[live] session died: {type(e).__name__}: "
                      f"{str(e)[:80]}; retrying in {delay:.1f}s")
                server.broadcast(ops.status("error"))
                await asyncio.sleep(delay)

    # The screen drives the mics: it needs the join code, the roster, and this
    # machine's real input devices.
    macmic = audio.MacMic(q, floor)
    spk = speaker_mod.Speaker()
    server.CONTROL.update({
        "floor": floor, "macmic": macmic, "max_mics": mics.MAX_MICS,
        "devices": audio.input_devices, "brain": brain, "memory": mem,
        "speaker": spk, "macmic_obj": macmic,
        "join_url": f"https://{mic_server.lan_ip()}:{CFG.mic_port}/",
    })
    server.CONTROL["qr_svg"] = qr_svg(server.CONTROL["join_url"])

    if not args.live:
        # Local ears own transcription; nothing connects to the Live API at all, so
        # no turn-taking, no VAD guessing, no keepalive drops. The brain is the only
        # thing that can draw, so it is on by definition.
        local = ears_local.LocalEar(server.broadcast, brain, mem)
        server.CONTROL["ear"] = "local"
        await brain.set_enabled(True)
        tasks = [server.serve(), mem.loop(), loop_watchdog(), brain.loop(),
                 local.loop(), local_pump(q, local, brain)]
    else:
        # --live --no-llm is a valid pair: the Live API is the ear and LocalBrain
        # draws from its transcripts, so the screen keeps working on zero Gemini
        # text quota. gemini_live refuses the ear's own tool calls whenever a
        # brain is enabled, so only one of them ever draws.
        server.CONTROL["ear"] = "live"
        tasks = [server.serve(), ear(), mem.loop(), loop_watchdog(), brain.loop()]
        if args.brain:
            await brain.set_enabled(True)

    if args.pcm:
        # One voice, but still through the floor: that is what produces the turn
        # boundaries the Live model needs in order to respond at all.
        tasks.append(audio.file_chunks(q, args.pcm, floor, "Recording"))
    elif not args.phones_only:
        # Start on the requested device (or the default) but keep it switchable
        # from the dock for the rest of the session.
        macmic.select(args.device if args.device is not None
                      else next((d["index"] for d in audio.input_devices()
                                 if d["default"]), 0))
        tasks.append(macmic.run())
    else:
        tasks.append(macmic.run())          # idle, but selectable from the screen

    if not args.pcm:
        tasks.append(roster_ticker(floor))

    if not args.no_phones and not args.pcm:
        tasks += [mic_server.serve_mics(
                      floor,
                      lambda pcm: audio.offer(q, ("audio", pcm)),
                      lambda kind, value: audio.offer(q, (kind, value)),
                      spk.feed)]

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--devices", action="store_true")
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--pcm", type=str, default=None)
    p.add_argument("--no-phones", action="store_true",
                   help="don't run the phone-mic server")
    p.add_argument("--phones-only", action="store_true",
                   help="ignore the Mac's own mic; phones only")
    p.add_argument("--live", action="store_true",
                   help="use the Gemini Live API as the ear instead of local Whisper. "
                        "Measured WORSE on the same audio (0 transcripts vs 8): the "
                        "model guesses when you stopped speaking and is often wrong. "
                        "Kept for the 'audio straight into Gemini' story.")
    p.add_argument("--local", action="store_true",
                   help="(default; accepted for compatibility)")
    p.add_argument("--no-llm", action="store_true",
                   help="decide what to draw with local pattern matching instead "
                        "of Gemini — works with no API quota at all")
    p.add_argument("--brain", action="store_true",
                   help="let gemini-3.7-flash make the drawing decisions from the "
                        "transcript instead of the Live session (fallback path)")
    a = p.parse_args()
    if a.devices:
        print(audio.list_devices())
        sys.exit(0)
    try:
        asyncio.run(main(a))
    except KeyboardInterrupt:
        print("\n[main] bye")
