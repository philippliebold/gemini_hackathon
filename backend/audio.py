"""Mic -> 16 kHz mono little-endian 16-bit PCM chunks on an asyncio queue.

The Live API wants exactly that format (mime type "audio/pcm;rate=16000").

Queue items are tagged tuples, because phone mics put speaker changes on the same
queue and ordering against the audio matters:

    ("audio",   pcm_bytes)
    ("speaker", "Yufei")     # this person just took the floor

Pass a `mics.Floor` and the Mac's own mic becomes just another mic competing for
the floor alongside the phones. Omit it and everything is forwarded, which is the
single-mic behaviour.
"""
import asyncio
import sys

import numpy as np
import sounddevice as sd

from config import CFG


def list_devices() -> str:
    return str(sd.query_devices())


def offer(queue: asyncio.Queue, item) -> None:
    """Queue or drop. A full queue means we are ~4 s behind; stale audio is worse
    than a gap, and blocking here would stall the whole capture path."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        pass


async def mic_chunks(queue: asyncio.Queue, device: int | None = None,
                     floor=None, label: str = "Mac mic"):
    """Push tagged PCM onto `queue`. Runs until cancelled."""
    loop = asyncio.get_running_loop()
    frames = int(CFG.sample_rate * CFG.chunk_ms / 1000)
    mic = floor.join(label) if floor is not None else None

    def submit(pcm: bytes) -> None:
        # Runs on the event loop, so all Floor mutation stays single-threaded even
        # though the capture callback below fires on the audio thread.
        if floor is None:
            offer(queue, ("audio", pcm))
            return
        forward, events = floor.accept(mic.id, pcm)
        for ev in events:          # turn boundaries must precede their audio
            offer(queue, ev)
        if forward:
            offer(queue, ("audio", pcm))

    def cb(indata, _frames, _time, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
        loop.call_soon_threadsafe(submit, pcm)

    with sd.InputStream(samplerate=CFG.sample_rate, channels=CFG.channels,
                        dtype="float32", blocksize=frames, device=device,
                        callback=cb):
        print(f"[audio] mic open @ {CFG.sample_rate} Hz, {CFG.chunk_ms} ms chunks")
        while True:
            await asyncio.sleep(0.1)


async def file_chunks(queue: asyncio.Queue, path: str, floor=None,
                     label: str = "Recording"):
    """Replay a raw 16 kHz PCM file instead of the mic. Use in a loud room.

    Goes through the same Floor as a live mic, so a replay produces the same turn
    boundaries the model needs — otherwise `--pcm` would feed audio the model never
    gets told to listen to.
    """
    frames = int(CFG.sample_rate * CFG.chunk_ms / 1000) * 2  # 2 bytes/sample
    mic = floor.join(label) if floor is not None else None
    with open(path, "rb") as fh:
        while chunk := fh.read(frames):
            if floor is None:
                await queue.put(("audio", chunk))
            else:
                forward, events = floor.accept(mic.id, chunk)
                for ev in events:
                    offer(queue, ev)
                if forward:
                    offer(queue, ("audio", chunk))
            await asyncio.sleep(CFG.chunk_ms / 1000)
    if floor is not None:
        offer(queue, ("activity", "end"))     # close the final turn at EOF
        print(f"[audio] {path} finished")


if __name__ == "__main__":
    print(list_devices())


def input_devices() -> list[dict]:
    """Real input devices on this machine, for the screen's mic picker."""
    out = []
    try:
        default = sd.default.device[0]
    except Exception:                                    # noqa: BLE001
        default = None
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                out.append({"index": i, "name": d.get("name", f"Input {i}"),
                            "default": i == default})
    except Exception as e:                               # noqa: BLE001
        print(f"[audio] could not list devices: {e}")
    return out


class MacMic:
    """The Mac's own input, switchable while the talk is running.

    The original code opened one device at startup and that was it. The dock needs
    to change or drop it mid-setup, so the stream lives in a supervised task that
    tears down and reopens on request. It joins the Floor only while active, so a
    disabled Mac mic cannot hold the floor against the phones.
    """

    def __init__(self, queue: asyncio.Queue, floor=None, label: str = "Mac mic"):
        self.queue, self.floor, self.label = queue, floor, label
        self.device: int | None = None
        self.active = False
        self._change = asyncio.Event()

    def select(self, device: int | None) -> None:
        self.device = device
        self.active = device is not None
        self._change.set()

    def off(self) -> None:
        self.select(None)

    async def run(self) -> None:
        while True:
            self._change.clear()
            if not self.active:
                await self._change.wait()
                continue
            # Name the chip after the real device: the dock shows this label, and
            # "Mac mic" is a lie once you have picked the iPhone or an interface.
            name = next((d["name"] for d in input_devices()
                         if d["index"] == self.device), None)
            label = name or self.label
            mic = self.floor.join(label) if self.floor is not None else None
            loop = asyncio.get_running_loop()
            frames = int(CFG.sample_rate * CFG.chunk_ms / 1000)

            def submit(pcm: bytes) -> None:
                if self.floor is None:
                    offer(self.queue, ("audio", pcm))
                    return
                forward, events = self.floor.accept(mic.id, pcm)
                for ev in events:
                    offer(self.queue, ev)
                if forward:
                    offer(self.queue, ("audio", pcm))

            def cb(indata, _f, _t, status_):
                if status_:
                    print(f"[audio] {status_}", file=sys.stderr)
                pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
                loop.call_soon_threadsafe(submit, pcm)

            try:
                with sd.InputStream(samplerate=CFG.sample_rate, channels=CFG.channels,
                                    dtype="float32", blocksize=frames,
                                    device=self.device, callback=cb):
                    print(f"[audio] Mac mic open on device {self.device}")
                    await self._change.wait()
            except Exception as e:                       # noqa: BLE001
                print(f"[audio] could not open device {self.device}: {e}")
                self.active = False
                await asyncio.sleep(0.5)
            finally:
                if mic is not None:
                    self.floor.leave(mic.id)
                print("[audio] Mac mic closed")
