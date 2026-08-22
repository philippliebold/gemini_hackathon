"""Verify the phone-mic path end to end, with no phones.

Spins up the real TLS mic server, connects synthetic clients over real wss, and
pushes real PCM through the real floor controller. This is how you know the audio
path works before four people are standing in front of a screen.

    python backend/mic_check.py
"""
import asyncio
import json
import ssl
import sys
import time

import numpy as np
import websockets

import mic_server
import mics
from config import CFG

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
_fails: list[str] = []
FRAME = 320                      # 20 ms at 16 kHz


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"{PASS if cond else FAIL} {label}" + (f"  — {detail}" if not cond else ""))
    if not cond:
        _fails.append(label)


def tone(amp: float, n: int = FRAME) -> bytes:
    """One frame of 220 Hz at a given amplitude, as 16-bit LE PCM."""
    t = np.arange(n) / CFG.sample_rate
    return (np.sin(2 * np.pi * 220 * t) * amp * 32767).astype(np.int16).tobytes()


SILENCE = tone(0.0005)           # room noise, below the gate
QUIET = tone(0.03)
LOUD = tone(0.30)


class Client:
    def __init__(self, label: str):
        self.label, self.ws, self.id = label, None, None
        self.holder = False

    async def join(self, url: str, sslctx):
        self.ws = await websockets.connect(url, ssl=sslctx, max_size=4096)
        await self.ws.send(json.dumps({"hello": 1, "label": self.label}))
        reply = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=5))
        if reply.get("error"):
            return reply["error"]
        self.id = reply["id"]
        return None

    async def send(self, pcm: bytes, frames: int = 1):
        for _ in range(frames):
            await self.ws.send(pcm)
            await asyncio.sleep(0.002)

    async def close(self):
        if self.ws:
            await self.ws.close()


