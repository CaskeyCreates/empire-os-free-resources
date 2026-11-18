"""The five sounds (cut, hit, whip, push, tick), made once per install with ElevenLabs Sound Effects into
sounds/ and measured, so every render can place them a set distance under the voice.

APIs: POST https://api.elevenlabs.io/v1/sound-generation
  https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert
  (text, duration_seconds 0.5 to 30, prompt_influence 0 to 1, model_id, output_format as a query parameter)
and GET https://api.elevenlabs.io/v1/user/subscription (its `tier` is "free" on the free plan)
  https://elevenlabs.io/docs/api-reference/user/subscription/get

Licence: ElevenLabs Terms of Use 1(c)(i) make the free plan non-commercial and Starter and above
commercial, so nothing is made on a free plan. With no key, a refused key (401, 402, 403), a free plan or
ADEDITOR_NO_SOUNDS=1, no sounds are made and every render says why; the ad still renders, without them."""
import array
import datetime as dt
import json
import math
import os
import subprocess
from pathlib import Path

import requests
from dotenv import dotenv_values

from adeditor import keys, media, paths

MODEL = "eleven_text_to_sound_v3"
API = "https://api.elevenlabs.io/v1/sound-generation"
PLAN_API = "https://api.elevenlabs.io/v1/user/subscription"
FORMAT = "mp3_44100_128"  # every plan gets it
RATE = 48000
STEP = 0.01  # the envelope's step, seconds
ONSET_DB = 20.0  # the transient is the first step this close to the loudest
LEAD = 0.005  # kept before the transient
FADE_OUT = 0.015
SWITCH = "ADEDITOR_NO_SOUNDS"
ROLES = ("cut", "hit", "whip", "push", "tick")
LAST_REASON = "last-reason.txt"
LICENCE = ("These sounds were made on your own ElevenLabs account with Sound Effects. The free plan is "
           "non-commercial only (ElevenLabs Terms of Use 1(c)(i)); on Starter and above you may use them "
           "commercially (1(c)(ii)).")
PROMPTS = {
    "cut": {"prompt": "A single crisp modern camera shutter click layered with a very short bright puff of air, the "
                      "flash transition in a premium social video edit. Clean, tight and dry, close up, no music, "
                      "no reverb tail.", "duration_seconds": 0.6, "prompt_influence": 0.6},
    "whip": {"prompt": "One fast tight whip swish of air, a quick camera whip pan in a modern video edit. Clean, "
                       "close and dry, short, no music, no bass, no impact.",
             "duration_seconds": 0.5, "prompt_influence": 0.6},
    "push": {"prompt": "A soft smooth airy whoosh that swells gently and settles, a slow camera push in on a premium "
                       "minimal motion graphic. Subtle and clean, no music, no impact, no rumble.",
             "duration_seconds": 0.9, "prompt_influence": 0.5},
    "hit": {"prompt": "A deep soft low sub thump, felt more than heard, a subtle cinematic bass hit as a title lands "
                      "in a premium minimal video. Short tail, clean, no music, no boom reverb, no distortion.",
            "duration_seconds": 0.8, "prompt_influence": 0.5},
    "tick": {"prompt": "A tiny soft muted interface tick, one delicate click as a label appears in a clean minimal "
                       "motion graphic. Very short, soft and high, dry, no music, no beep.",
             "duration_seconds": 0.5, "prompt_influence": 0.6},
}


def folder() -> Path:
    return paths.ROOT / "sounds"


