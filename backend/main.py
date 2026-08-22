"""The real thing: mic -> Gemini Live -> WebSocket.

    python backend/main.py                  # mic
    python backend/main.py --devices        # list input devices, then exit
    python backend/main.py --device 2       # pick one
    python backend/main.py --pcm talk.pcm   # replay a recording instead of the mic
"""
import argparse
import asyncio
import sys

import audio
import gemini_live
import memory as memory_mod
import ops
import server
from config import CFG


async def main(args):
    if not CFG.api_key:
        sys.exit("GEMINI_API_KEY is not set. cp .env.example .env and fill it in.")

    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    source = (audio.file_chunks(q, args.pcm) if args.pcm
              else audio.mic_chunks(q, args.device))

    mem = memory_mod.TopicMemory()
    # Survives every reconnect: holds the Live API resumption handle so a dropped
    # session comes back into the same conversation rather than a blank one.
    session_state: dict = {}

    async def ear():
        while True:
            try:
                await gemini_live.run(q, server.broadcast, mem, session_state)
            except Exception as e:  # noqa: BLE001 - reconnect, never die on stage
                print(f"[live] session died: {type(e).__name__}: {e}; retrying in 2s")
                server.broadcast(ops.status("error"))
                await asyncio.sleep(2)

    await asyncio.gather(server.serve(), source, ear(), mem.loop())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--devices", action="store_true")
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--pcm", type=str, default=None)
    a = p.parse_args()
    if a.devices:
        print(audio.list_devices())
        sys.exit(0)
    try:
        asyncio.run(main(a))
    except KeyboardInterrupt:
        print("\n[main] bye")
