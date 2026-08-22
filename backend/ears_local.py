"""Local continuous transcription. No turn-taking, no waiting for the model.

Why this exists: the Live API decides for itself when you have stopped speaking,
and it is wrong often enough to be unusable — measured on the same 33 s recording,
its own VAD produced ZERO transcripts and driving turns by hand produced one. It
also insists on generating a spoken reply per turn, which swallows the turns behind
it. That is the "works once, then randomly" behaviour.

Whisper on this machine transcribes 6 s of audio in 0.12 s — about 48x realtime. So
we can simply transcribe continuously, locally, and never ask a remote model to
guess where a sentence ended:

    audio -> Floor (who is speaking) -> here (what they said) -> brain (what to draw)

The audio never leaves the machine, the transcript is free, and gemini-3.7-flash is
left doing the part it is actually good at.
"""
import asyncio
import re
import time
from collections.abc import Callable

import numpy as np

import mics
import ops
import runtime
import vitals
from config import CFG

import os

MODEL = os.getenv("WHISPER_MODEL", "mlx-community/whisper-small-mlx")
# Whisper spells names it has never been told to expect: measured, "Jetson Orin"
# came back "GESEL 9-0" and "central question" became "sentry question". Priming it
# with the talk's proper nouns fixes those without a bigger model. Keep it short —
# it is prepended to every transcription.
VOCAB = os.getenv("EAR_VOCAB", "").strip() or None
# Transcribe costs ~0.15s, so the loop is nowhere near CPU bound and a coarse
# tick is just latency: at 0.3 it added up to 300 ms of dead wait per utterance.
TICK_S = float(os.getenv("EAR_TICK", "0.1"))
INTERIM_EVERY_S = 0.9    # re-transcribe the live utterance this often, for the ticker
# A sentence boundary in real speech is a ~0.4 s pause. Shorter cuts mid-thought;
# longer makes the screen feel like it is lagging behind the room.
FINAL_GAP_S = float(os.getenv("EAR_GAP", "0.45"))
# Hard cap on how long a visual can be held back by someone who does not pause.
MAX_UTTERANCE_S = float(os.getenv("EAR_MAX", "7.0"))
# How long an UNFINISHED thought may be held back waiting for its full stop.
# Measured: holding to MAX_UTTERANCE_S put 9 of 31 utterances at the 7 s cap and
# doubled the ear's median wait from 2.6 s to 5.0 s. A thought that has already
# run this long is worth drawing imperfectly — waiting for it to close is worse.
HOLD_MAX_S = float(os.getenv("EAR_HOLD_MAX", "2.5"))
# Measured in this room: every utterance under 1 s came back as garbage or an
# outright invention ("é"; "Thank you." eight times from 0.7 s of hum). Whisper
# needs roughly a second of real speech to have any context to work with, so a
# shorter clip is not a short sentence — it is a shard, and transcribing it costs
# accuracy everywhere downstream.
# 1.2 was set to stop Whisper inventing on shards. _invented() now catches those
# on their own merits, so the floor no longer has to do that job twice — and every
# 0.1 here is 0.1 the room waits.
MIN_UTTERANCE_S = float(os.getenv("EAR_MIN", "0.8"))
# Whisper punctuates. If the interim transcript has already closed a sentence, send
# it now rather than waiting for the speaker to stop — you should be able to keep
# talking and still have the last thought land.
SEND_ON_SENTENCE = os.getenv("EAR_SENTENCE", "1").lower() in ("1", "true", "yes")
# Whisper puts a full stop after two words constantly, so a low bar here cut
# mid-thought: "So now everything is over." was three separate utterances.
SENTENCE_MIN_WORDS = int(os.getenv("EAR_SENTENCE_WORDS", "8"))
# SPECULATIVE DRAW. The interim transcript already exists for the caption ticker;
# handing it to the brain as well puts a visual up WHILE the speaker is still
# talking, and the final transcript then refines the same block in place. Exactly
# the two-phase trick show_image/show_route use for slow assets, applied to the
# decision instead. Fires at most once per utterance, so the cost is bounded at
# 2 calls rather than one per interim tick.
SPECULATE = os.getenv("EAR_SPECULATE", "1").lower() in ("1", "true", "yes")
SPEC_MIN_WORDS = int(os.getenv("EAR_SPEC_WORDS", "6"))


