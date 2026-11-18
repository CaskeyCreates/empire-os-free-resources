import json
import math
import random
import shutil
import struct
import wave
from pathlib import Path

import pytest

from adeditor import media

EXAMPLE_BRAND = Path(__file__).resolve().parent.parent / "clients" / "example" / "brand"


def make_speech(tmp_path: Path, pattern: list[tuple[str, float]], rate: int = 16000) -> Path:
    """WAV where ("w", d) is d s of a 440 Hz tone at -20 dBFS and ("s", d) is d s of -70 dBFS noise."""
    rng = random.Random(1)
    tone_amp = 10 ** (-20 / 20)
    noise_amp = 10 ** (-70 / 20)
    samples: list[float] = []
    for kind, dur in pattern:
        n = int(round(dur * rate))
        if kind == "w":
            samples += [tone_amp * math.sin(2 * math.pi * 440 * i / rate) for i in range(n)]
        else:
            samples += [noise_amp * rng.uniform(-1, 1) for _ in range(n)]
    path = tmp_path / "speech.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))
    return path


def make_video(tmp_path: Path, seconds: float, w: int, h: int, audio: Path | None = None) -> Path:
    """An mp4 from ffmpeg's testsrc2, with `audio` muxed in when given."""
    out = tmp_path / f"video_{w}x{h}.mp4"
    args = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration={seconds}"]
    if audio is not None:
        args += ["-i", str(audio), "-c:a", "aac", "-shortest"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
    media.run(args)
    return out


def speech_words(pattern: list[tuple[str, float]], texts: list[str] | None = None, inset: float = 0.08) -> list[dict]:
    """Transcript words for a make_speech pattern: one per ("w", d), each set `inset` s inside its
    tone, as a transcriber often places them (the sound starts before the word time says)."""
    words, t = [], 0.0
    for kind, dur in pattern:
        if kind == "w":
            n = len(words)
            text = texts[n] if texts else f"w{n}"
            words.append({"word": text, "start": round(t + inset, 3), "end": round(t + dur - inset, 3)})
        t += dur
    return words


def put_in_raw(client: str = "acme", name: str = "take1.mp4") -> Path:
    """A stand-in video in the client's raw/ folder (under the current paths.ROOT), where every command
    looks for a video given by its name alone."""
    from adeditor import paths

    raw = paths.ROOT / "clients" / client / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_bytes(b"x")
    return raw / name


@pytest.fixture
def speech(tmp_path):
    return lambda pattern: make_speech(tmp_path, pattern)


@pytest.fixture
def take(tmp_path):
    """A take as prepare leaves it: (wav, transcript words, 10 ms envelope) for a speech pattern."""
    from adeditor import envelope

    def build(pattern, texts=None, inset=0.08):
        wav = make_speech(tmp_path, pattern)
        f32 = media.extract_audio(wav, tmp_path / "speech_f32.wav")
        return wav, speech_words(pattern, texts, inset), envelope.envelope_db(f32)

    return build


@pytest.fixture
def video(tmp_path):
    return lambda seconds, w, h, audio=None: make_video(tmp_path, seconds, w, h, audio)


def cut_words(text: str, start: float = 0.4, step: float = 0.36, length: float = 0.3) -> list[dict]:
    """cut.json words for `text`, one every `step` s from `start`, each `length` s long."""
    words, t = [], start
    for raw in text.split():
        words.append({"text": raw, "start": round(t, 3), "end": round(t + length, 3), "src": round(t, 3)})
        t += step
    return words


@pytest.fixture
def ad_job(tmp_path, monkeypatch, video):
    """A job ready for the page and the render: the example client's kit (approved), cut.json, an
    A-roll, a face map on the 1x clock, approved strikes, Pexels clips in the cache and plan.json."""
    from adeditor import paths, verify
    from adeditor.paths import Job

    monkeypatch.setattr(paths, "ROOT", tmp_path)

    def build(words, duration, *, plan=None, segments=None, face=None, aroll=None, kit=None,
              strikes_approved=True, clips=()):
        job = Job("acme", Path("take1.mp4"))
        job.work.mkdir(parents=True)
        shutil.copytree(EXAMPLE_BRAND, job.brand)
        kit_json = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
        kit_json.update({"approved": True, **(kit or {})})
        (job.brand / "kit.json").write_text(json.dumps(kit_json), encoding="utf-8")
        if segments is None:
            edges = [round(duration * i / 4, 4) for i in range(5)]
            segments = [{"src": [a, b], "out": [a, b]} for a, b in zip(edges, edges[1:])]
        cut = {"segments": segments, "words": words, "duration": duration,
               "tight_joins": [], "tight_words": [], "struck": []}
        (job.work / "cut.json").write_text(json.dumps(cut), encoding="utf-8")
        shutil.copy(aroll or video(2, 1080, 1920), job.work / "aroll.mp4")
        job.raw.mkdir(parents=True)
        shutil.copy(job.work / "aroll.mp4", job.raw / job.video.name)  # the commands find "take1.mp4" here
        box = face or {"x": 0.37, "y": 0.30, "w": 0.30, "h": 0.17}
        faces = [{"t": round(i * 0.5, 3), **box} for i in range(int(duration * 2) + 1)]
        fp = verify.fingerprint(cut)  # the word check and the face map both ran on this cut
        (job.work / "faces.json").write_text(json.dumps({"cut": fp, "samples": faces}), encoding="utf-8")
        checked = {"cut": fp, "matched": len(words), "total": len(words), "near": [], "far": [], "accepted": []}
        (job.work / "verify.json").write_text(json.dumps(checked), encoding="utf-8")
        strikes = {"strikes": [], "fillers": [], "approved": strikes_approved}
        (job.work / "strikes.json").write_text(json.dumps(strikes), encoding="utf-8")
        job.broll_cache.mkdir(parents=True, exist_ok=True)
        for clip_id, seconds in clips:
            shutil.copy(video(seconds, 360, 640), job.broll_cache / f"{clip_id}.mp4")
        (job.work / "plan.json").write_text(json.dumps({"lockup": "kit", **(plan or {})}), encoding="utf-8")
        return job

    return build
