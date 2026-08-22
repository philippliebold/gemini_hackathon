"""Gemini function declarations + their local implementations.

The model does not describe visuals — it CALLS these. Each tool returns a list of
wire frames (from ops.py) that get broadcast immediately.

Every visual carries a topic `key`. Reusing a key GROWS the block that already owns
it; setting `revises` places a contradiction beside the original. All of that state
lives in canvas.py — nothing here hand-builds a frame or tracks a block id.

Adding a tool: declare it in DECLARATIONS, implement `tool_<name>`, done.
The dispatcher finds it by name.
"""
from typing import Any

import asyncio
import base64
import time

import canvas
import ops
from config import CFG

# Set once by main.py so slow tools (image generation) can push a second
# frame when their result lands. Without it they simply stay a placeholder.
BROADCAST = None

# True only for the duration of a REFINING dispatch: the second, better call for an
# utterance whose speculative visual is already on screen. Refinement is the whole
# point of drawing early, so it is allowed past the per-key cooldown and the form
# lock — both of which exist to stop the model thrashing, not to stop us improving
# a block we deliberately drew ahead of time. Set and cleared inside dispatch(),
# which never awaits, so it cannot interleave with another call.
_REFINE = False


async def _generate_image(block_id: str, prompt: str) -> None:
    """Fill an image placeholder. Takes ~12s, so it must never block the
    live audio loop -- it runs as a detached task and updates in place."""
    if not BROADCAST or not CFG.api_key:
        return
    try:
        from google import genai
        from google.genai import types as gt
        client = genai.Client(api_key=CFG.api_key)
        r = await asyncio.to_thread(
            client.models.generate_content,
            model=CFG.image_model,
            contents=(f"{prompt}. Cinematic, photographic, dramatic lighting, "
                      "wide establishing shot, no text, no watermark."),
            config=gt.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        for part in r.candidates[0].content.parts:
            blob = getattr(part, "inline_data", None)
            if blob and blob.data:
                uri = (f"data:{blob.mime_type};base64,"
                       + base64.b64encode(blob.data).decode())
                BROADCAST(ops.block_update(block_id, {"src": uri}))
                return
    except Exception as exc:                      # never kill the session
        print(f"[image] {type(exc).__name__}: {exc}")

def _decode_polyline(enc: str) -> list[list[float]]:
    """Google's encoded polyline -> [[lat, lng], ...]. ~15 lines beats a dependency."""
    pts, lat, lng, i = [], 0, 0, 0
    while i < len(enc):
        for is_lat in (True, False):
            shift, result = 0, 0
            while i < len(enc):
                b = ord(enc[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else (result >> 1)
            if is_lat:
                lat += d
            else:
                lng += d
        pts.append([lat / 1e5, lng / 1e5])
    return pts


_TRAVEL_MODE = {"walking": "WALK", "driving": "DRIVE",
                "transit": "TRANSIT", "bicycling": "BICYCLE"}


def _human(seconds: int, metres: int) -> tuple[str, str]:
    mins = max(1, round(seconds / 60))
    dur = f"{mins} min" if mins < 60 else f"{mins // 60} h {mins % 60} min"
    dist = f"{metres} m" if metres < 1000 else f"{metres / 1000:.1f} km"
    return dur, dist


def _localise(place: str | None) -> str:
    """Bare local shorthand does not geocode. "one-north" alone matched a point
    6,135 km away; biasing by region alone then found no route at all. Appending
    the locality is what actually resolves it."""
    place = (place or "").strip()
    if not place or not CFG.maps_near:
        return place
    if CFG.maps_near.lower() in place.lower() or "," in place:
        return place
    return f"{place}, {CFG.maps_near}"


async def _fill_route(block_id: str, origin: str, destination: str,
                      mode: str) -> None:
    """Fetch the real duration/distance/polyline and update the block in place.

    Uses the Routes API, not the legacy Directions API — the latter answers
    REQUEST_DENIED ("You're calling a legacy API") on any recently created project.

    Two-phase like image generation: the embed iframe is already on screen showing
    the real route, so this only fills the overlay chip and the SVG fallback. A
    failure leaves the map standing rather than blanking it.
    """
    if not BROADCAST or not CFG.maps_key:
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=4.0) as http:
            r = await http.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                headers={"X-Goog-Api-Key": CFG.maps_key,
                         "X-Goog-FieldMask": "routes.duration,"
                                             "routes.distanceMeters,"
                                             "routes.polyline.encodedPolyline"},
                json={"origin": {"address": _localise(origin)},
                      "destination": {"address": _localise(destination)},
                      "travelMode": _TRAVEL_MODE.get(mode, "WALK"),
                      # without this, "one-north" geocodes to another continent
                      "regionCode": CFG.maps_region})
        body = r.json()
        routes = body.get("routes") or []
        if not routes:
            err = (body.get("error") or {}).get("message", body)
            print(f"[route] no route: {str(err)[:130]}")
            return
        route = routes[0]
        secs = int(str(route.get("duration", "0s")).rstrip("s") or 0)
        dur, dist = _human(secs, int(route.get("distanceMeters", 0)))
        patch = {"duration": dur, "distance": dist}
        enc = (route.get("polyline") or {}).get("encodedPolyline")
        if enc:
            patch["polyline"] = _decode_polyline(enc)
        BROADCAST(ops.block_update(block_id, patch))
        print(f"[route] {origin} -> {destination}: {dur}, {dist}")
    except Exception as exc:                      # never kill the session
        print(f"[route] {type(exc).__name__}: {exc}")


# --- the two params that make the board evolve ------------------------------
# Spliced into every show_* declaration. This is the whole decision surface:
#   same key, no revises  -> the existing block grows
#   new key + revises     -> a contradiction lands beside the original
#   new key               -> a new topic
KEY_PARAM = {
    "type": "string",
    "description": (
        "Short stable slug for the TOPIC this visual is about, lowercase, e.g. "
        "'pricing', 'latency', 'pipeline'. If you are refining something already on "
        "the canvas, REUSE ITS EXACT KEY — the existing block grows instead of "
        "duplicating. Check the CANVAS list in your last tool result first."
    ),
}
REVISES_PARAM = {
    "type": "string",
    "description": (
        "Only when this CONTRADICTS or corrects something already on the canvas: set "
        "to that block's key. The new visual is placed beside the original and both "
        "stay visible. Leave empty when merely adding detail — reuse the same key "
        "for that instead."
    ),
}


def _decl(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {**props, "key": KEY_PARAM, "revises": REVISES_PARAM},
            "required": [*required, "key"],
        },
    }


