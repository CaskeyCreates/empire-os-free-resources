"""The loudness envelope: one RMS value in dBFS per 10 ms frame."""
import struct
from pathlib import Path

import numpy as np

FRAME_SECONDS = 0.01
FLOOR_DB = -100.0
LINE_ABOVE_FLOOR = 12.0


def _read_float_wav(wav: Path) -> tuple[np.ndarray, int]:
    """Samples and rate of a mono 32-bit float WAV (what media.extract_audio writes).
    The standard wave module cannot read float WAVs, so walk the RIFF chunks."""
    data = Path(wav).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{wav} is not a WAV file")
    pos, rate, samples = 12, 0, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag not in (3, 0xFFFE) or bits != 32 or channels != 1:
                raise ValueError(f"{wav} must be mono 32-bit float; make it with media.extract_audio")
        elif cid == b"data":
            samples = np.frombuffer(body[:len(body) // 4 * 4], dtype="<f4")
            break
        pos += 8 + size + (size & 1)
    if samples is None or not rate:
        raise ValueError(f"{wav} has no audio data")
    return samples.astype(np.float64), rate


def envelope_db(wav: Path) -> list[float]:
    """RMS in dBFS for each 10 ms frame, floored at -100."""
    samples, rate = _read_float_wav(wav)
    step = int(round(rate * FRAME_SECONDS))
    count = len(samples) // step
    if count == 0:
        return []
    frames = samples[:count * step].reshape(count, step)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    with np.errstate(divide="ignore"):
        db = 20 * np.log10(np.maximum(rms, 1e-12))
    return [float(v) for v in np.maximum(db, FLOOR_DB)]


def floor_and_line(env: list[float]) -> tuple[float, float]:
    """(floor, line): the 10th percentile frame, and 12 dB above it. Digital silence at the very
    start is left out: it's what fills the gap before an audio stream that starts after the picture
    (media.extract_audio), not the room, and in a short take it would sink the floor to -100."""
    lead = 0
    while lead < len(env) and env[lead] <= FLOOR_DB:
        lead += 1
    p10 = float(np.percentile(env[lead:] or env, 10))
    return p10, p10 + LINE_ABOVE_FLOOR
