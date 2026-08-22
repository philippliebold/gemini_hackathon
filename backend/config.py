"""Single place for env config. Import CFG, never os.environ directly."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

# override=True on purpose: .env is where this project's config lives, so editing it
# must take effect. The default leaves a stale shell variable winning silently, which
# means rotating a key in .env appears to do nothing.
load_dotenv(override=True)


@dataclass(frozen=True)
class Config:
    api_key: str = os.getenv("GEMINI_API_KEY", "")
    live_model: str = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
    # Live models only emit AUDIO. We discard it — see gemini_live.build_config.
    live_modality: str = os.getenv("GEMINI_LIVE_MODALITY", "AUDIO")
    # Drive turn boundaries from our own gate instead of the model's VAD. The
    # model's own detection works on a live mic but never fires on a replayed PCM
    # feed, so set MANUAL_ACTIVITY=1 for --pcm rehearsals.
    manual_activity: bool = os.getenv("MANUAL_ACTIVITY", "0").lower() \
        in ("1", "true", "yes")
    model: str = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
    # Measured 0.95 s with thinking off while 3.7-flash timed out past 25 s under
    # load. Used whenever the primary is too slow to keep up with a talk.
    model_fast: str = os.getenv("GEMINI_MODEL_FAST", "gemini-3.1-flash-lite")
    # Tried in order, only if the primary is unusable at startup (429 quota, 503).
    model_fallback: str = os.getenv("GEMINI_MODEL_FALLBACK", "gemini-3.6-flash")
    model_fallback2: str = os.getenv("GEMINI_MODEL_FALLBACK2", "gemini-3.5-flash")
    image_model: str = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    maps_key: str = os.getenv("GOOGLE_MAPS_API_KEY", "")
    # Bias geocoding to the venue's country. Without it a bare place name like
    # "one-north" matches somewhere on the other side of the planet: the first
    # generated demo routed it 6,135 km.
    maps_region: str = os.getenv("MAPS_REGION", "SG")
    # Speakers say "one-north", not "one-north, Singapore". Appended to any
    # address that does not already name a place, so local shorthand resolves.
    maps_near: str = os.getenv("MAPS_NEAR", "Singapore")
    ws_host: str = os.getenv("WS_HOST", "127.0.0.1")
    ws_port: int = int(os.getenv("WS_PORT", "8765"))

    # Phone mics. Binds 0.0.0.0 because the phones are on the LAN, not localhost.
    mic_host: str = os.getenv("MIC_HOST", "0.0.0.0")
    mic_port: int = int(os.getenv("MIC_PORT", "8766"))
    # Tell the model who just took the floor. Off if it ever disturbs turn-taking.
    announce_speakers: bool = os.getenv("ANNOUNCE_SPEAKERS", "1").lower() \
        not in ("0", "false", "no")

    # Audio: the Live API wants raw little-endian 16-bit PCM at 16 kHz.
    sample_rate: int = 16000
    channels: int = 1
    chunk_ms: int = 20


CFG = Config()
