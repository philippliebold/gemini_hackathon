"""WebSocket fan-out. Frontend connects here; every frame goes to every client."""
import asyncio
import json

import websockets

import canvas
import mics
import ops
import runtime
from config import CFG

# Set by main.py. Lets the screen drive the mics without server.py importing the
# audio stack (which would drag sounddevice into mock_server.py).
CONTROL: dict = {}

CLIENTS: set = set()
HISTORY: list[dict] = []   # replay to late joiners so a refresh never loses the canvas
MAX_HISTORY = 500


def broadcast(frame: dict) -> None:
    """Sync-callable from anywhere. Fire and forget."""
    if frame["op"] != "status":
        HISTORY.append(frame)
        del HISTORY[:-MAX_HISTORY]
    if frame["op"] == "canvas.clear":
        HISTORY.clear()
        HISTORY.append(frame)
    msg = ops.dumps(frame)
    for ws in list(CLIENTS):
        asyncio.create_task(_safe_send(ws, msg))


async def _safe_send(ws, msg: str) -> None:
    try:
        await ws.send(msg)
    except Exception:  # noqa: BLE001 - a dead client must not stop the show
        CLIENTS.discard(ws)


def mics_payload() -> dict:
    """Everything the screen needs to show the join code and who is live."""
    floor = CONTROL.get("floor")
    mac = CONTROL.get("macmic")
    brain = CONTROL.get("brain")
    devices = CONTROL.get("devices") or (lambda: [])
    return {
        "join_url": CONTROL.get("join_url"),
        "qr_svg": CONTROL.get("qr_svg"),
        "max_mics": CONTROL.get("max_mics", 4),
        "roster": floor.roster() if floor is not None else [],
        "devices": devices() if callable(devices) else devices,
        "mac": {"active": bool(mac and mac.active),
                "device": mac.device if mac else None},
        "gate": mics.GATE_RMS,
        "ear": CONTROL.get("ear", "live"),
        "listening": runtime.listening(),
        "speaker": (CONTROL["speaker"].state()
                    if CONTROL.get("speaker") is not None else None),
        "notes": (CONTROL["memory"].summary
                  if CONTROL.get("memory") is not None else None),
        "brain": {"enabled": bool(brain and brain.enabled),
                  "model": (brain.model if brain and brain.enabled else None)},
    }


def push_mics() -> None:
    """Broadcast mic state. Safe to call from anywhere; never raises."""
    try:
        broadcast(ops.mics_state(mics_payload()))
    except Exception as e:                                # noqa: BLE001
        print(f"[ws] mics.state failed: {e}")


async def handler(ws):
    CLIENTS.add(ws)
    print(f"[ws] client connected ({len(CLIENTS)} total)")
    try:
        for frame in HISTORY:
            await ws.send(ops.dumps(frame))
        # A late joiner needs the join code immediately, not on the next change.
        if CONTROL:
            await ws.send(ops.dumps(ops.mics_state(mics_payload())))
        # NOTE: a client that sends a command *during* the replay above can have it
        # dropped if it disconnects mid-send. The display holds its socket open, so
        # this only bites short-lived scripted clients.
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("cmd") == "presenter":
                await on_presenter(msg.get("action"))
    except websockets.ConnectionClosed:
        pass
    finally:
        CLIENTS.discard(ws)
        print(f"[ws] client gone ({len(CLIENTS)} left)")


def _silence_mac_mic(why: str) -> None:
    """Feedback protection: the Mac mic cannot be open while the Mac is playing the
    phone into the room."""
    mac = CONTROL.get("macmic")
    if mac is not None and getattr(mac, "active", False):
        mac.off()
        print(f"[spk] Mac mic switched off — {why}")


