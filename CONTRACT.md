# The Contract (v1) — FROZEN at kickoff

Backend and frontend never read each other's code. They only agree on this.

**Rule: if you want to change this file, you must say so out loud to the whole team first.**
A silent schema change is the one thing that can kill us today.

---

## Transport

One WebSocket, backend is the server, frontend is the client.

```
ws://127.0.0.1:8765
```

Backend broadcasts to every connected client. Frontend sends nothing except an
optional control message (see "Client → Server" at the bottom). Reconnect on
drop with a 1s retry — the display must never need a manual refresh mid-demo.

Every frame is a single JSON object, one op per frame.

---

## Envelope

```json
{
  "v": 1,
  "seq": 42,
  "ts": 1755861234.123,
  "op": "block.add",
  "payload": { }
}
```

| Field | Type | Meaning |
|---|---|---|
| `v` | int | Schema version. Always `1` today. |
| `seq` | int | Monotonic, starts at 1. Frontend drops any frame with `seq` <= last seen. |
| `ts` | float | Unix seconds, backend clock. Used for latency measurement only. |
| `op` | string | One of the ops below. |
| `payload` | object | Shape depends on `op`. |

---

## Ops

### `block.add`
```json
{ "id": "b_7", "type": "stat", "x": 400, "y": 120, "w": 320, "h": 200,
  "data": { }, "enter": "pop" }
```
- `id` — backend-generated, unique for the session, `b_<n>`.
- `x`,`y` — top-left in **canvas units** (not screen px). Origin `0,0` is canvas centre.
- `w`,`h` — hint. Frontend may grow `h` to fit content; must respect `w`.
- `enter` — `"pop" | "fade" | "draw"`. Frontend picks the animation. Optional, default `"pop"`.

### `block.update`
```json
{ "id": "b_7", "data": { }, "x": 400, "y": 120 }
```
Partial. Only the keys present change. `data` is merged shallowly, not replaced.

### `block.remove`
```json
{ "id": "b_7" }
```

### `link.add`
```json
{ "id": "l_2", "from": "b_3", "to": "b_7", "label": "feeds", "style": "arrow" }
```
`style` — `"arrow" | "line" | "dashed"`. Optional, default `"arrow"`.

### `link.remove`
```json
{ "id": "l_2" }
```

### `canvas.focus`
```json
{ "ids": ["b_3", "b_7"], "padding": 80 }
```
Pan/zoom the camera so those blocks fill the viewport. Frontend animates over ~600ms.
Empty `ids` means "fit everything".

### `canvas.clear`
```json
{ }
```
Remove everything. Used for the "clear that" presenter command and at demo start.

### `status`
```json
{ "state": "listening", "transcript": "so the pipeline starts at the mic" }
```
`state` — `"idle" | "listening" | "thinking" | "drawing" | "error"`.
Frontend renders this as a small unobtrusive indicator, NOT as a block.
`transcript` is optional and partial — nice for debugging, hide it in the real demo.

---

## Block types and their `data`

Frontend must render all of these. Backend must not invent a type outside this list.

### `text` — concept card
```json
{ "title": "Three-stage pipeline", "body": "optional paragraph",
  "bullets": ["mic in", "model decides", "canvas draws"], "accent": "violet" }
```
`accent` — `violet | amber | emerald | rose | slate`. Optional.

### `stat` — big number callout
```json
{ "value": "1.4s", "label": "sentence to pixels", "delta": "-60%", "unit": null }
```

### `diagram` — Mermaid
```json
{ "title": "Architecture", "mermaid": "graph LR\n  A[mic] --> B[Gemini]\n  B --> C[canvas]" }
```
Frontend renders with mermaid.js. **If mermaid throws, render the raw source in a
mono block instead of crashing.** Backend should prefer `graph LR` / `flowchart TD`.

### `chart`
```json
{ "title": "Latency by stage", "kind": "bar",
  "series": [{ "label": "capture", "value": 40 }, { "label": "model", "value": 900 }],
  "unit": "ms" }
```
`kind` — `bar | line | pie`.

### `table`
```json
{ "title": "Comparison", "columns": ["", "Deck", "Us"],
  "rows": [["prep time", "2h", "0"], ["adapts live", "no", "yes"]] }
```

### `image`
```json
{ "src": "data:image/png;base64,...", "caption": "imagined UI", "alt": "..." }
```
`src` is either a `data:` URI or an `http(s)` URL. Frontend shows a shimmer
placeholder until it loads.

### `map`
```json
{ "from": "NG Greenhouse", "to": "Luckin Coffee", "mode": "walking",
  "duration": "8 min", "distance": "650 m",
  "polyline": [[lat, lng], [lat, lng]],
  "embed_url": "https://www.google.com/maps/embed/v1/directions?..." }
```
Frontend prefers `embed_url` in an iframe if present; otherwise draws `polyline`
on a simple SVG. `duration`/`distance` always render as an overlay chip.

### `code`
```json
{ "lang": "python", "source": "async with client.aio.live.connect(...)", "title": null }
```

---

## Client → Server (the only upstream message)

```json
{ "v": 1, "cmd": "presenter", "action": "clear" }
```
`action` — `"clear" | "undo" | "pause" | "resume"`. Bound to keyboard shortcuts on
the display. Backend may ignore these on day one; frontend should still send them.

---

## Coordinate system

- Canvas units == CSS px at zoom 1.0.
- Origin `(0,0)` is the centre of the initial viewport.
- Backend lays out on a loose 3-column grid: columns at `x = -720, -40, 640`,
  rows step `y += 360`. Backend owns position; frontend owns camera.
- Backend may re-emit `block.update` with new `x,y` to rearrange. Frontend must
  animate the move, not teleport.

## Latency budget (the product IS the speed)

| Stage | Target |
|---|---|
| mic chunk → backend | 20 ms |
| Live model → first tool call | < 1200 ms |
| tool call → ws frame | 30 ms |
| ws frame → pixels | 150 ms |
| **sentence → something on screen** | **< 2 s** |

`ts` in the envelope exists so we can prove this on stage.
