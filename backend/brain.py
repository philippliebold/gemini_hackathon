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
import os
import re
import time
from collections.abc import Callable

from google import genai
from google.genai import types

import canvas
import ops
import runtime
import tools
from config import CFG

# Only for the --live path, where input_transcription arrives as overlapping
# fragments that have to settle. The local ear hands over whole utterances and
# already knows where they ended, so debouncing those was 0.45 s of waiting for a
# settling that had already happened — see feed(complete=True).
DEBOUNCE_S = float(os.getenv("BRAIN_DEBOUNCE", "0.45"))
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


# Words that carry no subject. A line made only of these is grammar, not content.
_EMPTY = {
    "the", "a", "an", "and", "or", "but", "so", "then", "that", "this", "it",
    "is", "are", "was", "were", "be", "been", "am", "do", "does", "did", "to",
    "of", "in", "on", "at", "for", "with", "as", "we", "i", "you", "they", "he",
    "she", "our", "your", "my", "his", "her", "their", "just", "really", "very",
    "actually", "basically", "like", "about", "here", "there", "what", "when",
    "how", "why", "now", "okay", "ok", "right", "well", "yeah", "yep", "no",
    "not", "going", "gonna", "get", "got", "go", "know", "think", "mean", "say",
    "said", "would", "could", "should", "can", "will", "let", "lets", "if",
    "all", "some", "any", "one", "thing", "things", "stuff", "kind", "sort",
}
# Two content words is the bar. Measured over one real session the old bar let
# 51 of 62 utterances through to the model, and the model then drew on ALL of
# them — restraint was 0%, and the only thing holding back visual noise was a
# mechanical cooldown. Half that traffic was lines like "So I'm going to go."
MIN_CONTENT_WORDS = int(os.getenv("BRAIN_MIN_CONTENT", "2"))


def _stem(word: str) -> str:
    """Lowercase and drop a contraction tail, so 'That\'s' tests as 'that'."""
    w = word.lower()
    for tail in ("n't", "'s", "'re", "'ve", "'ll", "'d", "'m"):
        if w.endswith(tail) and len(w) > len(tail):
            return w[: -len(tail)]
    return w


def worth_asking(line: str) -> bool:
    """Cheap local salience check, applied BEFORE any API call.

    This is deliberately a single pure function of one string: it is the seat a
    local Gemma salience model drops straight into (IDEA.md's second track), and
    until then plain word counting does the same job for free. Restraint is a
    product feature, not an optimisation — see taste.WHEN.
    """
    t = line.strip()
    if FILLER.match(t):
        return False
    words = re.findall(r"[a-zA-Z0-9']+", t)
    # A spoken figure is always worth a look, even in a short phrase — "40k a
    # month" is the whole point of show_stat.
    if any(c.isdigit() for c in t):
        return len(words) >= 2
    if len(t) < MIN_CHARS or len(words) < 4:
        return False
    # A line that is all grammar and no subject cannot become a visual anyone can
    # read from ten metres. Contractions have to be reduced first or the stop list
    # misses them: "that's" and "I'm" are the same non-words as "that" and "I".
    content = [w for w in (_stem(x) for x in words) if w and w not in _EMPTY]
    return len(content) >= MIN_CONTENT_WORDS
WINDOW = 14              # recent lines handed over verbatim
REPROBE_S = 45.0         # how often to try to climb back to the primary model
# A model that answers in 25 s is not "available" for this product — the visual has
# to land while the speaker is still on the topic. Probes are judged on latency, not
# just on returning something.
PROBE_TIMEOUT_S = float(os.getenv("BRAIN_PROBE_TIMEOUT", "4.0"))
# 3.7-flash under load returns 503s and takes anywhere from 2 s to 90 s for the same
# prompt. Killing a request because it is slow just loses the visual entirely — late
# is strictly better than never, and the speaker is usually still on the topic. This
# is a safety net against a hung request, not a latency policy.
DEADLINE_S = float(os.getenv("BRAIN_DEADLINE", "30.0"))
# Several sentences may be in flight together: one slow call must never hold up the
# sentences spoken after it.
MAX_INFLIGHT = int(os.getenv("BRAIN_INFLIGHT", "4"))

