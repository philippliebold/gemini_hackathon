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
python -u backend/main.py           # then talk
```

Frontend is the same: `cd frontend && python3 -m http.server 5173`. Open
<http://localhost:5173> and make sure the toggle top-right says **Live**, not Demo —
in Demo mode the socket is closed and the dock does nothing.

`-u` matters if you redirect the log to a file: Python block-buffers stdout there,
so `[ear]` and `[brain]` lines arrive minutes late without it. Straight to a
terminal it is line-buffered and you can leave `-u` off.

The default ear is **local Whisper**, not the Live API — see "Two ears" below. So
`gemini-3.7-flash` is always the thing deciding what to draw, and the brain is on
from the first sentence.

## Controls

The dock lives at the bottom of the display. It auto-hides after 3s of stillness;
any mouse movement brings it back, and `H` strips it completely for the room.

| | Does |
|---|---|
| **⏸ Stop / ▶ Start** (`L`) | The kill switch, and the first button in the dock. Halts transcription, the brain and the summariser, and **cancels calls already in flight**. Nothing bills while stopped. Green when live, red with a `MIC STOPPED` banner when not. |
| **Mic** (`M`) | Pick this Mac's input, or turn it off. Turning it off closes the device; Stop only discards its audio. |
| **Speaker** (`S`) | Play the phones through this Mac's output. Right-click to choose which output. |
| **Phone** | QR + join URL for phone mics, and the **Sensitivity** slider — the live level meter sits behind the handle, so you can set the gate while talking. |
| **Notes** (`N`) | The running record: topics, numbers, decisions, open questions. |
| **Reset** | Clears the board *and* the record. Use it between rehearsals. |
| **Brain** | Hands the drawing decision to `gemini-3.7-flash`. Locked on with local ears, because nothing else can draw. |
| **Clear** (`C`) · **Fullscreen** (`F`) · **Hide** (`H`) · **Help** (`?`) | |

`Escape` closes any open panel and un-hides the chrome. `?` lists all of it on
screen, so nobody has to read this table on stage.

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
- **Live-model tool calling lost to local Whisper, so the default flipped.** On the
  same audio the Live path produced 0 transcripts against the local ear's 8: the
  model guesses when you stopped speaking and is often wrong. `main.py` now defaults
  to Whisper for the ear and `gemini-3.7-flash` for the decision. `--live` still
  exists for the "audio straight into Gemini" story, and is measurably worse.
- **Whisper invents when you hand it less than a second of audio.** Half the
  utterances in one run were 0.7–0.9 s, and they came back as `'é'` and as
  `'Thank you.'` eight times from room hum — Whisper's two standard failures on
  near-silence: a stock phrase from its subtitle training data, or one phrase
  looped. Fixed on three fronts, all in `ears_local.py`: a 1.2 s floor on an
  utterance (`EAR_MIN`), an 8-word floor before a full stop counts as a sentence
  (`EAR_SENTENCE_WORDS` — Whisper punctuates after two words constantly, which was
  splitting single thoughts into three), and a guard that drops both artefact
  shapes before they can reach the screen. **The model was never the problem; the
  slicing was.**
- **A stopped mic must cancel in-flight work, not just refuse new work.** A request
  issued a moment before you hit Stop still completes, still bills and still draws.
  `runtime.LISTENING` gates every caller and `Brain.abort()` cancels what is already
  running. Caveat, and it is a real one: `gemini_live.py` does **not** consult
  `runtime`, so under `--live` the Stop button does not stop the audio stream.

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
python backend/replay.py          # 44 checks: growth, branching, merging, reconnect
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
| `backend/ears_local.py` | backend | **The default ear.** Continuous local Whisper: where an utterance starts and stops, and what is speech vs. invention |
| `backend/brain.py` | backend | **The default decider.** `gemini-3.7-flash` reads the transcript and calls one tool, or none |
| `backend/taste.py` | backend | The shared prompt: when to draw, which shape, how the board evolves |
| `backend/gemini_live.py` | backend | `--live` only: Live session, streaming audio ear |
| `backend/audio.py` | backend | Mic → 16 kHz PCM |
| `backend/tools.py` | backend | Function declarations + implementations |
| `backend/canvas.py` | backend | What is on screen, keyed by topic. Upsert vs. branch, merge rules, layout |
| `backend/memory.py` | backend | Rolling topic memory on 3.7-flash. Reconnect brief, recall |
| `backend/replay.py` | backend | Offline harness. No mic, no key, no network |
| `backend/server.py` | backend | WebSocket fan-out |
| `frontend/stage.js` | frontend | Scene lifecycle, one renderer per block type, WS client |
| `frontend/dock.js` | frontend | Control dock: mics, speaker, notes, presenter commands |
| `frontend/stage.css`, `frontend/dock.css` | frontend | Look and feel |
| `CONTRACT.md`, `shared/` | everyone reads, nobody edits alone | |

Within a pair, split by file rather than working the same one at once — the
natural seam is session/audio vs. plumbing on the backend, and canvas shell vs.
renderers on the frontend. That's advice, not an assignment; sort it out
between the two of you.

## Two ears, and which model drives what

There are two ways audio becomes a visual, and the default is **not** the Live API.

```
DEFAULT   mic -> Floor -> ears_local.py (Whisper, on-device) -> brain.py (3.7-flash) -> canvas
--live    mic -> Floor -> gemini_live.py (Live API, streaming) ----------------------> canvas
```

| | Model | Notes |
|---|---|---|
| Ear (default) | local Whisper via `mlx-whisper` | Free, private, ~48x realtime. `WHISPER_MODEL`; `small` is the sweet spot, `large-v3-turbo` is more accurate and a ~1.5 GB download |
| Ear (`--live`) | `gemini-3.1-flash-live-preview` | The only model that takes a streaming mic. Measurably worse at knowing when you stopped talking |
| Brain | `gemini-3.7-flash`, `thinking_budget=0` | Decides what to draw. Falls back through `GEMINI_MODEL_FAST` / `_FALLBACK` / `_FALLBACK2` if the primary is slow or rate limited, and reclaims the primary on a timer |
| Memory | `gemini-3.7-flash` | The running record, off the hot path on a 30 s timer |
| Images | `gemini-2.5-flash-image` | Two-phase: placeholder now, pixels when they land |

`gemini-3.7-flash` is the workhorse but is **not** a Live API model — it cannot take
a streaming mic, which is exactly why the ear and the brain are different models.

**Check what is actually running** rather than trusting this table: the startup log
prints `[brain] driving the canvas with <model>`, and the dock's brain tooltip names
it too. If `GEMINI_MODEL` in your `.env` points somewhere else, that is what you get.
