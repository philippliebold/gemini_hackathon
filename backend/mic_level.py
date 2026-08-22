"""Measure the room, then pick a gate. Run this before a demo in a new space.

    python backend/mic_level.py            # 5s of the default input
    python backend/mic_level.py --device 1 --seconds 8

Sit quiet for the first half, talk for the second. It prints both floors and the
gate that separates them, which is the single most important knob for whether the
screen stays blank when nobody is saying anything.
"""
import argparse

import numpy as np
import sounddevice as sd

import mics
from config import CFG


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--seconds", type=float, default=5.0)
    a = p.parse_args()

    print(f"recording {a.seconds:g}s — stay quiet, then talk for the second half")
    rec = sd.rec(int(a.seconds * CFG.sample_rate), samplerate=CFG.sample_rate,
                 channels=1, dtype="float32", device=a.device)
    sd.wait()
    x = rec[:, 0]

    frame = int(CFG.sample_rate * CFG.chunk_ms / 1000)
    fr = x[:len(x) // frame * frame].reshape(-1, frame)
    lv = np.sqrt((fr ** 2).mean(axis=1))

    print("\n  " + "".join("▁▂▃▄▅▆▇█"[min(7, int(v * 60))] for v in lv[::5]))
    quiet, loud = np.percentile(lv, 10), np.percentile(lv, 90)
    print(f"\n  quiet floor (p10): {quiet:.4f}")
    print(f"  speech    (p90): {loud:.4f}")
    print(f"  current gate    : {mics.GATE_RMS:.4f}")

    if loud < quiet * 2:
        print("\n  Not much difference between quiet and loud — did anyone talk?")
        return
    suggest = quiet + (loud - quiet) * 0.25
    print(f"\n  suggested        : MIC_GATE={suggest:.4f}")
    if mics.GATE_RMS <= quiet:
        print("  Your gate is AT OR BELOW the room floor: it will draw from noise.")
    elif mics.GATE_RMS >= loud:
        print("  Your gate is ABOVE your speech level: it will never hear you.")
    else:
        print("  Current gate sits between the two — good.")


if __name__ == "__main__":
    main()