def _load() -> dict:
    try:
        data = json.loads((folder() / "manifest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _made_file(manifest: dict, role: str) -> bool:
    entry = manifest.get(role)
    return isinstance(entry, dict) and isinstance(entry.get("file"), str) and (folder() / entry["file"]).is_file()


def _last_reason() -> str | None:
    try:
        return (folder() / LAST_REASON).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _remember(reason: str | None) -> None:
    """Keep why the last make() stopped, so every render can say it; cleared when all five are made."""
    path = folder() / LAST_REASON
    if reason is None:
        path.unlink(missing_ok=True)
        return
    folder().mkdir(parents=True, exist_ok=True)
    path.write_text(reason + "\n", encoding="utf-8")


def signature() -> str:
    """What a render can hear of the sounds, for render.inputs(): "off", "none" or the made dates."""
    if switched_off():
        return "off"
    manifest = _load()
    return ",".join(f"{r}:{manifest[r].get('made')}" for r in ROLES if _made_file(manifest, r)) or "none"


def switched_off() -> bool:
    """On when ADEDITOR_NO_SOUNDS is 1 in the environment or in .env; the environment wins when both are set.
    .env is read the way keys.py reads it and never put in os.environ."""
    value = os.environ.get(SWITCH)
    if value is None:
        value = dotenv_values(paths.ROOT / ".env").get(SWITCH)
    return (value or "").strip() == "1"


def available() -> tuple[dict | None, str | None]:
    """(the manifest, None) when all five sounds are made and on disk, else (None, why not)."""
    if switched_off():
        return None, f"{SWITCH}=1 is set"
    manifest = _load()
    have = [r for r in ROLES if _made_file(manifest, r)]
    if len(have) == len(ROLES):
        return manifest, None
    stopped = _last_reason()
    if stopped and NO_KEY in stopped and keys.present(keys.ELEVENLABS):
        stopped = None  # the key has been added since that reason was stored
    if stopped:
        return None, stopped
    if not have:
        return None, "the sounds haven't been made yet; run adeditor doctor --fix"
    return None, f"only {len(have)} of the 5 sounds are made; run adeditor doctor --fix"


# ------------------------------------------------------------------------------------------ measuring

def _decode(path: Path) -> list[float]:
    r = subprocess.run([media.tool("ffmpeg"), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE),
                        "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0:
        raise media.ToolError(f"ffmpeg couldn't read {Path(path).name}: {r.stderr.decode('utf-8', 'replace')[-300:]}")
    raw = r.stdout
    samples = array.array("f")
    samples.frombytes(raw[: len(raw) // 4 * 4])
    return samples.tolist()


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-9))


def _envelope(samples: list[float]) -> list[float]:
    step = int(STEP * RATE)
    return [math.sqrt(sum(v * v for v in samples[i:i + step]) / step) for i in range(0, len(samples) - step + 1, step)]


def onset(samples: list[float]) -> float:
    """Seconds to the first 10 ms step within 20 dB of the loudest."""
    env = _envelope(samples)
    if not env or max(env) <= 0:
        return 0.0
    floor = _db(max(env)) - ONSET_DB
    return next(i for i, e in enumerate(env) if _db(e) > floor) * STEP


def peak100(samples: list[float]) -> float:
    """The loudest 100 ms as RMS, dBFS."""
    win = int(0.1 * RATE)
    if len(samples) <= win:
        return _db(math.sqrt(sum(v * v for v in samples) / max(1, len(samples))))
    step = int(STEP * RATE)
    best = 0.0
    csum = [0.0]
    for v in samples:
        csum.append(csum[-1] + v * v)
    for i in range(0, len(samples) - win + 1, step):
        best = max(best, (csum[i + win] - csum[i]) / win)
    return _db(math.sqrt(best))


def trim(src: Path, out: Path) -> dict:
    """Write `src` as 48 kHz stereo WAV starting 5 ms before its transient, with a 15 ms fade out, and
    measure it: {"file", "transient", "peak100_db"}."""
    start = max(0.0, onset(_decode(src)) - LEAD)
    length = len(_decode(src)) / RATE - start
    fade = min(FADE_OUT, length / 4)
    media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-af",
               f"afade=t=out:st={length - fade:.3f}:d={fade:.3f},aformat=sample_rates={RATE}:channel_layouts=stereo",
               "-c:a", "pcm_s16le", str(out)])
    made = _decode(out)
    return {"file": out.name, "transient": round(onset(made), 3), "peak100_db": round(peak100(made), 2)}


# ------------------------------------------------------------------------------------------ making

NO_KEY = f"no {keys.ELEVENLABS} in ai-ad-editor/.env"
NOT_ALLOWED = "the key isn't allowed to read the account's plan or make sound effects"


def _refusal(code: int) -> str:
    if code in (401, 403):
        return (f"{NOT_ALLOWED} ({code}); if you limited what the key can do, "
                "leave Sound Effects and User (read) switched on")
    return f"the ElevenLabs plan doesn't allow sound effects ({code})"


def make(force: bool = False) -> dict:
    """Make the sounds that aren't made yet (all of them with force). Returns {"made": bool, "reason": str | None}:
    made is True when anything was generated; reason says why nothing was, and is None when the sounds were
    already there. Each sound is written to the manifest as soon as it's made, so a stop part way never makes
    the same sound (and spends its credits) twice."""
    if switched_off():
        return {"made": False, "reason": f"{SWITCH}=1 is set"}
    manifest = _load()
    todo = [r for r in ROLES if force or not _made_file(manifest, r)]
    if not todo:
        return {"made": False, "reason": None}

    def stop(reason: str, made: bool = False) -> dict:
        _remember(reason)
        return {"made": made, "reason": reason}

    try:
        key = keys.get(keys.ELEVENLABS)
    except keys.MissingKey:
        return stop(f"there's {NO_KEY}")
    headers = {"xi-api-key": key}
    try:
        r = requests.get(PLAN_API, headers=headers, timeout=30)
        if r.status_code in (401, 402, 403):
            return stop(_refusal(r.status_code))
        if r.status_code != 200:
            return stop(f"ElevenLabs couldn't check the plan (HTTP {r.status_code})")
        tier = str(r.json().get("tier") or "")
    except (requests.RequestException, ValueError, AttributeError) as exc:
        return stop(f"ElevenLabs couldn't check the plan ({type(exc).__name__})")
    if tier in ("", "free"):
        return stop("the ElevenLabs account is on the free plan, which is non-commercial only "
                    "(Terms of Use 1(c)(i)); move to Starter or above to make the sounds")
    folder().mkdir(parents=True, exist_ok=True)
    (folder() / "LICENCE.txt").write_text(LICENCE + "\n", encoding="utf-8")
    made = False
    for role in todo:
        spec = PROMPTS[role]
        body = {"text": spec["prompt"], "duration_seconds": spec["duration_seconds"],
                "prompt_influence": spec["prompt_influence"], "loop": False, "model_id": MODEL}
        try:
            r = requests.post(API, params={"output_format": FORMAT}, json=body, headers=headers, timeout=120)
        except requests.RequestException as exc:
            return stop(f"ElevenLabs didn't answer ({type(exc).__name__})", made)
        if r.status_code != 200 or not r.content:
            return stop(_refusal(r.status_code) if r.status_code in (401, 402, 403)
                        else f"ElevenLabs couldn't make the {role} sound (HTTP {r.status_code})", made)
        raw = folder() / f".{role}.mp3"
        raw.write_bytes(r.content)
        try:
            row = trim(raw, folder() / f"{role}.wav")
        except (media.ToolError, subprocess.SubprocessError) as exc:
            return stop(f"the {role} sound couldn't be trimmed ({exc})", made)
        finally:
            raw.unlink(missing_ok=True)
        manifest[role] = {**row, "made": dt.date.today().isoformat()}
        (folder() / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        made = True
    _remember(None)
    return {"made": made, "reason": None}
