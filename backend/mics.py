"""Multi-mic floor control: many phones in, one clean audio stream out.

Four phones in one room is four noise floors. Summing them would hand the model a
mush of everyone's air conditioning, and two devices near each other would feed it
the same voice twice with a few ms of skew — which is exactly the echo/cross-talk
risk IDEA.md flags.

So we do not mix. At any instant exactly ONE mic holds the floor and only its audio
is forwarded. That buys three things at once:
  * clean audio — one voice, one noise floor
  * attribution for free — we always know who is talking
  * no extra latency — selection is a comparison, not a resample or a sum

Nobody gets cut off mid-word: the holder keeps the floor through a hangover window
after it goes quiet, and a clearly louder mic can steal it immediately so an
interjection still lands.

This module is pure logic — no sockets, no audio device. See mic_server.py.
"""
import itertools
import os
import time
from dataclasses import dataclass, field

import numpy as np

MAX_MICS = 4

# Tuning. Defaults are for phones held at conversational distance in a loud room.
# Measured in this room over 40 s, from the live roster rather than guessed:
#   room noise   0.0044 -> 0.0171
#   real speech  0.0185 -> 0.0616
# Those bands nearly touch, so this gate cannot cleanly separate them. It is set
# LOW on purpose and only decides who *takes* the floor — once taken, audio flows
# continuously (see accept()). A gate high enough to reject all noise also chops
# the quiet parts out of real sentences, which is far worse: the model then hears
# fragments and transcribes nothing.
GATE_RMS = float(os.getenv("MIC_GATE", "0.014"))
HANGOVER_S = 0.70       # holder keeps the FLOOR this long after going quiet
TURN_SILENCE_S = 0.35   # ...but the TURN closes this early. See below.
STEAL_MARGIN = 1.8      # a rival must be this much louder to interrupt

# Floor retention and turn length are deliberately different numbers. Holding the
# floor for 0.7 s stops a neighbour's mic stealing your sentence during a breath.
# But the model only answers once a turn CLOSES, so waiting 0.7 s to close would
# add 0.7 s to every visual. Closing at 0.35 s — a natural inter-sentence pause —
# gets the answer moving while you keep your claim on the floor.

_n = itertools.count(1)


@dataclass
class Mic:
    id: str
    label: str
    joined: float
    frames: int = 0
    bytes_in: int = 0
    last_rms: float = 0.0
    last_voice: float = 0.0
    stolen: int = 0          # times this mic took the floor
    gain: float = 1.0        # audible level in the room; never affects transcription
    muted: bool = False      # set by the phone; nothing it sends is used

    @property
    def talking(self) -> bool:
        return time.time() - self.last_voice < HANGOVER_S


class MicFull(Exception):
    """Fifth mic tried to join."""


def rms_of(pcm: bytes) -> float:
    """Root-mean-square of a 16-bit LE PCM frame, normalised to 0..1."""
    if not pcm:
        return 0.0
    a = np.frombuffer(pcm, dtype=np.int16)
    if a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(a.astype(np.float32) ** 2)) / 32768.0)


def set_gate(value: float) -> float:
    """Change the speech gate while the talk is running.

    Every room is different and the only honest calibration is a human watching
    their own level against the threshold, so this is exposed on the screen rather
    than buried in an env var. Clamped to where the value still means something:
    0 forwards everything, above ~0.12 rejects normal speech.
    """
    global GATE_RMS
    GATE_RMS = max(0.0, min(0.12, float(value)))
    return GATE_RMS


