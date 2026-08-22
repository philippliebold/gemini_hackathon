"""Single place for env config. Import CFG, never os.environ directly."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


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
    # Tried in order, only if the primary is unusable at startup (429 quota, 503).
    model_fallback: str = os.getenv("GEMINI_MODEL_FALLBACK", "gemini-3.6-flash")
    model_fallback2: str = os.getenv("GEMINI_MODEL_FALLBACK2", "gemini-3.5-flash")
    image_model: str = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    maps_key: str = os.getenv("GOOGLE_MAPS_API_KEY", "")
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
