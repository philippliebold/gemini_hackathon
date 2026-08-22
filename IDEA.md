# Co-Presenter

**One-liner:** A live AI co-presenter that takes over the big screen and builds your presentation *while* you talk — no slides made in advance.

**Tracks:** Most Creative Gemini Hack (primary) · Best Use of Gemma (optional secondary, for on-device audio/vision)

---

## The problem

Nobody wants to build slides for a 10-minute brainstorm, a standup, a client walkthrough, or a hackathon pitch. Yet the moment you stand in front of a screen, the screen is dead weight — a static deck you made yesterday, or nothing at all.

Making a deck is slow, and it locks the talk into a script. A live conversation goes wherever it goes; the deck can't follow.

## The idea

The screen starts **blank**. You just start talking.

Gemini listens (mic) and watches (laptop camera), and paints the screen as you speak — an infinite Miro-like canvas that fills itself:

- You describe an idea → a diagram appears.
- You quote numbers → a chart or table appears.
- You say "I imagine it looking like this…" → a generated visual/mockup appears.
- You say "I'm at NG Greenhouse, I want to get to Luckin Coffee" → a map with the fastest route appears.
- You hold something up to the camera → it gets read, labeled, and pulled onto the canvas.

You are the presenter. Gemini is the co-presenter that draws.

## Why this needs Gemini specifically

- **Speed is the product.** If the visual lands 8 seconds after the sentence, the moment is gone. Gemini Flash 3.7's latency is what makes this feel like a co-presenter rather than a batch renderer.
- **Multimodal in, multimodal out.** Audio + camera in; text, layout, charts, images out. One model spans the whole loop.
- **Function calling closes the loop.** Maps/route lookup, search, chart generation are *actions* Gemini triggers, not things it describes.
- **Not a fixed script.** The canvas is generated live from whatever is said — the audience can see it adapt to an unexpected sentence, which is exactly the "wow" the track asks for.

## The demo moment (design this first)

1. Screen is white. Silence.
2. Presenter says one sentence. Something appears — fast.
3. Presenter keeps talking; the canvas grows, arranges itself, connects related blocks.
4. Someone from the audience shouts an unplanned input. The canvas responds to it.
5. Presenter says the "I'm here, get me there" line → live route on screen.

Everything on screen was empty 90 seconds earlier. That's the demo.

## Core loop

```
mic ──┐
      ├──► Gemini (live, streaming) ──► canvas ops (add / update / arrange / link)
cam ──┘                              └─► tool calls (route, search, chart, image)
```

The model does not emit slides. It emits **canvas operations** against a spatial board — so the presentation is an evolving artifact, not a sequence.

## What makes it not-a-chatbot

- No prompt box. No turn-taking. You never address the AI.
- Output is spatial and visual, not a transcript.
- It intervenes on its own judgment of what deserves to be on screen — it stays quiet when nothing does.

## Scope for today

**Must have**
- Blank infinite canvas that fills from live speech
- At least three block types (text/concept card, chart or table, generated image)
- One tool call that visibly does something real (maps route)
- Sub-2s perceived latency from sentence → something on screen

**Nice to have**
- Camera input as a source
- Auto-layout / clustering of related blocks
- Presenter-controlled undo / "clear that"

**Explicitly out of scope**
- Editing UI, export, accounts, persistence

## Open questions

- Canvas rendering: DOM + absolute positioning vs. a canvas lib — pick whichever is fastest to make look good.
- How aggressively should it draw? Every sentence is too much; the taste of *when to stay blank* is part of the product.
- Camera: worth the latency budget, or a bonus?
- Possible Gemma angle: run the wake/intent gate locally so audio isn't streamed continuously — makes the second track honest rather than bolted on.

## Risks

- Live audio in a loud room. Have a backup mic; consider a rehearsed fallback path.
- Overdrawing → visual noise. Bias toward fewer, bigger, better blocks.
- Judges must be told clearly what was live vs. precomputed. Nothing precomputed if we can help it.
