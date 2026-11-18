"""The client's music bed: made once with ElevenLabs Music, or a track the person is licensed to use."""
import shutil
from pathlib import Path

import requests

from adeditor import brand, keys, media

# https://elevenlabs.io/docs/api-reference/music/compose
# POST /v1/music, header xi-api-key, JSON body prompt, music_length_ms (3000 to 600000),
# force_instrumental. The response body is the audio (mp3, 44.1 kHz at the default format).
URL = "https://api.elevenlabs.io/v1/music"
MIN_SECONDS, MAX_SECONDS = 3, 600
BED = "music/bed.mp3"


class MusicError(RuntimeError):
    """ElevenLabs Music answered with an error. The message holds the status and a short reason, never the key."""


def _record(client: str, bpm: float | None) -> None:
    kit = brand.read(client)
    kit["music"], kit["music_bpm"], kit["approved"] = BED, bpm, False
    brand.save(client, kit)


def make_bed(client: str, prompt: str, seconds: int = 240, bpm: float = 72) -> Path:
    brand.read(client)  # a clear error first when there's no kit
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise ValueError(f"A bed is {MIN_SECONDS} to {MAX_SECONDS} seconds long.")
    resp = requests.post(
        URL,
        headers={"xi-api-key": keys.get(keys.ELEVENLABS)},
        json={"prompt": prompt, "music_length_ms": seconds * 1000, "force_instrumental": True},
        timeout=600,
    )
    if resp.status_code != 200:
        raise MusicError(f"ElevenLabs Music returned HTTP {resp.status_code}: {resp.text[:200]}")
    out = brand.brand_dir(client) / BED
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(resp.content)
    _record(client, bpm)
    return out


def use_file(client: str, source: Path, bpm: float | None = None) -> Path:
    """Copy the person's own licensed track in as the bed (any audio file; it becomes an mp3)."""
    brand.read(client)
    source = Path(source)
    if not source.is_file():
        raise ValueError(f"There's no file at {source}.")
    out = brand.brand_dir(client) / BED
    out.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".mp3":
        shutil.copyfile(source, out)
    else:
        media.run(["ffmpeg", "-y", "-i", str(source), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(out)])
    _record(client, bpm)
    return out
