# Co-Presenter

A live AI co-presenter that takes over the big screen and builds your
presentation *while* you talk. No slides made in advance.

See [IDEA.md](IDEA.md) for the pitch, [CONTRACT.md](CONTRACT.md) for the
backend↔frontend interface, and [PLAN.md](PLAN.md) for who does what today.

---

## Run it in 60 seconds (no API key needed)

Two terminals.

**Terminal 1 — fake backend, replays a scripted demo:**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
python backend/mock_server.py
```

**Terminal 2 — the display:**
```bash
cd frontend && python3 -m http.server 5173
```

Open <http://localhost:5173>. Blocks should start appearing.

That's the whole frontend development loop. You never need the Gemini side.

## Run it for real

```bash
cp .env.example .env        # put your GEMINI_API_KEY in it
source .venv/bin/activate
python backend/main.py --devices    # find your mic
python backend/main.py --device 1   # then talk
```

Frontend is the same: `cd frontend && python3 -m http.server 5173`.

## Layout

```
backend/     Python. Audio in, Gemini Live, tool calls out over WebSocket.
frontend/    Static HTML/JS/Tailwind. Infinite canvas that renders ops.
shared/      The contract: JSON schema + demo fixture. Both sides read this.
```

Backend and frontend never import from each other. The only coupling is
[CONTRACT.md](CONTRACT.md).

## Files, and who owns them

| File | Owner | What it is |
|---|---|---|
| `backend/gemini_live.py` | A | Live session, system prompt, the "when to draw" taste |
| `backend/audio.py` | A | Mic → 16 kHz PCM |
| `backend/tools.py` | B | Function declarations + implementations |
| `backend/server.py` | B | WebSocket fan-out |
| `frontend/app.js` | C | Camera, block lifecycle, links, WS client |
| `frontend/blocks.js` | D | One renderer per block type |
| `frontend/styles.css` | D | Look and feel |
| `CONTRACT.md`, `shared/` | everyone reads, nobody edits alone | |

Stay in your files and you will not hit a merge conflict.

## Model note

`gemini-3.7-flash` is the stable workhorse, but it is **not** a Live API model —
it can't take a streaming mic. Live audio goes through
`gemini-3.1-flash-live-preview` (see `GEMINI_LIVE_MODEL` in `.env`). Keep
3.7-flash available for async enrichment off the hot path if we need it.
