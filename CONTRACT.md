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

## Added after the freeze (announced, and already on the wire)

Everything above is v1 as frozen at kickoff. These were added during the build,
they are live in `backend/ops.py` and rendered by `frontend/stage.js`, and they are
recorded here so the contract stops lagging the code. Same envelope, same rules.

### `hero` — an emoji and 2-5 words. The DEFAULT block type
```json
{ "emoji": "🚀", "title": "No slides needed", "sub": "1937 · 2,737 m", "big": true }
```
`big` marks the single most important idea on screen. This is the workhorse: it
reads from the back of a room where a paragraph does not.

### `math` — a typeset formula
```json
{ "tex": "y = \\frac{wx^2}{2H}", "title": "Cable curve", "note": "under 8 words" }
```
`tex` is LaTeX **without** delimiters. Frontend renders with KaTeX; on a parse
failure it shows the raw source rather than blanking.

### `summary` — the closing recap, every point on one screen
```json
{ "title": "In summary",
  "items": [ { "emoji": "⚡", "value": "1.4s", "label": "sentence to pixels" } ] }
```
4-12 tiles, `value` and `emoji` both optional. Tiles stagger in so the room
watches it assemble.

### `term` — a defined term, in brackets
```json
{ "term": "co-presenter", "sub": "it draws while you talk" }
```
Renderer exists; no tool emits it yet.

---

## Chrome ops (added after the freeze)

Both are **chrome, never a block**. The frontend renders them in the dock and
never on the canvas. A client that ignores them loses nothing but convenience.

### `mics.state`
```json
{ "join_url": "https://10.0.0.5:8766/", "qr_svg": "<svg .../>", "max_mics": 4,
  "roster": [{ "id": "m_1", "label": "Yufei", "holding": true, "talking": true,
               "rms": 0.03, "gain": 1.0, "muted": false }],
  "devices": [{ "index": 1, "name": "MacBook Pro Microphone", "default": true }],
  "mac": { "active": true, "device": 1 },
  "gate": 0.014, "ear": "local", "listening": true,
  "speaker": { "active": false, "device": null, "buffered_ms": 0, "devices": [] },
  "brain": { "enabled": true, "model": "gemini-3.7-flash" } }
```
Lets the screen show the phone-join code, who holds the floor, and whether the
mic is live — so the room can set itself up without anyone reading a terminal.
`ear` is `"local"` or `"live"`.

### `notes.state`
```json
{ "thread": "what is being discussed right now",
  "topics": [{ "key": "pricing", "gist": "under 12 words" }],
  "numbers": [{ "value": "41k", "of": "monthly burn" }],
  "decisions": ["ship the mic-first version"],
  "questions": ["do we need the camera at all"] }
```
The running record — the thing that remains after the talking stops.

---

## Diagnostic ops (added after the freeze)

Neither is ever a block, and the stage ignores both. They exist because every stage
of the pipeline can decide **not** to draw, and until these were on the wire the
only place that said so was a terminal — which made a dead Whisper model, an
exhausted quota, a per-key cooldown and "the model chose silence" all look identical
from the screen. `frontend/console.html` is the client for them.

### `trace`
```json
{ "stage": "brain", "verdict": "skip",
  "reason": "not worth asking (MIN_CHARS 18, BRAIN_MIN_CONTENT 2)",
  "text": "so anyway", "detail": "no API call made", "ms": 12.4, "n": 1 }
```
One event per decision, emitted through `backend/vitals.py`.

| Field | |
|---|---|
| `stage` | `mic` \| `ear` \| `brain` \| `tool` \| `canvas` \| `asset` |
| `verdict` | `ok` \| `hold` \| `skip` \| `drop` \| `block` \| `error` |
| `reason` | Names the constant that made the call **and its value**, because the next question is always which knob to turn |
| `text` | The transcript line this decision was about, when there is one |
| `detail` | A sentence of context, in plain language |
| `ms` | How long the thing took, when it took time |
| `n` | Present and >1 when identical events were coalesced — high-frequency callers are throttled rather than allowed to flood the socket |

