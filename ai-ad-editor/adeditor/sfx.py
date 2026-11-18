"""Placing the five sounds: which picture changes get one, how loud, at what pitch, and the one effects
stem they are mixed into. Ported from a house video tool's board sound planner (limits, priorities,
levels, pitch rates), with the voice never touched: the stem is its own file, summed beside the voice.

A sound's gain puts its loudest 100 ms at a set level under the voice's integrated loudness:
gain = voice LUFS + level - the sound's own 100 ms RMS + the plan's sfx_offset_db, held to -40..+6 dB.

Filters: https://ffmpeg.org/ffmpeg-filters.html (adelay, asetrate, aresample, volume, amix, anullsrc)."""
from pathlib import Path

from adeditor import media

FPS = 30
FRAME = 1 / FPS
EPS = 1e-6
RATE = 48000
LEVEL = {"cut": -9.0, "hit": -8.0, "whip": -11.0, "push": -13.0, "tick": -16.0}  # LU under the voice
PRIORITY = {"cut": 5, "hit": 4, "whip": 3, "push": 2, "tick": 1}
RATES = (0.97, 1.0, 1.03)
GAIN_CLAMP = (-40.0, 6.0)
MIN_GAP_FRAMES = 4  # moments are at least this far apart
LAYER_FRAMES = 2  # a hit may sit this close under a cut, whip or push
PER_SECOND = 3
END_GUARD = 0.05
LAYER_UNDER = {"cut", "whip", "push"}


def _prio(e: dict) -> int:
    return PRIORITY.get(e["role"], 0)


def limit(events: list[dict], duration: float) -> tuple[list[dict], list[dict]]:
    """(kept, dropped), each sorted by time; a dropped event carries "why". Drops events with sfx false and
    any in the last 0.05 s, keeps one sound per moment (a hit may layer under a cut, whip or push within
    2 frames), moments at least 4 frames apart, then at most 3 moments in any second, the strongest staying."""
    dropped: list[dict] = []

    def drop(e, why):
        dropped.append({**e, "why": why})

    allowed = []
    for e in sorted(events, key=lambda e: (e["at"], -_prio(e))):
        if not e.get("sfx", True):
            drop(e, "turned off in the plan")
        elif e["at"] > duration - END_GUARD + EPS:
            drop(e, "in the last moment of the ad")
        else:
            allowed.append(e)

    moments: list[dict] = []
    for e in allowed:
        m = moments[-1] if moments else None
        if m is None or e["at"] - m["lead"]["at"] >= MIN_GAP_FRAMES * FRAME - EPS:
            moments.append({"lead": e, "layer": None})
            continue
        close = abs(e["at"] - m["lead"]["at"]) <= LAYER_FRAMES * FRAME + EPS
        if close and not m["layer"] and e["role"] == "hit" and m["lead"]["role"] in LAYER_UNDER:
            m["layer"] = e
        elif close and not m["layer"] and m["lead"]["role"] == "hit" and e["role"] in LAYER_UNDER:
            m["layer"], m["lead"] = m["lead"], e
        elif _prio(e) > _prio(m["lead"]):
            drop(m["lead"], f"too close to a {e['role']}")
            if m["layer"] and not (m["layer"]["role"] == "hit" and e["role"] in LAYER_UNDER):
                drop(m["layer"], f"too close to a {e['role']}")
                m["layer"] = None
            m["lead"] = e
        else:
            drop(e, f"too close to a {m['lead']['role']}")

    accepted: list[dict] = []

    def fits(m):
        times = [x["lead"]["at"] for x in accepted] + [m["lead"]["at"]]
        return all(sum(1 for u in times if t - 1 + EPS < u <= t + EPS) <= PER_SECOND for t in times)

    for m in sorted(moments, key=lambda m: (-_prio(m["lead"]), m["lead"]["at"])):
        if fits(m):
            accepted.append(m)
        else:
            drop(m["lead"], "over three sounds in a second")
            if m["layer"]:
                drop(m["layer"], "over three sounds in a second")
    kept = sorted((e for m in accepted for e in ([m["lead"], m["layer"]] if m["layer"] else [m["lead"]])),
                  key=lambda e: e["at"])
    return kept, sorted(dropped, key=lambda e: e["at"])


def gain_db(role: str, voice_lufs: float, peak100_db: float, offset_db: float = 0.0) -> tuple[float, bool]:
    """(gain in dB, whether the clamp held it)."""
    g = voice_lufs + LEVEL[role] - peak100_db + offset_db
    lo, hi = GAIN_CLAMP
    held = min(hi, max(lo, g))
    return round(held, 3), held != g


def place(events: list, duration: float, voice_lufs: float, manifest: dict, offset_db: float = 0.0) -> list[dict]:
    """The cues, sorted by time: {"role", "event", "at", "start", "gain_db", "rate", "clamped"}. The sound's
    transient lands on `at`: it starts at `at` minus its transient (stretched by its pitch rate)."""
    kept, _ = limit(list(events), duration)
    turns: dict[str, int] = {}
    cues = []
    for e in kept:
        role = e["role"]
        if role not in manifest:
            continue
        n = turns.get(role, 0)
        turns[role] = n + 1
        rate = RATES[n % len(RATES)]
        sound = manifest[role]
        g, held = gain_db(role, voice_lufs, float(sound["peak100_db"]), offset_db)
        start = max(0.0, e["at"] - float(sound["transient"]) / rate)
        cues.append({"role": role, "event": e.get("event", role), "at": round(e["at"], 3), "start": round(start, 3),
                     "gain_db": g, "rate": rate, "clamped": held})
    return cues


def _file(entry: dict) -> Path:
    from adeditor import sounds

    return Path(entry["path"]) if entry.get("path") else sounds.folder() / entry["file"]


def stem(cues: list[dict], manifest: dict, duration: float, out: Path) -> Path:
    """One effects WAV (48 kHz stereo, 32-bit float) the ad's length: each cue pitched by its rate,
    delayed to its start (to the sample) and set to its gain, summed with amix normalize=0."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    d = f"{duration:.3f}"
    args = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", d, "-i", f"anullsrc=r={RATE}:cl=stereo"]
    parts = []
    for i, c in enumerate(cues, 1):
        args += ["-i", str(_file(manifest[c["role"]]))]
        delay = int(round(c["start"] * RATE))
        parts.append(f"[{i}:a]aformat=sample_rates={RATE}:channel_layouts=stereo,asetrate={round(RATE * c['rate'])},"
                     f"aresample={RATE},adelay={delay}S|{delay}S,volume={c['gain_db']:.3f}dB[s{i}]")
    labels = "".join(f"[s{i}]" for i in range(1, len(cues) + 1))
    graph = ";".join(parts + [f"[0:a]{labels}amix=inputs={len(cues) + 1}:duration=first:normalize=0:"
                              f"dropout_transition=0[out]"])
    media.run([*args, "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_f32le", "-t", d, str(out)])
    return out
