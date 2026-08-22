"""Builders for the wire protocol in CONTRACT.md.

Nothing else in the backend is allowed to hand-build a frame. If you need a new
op, add a builder here AND update CONTRACT.md AND tell the frontend owner.
"""
import itertools
import json
import time
from typing import Any, Iterable

V = 1

BLOCK_TYPES = {"text", "stat", "diagram", "chart", "table", "image", "map", "code",
               "hero", "math", "term", "summary"}
STATES = {"idle", "listening", "thinking", "drawing", "error"}

# Where in the pipeline a decision was taken, and what it decided. Every stage can
# choose not to draw, and until these existed the terminal was the only place that
# said so — which made a dead Whisper model, an exhausted quota and "the model chose
# silence" all look identical from the screen.
TRACE_STAGES = {"mic", "ear", "brain", "tool", "canvas", "asset"}
TRACE_VERDICTS = {"ok", "hold", "skip", "drop", "block", "error"}

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


def mics_state(payload: dict) -> dict:
    """CONTRACT ADDITION (announced): op "mics.state".

    Lets the screen show the phone-join code and who is live, so the room can set
    itself up without anyone reading a terminal. Purely informational — the frontend
    renders it as chrome, never as a block.

        {"join_url": "https://10.0.0.5:8766/", "qr_svg": "<svg .../>",
         "roster": [{"id","label","holding","talking","rms"}],
         "devices": [{"index","name","default"}],
         "mac": {"active": bool, "device": int|None}}
    """
    return _frame("mics.state", payload)


def notes_state(summary: dict) -> dict:
    """CONTRACT ADDITION (announced): op "notes.state".

    The rolling record of the talk — thread, topics, numbers, decisions, open
    questions. Chrome, never a scene. This is the thing that remains after the
    talking stops, and the reason the canvas is more than a slideshow.
    """
    return _frame("notes.state", summary)


def status(state: str, transcript: str | None = None) -> dict:
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}")
    p: dict[str, Any] = {"state": state}
    if transcript:
        p["transcript"] = transcript
    return _frame("status", p)


def trace(stage: str, verdict: str, reason: str, *, text: str | None = None,
          ms: float | None = None, detail: str | None = None, n: int = 1) -> dict:
    """CONTRACT ADDITION (announced): op "trace".

    One event per decision the pipeline takes. Diagnostic only — the stage ignores
    it; the operator console renders it. `reason` names the constant that made the
    call ("EAR_MIN 0.8s", "cooldown 6.0s") so the log says which knob to turn.

        {"stage": "ear", "verdict": "drop", "reason": "EAR_MIN 0.8s",
         "text": "...", "ms": 120.4, "detail": "0.6s of audio", "n": 1}
    """
    if stage not in TRACE_STAGES:
        raise ValueError(f"unknown trace stage {stage!r}")
    if verdict not in TRACE_VERDICTS:
        raise ValueError(f"unknown trace verdict {verdict!r}")
    p: dict[str, Any] = {"stage": stage, "verdict": verdict, "reason": reason}
    if text:
        p["text"] = text[:200]
    if ms is not None:
        p["ms"] = round(ms, 1)
    if detail:
        p["detail"] = detail
    if n != 1:
        p["n"] = n
    return _frame("trace", p)


def health(payload: dict) -> dict:
    """CONTRACT ADDITION (announced): op "health.state".

    The pipeline's vital signs, pushed on a timer. Diagnostic only — the stage
    ignores it, the console shows it. This is what tells you the ear is warming
    rather than broken, and which model is actually answering.

        {"ear": {"state","model"}, "brain": {"model","fallback","inflight"},
         "listening": bool, "audio": {"queued","capacity","dropped"},
         "loop_lag_ms": 4.0, "blocks": 1, "counts": {...}}
    """
    return _frame("health.state", payload)


def dumps(frame: dict) -> str:
    return json.dumps(frame, separators=(",", ":"), ensure_ascii=False)
