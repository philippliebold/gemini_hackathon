"""Mic -> 16 kHz mono little-endian 16-bit PCM chunks on an asyncio queue.

The Live API wants exactly that format (mime type "audio/pcm;rate=16000").
"""
import asyncio
import sys

import numpy as np
import sounddevice as sd

from config import CFG


def list_devices() -> str:
    return str(sd.query_devices())


async def mic_chunks(queue: asyncio.Queue, device: int | None = None):
    """Push raw PCM bytes onto `queue`. Runs until cancelled."""
    loop = asyncio.get_running_loop()
    frames = int(CFG.sample_rate * CFG.chunk_ms / 1000)

    def cb(indata, _frames, _time, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
        loop.call_soon_threadsafe(queue.put_nowait, pcm)

    with sd.InputStream(samplerate=CFG.sample_rate, channels=CFG.channels,
                        dtype="float32", blocksize=frames, device=device,
                        callback=cb):
        print(f"[audio] mic open @ {CFG.sample_rate} Hz, {CFG.chunk_ms} ms chunks")
        while True:
            await asyncio.sleep(0.1)


async def file_chunks(queue: asyncio.Queue, path: str):
    """Replay a raw 16 kHz PCM file instead of the mic. Use in a loud room."""
    frames = int(CFG.sample_rate * CFG.chunk_ms / 1000) * 2  # 2 bytes/sample
    with open(path, "rb") as fh:
        while chunk := fh.read(frames):
            await queue.put(chunk)
            await asyncio.sleep(CFG.chunk_ms / 1000)


if __name__ == "__main__":
    print(list_devices())
