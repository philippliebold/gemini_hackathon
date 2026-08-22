"""Why the screen stayed blank.

Every stage of the pipeline can decide not to draw — the mic gate, the utterance
floor, Whisper's invention guard, the brain's salience check, the per-key cooldown,
the form lock. Before this module all of those were `print()` at best, so from the
screen a dead Whisper model, an exhausted quota, a cooldown and "the model chose
silence" were the same thing: nothing happening.

Two jobs:

    trace()     one frame per decision, so the console can answer "why not?"
    snapshot()  the vitals, so the console can answer "is it even alive?"

Nothing here imports server: `BROADCAST` is installed by main.py the same way
tools.BROADCAST is, which keeps this importable from the audio hot path.
"""
import time
from collections import Counter
from typing import Any, Callable

import ops

# Installed by main.py. None in the offline harness, where traces are collected
# locally instead — see replay.py.
BROADCAST: Callable[[dict], None] | None = None

# Live state, written by whoever owns the thing. One dict so the health payload has
# a single source and no module has to be interrogated for it.
#   ear:    "warming" | "ready" | "dead" | "off"    (ears_local.LocalEar)
#   brain:  model name, or None when nothing usable answered   (brain.Brain)
STATE: dict[str, Any] = {
    "ear": "off",
    "ear_model": None,
    "ear_error": None,
    "brain_model": None,
    "brain_fallback": False,
    "brain_error": None,
    "audio_queued": 0,
    "audio_capacity": 0,
    "loop_lag_ms": 0.0,
}

COUNTS: Counter = Counter()

# A trace per dropped audio frame would be 50 frames a second. High-frequency
# callers pass throttle=; the suppressed ones are counted and reported as `n` on the
# next frame that gets through, so the log stays readable without lying about volume.
_last_at: dict[tuple[str, str], float] = {}
_suppressed: dict[tuple[str, str], int] = {}


def reset() -> None:
    """Between rehearsals: the counters describe this take, not the last one."""
    COUNTS.clear()
    _last_at.clear()
    _suppressed.clear()


def bump(name: str, n: int = 1) -> None:
    COUNTS[name] += n


def trace(stage: str, verdict: str, reason: str, *, text: str | None = None,
          ms: float | None = None, detail: str | None = None,
          throttle: float = 0.0, count: str | None = None) -> None:
    """Record a decision and tell the console about it.

    `reason` should name the constant that made the call and its value — "EAR_MIN
    0.8s" rather than "too short" — because the next question is always which knob
    to turn. `count` bumps an extra semantic counter on top of the automatic
    "<stage>.<verdict>" one.
    """
    COUNTS[f"{stage}.{verdict}"] += 1
    if count:
        COUNTS[count] += 1

    n = 1
    if throttle > 0:
        k = (stage, reason)
        now = time.time()
        # `None` rather than 0.0 for "never seen": comparing against the epoch
        # happens to work for small windows and silently swallows the FIRST event
        # of a burst for large ones — the one event you most want to see.
        seen_at = _last_at.get(k)
        if seen_at is not None and now - seen_at < throttle:
            _suppressed[k] = _suppressed.get(k, 0) + 1
            return
        _last_at[k] = now
        n = 1 + _suppressed.pop(k, 0)

    if BROADCAST is None:
        return
    try:
        BROADCAST(ops.trace(stage, verdict, reason, text=text, ms=ms,
                            detail=detail, n=n))
    except Exception as e:                                   # noqa: BLE001
        # A diagnostic channel must never be the thing that takes the talk down.
        print(f"[vitals] trace failed: {type(e).__name__}: {e}")


def snapshot(*, listening: bool, blocks: int) -> dict:
    """The health payload. Callers own `listening` and `blocks` because importing
    runtime and canvas from here would drag them into the audio hot path."""
    return {
        "ear": {"state": STATE["ear"], "model": STATE["ear_model"],
                "error": STATE["ear_error"]},
        "brain": {"model": STATE["brain_model"],
                  "fallback": bool(STATE["brain_fallback"]),
                  "error": STATE["brain_error"]},
        "listening": bool(listening),
        "audio": {"queued": STATE["audio_queued"],
                  "capacity": STATE["audio_capacity"],
                  "dropped": COUNTS.get("audio.dropped", 0)},
        "loop_lag_ms": round(STATE["loop_lag_ms"], 1),
        "blocks": blocks,
        "counts": dict(COUNTS),
    }