# Whisper does not fail quietly on near-silence: it invents, and always the same
# two ways. Either a stock phrase out of its subtitle training data, or one phrase
# looped. Both were on screen during the last run, so both get dropped here rather
# than becoming a card the room reads.
_ARTEFACTS = {
    "thank you", "thanks for watching", "thank you for watching",
    "thanks for listening", "please subscribe", "subscribe to my channel",
    "bye", "bye bye", "see ya", "you", "okay", "oh", "hmm", "so", "yeah",
    "stand by", "the end", "music", "applause",
}


def _complete(text: str) -> bool:
    """Has the speaker finished the thought?

    Whisper answers this for free and we were ignoring it: it closes a finished
    sentence with . ! or ? and trails an interrupted one off with an ellipsis.
    Measured, one sentence arrived as four utterances — "answer this sentry..." /
    "question which is" / "What happens when you give Gemma an eye?" / "a mouth and
    an ear." — and the brain redrew the same topic four times because of it. No
    model call needed to spot that; the punctuation already says so.
    """
    t = (text or "").rstrip()
    if t.endswith("...") or t.endswith("\u2026"):
        return False
    return t[-1:] in ".!?"


def _invented(text: str) -> bool:
    """True when a transcript is an artefact of silence rather than speech."""
    t = (text or "").strip()
    words = t.lower().strip(" .,!?-").split()
    if not words:
        return True
    if " ".join(words) in _ARTEFACTS:
        return True
    # One phrase on a loop: "Thank you. Thank you. Thank you. ..."
    parts = [x.strip().lower() for x in re.split(r"[.!?]+", t) if x.strip()]
    if len(parts) >= 3 and len(set(parts)) == 1:
        return True
    return False


