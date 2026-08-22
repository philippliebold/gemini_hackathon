# Co-Presenter

**One-liner:** A live AI co-presenter that takes over the big screen and builds the presentation *while the room talks* — no slides made in advance.

**Tracks:** Most Creative Gemini Hack (primary) · Best Use of Gemma (optional secondary, for on-device audio gating)

---

## The problem

Nobody wants to build slides for a 10-minute brainstorm, a standup, a client walkthrough, or a hackathon pitch. Yet the moment you stand in front of a screen, the screen is dead weight — a static deck you made yesterday, or nothing at all.

Making a deck is slow, and it locks the talk into a script. A live conversation goes wherever it goes; the deck can't follow.

## The idea

The screen starts **blank**. You just start talking.

Gemini listens and paints the screen as you speak — an infinite Miro-like canvas that fills itself. You are the presenter. Gemini is the co-presenter that draws.

**Microphone is the primary input, and the room is the source — not one laptop.** Multiple mics can join the same session (each person's phone or laptop becomes a mic), so a four-person brainstorm feeds one shared canvas. The model knows who said what and can attribute, cluster, and reconcile across speakers.

**Camera is optional** — a bonus channel, not a dependency. If it's on, held-up objects, whiteboards, and documents become canvas material. If it's off, nothing about the product breaks.

## Why this needs Gemini specifically

- **Speed is the product.** If the visual lands 8 seconds after the sentence, the moment is gone. Gemini Flash 3.7's latency is what makes this feel like a co-presenter rather than a batch renderer.
- **Multimodal in, multimodal out.** Live audio from several streams in; text, layout, charts, images out. One model spans the whole loop.
- **Function calling closes the loop.** Routes, search, charts, bookings are *actions* Gemini triggers, not things it describes.
- **Not a fixed script.** The canvas is generated live from whatever is said — the audience can see it adapt to an unexpected sentence, which is exactly the "wow" the track asks for.

---

## What the co-presenter can actually do

The canvas is not one trick. These are the capability families — each is a different reason the screen is worth looking at.

### 1. Render the argument
Turn what's being said into the right *shape*, automatically.
- Spoken idea → concept card, on the board, positioned near related ideas.
- "There are basically three options here" → a three-column comparison that fills in as each is discussed.
- "First we do X, then Y, then Z" → a flow diagram, built step by step.
- "It's a tradeoff between cost and speed" → a 2×2, with items placed as they're mentioned.
- "Our org has four teams under two leads" → a hierarchy tree.
- Cause-and-effect language → an arrow diagram that grows.

The taste here is choosing the *form*: the same sentence deserves a table sometimes and a diagram other times. That judgment is the model's job and it is visible on screen.

### 2. Bring in facts nobody has open
The screen answers questions the room raises, without anyone breaking flow to search.
- "What's the population of Singapore again?" → the number, sourced, on the board.
- "How big is that market?" → a sized figure with its source and date.
- "When did that ship?" → a timeline.
- A named company, person, or product → a compact fact card.
- Two things compared aloud → a side-by-side spec table.
- Anything uncertain gets marked as uncertain rather than asserted.

### 3. Do things in the world (function calls)
Not descriptions of actions — actions.
- **Route / maps:** "I'm at NG Greenhouse, I want Luckin Coffee" → live route, time, distance.
- **Nearby search:** "where can we eat around here" → mapped options with hours.
- **Time zones:** "can we do 3pm with the SF team" → the overlap grid.
- **Weather:** for a discussed date and place, when it matters to the plan.
- **Math and units:** "that's 40k a month for eighteen months" → the computed total, kept live as assumptions change.
- **Currency conversion** at today's rate.
- **Pull a URL** somebody says out loud → its content summarized onto the board.

### 4. Numbers become pictures
- Figures spoken in sequence → a chart that builds as they're said.
- "Say we grow 20% a month from 500" → a projection curve, recalculated the instant someone says "no, make it 15."
- Budget discussed aloud → a breakdown table that stays consistent and re-totals itself.
- Named metrics tracked across the conversation, so a contradiction later can be flagged.

### 5. Show what doesn't exist yet
- "I imagine it looking like this…" → a generated mockup or image.
- A described UI → a rough wireframe block.
- A described physical object, space, or scene → an illustrative visual.
- A described architecture → boxes and arrows.

### 6. Keep the room honest (the meeting-memory layer)
This is what remains after the talking stops, and it's the reason it's more than a toy.
- **Decisions** get captured as decisions, distinct from ideas.
- **Action items** with owners, caught from "I'll take that."
- **Open questions** parked in a corner of the board instead of lost.
- **Disagreements** surfaced: "Two of you assumed different numbers here."
- **Contradiction catch:** something asserted now conflicts with something asserted ten minutes ago — the board says so.
- **Callback:** "what did we say about pricing earlier?" → that region of the canvas is found and focused.
- At the end, the canvas *is* the summary. Nothing to write up.

### 7. Multi-speaker intelligence (this is what multiple mics buys)
- **Attribution:** ideas carry who said them.
- **Convergence:** two people saying the same thing in different words get merged into one block, credited to both.
- **Tension:** two people saying opposite things get placed side by side, not silently overwritten.
- **Airtime:** who has and hasn't spoken — surfaced gently, only if asked.
- **Cross-language:** two people in different languages, one canvas in a common language.
- **Quiet-channel input:** someone types into their phone instead of speaking, and it lands on the same board.

### 8. Direct the screen without addressing it
Light spoken control that never feels like talking to a robot.
- "Let's park that" → block moves to the parking area.
- "Scratch that" / "no, not that" → undo.
- "Go back to the pricing bit" → canvas pans there.
- "Blow that up" → focus one block full-screen.
- "Clean this up" → re-layout and cluster.

### 9. Know when to stay blank
The hardest capability and the most valuable: most sentences do not deserve a visual. A board that reacts to everything is noise. Restraint is a feature we should demo explicitly — silence on small talk, then a strong response to a substantive line.

---

## The demo moment (design this first)

1. Screen is white. Silence.
2. Someone says one sentence. Something appears — fast.
3. A **second person on a second mic** adds to it. Their point lands on the same canvas, attributed, near the related block.
4. They disagree on a number. The board shows the tension instead of picking one.
5. Someone from the audience shouts an unplanned input. The canvas responds to it.
6. The "I'm here, get me there" line → live route on screen.
7. Pull back: the whole board was empty 90 seconds ago, and it's now a better artifact than a deck anyone would have made.

## Core loop

```
mic 1 ──┐
mic 2 ──┤
mic n ──┼──► Gemini (live, streaming) ──► canvas ops (add / update / move / link / focus)
cam ────┘   (camera optional)          └─► tool calls (route, search, chart, image, compute)
```

The model does not emit slides. It emits **canvas operations** against a spatial board — so the presentation is an evolving artifact, not a sequence.

## What makes it not-a-chatbot

- No prompt box. No turn-taking. You never address the AI.
- Output is spatial and visual, not a transcript.
- It intervenes on its own judgment of what deserves to be on screen — it stays quiet when nothing does.

## Scope for today

**Must have**
- Blank infinite canvas that fills from live speech
- **Two mics joining one session**, with attribution
- At least three block types (concept card, chart or table, generated image)
- One tool call that visibly does something real (maps route)
- Sub-2s perceived latency from sentence → something on screen
- Visible restraint: it does not draw on every sentence

**Nice to have**
- Decisions / actions / open-questions capture
- Convergence + tension between speakers
- Spoken control (park, scratch, go back)
- Auto-layout / clustering
- Camera as a bonus input channel

**Explicitly out of scope**
- Editing UI, export, accounts, persistence

## Open questions

- Canvas rendering: DOM + absolute positioning vs. a canvas lib — pick whichever is fastest to make look good.
- Multi-mic transport: separate streams to the model, or mixed with speaker labels? Separate is cleaner for attribution, costlier in latency.
- How aggressively should it draw? The taste of *when to stay blank* is part of the product.
- Possible Gemma angle: run the salience gate locally — decide on-device whether a snippet is worth sending — so audio isn't streamed continuously. Makes the second track honest rather than bolted on, and cuts cost.

## Risks

- Live audio in a loud room. Have a backup mic; consider a rehearsed fallback path.
- Multiple mics = echo and cross-talk if two devices are near each other. Headsets, or mute-when-not-speaking.
- Overdrawing → visual noise. Bias toward fewer, bigger, better blocks.
- Judges must be told clearly what was live vs. precomputed. Nothing precomputed if we can help it.
