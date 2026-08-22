"""The brain: gemini-3.7-flash decides what goes on the canvas.

Why this is a separate module from the ear:

The Live API models will stream audio and transcribe it well, but they do not
reliably call our tools — measured on the real pipeline, 11 turns of clean speech
produced 10 transcripts and ZERO tool calls, on two different live models. They are
built to converse, not to drive functions.

So we split the job. gemini_live.py owns the ear (audio -> text, streaming, fast).
This owns the decision (text -> tool call) on gemini-3.7-flash, where function
calling is a first-class, non-preview feature.

Bonus: text tokens instead of audio tokens for the expensive half, and the evolving
context becomes something we hand over explicitly rather than hope the session kept.
"""
import asyncio
import re
import time
from collections.abc import Callable

from google import genai
from google.genai import types

import canvas
import ops
import tools
from config import CFG

DEBOUNCE_S = 0.45        # flush a transcript this long after the words stop
MIN_CHARS = 18           # "so, right" is not worth a call

# Free local gate, applied BEFORE any API call. Two reasons it earns its place:
# restraint is a product feature ("know when to stay blank"), and on the free tier
# gemini-3.7-flash allows only 20 requests PER DAY — so every request spent on
# "Right?" is one the demo does not get back. This is also the natural seat for a
# local Gemma salience model later; the interface is already a single function.
FILLER = re.compile(
    r"^(?:so|right|okay|ok|anyway|well|um|uh+|erm|yeah|yep|nope|hmm|"
    r"you know|i mean|sort of|kind of|basically|and|but|then|actually)"
    r"[\s,.!?\-]*$", re.I)


def worth_asking(line: str) -> bool:
    """Cheap local salience check. Conservative: only drops obvious non-content."""
    t = line.strip()
    if FILLER.match(t):
        return False
    words = re.findall(r"[a-zA-Z0-9']+", t)
    # A spoken figure is always worth a look, even in a short phrase — "40k a
    # month" is the whole point of show_stat.
    if any(c.isdigit() for c in t):
        return len(words) >= 2
    return len(t) >= MIN_CHARS and len(words) >= 4
WINDOW = 6               # utterances of context handed over
REPROBE_S = 45.0         # how often to try to climb back to the primary model
DEADLINE_S = 4.5         # abandon a decision that misses the moment
MAX_INFLIGHT = 2         # a slow call must not block the next sentence

SYSTEM = """\
You are a silent co-presenter. A human is giving a live talk to an audience and you
control the screen behind them. You never speak. Your only output is tool calls that
put things on a shared canvas.

WHEN TO DRAW:
- Draw when the latest line carries something a slide would have carried: a claim
  worth anchoring, a real number, a system or flow, a comparison, a place.
- Otherwise call NOTHING. Filler, throat-clearing and transitions get no visual.
  An empty screen beats a noisy one.
- One tool call at most per line. Never restate what is already on screen.

THE BOARD EVOLVES — the core of the job:
Every visual belongs to a topic `key`. You are given CANVAS, the list of what is on
screen with each block's key. That list is the truth.
- The line adds detail to something already up there → SAME tool, SAME key. The
  block grows in place. Updating is CHEAP. Prefer it.
- The line contradicts or corrects something up there → new key, and set `revises`
  to the old key. Both stay visible. NEVER silently overwrite a number.
- A genuinely new topic → new key.
- Never create a second block about a topic that already has one.

MULTIPLE SPEAKERS: lines may be tagged with who said them. Two people making the
same point → one block, same key. Two people disagreeing → `revises`, both stand.

CONTENT RULES:
- Never invent a number, name or fact that was not said.
- Titles 3-8 words. Bullets under 8 words. No sentences on screen.
- Read from across a room. Terse wins.
"""


