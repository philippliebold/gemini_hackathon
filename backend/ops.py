"""Builders for the wire protocol in CONTRACT.md.

Nothing else in the backend is allowed to hand-build a frame. If you need a new
op, add a builder here AND update CONTRACT.md AND tell the frontend owner.
"""
import itertools
import json
import time
from typing import Any, Iterable

V = 1

BLOCK_TYPES = {"text", "stat", "diagram", "chart", "table", "image", "map", "code"}
STATES = {"idle", "listening", "thinking", "drawing", "error"}

_seq = itertools.count(1)
_block_n = itertools.count(1)
_link_n = itertools.count(1)


def new_block_id() -> str:
    return f"b_{next(_block_n)}"


def new_link_id() -> str:
    return f"l_{next(_link_n)}"


def _frame(op: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"v": V, "seq": next(_seq), "ts": round(time.time(), 3),
            "op": op, "payload": payload}


# --- layout -----------------------------------------------------------------
# Backend owns position. Loose 3-column grid; see CONTRACT.md.
_COLS = (-720, -40, 640)
_ROW_STEP = 360
_slot = itertools.count(0)


def next_slot() -> tuple[int, int]:
    """Return the next free (x, y) on the grid."""
    i = next(_slot)
    return _COLS[i % 3], -180 + (i // 3) * _ROW_STEP


# --- builders ---------------------------------------------------------------
def block_add(type_: str, data: dict, *, x=None, y=None, w=440, h=280,
              enter="pop", block_id=None) -> dict:
    if type_ not in BLOCK_TYPES:
        raise ValueError(f"unknown block type {type_!r}; see CONTRACT.md")
    if x is None or y is None:
        x, y = next_slot()
    return _frame("block.add", {
        "id": block_id or new_block_id(), "type": type_,
        "x": x, "y": y, "w": w, "h": h, "enter": enter, "data": data,
    })


def block_update(block_id: str, data: dict | None = None, **pos) -> dict:
    payload: dict[str, Any] = {"id": block_id}
    if data is not None:
        payload["data"] = data
    payload.update({k: v for k, v in pos.items() if v is not None})
    return _frame("block.update", payload)


def block_remove(block_id: str) -> dict:
    return _frame("block.remove", {"id": block_id})


def link_add(from_id: str, to_id: str, label: str | None = None,
             style: str = "arrow") -> dict:
    return _frame("link.add", {"id": new_link_id(), "from": from_id,
                               "to": to_id, "label": label, "style": style})


def link_remove(link_id: str) -> dict:
    return _frame("link.remove", {"id": link_id})


def canvas_focus(ids: Iterable[str] = (), padding: int = 80) -> dict:
    return _frame("canvas.focus", {"ids": list(ids), "padding": padding})


def canvas_clear() -> dict:
    return _frame("canvas.clear", {})


def status(state: str, transcript: str | None = None) -> dict:
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}")
    p: dict[str, Any] = {"state": state}
    if transcript:
        p["transcript"] = transcript
    return _frame("status", p)


def dumps(frame: dict) -> str:
    return json.dumps(frame, separators=(",", ":"), ensure_ascii=False)