import taste

SYSTEM = taste.drawing_prompt()


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
    def __init__(self, broadcast: Callable[[dict], None], memory=None):
        self.broadcast = broadcast
        self.memory = memory
        self.client = genai.Client(api_key=CFG.api_key)
        # Preference order. thinking_budget=0 roughly halves 3.7-flash's latency
        # (3.0s -> 1.7s measured); older flash models reject the parameter with a
        # 400, so it is per-model rather than global.
        # Order is preference, but selection is gated on measured latency below.
        # 3.1-flash-lite with thinking off answered in 0.95 s while 3.7-flash was
        # timing out past 25 s under hackathon load; it is in the chain so the
        # screen keeps up when the bigger model cannot.
        self.chain: list[tuple[str, int | None]] = [
            (CFG.model, 0), (CFG.model_fast, 0),
            (CFG.model_fallback, None), (CFG.model_fallback2, None)]
        self.model, self.think = self.chain[0]
        self.recent: list[str] = []
        self.speaker: str | None = None
        self._buf = ""
        self._last = 0.0
        self._seen = ""
        self._inflight = 0
        self._tasks: set = set()
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
                await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=model, contents="Reply with the single word ok.",
                        config=self._cfg(think)),
                    timeout=PROBE_TIMEOUT_S)
            except asyncio.TimeoutError:
                print(f"[brain] {model} too slow (>{PROBE_TIMEOUT_S:.0f}s) — skipping")
                continue
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
    def abort(self) -> int:
        """Cancel work already in flight.

        Refusing NEW calls is not the same as stopping: a request issued a moment
        before you hit stop still completes, still bills, and still draws. Stop has
        to mean stop.
        """
        n = 0
        for t in list(self._tasks):
            if not t.done():
                t.cancel()
                n += 1
        self._buf = ""
        if n:
            print(f"[brain] cancelled {n} call(s) in flight")
        return n

    def clear(self) -> None:
        """Forget the recent lines. Paired with memory.reset()."""
        self.recent.clear()
        self._buf = ""
        self._seen = ""
        self.speaker = None

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

    def feed(self, text: str, complete: bool = False,
             refine: bool = False) -> None:
        """A line from the ear.

        `complete=True` means the caller already knows this is a whole utterance —
        the local ear does, because it owns the sentence boundary. Those go straight
        out with no debounce; waiting again would just be latency.

        `refine=True` means a speculative visual for this utterance is already on
        screen and this is the better version of it, so the write is allowed past
        the per-key cooldown and the form lock.

        Everything else is a --live fragment: the Live API interleaves incremental
        fragments with cumulative re-sends of the same turn, so the same words
        arrive more than once and have to settle before we act.
        """
        if not self.enabled or not runtime.listening():
            return
        t = text.strip()
        if not t or t in self._seen[-400:]:
            return
        self._seen = (self._seen + " " + t)[-2000:]
        if complete:
            self._submit(t, refine=refine)
            return
        self._buf += text
        self._last = time.time()

    def _submit(self, line: str, refine: bool = False) -> None:
        """Gate locally, then fire and forget. A slow decision must never hold up
        the sentence spoken after it."""
        if not worth_asking(line):
            self.skipped += 1
            return
        if self._inflight >= MAX_INFLIGHT:
            self.dropped += 1
            return
        t = asyncio.create_task(self._guarded(line, refine=refine))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def set_speaker(self, label: str) -> None:
        self.speaker = label

    # --- the loop -----------------------------------------------------------
    async def loop(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            if not self.enabled or not runtime.listening():
                continue
            if not self._buf.strip() or time.time() - self._last < DEBOUNCE_S:
                continue
            if self._inflight >= MAX_INFLIGHT:
                continue
            line, self._buf = self._buf.strip(), ""
            self._submit(line)

    async def _guarded(self, line: str, refine: bool = False) -> None:
        self._inflight += 1
        try:
            await asyncio.wait_for(self._consider(line, refine=refine),
                                   timeout=DEADLINE_S)
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
            # Must be FAST to be worth reclaiming, not merely alive.
            await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=CFG.model, contents="ok", config=self._cfg(0)),
                timeout=PROBE_TIMEOUT_S)
        except Exception:                        # noqa: BLE001 - slow or unavailable
            return
        self.model, self.think = CFG.model, 0
        self._demoted_at = 0.0
        print(f"[brain] reclaimed {CFG.model} — thinking off")
        import server
        server.push_mics()

    async def _consider(self, line: str, refine: bool = False) -> None:
        tagged = f"{self.speaker}: {line}" if self.speaker else line
        self.recent.append(tagged)
        del self.recent[:-WINDOW]

        # THE RECORD is what makes this build instead of react. Without it the model
        # sees one sentence at a time and draws unconnected fragments; with it, a new
        # line is read against everything already established.
        record = ""
        if self.memory is not None:
            sm = self.memory.summary or {}
            bits = []
            if sm.get("thread"):
                bits.append(f"Currently discussing: {sm['thread']}")
            if sm.get("topics"):
                bits.append("Topics so far: " + "; ".join(
                    f"{t.get('key','?')} — {t.get('gist','')}" for t in sm["topics"]))
            if sm.get("numbers"):
                bits.append("Numbers stated: " + "; ".join(
                    f"{n.get('value')} ({n.get('of')})" for n in sm["numbers"]))
            if sm.get("decisions"):
                bits.append("Decided: " + "; ".join(sm["decisions"]))
            if sm.get("questions"):
                bits.append("Open questions: " + "; ".join(sm["questions"]))
            if bits:
                record = "THE RECORD OF THIS TALK SO FAR:\n" + "\n".join(bits) + "\n\n"

        prompt = (
            f"{record}"
            f"{canvas.manifest_text()}\n\n"
            f"RECENT LINES:\n" + "\n".join(self.recent[:-1]) +
            f"\n\nTHE LINE JUST SPOKEN:\n{tagged}\n\n"
            "Read the new line against the record and the canvas.\n"
            "Decide in this order:\n"
            "1. Is this already on the canvas or in the record? Then CALL NOTHING.\n"
            "2. Does it carry a claim, a number, a system, a comparison or a place "
            "worth anchoring for the rest of the talk? If not, CALL NOTHING.\n"
            "3. Only if it does: one call. Same key to grow an existing topic, a "
            "new key for a genuinely new subject.\n"
            "Calling nothing is the most common correct answer and costs you "
            "nothing. Do not narrate the talk."
        )
        t0 = time.time()
        self.broadcast(ops.status("thinking", line))
        # 3.7-flash is the model we want, but under hackathon load it returns 503
        # "high demand" constantly — 7 in one short run. Rather than lose the visual,
        # retry it once and then step sideways through the chain. The primary is
        # still reclaimed on a timer, so this never becomes a permanent demotion.
        attempts = [(self.model, self.think), (self.model, self.think)]
        attempts += [c for c in self.chain if c[0] != self.model]
        last: Exception | None = None
        resp = None
        for i, (model, think) in enumerate(attempts):
            try:
                resp = await self.client.aio.models.generate_content(
                    model=model, contents=prompt, config=self._cfg(think))
                if model != self.model:
                    print(f"[brain] {self.model} unavailable, drew with {model}")
                break
            except Exception as e:                   # noqa: BLE001
                last = e
                if "503" not in str(e) and "UNAVAILABLE" not in str(e) \
                        and "429" not in str(e):
                    raise
        if resp is None:
            raise last if last else RuntimeError("no model answered")
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
            frames, result = tools.dispatch(fc.name, dict(fc.args or {}),
                                            refine=refine)
            self.calls += 1
            _a = fc.args or {}
            print(f"[brain] {time.time()-t0:.2f}s{' refine' if refine else ''} "
                  f"{fc.name} "
                  f"key={_a.get('key')}"
                  + (f" revises={_a.get('revises')}" if _a.get("revises") else "")
                  + " -> "
                  f"{result.get('action') or result.get('skipped') or result.get('error')}")
            for f in frames:
                self.broadcast(f)
        self.broadcast(ops.status("listening"))
