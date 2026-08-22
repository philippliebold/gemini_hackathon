"""Replays shared/fixtures/demo.jsonl over the real WebSocket.

No API key, no mic, no network. This is how the frontend works all day without
ever waiting on the Gemini side.

    python backend/mock_server.py            # loop the demo script
    python backend/mock_server.py --once     # play once and hold
    python backend/mock_server.py --speed 2  # twice as fast
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import ops
import server
from config import CFG

FIXTURE = Path(__file__).resolve().parent.parent / "shared" / "fixtures" / "demo.jsonl"


async def play(once: bool, speed: float):
    lines = [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]
    while True:
        for entry in lines:
            await asyncio.sleep(entry.get("delay", 1.0) / speed)
            frame = {"v": ops.V, "seq": next(ops._seq),
                     "ts": round(time.time(), 3),
                     "op": entry["op"], "payload": entry["payload"]}
            print(f"[mock] {frame['op']:<14} {str(frame['payload'])[:70]}")
            server.broadcast(frame)
        if once:
            await asyncio.Future()
        await asyncio.sleep(3)
        server.broadcast(ops.canvas_clear())


async def main(args):
    await asyncio.gather(server.serve(), play(args.once, args.speed))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--speed", type=float, default=1.0)
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        print("\n[mock] bye")
