"""Replays shared/fixtures/demo.jsonl over the real WebSocket.

No API key, no mic, no network. This is how the frontend works all day without
ever waiting on the Gemini side.

    python backend/mock_server.py            # loop the demo script
    python backend/mock_server.py --once     # play once and hold
    python backend/mock_server.py --speed 2  # twice as fast
    python backend/mock_server.py --fixture evolve   # cards that GROW, not just appear
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import ops
import server
from config import CFG

FIXTURES = Path(__file__).resolve().parent.parent / "shared" / "fixtures"
FIXTURE = FIXTURES / "demo.jsonl"


def resolve_fixture(name: str) -> Path:
    p = Path(name)
    for cand in (p, FIXTURES / name, FIXTURES / f"{name}.jsonl"):
        if cand.is_file():
            return cand
    raise SystemExit(f"no such fixture: {name}. Available: "
                     + ", ".join(sorted(f.stem for f in FIXTURES.glob("*.jsonl"))))


async def play(once: bool, speed: float, fixture: Path = FIXTURE):
    lines = [json.loads(l) for l in fixture.read_text().splitlines() if l.strip()]
    print(f"[mock] playing {fixture.name} ({len(lines)} frames)")
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
    fixture = resolve_fixture(args.fixture) if args.fixture else FIXTURE
    await asyncio.gather(server.serve(), play(args.once, args.speed, fixture))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--fixture", default=None,
                   help="fixture name or path (default: demo). Try 'evolve' to see "
                        "block.update frames — cards that grow instead of duplicate.")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        print("\n[mock] bye")
