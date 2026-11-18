"""Audio to words: the Scribe v2 transcript, after a check that there is sound to hear."""
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import requests

from adeditor import envelope, keys, media
from adeditor.paths import Job

# https://elevenlabs.io/docs/api-reference/speech-to-text/convert
# Header xi-api-key; multipart fields model_id, timestamps_granularity, keyterms (repeated),
# file. Keyterms: at most 1000, each up to 50 characters and 5 words, none of < > { } [ ] \.
URL = "https://api.elevenlabs.io/v1/speech-to-text"
MODEL = "scribe_v2"
MAX_KEYTERMS = 1000
MAX_KEYTERM_CHARS = 50
MAX_KEYTERM_WORDS = 5
NO_SOUND_DB = -55.0

Word = dict  # {"word": str, "start": float, "end": float}


class NoSound(Exception):
    pass


class ScribeError(RuntimeError):
    """ElevenLabs Scribe answered with an error. The message holds the status and a short reason,
    never the key."""


def why(exc: Exception) -> str:
    """A failed call to ElevenLabs as a plain reason on one line, never the key."""
    if isinstance(exc, requests.Timeout):
        return "ElevenLabs took too long to answer"
    if isinstance(exc, requests.ConnectionError):
        return "we couldn't reach ElevenLabs, so check your internet connection"
    if isinstance(exc, requests.RequestException):
        return f"the call to ElevenLabs failed ({type(exc).__name__})"
    return " ".join(str(exc).split()).rstrip(".")


def clean_keyterms(keyterms: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for term in keyterms:
        term = re.sub(r"[<>{}\[\]\\]", "", term).strip()
        if not term or len(term) > MAX_KEYTERM_CHARS or len(term.split()) > MAX_KEYTERM_WORDS:
            continue
        if term not in seen:
            seen.add(term)
            out.append(term)
    return out[:MAX_KEYTERMS]


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _words(response: dict) -> list[Word]:
    return [
        {"word": item["text"], "start": float(item["start"]), "end": float(item["end"])}
        for item in response.get("words", [])
        if item.get("type") == "word" and item.get("start") is not None and item.get("end") is not None
    ]


def transcribe(audio: Path, keyterms: list[str], cache_dir: Path) -> list[Word]:
    """Word timings for `audio`. The response is cached by the audio's MD5 plus the
    keyterms sent, so the same audio and terms are never paid for twice."""
    audio, cache_dir = Path(audio), Path(cache_dir)
    terms = clean_keyterms(keyterms)
    key = hashlib.md5((_md5(audio) + "|" + "\n".join(terms)).encode()).hexdigest()
    cached = cache_dir / f"scribe_{key}.json"
    if cached.exists():
        return _words(json.loads(cached.read_text(encoding="utf-8")))
    data = [("model_id", MODEL), ("timestamps_granularity", "word")]
    data += [("keyterms", t) for t in terms]
    with open(audio, "rb") as f:
        resp = requests.post(
            URL,
            headers={"xi-api-key": keys.get(keys.ELEVENLABS)},
            data=data,
            files={"file": (audio.name, f, "audio/wav")},
            timeout=600,
        )
    if resp.status_code != 200:
        # The body can echo request details; show the status and a short reason only.
        raise ScribeError(f"ElevenLabs Scribe returned HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(body), encoding="utf-8")
    return _words(body)


def prepare(job: Job, keyterms: list[str]) -> list[Word]:
    """Extract audio, write the envelope, stop if it is silent, then transcribe."""
    work = job.work
    work.mkdir(parents=True, exist_ok=True)
    if not media.probe(job.video).has_audio:
        raise NoSound("This video has no sound we can hear: it has no sound track at all. "
                      "Check the mic was on and the file has audio.")
    wav = media.extract_audio(job.video, work / "audio.wav")
    env = envelope.envelope_db(wav)
    (work / "envelope.json").write_text(json.dumps(env), encoding="utf-8")
    loud = float(np.percentile(env, 90)) if env else envelope.FLOOR_DB
    if loud < NO_SOUND_DB:
        raise NoSound(
            f"This video has no sound we can hear: the loud parts measure {loud:.1f} dBFS "
            f"and the limit is {NO_SOUND_DB:.0f}. Check the mic was on and the file has audio."
        )
    words = transcribe(wav, keyterms, work / "cache")
    (work / "transcript.json").write_text(json.dumps({"words": words}, indent=1), encoding="utf-8")
    return words