class Floor:
    """Decides whose audio reaches the model."""

    def __init__(self, gate: float = GATE_RMS, hangover: float = HANGOVER_S,
                 steal_margin: float = STEAL_MARGIN,
                 turn_silence: float = TURN_SILENCE_S,
                 gate_turns: bool | None = None) -> None:
        self.mics: dict[str, Mic] = {}
        self.holder: str | None = None
        self._gate_override = gate if gate != GATE_RMS else None
        self.hangover = hangover
        self.steal_margin = steal_margin
        self.turn_silence = turn_silence
        # Only chop the stream into turns when WE drive the turn boundaries.
        from config import CFG as _CFG
        self.gate_turns = _CFG.manual_activity if gate_turns is None else gate_turns
        self.switches = 0
        self.announced: str | None = None
        self.turn_open = False

    @property
    def gate(self) -> float:
        """Follow the module-level gate unless this Floor was given its own."""
        return GATE_RMS if self._gate_override is None else self._gate_override

    # --- roster -------------------------------------------------------------
    def join(self, label: str | None = None) -> Mic:
        if len(self.mics) >= MAX_MICS:
            raise MicFull(f"already {MAX_MICS} mics connected")
        i = next(_n)
        mic = Mic(id=f"m_{i}", label=(label or "").strip() or f"Mic {i}",
                  joined=time.time())
        self.mics[mic.id] = mic
        return mic

    def leave(self, mic_id: str) -> None:
        self.mics.pop(mic_id, None)
        if self.holder == mic_id:
            self.holder = None
        if self.announced == mic_id:
            self.announced = None

    # --- the decision -------------------------------------------------------
    def accept(self, mic_id: str, pcm: bytes) -> tuple[bool, list[tuple[str, str]]]:
        """Should this frame be forwarded, and what should be told to the model?

        Returns (forward, events). Events are queue items the caller emits BEFORE
        the frame, in order:

            ("activity", "start")  a turn opens  — someone began speaking
            ("activity", "end")    a turn closes — the model may now answer
            ("speaker",  "Yufei")  the voice changed identity

        The activity events are not decoration. `gemini-3.1-flash-live-preview`'s
        own voice-activity detection does not fire on a streamed feed at all — with
        it enabled the model transcribes nothing and calls nothing. Driving activity
        from this gate is what makes it hear the room, and the floor logic already
        knows where an utterance starts and stops. See MICS.md.

        Audio is only forwarded while a turn is open: with automatic detection off,
        anything sent outside a turn is not attributed to one.
        """
        mic = self.mics.get(mic_id)
        if mic is None:
            return False, []
        if mic.muted:
            # Muted means muted: not forwarded, not levelled, and it gives up the
            # floor so a muted phone cannot sit on the stream.
            mic.last_rms = 0.0
            if self.holder == mic_id:
                self.holder = None
                if self.turn_open:
                    self.turn_open = False
                    return False, [("activity", "end")]
            return False, []

        now = time.time()
        level = rms_of(pcm)
        mic.frames += 1
        mic.bytes_in += len(pcm)
        mic.last_rms = level
        events: list[tuple[str, str]] = []

        if level >= self.gate:
            mic.last_voice = now
            prev = self.holder
            took = False
            if prev is None or prev not in self.mics:
                self.holder, took = mic_id, True
            elif prev != mic_id:
                held = self.mics[prev]
                if (now - held.last_voice) > self.hangover \
                        or level > held.last_rms * self.steal_margin:
                    self.holder, took = mic_id, True

            if took:
                self.switches += 1
                mic.stolen += 1
                if self.turn_open:           # close the previous voice's turn first
                    events.append(("activity", "end"))
                    self.turn_open = False
                # Only worth naming when there is more than one voice, and only
                # when it actually changed — otherwise every pause re-announces.
                if len(self.mics) > 1 and self.announced != mic_id:
                    events.append(("speaker", mic.label))
                    self.announced = mic_id

            if self.holder != mic_id:
                return False, events        # someone else owns the stream
            if not self.turn_open:
                events.append(("activity", "start"))
                self.turn_open = True
            return True, events

        # Below the gate.
        if self.holder != mic_id:
            return False, events
        quiet = now - mic.last_voice
        if self.turn_open and quiet > self.turn_silence:
            # Close the *turn* (only meaningful in manual mode).
            events.append(("activity", "end"))
            self.turn_open = False
        if quiet > self.hangover:
            self.holder = None
            return False, events

        # Still holding the floor. KEEP FORWARDING unless we are the ones deciding
        # turn boundaries. A speaking voice dips below any usable gate constantly —
        # between words, on unstressed syllables — and cutting the stream at each
        # dip hands the model shredded audio it cannot transcribe. When the model
        # runs its own VAD (the default) it needs a continuous stream and finds the
        # sentence ends itself; our job is only to pick whose mic to send.
        return (self.turn_open if self.gate_turns else True), events

    # --- reads --------------------------------------------------------------
    def roster(self) -> list[dict]:
        return [{"id": m.id, "label": m.label, "holding": self.holder == m.id,
                 "talking": m.talking, "frames": m.frames,
                 "rms": round(m.last_rms, 4), "gain": m.gain, "muted": m.muted}
                for m in sorted(self.mics.values(), key=lambda x: x.joined)]

    def summary(self) -> str:
        if not self.mics:
            return "no mics connected"
        return " | ".join(
            f"{'▶' if self.holder == m.id else ' '}{m.label}"
            f" {'▁▂▃▄▅▆▇█'[min(7, int(m.last_rms * 40))]}"
            for m in sorted(self.mics.values(), key=lambda x: x.joined))
