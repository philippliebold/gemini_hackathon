# Plan — build day

Deadline **3:30 pm sharp**. Public GitHub repo + demo video under 3 minutes.

The scaffold is already in place and the mock loop already runs. Nobody starts
from a blank file.

---

## The one thing that matters

> Everything on screen was empty 90 seconds earlier.

Every decision today gets measured against that sentence. If a task doesn't make
that moment land harder, it's out of scope.

**Corollary:** build the demo backwards. The 90-second script comes first, and
the code exists to serve it. Do not build features and hope a demo emerges.

---

## Rules that keep four people out of each other's way

1. **`CONTRACT.md` is frozen.** Changing it requires saying so out loud to
   everyone. A silent schema change is the only thing that can actually kill us.
2. **Stay in your own files** (see the table in README). If you need something
   in someone else's file, ask them; don't edit it.
3. **Commit small and push often.** `git pull --rebase` before every push.
4. **The frontend never waits on the backend.** `mock_server.py` replays
   `shared/fixtures/demo.jsonl`. If you're blocked on the Gemini side, you've
   misunderstood the setup.
5. **Nothing precomputed in the demo.** If we fake anything, we say so on stage.

---

## Owners

| | Owner | Mission |
|---|---|---|
| **A** | *(name)* | **The ear.** Mic → Live API → tool calls. Owns the system prompt and, with it, the taste of *when to draw*. |
| **B** | *(name)* | **The plumbing.** WebSocket, tool implementations, maps route, image gen. Owns "the tool call visibly did something real". |
| **C** | *(name)* | **The canvas.** Pan/zoom, block lifecycle, links, layout animation. Owns "does this feel alive". |
| **D** | *(name)* | **The look.** Block renderers, typography, motion. Owns "does this look like a product or a hackathon project". |

A+B share `backend/`. C+D share `frontend/`. Neither pair touches the other's
directory.

---

## Timeline

### Phase 0 — 20 min, everyone together
- [ ] Read `CONTRACT.md` out loud. Argue now, freeze it, never again.
- [ ] Everyone runs the mock loop and sees blocks on screen (README, 60 seconds).
      **This is the gate — nobody writes code until their machine shows the mock.**
- [ ] Assign the four names above.
- [ ] One person gets `GEMINI_API_KEY` from AI Studio and shares it with A and B.
- [ ] **Write the 90-second demo script now.** Literally the sentences the
      presenter will say, and what should appear after each. Put it in
      `DEMO.md`. Everything downstream is built to make that script work.

### Phase 1 — first 2 hours, "the spine"

Target: **one real sentence into a real mic puts one real block on screen.**
Ugly is fine. This is the only milestone that de-risks the project.

- **A** — Get `gemini_live.py` connecting. Confirm audio reaches the model
  (watch `input_transcription` in the status line). Get *any* tool call to fire.
- **B** — `server.py` runs and fans out. Verify frames arrive with `wscat` or the
  browser console. Then `show_route` against the real Directions API.
- **C** — Real WS data driving the canvas. Camera feels good: smooth pan, fit-to-
  content on `canvas.focus`, no jank when 8 blocks are up.
- **D** — Make `text`, `stat`, and `diagram` look genuinely good against the mock.
  These three carry the demo. Big type, high contrast, readable from the back
  of the room.

**Checkpoint at the 2-hour mark:** if the spine isn't alive, cut camera input and
image generation immediately and put everyone on the spine.

### Phase 2 — middle block, "make it feel like a co-presenter"

- **A** — Tune the system prompt. This is the highest-leverage work of the day.
  The failure mode is drawing on *every* sentence. Bias hard toward silence.
  Rehearse against a recorded talk (`--pcm`) so tuning is repeatable, not vibes.
- **B** — Real maps route with a live polyline. Then async image generation with
  the placeholder→update two-phase pattern already stubbed in `tools.py`.
- **C** — Auto-arrange: when a 4th block lands, reflow so nothing overlaps and
  the camera reframes. This is the "it's alive" beat.
- **D** — `chart`, `table`, `map`, `image`, `code`. Then a pass on motion —
  entrances should feel deliberate, not bouncy.

### Phase 3 — last 90 minutes, "lock it"

**Hard stop on new features.** Anything unfinished gets deleted, not debugged.

- [ ] Full rehearsal, top to bottom, three times. Time it.
- [ ] Kill anything that fires unreliably. A tool that works 70% of the time is
      worse than no tool.
- [ ] Record the demo video. Do it **early** — an hour before you think you need
      to. Under 3 minutes, and it must show the blank screen at the start.
- [ ] Push. Confirm the repo is public and the README quickstart actually works
      on a clean clone.
- [ ] Decide the one sentence you open with.

---

## Risks and the pre-decided answer

| Risk | Answer, decided now so nobody debates it at 2 pm |
|---|---|
| Loud room, mic fails | Record the talk to a `.pcm` beforehand. `main.py --pcm talk.pcm` replays it through the *real* pipeline. Live model, live tool calls, canned audio. Say so on stage. |
| Live API session drops | `main.py` already retries. Frontend already reconnects and replays history. Test both by killing the backend mid-demo. |
| Model over-draws → visual noise | Prompt bias toward silence + at most one call per sentence. If it's still noisy, add a hard rate limit in `server.broadcast`. |
| Mermaid throws on generated syntax | Already handled: raw source renders instead. Never blanks. |
| Image gen too slow | Placeholder lands immediately, real pixels arrive via `block.update`. If it's still ugly, cut the block type. |
| Someone edits `CONTRACT.md` quietly | Frontend drops the frame and logs a version mismatch instead of crashing. You'll see it. |

---

## Definition of done

- [ ] Blank screen → presenter talks → visuals appear, under 2 seconds
- [ ] Three block types working live (not from the fixture)
- [ ] The maps route fires on a spoken sentence
- [ ] Survives an unplanned sentence from the audience
- [ ] Video recorded, under 3 min
- [ ] Repo public, quickstart verified on a clean clone

## Known gaps in the scaffold

- The frontend has **not been rendered in a browser yet** — syntax checks and the
  wire protocol pass, but C/D should open it first thing and expect small fixes.
- `tools.py` maps route returns a canned polyline unless `GOOGLE_MAPS_API_KEY`
  is set; `show_image` returns a placeholder and never updates. Both are marked
  `TODO(person B)`.
- Camera input from the laptop webcam is not scaffolded at all. It is a
  nice-to-have — only start it if the spine is done well before Phase 2 ends.