def _why(exc: Exception) -> str:
    """Turn an API failure into something readable from across a room."""
    m = str(exc)
    if "spending cap" in m:
        return "API blocked: billing spend cap reached — ai.studio/billing"
    if "free_tier" in m or "RESOURCE_EXHAUSTED" in m:
        return "API blocked: free-tier quota exhausted — wait, or use a funded key"
    if "429" in m:
        return "API rate limited — retrying"
    if "404" in m:
        return "Model not found — check GEMINI_MODEL in .env"
    if "PERMISSION" in m or "401" in m or "403" in m:
        return "API key rejected — check GEMINI_API_KEY in .env"
    return f"API error: {type(exc).__name__}"


class Brain:
    def __init__(self, broadcast: Callable[[dict], None]):
        self.broadcast = broadcast
        self.client = genai.Client(api_key=CFG.api_key)
        # Preference order. thinking_budget=0 roughly halves 3.7-flash's latency
        # (3.0s -> 1.7s measured); older flash models reject the parameter with a
        # 400, so it is per-model rather than global.
        self.chain: list[tuple[str, int | None]] = [
            (CFG.model, 0), (CFG.model, None),
            (CFG.model_fallback, None), (CFG.model_fallback2, None)]
        self.model, self.think = self.chain[0]
        self.recent: list[str] = []
        self.speaker: str | None = None
        self._buf = ""
        self._last = 0.0
        self._seen = ""
        self._inflight = 0
        self.calls = 0
        self.dropped = 0
        self.skipped = 0
        self.enabled = False        # flipped from the screen; see server.on_presenter
        self._probed = False
        self._demoted_at = 0.0      # when we last fell back off the primary

    def _cfg(self, think: int | None) -> types.GenerateContentConfig:
        c = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            tools=[types.Tool(function_declarations=tools.DECLARATIONS)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True),
            temperature=0.3)
        if think is not None:
            c.thinking_config = types.ThinkingConfig(thinking_budget=think)
        return c

    async def select_model(self) -> None:
        """Probe the chain once at startup and stick to the first model that works.

        Doing this at boot rather than mid-talk matters: a 429 on the primary would
        otherwise cost a wasted round trip on every single sentence. The operator
        also gets told, up front, which brain is actually driving the screen.
        """
        for model, think in self.chain:
            t0 = time.time()
            try:
                await self.client.aio.models.generate_content(
                    model=model, contents="Reply with the single word ok.",
                    config=self._cfg(think))
            except Exception as e:  # noqa: BLE001
                print(f"[brain] {model} (thinking={think}) unavailable: "
                      f"{str(e)[:70]}")
                continue
            self.model, self.think = model, think
            tag = "thinking off" if think == 0 else "default thinking"
            print(f"[brain] driving the canvas with {model} ({tag}) "
                  f"— probe {time.time()-t0:.2f}s")
            if model != CFG.model:
                self._demoted_at = time.time()
                print(f"[brain] NOTE: on a fallback; retrying {CFG.model} "
                      f"every {REPROBE_S:.0f}s")
            else:
                self._demoted_at = 0.0
            return
        print("[brain] no usable model — the canvas will stay blank")

    # --- ingest -------------------------------------------------------------
    async def set_enabled(self, on: bool) -> None:
        """Turn the brain on or off mid-talk.

        Flips immediately and probes for a usable model in the background: the probe
        can take several seconds when the primary is rate limited, and a toggle that
        hangs that long reads as broken. Worst case the first sentence or two go
        undrawn while the probe settles.
        """
        self.enabled = on
        self._buf = ""               # drop anything queued from before the switch
        print(f"[brain] {'ENABLED' if on else 'disabled'} "
              f"- {'3.7-flash decides' if on else 'the ear decides'}")
        if on and not self._probed:
            self._probed = True
            asyncio.create_task(self._probe_then_report())

    async def _probe_then_report(self) -> None:
        try:
            await self.select_model()
        except Exception as e:                       # noqa: BLE001
            print(f"[brain] probe failed: {str(e)[:90]}")
        import server                                # local: avoids a cycle
        server.push_mics()

    def feed(self, text: str) -> None:
        """Transcript fragment from the ear.

        The Live API interleaves incremental fragments with cumulative re-sends of
        the same turn, so the same words arrive more than once. Feeding those
        through would have the brain redraw what it just drew.
        """
        if not self.enabled:
            return
        t = text.strip()
        if not t or t in self._seen[-400:]:
            return
        self._seen = (self._seen + " " + t)[-2000:]
        self._buf += text
        self._last = time.time()

    def set_speaker(self, label: str) -> None:
        self.speaker = label

    # --- the loop -----------------------------------------------------------
    async def loop(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            if not self.enabled:
                continue
            if not self._buf.strip() or time.time() - self._last < DEBOUNCE_S:
                continue
            if self._inflight >= MAX_INFLIGHT:
                continue
            line, self._buf = self._buf.strip(), ""
            if not worth_asking(line):
                self.skipped += 1
                continue
            # Fire and forget: a slow decision must never hold up the next
            # sentence, or one bad call stalls the whole board.
            asyncio.create_task(self._guarded(line))

    async def _guarded(self, line: str) -> None:
        self._inflight += 1
        try:
            await asyncio.wait_for(self._consider(line), timeout=DEADLINE_S)
        except asyncio.TimeoutError:
            self.dropped += 1
            print(f"[brain] missed the moment (>{DEADLINE_S}s) — {line[:50]!r}")
            self.broadcast(ops.status("listening"))
        except Exception as e:  # noqa: BLE001 - never take the demo down
            print(f"[brain] {type(e).__name__}: {str(e)[:110]}")
            # Say WHY on screen. A blank canvas from an exhausted quota looks
            # exactly like a blank canvas from a broken pipeline, and that
            # ambiguity cost real debugging time.
            self.broadcast(ops.status("error", _why(e)))
        finally:
            self._inflight -= 1
            await self._maybe_reclaim_primary()

    async def _maybe_reclaim_primary(self) -> None:
        """gemini-3.7-flash is the model this project is meant to run on, so a
        fallback is a temporary state, not a decision. Retry the primary on a timer
        so enabling billing takes effect without restarting mid-talk."""
        if not self._demoted_at or time.time() - self._demoted_at < REPROBE_S:
            return
        self._demoted_at = time.time()          # don't hammer it
        try:
            await self.client.aio.models.generate_content(
                model=CFG.model, contents="ok", config=self._cfg(0))
        except Exception:                        # noqa: BLE001 - still unavailable
            return
        self.model, self.think = CFG.model, 0
        self._demoted_at = 0.0
        print(f"[brain] reclaimed {CFG.model} — thinking off")
        import server
        server.push_mics()

    async def _consider(self, line: str) -> None:
        tagged = f"{self.speaker}: {line}" if self.speaker else line
        self.recent.append(tagged)
        del self.recent[:-WINDOW]

        prompt = (
            f"{canvas.manifest_text()}\n\n"
            f"EARLIER LINES:\n" + "\n".join(self.recent[:-1]) +
            f"\n\nTHE LINE JUST SPOKEN:\n{tagged}\n\n"
            "Call one tool, or none."
        )
        t0 = time.time()
        self.broadcast(ops.status("thinking", line))
        resp = await self.client.aio.models.generate_content(
            model=self.model, contents=prompt, config=self._cfg(self.think))
        fcs = [p.function_call
               for c in (resp.candidates or [])
               for p in ((c.content.parts if c.content else None) or [])
               if getattr(p, "function_call", None)]
        if not fcs:
            print(f"[brain] {time.time()-t0:.2f}s silent — {line[:60]!r}")
            self.broadcast(ops.status("listening"))
            return

        self.broadcast(ops.status("drawing"))
        for fc in fcs[:1]:                      # one visual per line, hard limit
            frames, result = tools.dispatch(fc.name, dict(fc.args or {}))
            self.calls += 1
            print(f"[brain] {time.time()-t0:.2f}s {fc.name} "
                  f"key={(fc.args or {}).get('key')} -> "
                  f"{result.get('action') or result.get('skipped') or result.get('error')}")
            for f in frames:
                self.broadcast(f)
        self.broadcast(ops.status("listening"))