async def on_presenter(action: str | None) -> None:
    """Presenter keyboard commands from the display.

    These must move backend state too, or canvas.py drifts out of sync with what
    the room can actually see — and the model is told the stale version.
    """
    if action == "clear":
        canvas.reset()
        broadcast(ops.canvas_clear())
    elif action == "undo":
        for frame in canvas.undo_last():
            broadcast(frame)
    elif action and action.startswith("mic_device:"):
        mac = CONTROL.get("macmic")
        if mac is not None:
            try:
                mac.select(int(action.split(":", 1)[1]))
            except ValueError:
                return
            push_mics()
    elif action == "mic_off":
        mac = CONTROL.get("macmic")
        if mac is not None:
            mac.off()
            push_mics()
    elif action and action.startswith("mic_gate:"):
        try:
            mics.set_gate(float(action.split(":", 1)[1]))
        except ValueError:
            return
        push_mics()
    elif action in ("brain_on", "brain_off"):
        brain = CONTROL.get("brain")
        if brain is None:
            return
        # With local ears nothing else can draw: the Live API is not even
        # connected. Switching the brain off there does not hand the canvas back
        # to anything, it just makes the screen go permanently blank.
        if action == "brain_off" and CONTROL.get("ear") == "local":
            print("[brain] refused: local ears have no other way to draw")
            push_mics()
            return
        await brain.set_enabled(action == "brain_on")
        push_mics()
    elif action and action.startswith("said:"):
        # Transcript from the browser's own speech recognition. Lets the screen
        # drive the canvas with no PortAudio, no sounddevice and no Live audio
        # session -- the three things that break on someone else's laptop.
        line = action.split(":", 1)[1].strip()
        if not line:
            return
        broadcast(ops.status("thinking", line))
        brain = CONTROL.get("brain")
        if brain is None:
            print("[said] no brain attached; run with --brain")
            return
        brain.feed(line)
    elif action and action.startswith("mic_gain:"):
        # mic_gain:<mic_id>:<0..3>
        try:
            _, mic_id, val = action.split(":", 2)
            g = float(val)
        except ValueError:
            return
        spk = CONTROL.get("speaker")
        floor = CONTROL.get("floor")
        if spk is not None:
            spk.set_gain(mic_id, g)
        if floor is not None and mic_id in floor.mics:
            floor.mics[mic_id].gain = max(0.0, min(3.0, g))
        push_mics()
    elif action and action.startswith("speaker_device:"):
        spk = CONTROL.get("speaker")
        if spk is not None:
            try:
                dev = int(action.split(":", 1)[1])
            except ValueError:
                return
            _silence_mac_mic("switching the PA output")
            spk.start(dev)
            push_mics()
    elif action in ("speaker_on", "speaker_off"):
        spk = CONTROL.get("speaker")
        if spk is None:
            return
        if action == "speaker_on":
            # Playing the phone through the speakers while the Mac mic is open is a
            # feedback loop. Close it rather than let the room howl.
            _silence_mac_mic("playing phone audio out loud")
            spk.start(spk.device)
        else:
            spk.stop()
        push_mics()
    elif action in ("listen_on", "listen_off"):
        runtime.set_listening(action == "listen_on")
        broadcast(ops.status("listening" if runtime.listening() else "idle"))
        push_mics()
    elif action == "context_reset":
        # Full reset: the board, the running record, and the model's recent lines.
        # Rehearsing the same script three times otherwise has it reasoning about
        # the previous run-throughs.
        canvas.reset()
        broadcast(ops.canvas_clear())
        mem = CONTROL.get("memory")
        if mem is not None:
            mem.reset()
        brain = CONTROL.get("brain")
        if brain is not None:
            brain.clear()
        push_mics()
        print("[ws] context reset by the presenter")
    elif action == "mics_refresh":
        push_mics()
    # TODO: pause / resume


async def serve():
    print(f"[ws] listening on ws://{CFG.ws_host}:{CFG.ws_port}")
    async with websockets.serve(handler, CFG.ws_host, CFG.ws_port):
        await asyncio.Future()
