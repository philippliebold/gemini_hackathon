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
import time
from collections.abc import Callable

import numpy as np

import mics
import ops
from config import CFG

import os

MODEL = os.getenv("WHISPER_MODEL", "mlx-community/whisper-small-mlx")
TICK_S = 0.3             # how often we look at the buffer
INTERIM_EVERY_S = 0.9    # re-transcribe the live utterance this often, for the ticker
# A sentence boundary in real speech is a ~0.4 s pause. Shorter cuts mid-thought;
# longer makes the screen feel like it is lagging behind the room.
FINAL_GAP_S = float(os.getenv("EAR_GAP", "0.45"))
# Hard cap on how long a visual can be held back by someone who does not pause.
MAX_UTTERANCE_S = float(os.getenv("EAR_MAX", "7.0"))
MIN_UTTERANCE_S = 0.45   # shorter than this is a cough
# Whisper punctuates. If the interim transcript has already closed a sentence, send
# it now rather than waiting for the speaker to stop — you should be able to keep
# talking and still have the last thought land.
SEND_ON_SENTENCE = os.getenv("EAR_SENTENCE", "1").lower() in ("1", "true", "yes")
SENTENCE_MIN_WORDS = 4


class LocalEar:
    def __init__(self, broadcast: Callable[[dict], None], brain=None, memory=None):
        self.broadcast, self.brain, self.memory = broadcast, brain, memory
        self._buf: list[bytes] = []
        self._samples = 0
        self._last_voice = 0.0
        self._last_interim = 0.0
        self._interim_text = ""
        self.utterances = 0
        self._ready = False

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
        self._interim_text = ""

    # --- transcription ------------------------------------------------------
    async def _transcribe(self, audio: np.ndarray) -> str:
        import mlx_whisper
        r = await asyncio.to_thread(
            mlx_whisper.transcribe, audio, path_or_hf_repo=MODEL,
            language="en", fp16=True, condition_on_previous_text=False)
        return (r.get("text") or "").strip()

    async def warm(self) -> None:
        """Load the model before the talk starts; the first call is otherwise slow."""
        try:
            t0 = time.time()
            await self._transcribe(np.zeros(16000, dtype=np.float32))
            self._ready = True
            print(f"[ear] local transcription ready ({MODEL}) in {time.time()-t0:.1f}s")
        except Exception as e:                       # noqa: BLE001
            print(f"[ear] local transcription unavailable: {type(e).__name__}: {e}")

    async def loop(self) -> None:
        await self.warm()
        if not self._ready:
            return
        while True:
            await asyncio.sleep(TICK_S)
            if not self._samples:
                continue
            secs = self._samples / CFG.sample_rate
            quiet = time.time() - self._last_voice

            # Utterance finished: they stopped, or they have gone on long enough.
            if (quiet >= FINAL_GAP_S and secs >= MIN_UTTERANCE_S) \
                    or secs >= MAX_UTTERANCE_S:
                audio = self._audio()
                self._reset()
                # drop the trailing pause: it is not speech and Whisper sometimes
                # hallucinates filler into it
                keep = int(len(audio) - (quiet - FINAL_GAP_S / 2) * CFG.sample_rate)
                if 0 < keep < len(audio):
                    audio = audio[:keep]
                try:
                    text = await self._transcribe(audio)
                except Exception as e:               # noqa: BLE001
                    print(f"[ear] {type(e).__name__}: {e}")
                    continue
                if not text:
                    continue
                self.utterances += 1
                print(f"[ear] {secs:.1f}s -> {text[:90]!r}")
                self.broadcast(ops.status("listening", text))
                if self.brain is not None:
                    self.brain.feed(text)
                if self.memory is not None:
                    self.memory.add_utterance(text + " ")
                continue

            # Still talking: refresh the ticker so the room can see it is alive,
            # and cut on a finished sentence so a long talker still gets visuals.
            if time.time() - self._last_interim >= INTERIM_EVERY_S and secs >= 0.8:
                self._last_interim = time.time()
                try:
                    text = await self._transcribe(self._audio())
                except Exception:                    # noqa: BLE001
                    continue
                if not text or text == self._interim_text:
                    continue
                self._interim_text = text
                self.broadcast(ops.status("listening", text))

                if SEND_ON_SENTENCE and text.rstrip()[-1:] in ".!?" \
                        and len(text.split()) >= SENTENCE_MIN_WORDS:
                    # A closed sentence: hand it over and start a fresh buffer. The
                    # speaker does not have to pause to be heard.
                    self._reset()
                    self.utterances += 1
                    print(f"[ear] {secs:.1f}s (sentence) -> {text[:80]!r}")
                    if self.brain is not None:
                        self.brain.feed(text)
                    if self.memory is not None:
                        self.memory.add_utterance(text + " ")
