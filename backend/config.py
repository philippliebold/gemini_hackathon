"""Single place for env config. Import CFG, never os.environ directly."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    api_key: str = os.getenv("GEMINI_API_KEY", "")
    live_model: str = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
    model: str = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
    image_model: str = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    maps_key: str = os.getenv("GOOGLE_MAPS_API_KEY", "")
    ws_host: str = os.getenv("WS_HOST", "127.0.0.1")
    ws_port: int = int(os.getenv("WS_PORT", "8765"))

    # Audio: the Live API wants raw little-endian 16-bit PCM at 16 kHz.
    sample_rate: int = 16000
    channels: int = 1
    chunk_ms: int = 20


CFG = Config()