# --- declarations the Live session advertises -------------------------------
# Keep descriptions blunt and behavioural. This is the taste knob for the whole
# product: it decides WHEN something lands on screen.

DECLARATIONS: list[dict[str, Any]] = [
    _decl(
        "show_photo",
        "Show a REAL photograph of a real, nameable thing -- a specific car, building, "
        "animal, landmark, product. Use this INSTEAD of show_image whenever the thing "
        "actually exists and the speaker named it. 'a Porsche 911 Turbo S' -> show_photo. "
        "'a cosy imaginary cabin' -> show_image.",
        {"query": {"type": "string",
                   "description": "Search terms, e.g. 'Porsche 911 Turbo S'"}},
        ["query"],
    ),
    _decl(
        "show_summary",
        "The closing recap: every important point of the session on ONE screen. Call "
        "this ONLY when the speaker is explicitly closing the whole talk -- 'to sum "
        "up', 'in summary', 'in conclusion', 'to wrap up', 'thanks for listening'. "
        "NOT on 'so that's it' or 'that's that' or 'anyway' -- those are ordinary "
        "mid-talk filler and firing on them puts the ending on screen while the talk "
        "is still going. Draw 4-9 items from what was ACTUALLY said "
        "earlier in the talk; never invent a point that was not made.",
        {
            "title": {"type": "string", "description": "e.g. 'In summary'"},
            "items": {
                "type": "array",
                "description": "4-9 recap tiles, most important first",
                "items": {
                    "type": "object",
                    "properties": {
                        "emoji": {"type": "string", "description": "one emoji"},
                        "value": {"type": "string", "description": "optional figure"},
                        "label": {"type": "string", "description": "under 5 words"},
                    },
                },
            },
        },
        ["items"],
    ),
    _decl(
        "show_hero",
        "The DEFAULT way to put an idea on screen. One emoji plus 2-5 words. Use this "
        "instead of show_concept whenever the point fits in a few words -- it reads from "
        "the back of a room, a paragraph does not. Pick an emoji that carries the meaning "
        "on its own. Prefer this over every other text tool.",
        {
            "emoji": {"type": "string",
                      "description": "ONE emoji that carries the idea, e.g. 'GG' bridge, rocket, warning"},
            "title": {"type": "string", "description": "2-5 words. Never a sentence."},
            "sub": {"type": "string", "description": "Optional: a few words of detail, e.g. '1937 - 2,737 m'"},
            "big": {"type": "boolean", "description": "true for the single most important idea on screen"},
        },
        ["title"],
    ),
    _decl(
        "show_math",
        "Render a formula or equation with real typesetting. Use when the speaker states "
        "a relationship, a formula, a rate, or any maths worth seeing set properly.",
        {
            "tex": {"type": "string",
                    "description": r"LaTeX WITHOUT delimiters, e.g. 'E = mc^2' or '\frac{wx^2}{2H}'"},
            "title": {"type": "string", "description": "Optional 1-3 word label"},
            "note": {"type": "string", "description": "Optional: what it means, under 8 words"},
        },
        ["tex"],
    ),
    _decl(
        "show_concept",
        "Put a concept card on the canvas. Use when the speaker makes a point worth "
        "anchoring: a claim, a definition, a list of reasons. Title must be 3-8 words. "
        "Max 4 bullets, each under 8 words. Reuse the key to append a bullet as the "
        "speaker develops the same point.",
        {
            "title": {"type": "string"},
            "bullets": {"type": "array", "items": {"type": "string"}},
            "accent": {"type": "string",
                       "enum": ["violet", "amber", "emerald", "rose", "slate"]},
        },
        ["title"],
    ),
    _decl(
        "show_stat",
        "Put one big number on screen. Use ONLY when the speaker says an actual "
        "figure, percentage, duration, or price. Never invent a number. If they "
        "later restate the same figure more precisely, reuse the key; if they "
        "CORRECT it, set revises so both numbers stay visible.",
        {
            "value": {"type": "string", "description": "e.g. '1.4s', '60%', '$0.75'"},
            "label": {"type": "string", "description": "what it measures, under 6 words"},
            "delta": {"type": "string"},
        },
        ["value", "label"],
    ),
    _decl(
        "show_diagram",
        "Draw a Mermaid diagram. Use when the speaker describes a system, a flow, a "
        "sequence of steps, or how parts connect. Prefer 'graph LR' with 3-7 nodes. "
        "Keep node labels under 3 words. Reuse the key with the full updated source "
        "to extend a diagram already on screen.",
        {
            "title": {"type": "string"},
            "mermaid": {"type": "string",
                        "description": "valid Mermaid source, e.g. 'graph LR\\n A[mic] --> B[model]'"},
        },
        ["mermaid"],
    ),
    _decl(
        "show_chart",
        "Chart numeric comparisons the speaker states. Use when 2+ related numbers "
        "are mentioned together. Reusing the key MERGES new series in by label — so "
        "send only the new points as the speaker says them and the chart fills in.",
        {
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
        ["kind", "series"],
    ),
    _decl(
        "show_table",
        "Compare things across dimensions. Max 4 columns, max 5 rows. Reusing the key "
        "merges rows by their first cell, so a comparison can be filled in one row at "
        "a time as each option is discussed.",
        {
            "title": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array",
                     "items": {"type": "array", "items": {"type": "string"}}},
        },
        ["columns", "rows"],
    ),
    _decl(
        "show_route",
        "Show a map with a route between two places. Use when the speaker names an "
        "origin and a destination, or asks how to get somewhere.",
        {
            "origin": {"type": "string"},
            "destination": {"type": "string"},
            "mode": {"type": "string",
                     "enum": ["walking", "driving", "transit", "bicycling"]},
        },
        ["origin", "destination"],
    ),
    _decl(
        "show_image",
        "Generate and show an illustrative image or mockup. Use when the speaker says "
        "they imagine, picture, or envision something visual. Slow (~3s) — use sparingly.",
        {
            "prompt": {"type": "string"},
            "caption": {"type": "string"},
        },
        ["prompt"],
    ),
    {
        "name": "connect_blocks",
        "description": (
            "Draw an arrow between two blocks already on the canvas, when the speaker "
            "relates two ideas. Use block ids from the CANVAS list in your last tool "
            "result — never guess an id."
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


# --- the single write path --------------------------------------------------
def _emit(type_: str, key: str, data: dict, revises: str | None = None,
          needs: tuple[str, ...] = (), **kw) -> tuple[list[dict], dict]:
    """Upsert or branch, then append a throttled camera hint.

    `needs` are the fields a BRAND NEW block cannot render without. An update may
    legitimately carry only the fields that changed, so they are enforced here on
    the add path rather than in the Python signatures.
    """
    data = {k: v for k, v in data.items() if v is not None}
    existing = canvas.BLOCKS.get(canvas.BY_KEY.get(key, ""))
    # A brand new block is created by: an unseen key, an explicit `revises`, or a
    # known key arriving with a different block type (which branches). All three
    # need the full payload — only a same-type update may be partial.
    # A topic that is already on screen in one shape keeps it. Swapping the shape
    # replaces the block, so the audience sees the card destroyed and rebuilt as
    # something else — and the model does this constantly when it cannot settle on
    # a form. Refusing is better than thrashing, and saying why lets it recover.
    if (existing is not None and existing.type != type_ and not revises
            and not _REFINE
            and time.time() - existing.touched < canvas.FORM_LOCK_S):
        return [], {"skipped": "form locked",
                    "reason": f"'{key}' is already a {existing.type} on screen. "
                              f"Grow it with the same tool you used before, or pick "
                              f"a NEW key if this is a new subject.",
                    "canvas": canvas.manifest()}
    if revises or existing is None or existing.type != type_:
        missing = [f for f in needs if not data.get(f)]
        if missing:
            return [], {"error": f"a new {type_} block needs {missing}; "
                                 f"resend with those fields", "key": key}
    if revises:
        frames, bid = canvas.branch(type_, key, data, revises=revises, **kw)
        action = "branch"
    else:
        frames, bid, action = canvas.upsert(type_, key, data, **kw)
    frames = frames + canvas.focus_frames(action, bid)
    return frames, {"block_id": bid, "key": key, "action": action}


# --- implementations --------------------------------------------------------
# Each returns (frames, result_for_model).

def tool_show_concept(key: str, title: str | None = None,
                      bullets: list[str] | None = None, accent: str | None = None,
                      revises: str | None = None, **_):
    return _emit("text", key, {"title": title, "bullets": bullets or [],
                               "accent": accent}, revises, needs=("title",))


def tool_show_stat(key: str, value: str | None = None, label: str | None = None,
                   delta: str | None = None, revises: str | None = None, **_):
    return _emit("stat", key, {"value": value, "label": label, "delta": delta},
                 revises, needs=("value",), w=320, h=200)


def tool_show_diagram(key: str, mermaid: str | None = None,
                      title: str | None = None, revises: str | None = None, **_):
    return _emit("diagram", key, {"title": title, "mermaid": mermaid}, revises,
                 needs=("mermaid",), w=560, h=320, enter="draw")


def tool_show_chart(key: str, kind: str | None = None,
                    series: list[dict] | None = None, title: str | None = None,
                    unit: str | None = None, revises: str | None = None, **_):
    return _emit("chart", key, {"title": title, "kind": kind, "unit": unit,
                                "series": series}, revises,
                 needs=("kind", "series"), w=460, h=300, enter="fade")


def tool_show_table(key: str, columns: list[str] | None = None,
                    rows: list[list[str]] | None = None, title: str | None = None,
                    revises: str | None = None, **_):
    return _emit("table", key, {"title": title, "columns": columns, "rows": rows},
                 revises, needs=("columns", "rows"), w=460, h=260, enter="fade")


def tool_show_route(key: str, origin: str | None = None,
                    destination: str | None = None, mode: str = "walking",
                    revises: str | None = None, **_):
    from urllib.parse import quote_plus
    data = {"from": origin, "to": destination, "mode": mode}
    if CFG.maps_key:
        # The iframe shows the real route immediately; duration/distance/polyline
        # arrive a moment later via _fill_route so nothing waits on the network.
        data["embed_url"] = (
            "https://www.google.com/maps/embed/v1/directions"
            f"?key={CFG.maps_key}&origin={quote_plus(_localise(origin))}"
            f"&destination={quote_plus(_localise(destination))}&mode={mode}"
            f"&region={CFG.maps_region.lower()}"
        )
        # The iframe draws the real route straight away. These are placeholders for
        # the overlay chip until _fill_route lands, and they stay this way if the
        # Routes API is not enabled on the project — a real map with no numbers,
        # rather than invented ones.
        data |= {"duration": "…", "distance": ""}
    else:
        # No key: say so rather than showing an invented 8-minute walk. PLAN.md
        # requires nothing be precomputed, and a fake route is exactly that.
        data |= {"duration": "no maps key", "distance": "",
                 "polyline": [[0, 0], [30, 60], [80, 70], [120, 140], [190, 160]]}
    frames, result = _emit("map", key, data, revises,
                           needs=("from", "to"), w=420, h=320)
    if CFG.maps_key and result.get("block_id"):
        try:
            asyncio.get_running_loop().create_task(
                _fill_route(result["block_id"], origin, destination, mode))
        except RuntimeError:
            pass                                  # no loop (tests): iframe only
    return frames, result | {"duration": data.get("duration")}


def tool_show_image(key: str, prompt: str | None = None,
                    caption: str | None = None, revises: str | None = None, **_):
    # Two-phase: placeholder now, real pixels when generation lands. The
    # placeholder is what keeps perceived latency under 2s.
    frames, result = _emit("image", key, {"src": None, "caption": caption or prompt,
                                          "alt": prompt}, revises,
                           needs=("alt",), w=420, h=320, enter="fade")
    if prompt:
        try:
            asyncio.get_running_loop().create_task(
                _generate_image(result["block_id"], prompt))
        except RuntimeError:
            pass                                  # no loop (tests): stay a placeholder
    return frames, result | {"status": "generating"}


def tool_connect_blocks(from_id: str, to_id: str, label: str | None = None, **_):
    frames = canvas.link(from_id, to_id, label)
    if not frames:
        return [], {"error": "one of those block ids is not on the canvas; "
                             "use ids from the CANVAS list"}
    return frames, {"ok": True}


def tool_clear_canvas(**_):
    canvas.reset()
    return [ops.canvas_clear()], {"ok": True}


def tool_show_hero(key: str, title: str | None = None, emoji: str | None = None,
                   sub: str | None = None, big: bool = False,
                   revises: str | None = None, **_):
    return _emit("hero", key, {"title": title, "emoji": emoji, "sub": sub,
                               "big": bool(big)}, revises,
                 needs=("title",), w=380, h=200, enter="pop")


def tool_show_math(key: str, tex: str | None = None, title: str | None = None,
                   note: str | None = None, revises: str | None = None, **_):
    return _emit("math", key, {"tex": tex, "title": title, "note": note}, revises,
                 needs=("tex",), w=420, h=180, enter="fade")


async def _find_photo(block_id: str, query: str) -> None:
    """Real photo of a real thing, from Wikimedia Commons.

    Generation invents a plausible Porsche; search returns *the* Porsche. It is
    also the licensing-safe choice for a public repo -- Commons media is freely
    licensed, unlike scraped image results.
    """
    if not BROADCAST:
        return
    try:
        import re

        import httpx
        params = {
            "action": "query", "generator": "search",
            "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": 6,
            "prop": "imageinfo", "iiprop": "url|extmetadata",
            "iiurlwidth": 1600, "format": "json",
        }
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://commons.wikimedia.org/w/api.php",
                                 params=params,
                                 headers={"User-Agent": "CoPresenter/1.0 (hackathon)"})
            pages = ((r.json().get("query") or {}).get("pages") or {}).values()

        for pg in sorted(pages, key=lambda x: x.get("index", 99)):
            info = (pg.get("imageinfo") or [{}])[0]
            thumb = (info.get("thumburl") or "").split("?")[0]
            if not thumb.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png")):
                continue
            meta = info.get("extmetadata") or {}
            artist = re.sub(r"<[^>]+>", "",
                            (meta.get("Artist", {}).get("value") or "")).strip()
            lic = meta.get("LicenseShortName", {}).get("value") or ""
            credit = " · ".join(x for x in (artist[:60], lic, "Wikimedia Commons") if x)
            BROADCAST(ops.block_update(block_id, {"src": thumb, "caption": credit}))
            return

        BROADCAST(ops.block_update(block_id, {"caption": f"no photo found: {query}"}))
    except Exception as exc:                       # never kill the session
        print(f"[photo] {type(exc).__name__}: {exc}")


def tool_show_photo(key: str, query: str | None = None, caption: str | None = None,
                    revises: str | None = None, **_):
    frames, result = _emit("image", key,
                           {"src": None, "caption": caption or "searching…",
                            "alt": query}, revises,
                           needs=("alt",), w=520, h=380, enter="fade")
    if query:
        try:
            asyncio.get_running_loop().create_task(
                _find_photo(result["block_id"], query))
        except RuntimeError:
            pass
    return frames, result | {"status": "searching"}


def tool_show_summary(key: str = "summary", title: str | None = None,
                      items: list | None = None, revises: str | None = None, **_):
    clean = [{"emoji": it.get("emoji"), "value": it.get("value"),
              "label": it.get("label")}
             for it in (items or [])[:12]
             if isinstance(it, dict) and (it.get("label") or it.get("value"))]
    return _emit("summary", key, {"title": title or "In summary", "items": clean},
                 revises, needs=("items",), w=1200, h=620, enter="fade")


def dispatch(name: str, args: dict, refine: bool = False) -> tuple[list[dict], dict]:
    """Run a tool call. Never raises — a bad call must not kill the session.

    Every result carries the canvas manifest back to the model. That round trip is
    what stops it duplicating a topic it already drew, and it costs no extra API call.

    `refine=True` is the second call for one utterance, replacing a visual we put up
    speculatively while the speaker was still talking. It bypasses the cooldown and
    the form lock, because those guard against thrash and this is the opposite:
    the promised improvement on a block we drew early on purpose.
    """
    global _REFINE
    fn = globals().get(f"tool_{name}")
    if fn is None:
        return [], {"error": f"unknown tool {name}", "canvas": canvas.manifest()}

    args = dict(args or {})
    if "key" in args:
        raw_key = args.get("key")
        args["key"] = canvas.normalize_key(raw_key)
        rev = args.get("revises")
        if rev:
            # The model is asked for a key but passes a block id (`b_2`) often
            # enough that it has to be handled.
            args["revises"] = canvas.key_of_block(rev) or canvas.normalize_key(rev)
            if args["revises"] == args["key"]:
                # Fuzzy matching collapsed the new key onto the very thing it is
                # contradicting ('pricing-revised' -> 'pricing'). Keep the branch and
                # give it a distinct key rather than silently dropping the tension.
                args["key"] = f"{args['key']}-alt"
        if (not refine and not args.get("revises")
                and not canvas.cooldown_ok(args["key"])):
            return [], {"skipped": "cooldown",
                        "reason": f"'{args['key']}' was just drawn; say something new "
                                  f"or wait before revisiting it",
                        "canvas": canvas.manifest()}
    _REFINE = refine
    try:
        frames, result = fn(**args)
        return frames, result | {"canvas": canvas.manifest()}
    except Exception as e:  # noqa: BLE001 - on stage, degrade don't die
        return [], {"error": f"{type(e).__name__}: {e}", "canvas": canvas.manifest()}
    finally:
        _REFINE = False
