"""Gemini function declarations + their local implementations.

The model does not describe visuals — it CALLS these. Each tool returns a list of
wire frames (from ops.py) that get broadcast immediately.

Adding a tool: declare it in DECLARATIONS, implement `tool_<name>`, done.
The dispatcher finds it by name.
"""
from typing import Any

import ops
from config import CFG

# --- declarations the Live session advertises -------------------------------
# Keep descriptions blunt and behavioural. This is the taste knob for the whole
# product: it decides WHEN something lands on screen.

DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "show_concept",
        "description": (
            "Put a concept card on the canvas. Use when the speaker makes a point "
            "worth anchoring: a claim, a definition, a list of reasons. "
            "Title must be 3-8 words. Max 4 bullets, each under 8 words."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "bullets": {"type": "array", "items": {"type": "string"}},
                "accent": {"type": "string",
                           "enum": ["violet", "amber", "emerald", "rose", "slate"]},
            },
            "required": ["title"],
        },
    },
    {
        "name": "show_stat",
        "description": (
            "Put one big number on screen. Use ONLY when the speaker says an actual "
            "figure, percentage, duration, or price. Never invent a number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "description": "e.g. '1.4s', '60%', '$0.75'"},
                "label": {"type": "string", "description": "what it measures, under 6 words"},
                "delta": {"type": "string"},
            },
            "required": ["value", "label"],
        },
    },
    {
        "name": "show_diagram",
        "description": (
            "Draw a Mermaid diagram. Use when the speaker describes a system, a flow, "
            "a sequence of steps, or how parts connect. Prefer 'graph LR' with 3-7 "
            "nodes. Keep node labels under 3 words."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "mermaid": {"type": "string",
                            "description": "valid Mermaid source, e.g. 'graph LR\\n A[mic] --> B[model]'"},
            },
            "required": ["mermaid"],
        },
    },
    {
        "name": "show_chart",
        "description": (
            "Chart numeric comparisons the speaker states. Use when 2+ related "
            "numbers are mentioned together."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "kind": {"type": "string", "enum": ["bar", "line", "pie"]},
                "unit": {"type": "string"},
                "series": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"label": {"type": "string"},
                                       "value": {"type": "number"}},
                        "required": ["label", "value"],
                    },
                },
            },
            "required": ["kind", "series"],
        },
    },
    {
        "name": "show_table",
        "description": "Compare things across dimensions. Max 4 columns, max 5 rows.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array",
                         "items": {"type": "array", "items": {"type": "string"}}},
            },
            "required": ["columns", "rows"],
        },
    },
    {
        "name": "show_route",
        "description": (
            "Show a map with a route between two places. Use when the speaker names "
            "an origin and a destination, or asks how to get somewhere."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "mode": {"type": "string",
                         "enum": ["walking", "driving", "transit", "bicycling"]},
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "show_image",
        "description": (
            "Generate and show an illustrative image or mockup. Use when the speaker "
            "says they imagine, picture, or envision something visual. Slow (~3s) — "
            "use sparingly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "caption": {"type": "string"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "connect_blocks",
        "description": (
            "Draw an arrow between two blocks already on the canvas, when the speaker "
            "relates two ideas. Use the ids you were told about."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "string"},
                "to_id": {"type": "string"},
                "label": {"type": "string"},
            },
            "required": ["from_id", "to_id"],
        },
    },
    {
        "name": "clear_canvas",
        "description": "Wipe the canvas. Only when the speaker explicitly asks to start over.",
        "parameters": {"type": "object", "properties": {}},
    },
]


# --- implementations --------------------------------------------------------
# Each returns (frames, result_for_model).

def tool_show_concept(title: str, bullets: list[str] | None = None,
                      accent: str = "slate", **_):
    f = ops.block_add("text", {"title": title, "bullets": bullets or [],
                               "accent": accent})
    return [f], {"block_id": f["payload"]["id"]}


def tool_show_stat(value: str, label: str, delta: str | None = None, **_):
    f = ops.block_add("stat", {"value": value, "label": label, "delta": delta},
                      w=320, h=200)
    return [f], {"block_id": f["payload"]["id"]}


def tool_show_diagram(mermaid: str, title: str | None = None, **_):
    f = ops.block_add("diagram", {"title": title, "mermaid": mermaid},
                      w=560, h=320, enter="draw")
    return [f], {"block_id": f["payload"]["id"]}


def tool_show_chart(kind: str, series: list[dict], title: str | None = None,
                    unit: str | None = None, **_):
    f = ops.block_add("chart", {"title": title, "kind": kind, "unit": unit,
                                "series": series}, w=460, h=300, enter="fade")
    return [f], {"block_id": f["payload"]["id"]}


def tool_show_table(columns: list[str], rows: list[list[str]],
                    title: str | None = None, **_):
    f = ops.block_add("table", {"title": title, "columns": columns, "rows": rows},
                      w=460, h=260, enter="fade")
    return [f], {"block_id": f["payload"]["id"]}


def tool_show_route(origin: str, destination: str, mode: str = "walking", **_):
    data = {"from": origin, "to": destination, "mode": mode}
    if CFG.maps_key:
        # TODO: call Directions API, fill duration/distance/polyline.
        data["embed_url"] = (
            "https://www.google.com/maps/embed/v1/directions"
            f"?key={CFG.maps_key}&origin={origin}&destination={destination}&mode={mode}"
        )
    else:
        data |= {"duration": "8 min", "distance": "650 m",
                 "polyline": [[0, 0], [30, 60], [80, 70], [120, 140], [190, 160]]}
    f = ops.block_add("map", data, w=420, h=320)
    return [f], {"block_id": f["payload"]["id"], "duration": data.get("duration")}


def tool_show_image(prompt: str, caption: str | None = None, **_):
    # Two-phase: placeholder now, real pixels when generation lands. The
    # placeholder is what keeps perceived latency under 2s.
    f = ops.block_add("image", {"src": None, "caption": caption or prompt,
                                "alt": prompt}, w=420, h=320, enter="fade")
    # TODO: kick off async image gen, then broadcast
    #   ops.block_update(block_id, {"src": data_uri})
    return [f], {"block_id": f["payload"]["id"], "status": "generating"}


def tool_connect_blocks(from_id: str, to_id: str, label: str | None = None, **_):
    return [ops.link_add(from_id, to_id, label)], {"ok": True}


def tool_clear_canvas(**_):
    return [ops.canvas_clear()], {"ok": True}


def dispatch(name: str, args: dict) -> tuple[list[dict], dict]:
    """Run a tool call. Never raises — a bad call must not kill the session."""
    fn = globals().get(f"tool_{name}")
    if fn is None:
        return [], {"error": f"unknown tool {name}"}
    try:
        return fn(**(args or {}))
    except Exception as e:  # noqa: BLE001 - on stage, degrade don't die
        return [], {"error": f"{type(e).__name__}: {e}"}
