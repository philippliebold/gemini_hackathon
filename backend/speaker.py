"""Phone in, Mac's speakers out. The phone becomes a wireless microphone.

Deliberately fed with RAW phone frames, before the floor gate. The gate exists to
keep noise away from the model; applying it to the audible path would chop the PA
every time a voice dipped, which sounds broken. What the room hears should be
everything the phone sent.

Latency is one small jitter buffer on top of the network hop — the phone already
sends 20 ms frames, so this adds ~40-60 ms and nothing else. Playback runs on
PortAudio's own thread; the event loop only appends bytes.

FEEDBACK: if the Mac's mic is open while this is playing, the mic hears the speakers
and sends it back. Turn the Mac mic off (the dock does this automatically) or use
headphones for monitoring.
"""
import collections
import threading

import numpy as np
import sounddevice as sd

from config import CFG

# Three 20 ms frames. Enough to ride out WiFi jitter, short enough that nobody
# notices; more buffer is more delay and this is a live PA.
TARGET_FRAMES = 3
MAX_FRAMES = 12          # beyond this we are behind, so drop rather than drift


def output_devices() -> list[dict]:
    out = []
    try:
        default = sd.default.device[1]
    except Exception:                                    # noqa: BLE001
        default = None
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_output_channels", 0) > 0:
                out.append({"index": i, "name": d.get("name", f"Output {i}"),
                            "default": i == default})
    except Exception as e:                               # noqa: BLE001
        print(f"[spk] could not list outputs: {e}")
    return out


class Speaker:
    def __init__(self) -> None:
        self.device: int | None = None
        self.active = False
        self.gain = 1.0
        # Per-mic gain, applied only on the AUDIBLE path. Transcription keeps the
        # original samples: turning a phone down in the room must not make it
        # harder for the model to hear.
        self.gains: dict[str, float] = {}
        self._buf: collections.deque[bytes] = collections.deque()
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        self.dropped = 0

    # --- audio thread --------------------------------------------------------
    def _cb(self, outdata, frames, _t, status):
        if status:
            pass                                          # underruns are audible enough
        want = frames * 2                                 # int16 mono
        chunk = b""
        with self._lock:
            while len(chunk) < want and self._buf:
                chunk += self._buf.popleft()
            if len(chunk) > want:                         # push the remainder back
                self._buf.appendleft(chunk[want:])
                chunk = chunk[:want]
        if len(chunk) < want:
            chunk += b"\x00" * (want - len(chunk))         # silence beats a click
        a = np.frombuffer(chunk, dtype="<i2")
        if self.gain != 1.0:
            a = np.clip(a.astype(np.float32) * self.gain, -32768, 32767).astype(np.int16)
        outdata[:] = a.reshape(-1, 1)

    # --- called from the event loop (must stay trivial) ---------------------
    def set_gain(self, mic_id: str, gain: float) -> float:
        g = max(0.0, min(3.0, float(gain)))
        self.gains[mic_id] = g
        return g

    def gain_of(self, mic_id: str) -> float:
        return self.gains.get(mic_id, 1.0)

    def feed(self, pcm: bytes, mic_id: str | None = None) -> None:
        if not self.active:
            return
        g = self.gains.get(mic_id or "", 1.0)
        if g != 1.0:
            a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * g
            pcm = np.clip(a, -32768, 32767).astype(np.int16).tobytes()
        with self._lock:
            self._buf.append(pcm)
            while len(self._buf) > MAX_FRAMES:            # we are behind; catch up
                self._buf.popleft()
                self.dropped += 1

    # --- control ------------------------------------------------------------
    def start(self, device: int | None = None) -> bool:
        self.stop()
        self.device = device
        try:
            self._stream = sd.OutputStream(
                samplerate=CFG.sample_rate, channels=1, dtype="int16",
                blocksize=int(CFG.sample_rate * CFG.chunk_ms / 1000),
                device=device, callback=self._cb, latency="low")
            self._stream.start()
            self.active = True
            name = next((d["name"] for d in output_devices()
                         if d["index"] == device), "default")
            print(f"[spk] playing phone audio through {name}")
            return True
        except Exception as e:                            # noqa: BLE001
            print(f"[spk] could not open output {device}: {e}")
            self._stream = None
            self.active = False
            return False

    def stop(self) -> None:
        self.active = False
        with self._lock:
            self._buf.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:                             # noqa: BLE001
                pass
            self._stream = None
            print("[spk] stopped")

    def state(self) -> dict:
        with self._lock:
            depth = len(self._buf)
        return {"active": self.active, "device": self.device,
                "buffered_ms": depth * CFG.chunk_ms, "dropped": self.dropped,
                "devices": output_devices()}
