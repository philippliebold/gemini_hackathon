"""Generate the demo fixture by actually giving a talk to the real model.

The ONLY human input here is TALK: the sentences a person says. Nothing decides
what appears on screen except Gemini — no tool is named, no argument is written
by hand. Whatever it chooses to draw is what the demo shows, which is the point:
the fixture is a recording of the product working, not a mock-up of it.

    python backend/make_demo.py                    # write shared/fixtures/demo.jsonl
    python backend/make_demo.py --out /tmp/x.jsonl
    python backend/make_demo.py --dry              # show choices, write nothing

Slow on purpose: image generation and photo search run for real, so a full pass
takes a couple of minutes. Re-run it whenever the tools or the prompt change and
the demo re-records itself.
"""
import argparse
import asyncio
import json
import pathlib
import re
import sys
import time

from google import genai
from google.genai import types

import canvas
import gemini_live
import ops
import tools
from config import CFG

# One person, one talk. Written to exercise every capability without ever
# naming one: a claim, a definition, figures, a trend, a real object, an
# imagined scene, a formula, a place, and a wrap-up cue at the end.
TALK = [
    "so nobody actually wants to build slides for a ten minute talk",
    "the screen should just build itself while you speak",
    "we get from a spoken sentence to pixels in about one point four seconds",
    "capture is forty milliseconds, the model is nine hundred, "
    "the websocket thirty, and rendering another one hundred and fifty",
    "sessions per week went one twenty, two hundred, one eighty five, "
    "three ninety, five twenty, and seven sixty",
    "for the interface I imagine something as clean as a Porsche 911",
    "now picture the golden gate bridge at sunset with fog rolling in",
    "the main cable follows a parabola, y equals w x squared over two H",
    "I'm at one-north right now, how do I walk to Fusionopolis",
    "okay so to sum up everything we covered today",
]

TURN_TIMEOUT = 55          # a slow tool (image gen) still has to land
SETTLE = 14                # seconds to wait for async tools after the last turn


def _redact(blob: str) -> str:
    """A fixture generated from a live run bakes real keys into embed URLs."""
    if CFG.maps_key:
        blob = blob.replace(CFG.maps_key, "YOUR_MAPS_KEY")
    if CFG.api_key:
        blob = blob.replace(CFG.api_key, "REDACTED")
    leaked = re.findall(r"AIzaSy[A-Za-z0-9_\-]{20,}|AQ\.[A-Za-z0-9_\-]{20,}", blob)
    if leaked:
        raise SystemExit(f"refusing to write: still contains {leaked[0][:12]}…")
    return blob


async def record(dry: bool) -> list[dict]:
    out: list[dict] = []
    t0 = time.monotonic()
    last = {"t": 0.0}

    def rec(frame: dict) -> None:
        """Timestamp every frame by when it really happened."""
        now = time.monotonic() - t0
        out.append({"delay": round(max(0.0, now - last["t"]), 2),
                    "op": frame["op"], "payload": frame["payload"]})
        last["t"] = now

    # Async tools (image generation, photo search, routes) push their second
    # frame through this, exactly as they do on stage.
    tools.BROADCAST = rec

    canvas.reset()
    canvas.COOLDOWN_S = 0.0        # the talk is already paced like real speech
    canvas.FOCUS_THROTTLE_S = 0.0

    rec(ops.canvas_clear())
    rec(ops.status("listening"))

    client = genai.Client(api_key=CFG.api_key)
    async with client.aio.live.connect(model=CFG.live_model,
                                       config=gemini_live.build_config()) as s:
        for line in TALK:
            rec(ops.status("thinking", line))
            print(f"\n  \033[2m“{line}”\033[0m")
            await s.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=line)]))

            drew = False
            try:
                async with asyncio.timeout(TURN_TIMEOUT):
                    async for r in s.receive():
                        if r.tool_call:
                            replies = []
                            for fc in r.tool_call.function_calls:
                                args = dict(fc.args or {})
                                frames, result = tools.dispatch(fc.name, args)
                                note = (result.get("error")
                                        or result.get("skipped") or "")
                                print(f"     → {fc.name}"
                                      f"{'  [' + str(note) + ']' if note else ''}")
                                for f in frames:
                                    rec(f)
                                    drew = True
                                replies.append(types.FunctionResponse(
                                    id=fc.id, name=fc.name, response=result))
                            if replies:
                                await s.send_tool_response(function_responses=replies)
                        sc = r.server_content
                        if sc and sc.turn_complete:
                            break
            except (asyncio.TimeoutError, TimeoutError):
                print("     … turn timed out")

            if not drew:
                print("     · stayed silent")
            await asyncio.sleep(1.4)          # the pause between sentences

    # Let image generation and photo search finish; their updates are the
    # difference between a shimmer and a picture.
    print(f"\n  waiting {SETTLE}s for slow tools…")
    await asyncio.sleep(SETTLE)
    rec(ops.status("listening"))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="shared/fixtures/demo.jsonl")
    p.add_argument("--dry", action="store_true", help="show choices, write nothing")
    a = p.parse_args()

    if not CFG.api_key:
        sys.exit("GEMINI_API_KEY is not set.")

    frames = asyncio.run(record(a.dry))

    kinds: dict[str, int] = {}
    for f in frames:
        if f["op"] == "block.add":
            kinds[f["payload"]["type"]] = kinds.get(f["payload"]["type"], 0) + 1
    total = sum(f["delay"] for f in frames)
    print(f"\n  {len(frames)} frames · {total:.0f}s · "
          + ", ".join(f"{k}×{v}" for k, v in sorted(kinds.items())))

    if a.dry:
        print("  --dry: nothing written")
        return 0

    blob = _redact("\n".join(json.dumps(o, ensure_ascii=False) for o in frames))
    path = pathlib.Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(blob + "\n")
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