async def run() -> int:
    got_audio: list[tuple[float, bytes]] = []
    speakers: list[str] = []
    activity: list[str] = []
    floor = mics.Floor()

    def on_event(kind: str, value: str) -> None:
        (speakers if kind == "speaker" else activity).append(value)

    if not mic_server.ensure_cert(mic_server.lan_ip()):
        print("openssl unavailable — cannot test the TLS mic path")
        return 1

    # A running `main.py` already owns this port. Without this check the clients
    # below silently connect to THAT server, whose Floor is a different object, and
    # every assertion fails for a reason that has nothing to do with the code.
    import socket
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", CFG.mic_port))
    except OSError:
        print(f"\033[31mport {CFG.mic_port} is already in use\033[0m — a backend is "
              f"running.\nStop it first:  pkill -f 'main.py'")
        return 1
    finally:
        probe.close()

    task = asyncio.create_task(mic_server.serve_mics(
        floor,
        lambda pcm: got_audio.append((time.perf_counter(), pcm)),
        on_event))
    await asyncio.sleep(1.2)

    sslctx = ssl.create_default_context()
    sslctx.check_hostname = False
    sslctx.verify_mode = ssl.CERT_NONE      # self-signed, as the phones will see
    url = f"wss://127.0.0.1:{CFG.mic_port}/"

    print("\n\033[1m── 1. the page a phone loads ─────────────────────────\033[0m")
    import urllib.request

    def fetch():
        # Must run off the loop: a blocking urlopen here would deadlock against the
        # very server we are testing, which cannot complete its TLS handshake while
        # the event loop is parked inside a socket read.
        with urllib.request.urlopen(f"https://127.0.0.1:{CFG.mic_port}/",
                                    context=sslctx, timeout=5) as r:
            return r.status, r.read().decode()

    status, page = await asyncio.to_thread(fetch)
    check("mic page served over https on the same port", status == 200, str(status))
    check("page carries the AudioWorklet capture path",
          "registerProcessor" in page and "pcm16" in page)
    check("page opens a wss socket, not ws", "wss://" in page
          and "ws://" not in page)

    print("\n\033[1m── 2. four mics join, the fifth is refused ──────────\033[0m")
    clients = [Client(n) for n in ("Marwin", "Till", "Philipp", "Yufei")]
    for c in clients:
        err = await c.join(url, sslctx)
        check(f"{c.label} joined", err is None, str(err))
    fifth = Client("Gatecrasher")
    err = await fifth.join(url, sslctx)
    check("fifth mic refused with a readable message",
          err is not None and "full" in err.lower(), str(err))
    await fifth.close()
    check("roster shows exactly 4", len(floor.mics) == 4, str(len(floor.mics)))

    print("\n\033[1m── 3. silence is not forwarded ──────────────────────\033[0m")
    got_audio.clear()
    for c in clients:
        await c.send(SILENCE, 5)
    await asyncio.sleep(0.25)
    check("a quiet room sends nothing to the model", not got_audio,
          f"{len(got_audio)} frames leaked")

    print("\n\033[1m── 4. one speaker takes the floor ───────────────────\033[0m")
    got_audio.clear(); speakers.clear(); activity.clear()
    await clients[0].send(LOUD, 10)
    await asyncio.sleep(0.3)
    check("their audio is forwarded", len(got_audio) >= 8, f"{len(got_audio)}")
    check("the model is told who is speaking", speakers == ["Marwin"], str(speakers))
    check("floor is held by them", floor.holder == clients[0].id)
    # Without this the model hears nothing at all — its own VAD never fires.
    check("a turn was OPENED for the model", activity == ["start"], str(activity))

    print("\n\033[1m── 5. cross-talk does NOT double up ─────────────────\033[0m")
    got_audio.clear()
    await asyncio.gather(clients[0].send(LOUD, 10), clients[1].send(QUIET, 10))
    await asyncio.sleep(0.3)
    # 20 frames went in from two mics; only the floor holder's may come out.
    check("only one stream reaches the model", len(got_audio) <= 12,
          f"{len(got_audio)} frames — two mics got through")
    check("the quieter mic did not steal the floor",
          floor.holder == clients[0].id, str(floor.holder))

    print("\n\033[1m── 6. a louder interjection DOES take over ──────────\033[0m")
    speakers.clear(); activity.clear()
    await clients[2].send(tone(0.85), 8)
    await asyncio.sleep(0.3)
    check("floor moved to the interrupter", floor.holder == clients[2].id,
          str(floor.holder))
    check("the switch was announced once", speakers == ["Philipp"], str(speakers))
    check("previous turn closed before the new one opened",
          activity == ["end", "start"], str(activity))

    print("\n\033[1m── 6b. the same voice is not re-announced ───────────\033[0m")
    speakers.clear()
    await asyncio.sleep(mics.HANGOVER_S + 0.1)      # let the floor lapse
    await clients[2].send(tone(0.85), 6)
    await asyncio.sleep(0.25)
    check("re-taking the floor does not repeat the name", speakers == [],
          str(speakers))

    print("\n\033[1m── 7. the floor is released after the hangover ──────\033[0m")
    activity.clear()
    await asyncio.sleep(mics.HANGOVER_S + 0.1)
    await clients[2].send(SILENCE, 2)
    await asyncio.sleep(0.15)
    check("nobody holds the floor once everyone stops",
          floor.holder is None, str(floor.holder))
    # The model only answers once a turn closes, so this event IS the latency.
    check("the turn was CLOSED so the model can respond",
          activity == ["end"], str(activity))

    print("\n\033[1m── 8. a leaving phone frees its slot ────────────────\033[0m")
    await clients[3].close()
    await asyncio.sleep(0.4)
    check("roster dropped to 3", len(floor.mics) == 3, str(len(floor.mics)))
    late = Client("Latecomer")
    err = await late.join(url, sslctx)
    check("a new phone can take the free slot", err is None, str(err))
    await late.close()

    print("\n\033[1m── 9. server-side latency per frame ─────────────────\033[0m")
    got_audio.clear()
    marks = []
    for _ in range(40):
        marks.append(time.perf_counter())
        await clients[0].send(LOUD, 1)
    await asyncio.sleep(0.4)
    if len(got_audio) >= 20:
        lat = [(got_audio[i][0] - marks[i]) * 1000
               for i in range(min(len(marks), len(got_audio)))]
        lat.sort()
        p50, p95 = lat[len(lat)//2], lat[int(len(lat)*0.95) - 1]
        print(f"   loopback wss->gate->queue: p50 {p50:.2f} ms, p95 {p95:.2f} ms "
              f"({len(lat)} frames)")
        check("no buffering in the server hop (p95 under 15 ms)", p95 < 15,
              f"p95 {p95:.2f} ms")
    else:
        check("enough frames to measure latency", False, f"{len(got_audio)}")

    for c in clients:
        await c.close()
    task.cancel()
    print()
    if _fails:
        print(f"\033[31m{len(_fails)} check(s) failed:\033[0m " + "; ".join(_fails))
        return 1
    print("\033[32mphone-mic path verified end to end\033[0m")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        sys.exit(130)
