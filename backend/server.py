"""WebSocket fan-out. Frontend connects here; every frame goes to every client."""
import asyncio
import json

import websockets

import canvas
import ops
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
    elif action in ("brain_on", "brain_off"):
        brain = CONTROL.get("brain")
        if brain is not None:
            await brain.set_enabled(action == "brain_on")
            push_mics()
    elif action == "mics_refresh":
        push_mics()
    # TODO: pause / resume


async def serve():
    print(f"[ws] listening on ws://{CFG.ws_host}:{CFG.ws_port}")
    async with websockets.serve(handler, CFG.ws_host, CFG.ws_port):
        await asyncio.Future()