class LocalEar:
    def __init__(self, broadcast: Callable[[dict], None], brain=None, memory=None):
        self.broadcast, self.brain, self.memory = broadcast, brain, memory
        self._buf: list[bytes] = []
        self._samples = 0
        self._last_voice = 0.0
        self._last_interim = 0.0
        self._interim_text = ""
        self._held = 0            # samples already transcribed and held back
        self._spec = False        # a speculative draw already went out for this one
        self.utterances = 0
        self._ready = False
        self._fails = 0           # consecutive transcribe failures; 3 means re-warm
        vitals.STATE["ear_model"] = MODEL

    # --- called from the audio queue (hot path: must stay trivial) ----------
    def feed(self, pcm: bytes) -> None:
        """Buffer everything, but time the silence from the audio ITSELF.

        The floor deliberately keeps forwarding through the dips inside a sentence,
        so "no frames arriving" is not silence — waiting for that gave 12-second
        utterances that only ended when the floor released. Measuring the level here
        cuts at real sentence ends instead, which is what makes it feel immediate.
        """
        self._buf.append(pcm)
        self._samples += len(pcm) // 2
        if mics.rms_of(pcm) >= mics.GATE_RMS:
            self._last_voice = time.time()

    def _audio(self) -> np.ndarray:
        raw = b"".join(self._buf)
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0

    def _reset(self) -> None:
        self._buf.clear()
        self._samples = 0
        self._held = 0
        self._spec = False
        self._interim_text = ""

    # --- transcription ------------------------------------------------------
    async def _transcribe(self, audio: np.ndarray) -> str:
        import mlx_whisper
        r = await asyncio.to_thread(
            mlx_whisper.transcribe, audio, path_or_hf_repo=MODEL,
            language="en", fp16=True, condition_on_previous_text=False,
            initial_prompt=VOCAB)
        return (r.get("text") or "").strip()

    async def warm(self) -> bool:
        """Load the model before the talk starts; the first call is otherwise slow.

        Returns whether the ear is usable, and says so in the vitals either way.
        This used to swallow the exception and leave `_ready` false, which meant a
        missing Whisper model produced a permanently blank screen and one line in a
        terminal — the single worst failure this project has, because it is
        indistinguishable from a quiet room.
        """
        vitals.STATE["ear"] = "warming"
        t0 = time.time()
        try:
            await self._transcribe(np.zeros(16000, dtype=np.float32))
        except Exception as e:                       # noqa: BLE001
            self._ready = False
            vitals.STATE["ear"] = "dead"
            vitals.STATE["ear_error"] = f"{type(e).__name__}: {e}"[:200]
            print(f"[ear] local transcription unavailable: {type(e).__name__}: {e}")
            vitals.trace("ear", "error", f"{MODEL} failed to load",
                         detail=f"{type(e).__name__}: {e}"[:160])
            return False
        self._ready = True
        self._fails = 0
        vitals.STATE["ear"] = "ready"
        vitals.STATE["ear_error"] = None
        took = time.time() - t0
        print(f"[ear] local transcription ready ({MODEL}) in {took:.1f}s")
        vitals.trace("ear", "ok", f"{MODEL} ready", ms=took * 1000)
        return True

    async def _rewarm(self) -> None:
        """Keep trying to get an ear. A dead ear is not a state the talk can
        continue in, so retry with backoff and keep saying so on screen rather than
        returning out of the loop and leaving the stage silent forever."""
        delay = 2.0
        while not await self.warm():
            self.broadcast(ops.status(
                "error", f"transcription unavailable — retrying in {delay:.0f}s"))
            await asyncio.sleep(delay)
            delay = min(30.0, delay * 2)

    async def loop(self) -> None:
        await self._rewarm()
        while True:
            await asyncio.sleep(TICK_S)
            if not runtime.listening():
                if self._samples:
                    secs = self._samples / CFG.sample_rate
                    self._reset()          # drop anything captured while stopped
                    vitals.trace("ear", "drop", "stopped (Stop is on)",
                                 detail=f"{secs:.1f}s of audio discarded",
                                 throttle=2.0)
                continue
            if not self._samples:
                continue
            secs = self._samples / CFG.sample_rate
            quiet = time.time() - self._last_voice

            # Utterance finished: they stopped, or they have gone on long enough.
            if (quiet >= FINAL_GAP_S and secs >= MIN_UTTERANCE_S) \
                    or secs >= MAX_UTTERANCE_S:
                # Nothing new since we last held this thought: no point paying to
                # transcribe the identical buffer again on every tick.
                if self._held and self._samples <= self._held:
                    continue
                audio = self._audio()
                # drop the trailing pause: it is not speech and Whisper sometimes
                # hallucinates filler into it
                keep = int(len(audio) - (quiet - FINAL_GAP_S / 2) * CFG.sample_rate)
                if 0 < keep < len(audio):
                    audio = audio[:keep]
                t_start = time.time()
                try:
                    text = await self._transcribe(audio)
                except Exception as e:               # noqa: BLE001
                    print(f"[ear] {type(e).__name__}: {e}")
                    self._reset()
                    self._fails += 1
                    vitals.trace("ear", "error", "transcribe failed",
                                 detail=f"{type(e).__name__}: {e}"[:160])
                    # Three in a row is not a bad clip, it is a broken ear. Reload
                    # the model rather than fail silently on every utterance.
                    if self._fails >= 3:
                        print("[ear] 3 failures in a row — reloading the model")
                        self._fails = 0
                        await self._rewarm()
                    continue
                self._fails = 0
                took_ms = (time.time() - t_start) * 1000
                # Mid-thought: they paused for breath, not for a full stop. Keep
                # the audio and let them finish. MAX_UTTERANCE_S is the backstop,
                # so a thought that never lands still reaches the screen.
                if text and secs < HOLD_MAX_S and not _complete(text):
                    self._held = self._samples
                    self._interim_text = text
                    self.broadcast(ops.status("listening", text))
                    vitals.trace("ear", "hold", f"EAR_HOLD_MAX {HOLD_MAX_S}s",
                                 text=text, ms=took_ms,
                                 detail="thought unfinished — letting them finish")
                    continue
                spec = self._spec          # read before _reset clears it
                self._reset()
                if not text:
                    vitals.trace("ear", "drop", "empty transcript", ms=took_ms,
                                 detail=f"{secs:.1f}s of audio, no words")
                    continue
                if _invented(text):
                    print(f"[ear] {secs:.1f}s dropped (invented) -> {text[:60]!r}")
                    vitals.trace("ear", "drop", "invented (silence artefact)",
                                 text=text, ms=took_ms,
                                 detail=f"{secs:.1f}s — Whisper filled in silence")
                    continue
                self.utterances += 1
                vitals.trace("ear", "ok",
                             "utterance" + (" (refine)" if spec else ""),
                             text=text, ms=took_ms, detail=f"{secs:.1f}s of audio",
                             count="heard")
                print(f"[ear] {secs:.1f}s ->{' (refine)' if spec else ''} "
                      f"{text[:90]!r}")
                self.broadcast(ops.status("listening", text))
                if self.brain is not None:
                    # complete=True: we own the sentence boundary, so the brain
                    # must not debounce it a second time.
                    self.brain.feed(text, complete=True, refine=spec)
                if self.memory is not None:
                    self.memory.add_utterance(text + " ")
                continue

            # Still talking: refresh the ticker so the room can see it is alive,
            # and cut on a finished sentence so a long talker still gets visuals.
            if time.time() - self._last_interim >= INTERIM_EVERY_S and secs >= 0.8:
                self._last_interim = time.time()
                try:
                    text = await self._transcribe(self._audio())
                except Exception as e:               # noqa: BLE001
                    vitals.trace("ear", "error", "interim transcribe failed",
                                 detail=f"{type(e).__name__}: {e}"[:160],
                                 throttle=5.0)
                    continue
                if not text or text == self._interim_text:
                    continue
                self._interim_text = text
                self.broadcast(ops.status("listening", text))

                if SEND_ON_SENTENCE and text.rstrip()[-1:] in ".!?" \
                        and len(text.split()) >= SENTENCE_MIN_WORDS \
                        and not _invented(text):
                    # A closed sentence: hand it over and start a fresh buffer. The
                    # speaker does not have to pause to be heard.
                    spec = self._spec
                    self._reset()
                    self.utterances += 1
                    print(f"[ear] {secs:.1f}s (sentence)"
                          f"{' (refine)' if spec else ''} -> {text[:80]!r}")
                    vitals.trace("ear", "ok",
                                 f"closed sentence (EAR_SENTENCE_WORDS "
                                 f"{SENTENCE_MIN_WORDS})",
                                 text=text, detail=f"{secs:.1f}s, still talking",
                                 count="heard")
                    if self.brain is not None:
                        self.brain.feed(text, complete=True, refine=spec)
                    if self.memory is not None:
                        self.memory.add_utterance(text + " ")
                    continue

                # SPECULATIVE DRAW — the whole point of Tier 2. Something lands on
                # screen while the sentence is still being said, and the final
                # transcript refines that same block in place a moment later. Once
                # per utterance, so the cost is bounded at two calls, not one per
                # interim tick.
                if (SPECULATE and self.brain is not None and not self._spec
                        and len(text.split()) >= SPEC_MIN_WORDS
                        and not _invented(text)):
                    self._spec = True
                    print(f"[ear] {secs:.1f}s (early) -> {text[:70]!r}")
                    vitals.trace("ear", "ok",
                                 f"speculative draw (EAR_SPEC_WORDS "
                                 f"{SPEC_MIN_WORDS})",
                                 text=text, detail="mid-sentence, will be refined")
                    self.brain.feed(text, complete=True)
