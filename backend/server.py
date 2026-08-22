"""WebSocket fan-out. Frontend connects here; every frame goes to every client."""
import asyncio
import json

import websockets

import canvas
import ops
from config import CFG

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


async def handler(ws):
    CLIENTS.add(ws)
    print(f"[ws] client connected ({len(CLIENTS)} total)")
    try:
        for frame in HISTORY:
            await ws.send(ops.dumps(frame))
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
    # TODO: pause / resume


async def serve():
    print(f"[ws] listening on ws://{CFG.ws_host}:{CFG.ws_port}")
    async with websockets.serve(handler, CFG.ws_host, CFG.ws_port):
        await asyncio.Future()
