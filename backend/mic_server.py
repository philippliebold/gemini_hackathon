"""Phone mics in. One TLS port serves the mic page AND the audio sockets.

    phone browser --(wss, binary 20 ms PCM frames)--> here --> floor --> queue

Why TLS, unavoidably: `getUserMedia` only works in a secure context. A phone on
`http://192.168.x.x` gets no microphone, full stop. So we serve the page over
https with a self-signed cert (generated on first run into `.session/`). Phones
accept the warning once — no tunnel, no account, no internet needed.

Latency: a 20 ms frame is 640 bytes, sent as a BINARY frame (base64 would add a
third more bytes plus an encode on the phone's main thread). Nothing is buffered
here — a frame is gated and forwarded in the same callback it arrives in. On a
quiet LAN the phone-to-Mac hop lands around 5-25 ms, well under the mic hardware
latency it rides on top of.
"""
import asyncio
import json
import socket
import ssl
import subprocess
from pathlib import Path
from typing import Callable

import websockets
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

import mics
from config import CFG

HERE = Path(__file__).resolve().parent
PAGE = HERE / "static" / "mic.html"
CERT_DIR = HERE.parent / ".session"
CERT, KEY = CERT_DIR / "mic-cert.pem", CERT_DIR / "mic-key.pem"


def lan_ip() -> str:
    """Our address on the local network. No packet is actually sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert(ip: str) -> bool:
    """Self-signed cert for the mic page. Returns False if we could not make one."""
    if CERT.exists() and KEY.exists():
        return True
    CERT_DIR.mkdir(exist_ok=True)
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(KEY), "-out", str(CERT), "-days", "30",
             "-subj", "/CN=co-presenter",
             "-addext", f"subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost"],
            check=True, capture_output=True)
        print(f"[mic] generated a self-signed cert in {CERT_DIR}")
        return True
    except (OSError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", b"") or b""
        print(f"[mic] could not generate a cert ({e}); {detail[:200]!r}")
        print("[mic] phone mics disabled — the Mac's own mic still works")
        return False


def _serve_page(connection, request):
    """Non-WebSocket GETs get the mic page; upgrades fall through to the handler."""
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    if not PAGE.exists():
        return Response(500, "Missing page", Headers({"Content-Length": "0"}), b"")
    body = PAGE.read_bytes()
    return Response(200, "OK", Headers({
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
    }), body)


def print_join_banner(url: str) -> None:
    print(f"\n[mic] phones join at:  {url}")
    print(f"[mic] up to {mics.MAX_MICS} mics. Accept the certificate warning once.")
    try:
        import qrcode  # optional; nice on stage, not required
        q = qrcode.QRCode(border=1)
        q.add_data(url)
        q.print_ascii(invert=True)
    except Exception:
        print("[mic] (pip install qrcode for a scannable code here)\n")


async def serve_mics(floor: mics.Floor, on_audio: Callable[[bytes], None],
                     on_event: Callable[[str, str], None],
                     on_raw: Callable[[bytes, str], None] | None = None) -> None:
    """Run the mic ingest server until cancelled. Never raises into the demo."""
    ip = lan_ip()
    if not ensure_cert(ip):
        await asyncio.Future()          # stay parked; the rest of the app runs on

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    url = f"https://{ip}:{CFG.mic_port}/"
    sockets: dict[str, object] = {}

    async def announce_floor() -> None:
        """Tell each phone whether it currently owns the stream."""
        for mic_id, ws in list(sockets.items()):
            try:
                await ws.send(json.dumps({"holder": floor.holder == mic_id}))
            except Exception:           # noqa: BLE001 - a dead phone is not our problem
                sockets.pop(mic_id, None)

    async def handler(ws):
        mic = None
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            mic = floor.join(str(hello.get("label", ""))[:24])
        except mics.MicFull as e:
            await ws.send(json.dumps({"error": f"Session is full — {e}."}))
            return
        except (asyncio.TimeoutError, json.JSONDecodeError, ValueError):
            await ws.send(json.dumps({"error": "Bad handshake."}))
            return

        sockets[mic.id] = ws
        await ws.send(json.dumps({"ok": True, "id": mic.id, "label": mic.label}))
        print(f"[mic] + {mic.label} ({mic.id}) joined — "
              f"{len(floor.mics)}/{mics.MAX_MICS}")
        try:
            async for frame in ws:
                if not isinstance(frame, bytes):
                    continue            # only the handshake is text
                # Raw tap BEFORE the gate: the audible path must not be chopped
                # by a noise gate meant for the model. See speaker.py.
                if on_raw is not None:
                    on_raw(frame, mic.id)
                forward, events = floor.accept(mic.id, frame)
                for kind, value in events:      # boundaries before their audio
                    on_event(kind, value)
                    if kind == "speaker":
                        print(f"[mic] floor -> {value}")
                if forward:
                    on_audio(frame)
                if events:
                    await announce_floor()
        except websockets.ConnectionClosed:
            pass
        finally:
            sockets.pop(mic.id, None)
            floor.leave(mic.id)
            print(f"[mic] - {mic.label} left — {len(floor.mics)}/{mics.MAX_MICS}")
            await announce_floor()

    async with serve(handler, CFG.mic_host, CFG.mic_port, ssl=ctx,
                     process_request=_serve_page,
                     max_size=4096,          # a 20 ms frame is 640 bytes
                     ping_interval=20):
        print_join_banner(url)
        await asyncio.Future()
