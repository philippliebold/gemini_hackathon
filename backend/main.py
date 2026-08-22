"""The real thing: mics -> Gemini Live -> WebSocket.

    python backend/main.py                  # Mac mic + phones
    python backend/main.py --devices        # list input devices, then exit
    python backend/main.py --device 2       # pick the Mac's input
    python backend/main.py --phones-only    # only phone mics, ignore the Mac's
    python backend/main.py --no-phones      # only the Mac's mic
    python backend/main.py --pcm talk.pcm   # replay a recording (add MANUAL_ACTIVITY=1)
    python backend/main.py --brain          # 3.7-flash decides instead of the ear

Up to four phones can join at the printed https:// URL. See MICS.md.
"""
import argparse
import asyncio
import sys

import audio
import brain as brain_mod
import gemini_live
import memory as memory_mod
import mic_server
import mics
import ops
import server
import tools
from config import CFG


async def roster_ticker(floor: mics.Floor) -> None:
    """One line showing who is connected and who has the floor. Setup aid: it is
    how you confirm all four phones are actually live before you start talking."""
    last = ""
    while True:
        await asyncio.sleep(2)
        line = floor.summary()
        if floor.mics and line != last:
            print(f"[mic] {line}")
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
    brain = brain_mod.Brain(server.broadcast) if args.brain else None
    # Survives every reconnect: holds the Live API resumption handle so a dropped
    # session comes back into the same conversation rather than a blank one.
    session_state: dict = {}

    async def ear():
        while True:
            try:
                await gemini_live.run(q, server.broadcast, mem, session_state,
                                      brain)
            except Exception as e:  # noqa: BLE001 - reconnect, never die on stage
                print(f"[live] session died: {type(e).__name__}: {e}; retrying in 2s")
                server.broadcast(ops.status("error"))
                await asyncio.sleep(2)

    tasks = [server.serve(), ear(), mem.loop()]
    if brain is not None:
        await brain.select_model()      # know which brain before the talk starts
        tasks.append(brain.loop())

    if args.pcm:
        # One voice, but still through the floor: that is what produces the turn
        # boundaries the Live model needs in order to respond at all.
        tasks.append(audio.file_chunks(q, args.pcm, floor, "Recording"))
    elif not args.phones_only:
        tasks.append(audio.mic_chunks(q, args.device, floor, "Mac mic"))

    if not args.no_phones and not args.pcm:
        tasks += [mic_server.serve_mics(
                      floor,
                      lambda pcm: audio.offer(q, ("audio", pcm)),
                      lambda kind, value: audio.offer(q, (kind, value))),
                  roster_ticker(floor)]

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
