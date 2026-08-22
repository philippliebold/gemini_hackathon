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

## ⚠️ The API key needs billing enabled

On the free tier `gemini-3.7-flash` allows **20 `generateContent` requests per day**:

```
Quota exceeded for metric: generate_content_free_tier_requests,
limit: 20, model: gemini-3.7-flash
```

That budget is spent in one rehearsal. **Enable billing on the key's project before
rehearsing** — this is the one blocker that cannot be coded around. Audio streaming
bills separately and far more cheaply (~$0.005/min), so the ear keeps working even
when the text quota is gone.

## Findings from the first live runs

Each of these cost a real run to learn. Keep them.

- **`response_modalities=["TEXT"]` is rejected by every Live model** — they are
  audio-out only, and the original scaffold would have failed at connect. We ask for
  `AUDIO`, tell the model never to speak, and never read the audio parts.
- **The model's VAD works on a live mic but never fires on a replayed PCM feed.** A
  `--pcm` run with automatic detection transcribes *nothing*. For rehearsals run
  `MANUAL_ACTIVITY=1`, which drives `activity_start`/`activity_end` from
  `mics.Floor` instead. Live mic: leave it off.
- **`thinking_budget=0` roughly halves latency** on 3.7-flash (3.0s → 1.7s median).
  Older flash models reject the parameter with a 400, so it is applied per-model.
- **Live-model tool calling is the primary path**, and it works from a real mic.
  `--brain` is the escape hatch: it moves the decision to `gemini-3.7-flash` reading
  the transcript, where function calling is a first-class non-preview feature. Use
  it if the live path proves flaky on stage.

## Rehearsing without a mic

```bash
say -r 155 -o /tmp/t.aiff "your script here"
afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/t.aiff /tmp/t.wav
# strip the WAV header to raw PCM, then:
MANUAL_ACTIVITY=1 python backend/main.py --pcm talk.pcm
```

Real pipeline, real model, canned audio — the loud-room fallback PLAN.md mandates,
and the only way to tune the prompt repeatably.

## How the board evolves (backend)

The screen is an evolving artifact, not a feed. Every visual carries a topic
`key`, and that key decides what happens next:

| The speaker… | The model… | On screen |
|---|---|---|
| adds detail to a live topic | reuses the same `key` | the block **grows** (`block.update`) |
| contradicts something up there | new key + `revises=<old key>` | new block **beside** it, arrow between — both survive |
| opens a new topic | new key | new block, new column |

`key` never touches the wire — CONTRACT.md is unchanged. It lives in
`backend/canvas.py`, which is the backend's single source of truth for what is on
screen. The model is told that truth via a compact CANVAS manifest riding back in
every tool response, which is why it revises instead of duplicating.

**Context is two layers.** The Live session is primary — it holds the verbatim
audio and the full tool history, with context compression and session resumption
on so it survives a long talk. `canvas.py` + `backend/memory.py` are the durable
net: they re-brief a reconnected session so it wakes up knowing what is already on
screen instead of drawing it all again. Raw transcript is never re-prompted on the
hot path.

### Test it without a mic or an API key

```bash
python backend/replay.py          # 34 checks: growth, branching, merging, reconnect
```

Scripted tool calls straight through the real dispatcher. A talk takes 90 seconds
to rehearse; this takes one. Run it after touching `canvas.py` or `tools.py`.

To watch a card actually grow in the browser (`demo.jsonl` has no `block.update`
frames, so it cannot show this):

```bash
python backend/mock_server.py --fixture evolve
```

Every frame in `shared/fixtures/evolve.jsonl` is real backend output —
regenerate with `python backend/replay.py --fixture shared/fixtures/evolve.jsonl`.

## Files, and who owns them

Two teams. **Backend pair** owns `backend/`, **frontend pair** owns `frontend/`.
Neither team touches the other's directory.

| File | Team | What it is |
|---|---|---|
| `backend/gemini_live.py` | backend | Live session, system prompt, the "when to draw" taste |
| `backend/audio.py` | backend | Mic → 16 kHz PCM |
| `backend/tools.py` | backend | Function declarations + implementations |
| `backend/canvas.py` | backend | What is on screen, keyed by topic. Upsert vs. branch, merge rules, layout |
| `backend/memory.py` | backend | Rolling topic memory on 3.7-flash. Reconnect brief, recall |
| `backend/replay.py` | backend | Offline harness. No mic, no key, no network |
| `backend/server.py` | backend | WebSocket fan-out |
| `frontend/app.js` | frontend | Camera, block lifecycle, links, WS client |
| `frontend/blocks.js` | frontend | One renderer per block type |
| `frontend/styles.css` | frontend | Look and feel |
| `CONTRACT.md`, `shared/` | everyone reads, nobody edits alone | |

Within a pair, split by file rather than working the same one at once — the
natural seam is session/audio vs. plumbing on the backend, and canvas shell vs.
renderers on the frontend. That's advice, not an assignment; sort it out
between the two of you.

## Model note

`gemini-3.7-flash` is the stable workhorse, but it is **not** a Live API model —
it can't take a streaming mic. Live audio goes through
`gemini-3.1-flash-live-preview` (see `GEMINI_LIVE_MODEL` in `.env`). Keep
3.7-flash available for async enrichment off the hot path if we need it.