Traces are kept out of the replay `HISTORY` (a talk produces far more of them than
blocks, and replaying them to the stage would evict the canvas). The backend holds
the last 400 and sends them to a client that introduces itself as the console.

### `health.state`
```json
{ "ear": { "state": "ready", "model": "mlx-community/whisper-small-mlx",
           "error": null, "kind": "local" },
  "brain": { "model": "gemini-3.7-flash", "fallback": false, "error": null,
             "enabled": true, "inflight": 0, "calls": 12 },
  "listening": true,
  "audio": { "queued": 0, "capacity": 200, "dropped": 0 },
  "loop_lag_ms": 1.2, "blocks": 1,
  "stage": { "max_live": 1, "lifetime_s": 26.0 },
  "clients": 2, "counts": { "ear.ok": 8, "brain.skip": 3, "tool.ok": 5 } }
```
Pushed once a second while any client is attached. `ear.state` is `warming` /
`ready` / `dead` / `off`, or `live-api` under `--live`. `counts` is a running tally
keyed `"<stage>.<verdict>"` plus the semantic counters (`heard`, `drawn`, `blocked`,
`skipped`, `dropped`, `silent`, `audio.dropped`), and it resets on `context_reset`.

---

## Client → Server

```json
{ "v": 1, "cmd": "presenter", "action": "clear" }
```

The frozen set was `"clear" | "undo" | "pause" | "resume"`. `clear` and `undo` are
implemented; **`pause` and `resume` never were** — `listen_off` / `listen_on` below
do that job instead. The display grew real controls during the build, so the full
set the backend answers today (`server.on_presenter`) is:

| Action | Does |
|---|---|
| `clear` | Wipe the board. Resets `canvas.py` too, so the model is not told about blocks the room can no longer see |
| `undo` | Remove the most recently touched block |
| `listen_off` / `listen_on` | **The kill switch.** Halts transcription, the brain and the summariser, and cancels calls already in flight. Nothing bills while stopped |
| `context_reset` | `clear` plus the running record and the brain's recent lines — a genuinely fresh rehearsal |
| `mic_device:<index>` / `mic_off` | Open or close this Mac's input |
| `mic_gate:<float>` | Move the speech gate while the talk runs |
| `mic_gain:<mic_id>:<0..3>` | How loud one phone is **in the room**. Never touches the transcription path |
| `speaker_on` / `speaker_off` / `speaker_device:<index>` | Play the phones through this Mac's output. Turning it on closes the Mac mic, because that pair is a feedback loop |
| `brain_on` / `brain_off` | Hand the drawing decision to `gemini-3.7-flash`. Refused while the ear is local, since nothing else could draw |
| `mics_refresh` | Ask for a fresh `mics.state` |
| `health_refresh` | Ask for a fresh `health.state` |

Unknown actions are ignored, so a frontend may send anything and an older backend
simply will not act on it.

### `hello` — a client introducing itself

```json
{ "v": 1, "cmd": "hello", "role": "stage", "max_live": 1, "lifetime_ms": 26000 }
```

Sent once on connect. Optional: an older client that never sends it gets the
defaults.

| Field | |
|---|---|
| `role` | `"stage"` (the display) or `"console"` (the operator page) |
| `max_live` | How many scenes the display can hold at once |
| `lifetime_ms` | How long a scene lives without being refreshed |

**The display owns those two numbers and the backend mirrors them.** `canvas.py`
hands the model a manifest of what is on screen, and that manifest is only worth
anything if it is true — these used to be constants copied by hand into both
codebases with a comment asking the next person to keep them in step, and they
drifted: the backend believed three scenes were up while the room saw one, so the
model spent its calls revising cards nobody could see.

A `console` role gets the recent `trace` backlog on connect; the stage does not,
because it has no use for it.

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
