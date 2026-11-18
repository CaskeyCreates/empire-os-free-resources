"""The mix: the voice and the client's music bed under a silent render, levelled for social. Ported
from the first hand-built ad's mix script.

The voice gets an 80 Hz high-pass and is levelled to -16 LUFS (measured, then a linear gain). The
bed is extended on a bar line with a 4 s crossfade when the ad outgrows it, flattened to -25 LUFS
and ducked under the voice by a sidechain compressor; it lifts on the end card, fades in over 0.6 s
and out over the last 1.4 s. The whole mix is levelled to -14 LUFS at -1.5 dBTP, two-pass and
linear. Measured on the stems, the bed sits a median of about 15 dB under the voice while they talk
(12 to 18 dB passes): 0 to 3 dB under fought the voice, and 25 dB under couldn't be heard on a phone.
Every bed is levelled to that: the ducked bed is measured against the voice once and its gain moved so the
median lands on 15 dB under; the plan's bed_offset_db then moves it from there (-3 is 18 dB under, +3 is 12).

Filters: https://ffmpeg.org/ffmpeg-filters.html (loudnorm, sidechaincompress, acrossfade, amix, ebur128)."""
import json
import math
import re
import statistics
from pathlib import Path

from adeditor import media

RATE = 48000
VOICE_PRE = f"highpass=f=80,aresample={RATE},aformat=channel_layouts=stereo"
VOICE_TARGET = "loudnorm=I=-16:TP=-2:LRA=7"
BED_FLAT = "loudnorm=I=-25:LRA=2:TP=-9"
DUCK = "sidechaincompress=threshold=0.05:ratio=3:attack=80:release=1200:knee=6"
FINAL_TARGET = "loudnorm=I=-14:TP=-1.5:LRA=11"
LIFT, LIFT_TIME = 2.0, 0.7  # the bed doubles over 0.7 s from the end card's start
FADE_IN, FADE_OUT = 0.6, 1.4
XFADE = 4.0  # a loop crossfades over 4 s
OUTRO = 8.0  # and leaves the first pass at least this far before the bed's own ending
LOOP_MARGIN = 1.0  # a looped bed runs this much past the ad, so the trim never runs short
VOICED = -70.0  # a momentary reading of the voice under this is no speech
BED_TARGET_DB = 15.0  # the bed's median under the voice at bed_offset_db 0
BED_MAX_BOOST_DB = 12.0  # the most the auto level lifts a very quiet bed
BED_OFFSET_LIMIT = 3.0  # bed_offset_db runs -3 (18 dB under) to +3 (12 dB under), inside the check's 12 to 18


def _stderr(args: list) -> str:
    return media.run(["ffmpeg", "-hide_banner", "-nostats", *[str(a) for a in args]]).stderr


def _measured(text: str) -> dict:
    """The last JSON block loudnorm printed."""
    blocks = re.findall(r"^\{.*?^\}", text, re.S | re.M)
    if not blocks:
        raise media.ToolError("ffmpeg's loudnorm printed no measurement")
    return json.loads(blocks[-1])


def _linear(target: str, m: dict) -> str:
    """The second loudnorm pass: the first pass's measurement applied as one linear gain."""
    return (f"{target}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


def _length(path: Path) -> float:
    out = media.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout
    return float(out.strip())


def bed_spans(bed_len: float, duration: float, bpm: float | None) -> list[tuple[float, float | None]]:
    """The stretches of the bed (start, end or None for its end) played one after another, each
    crossfading 4 s into the next. A bed as long as the ad plays once. A shorter one leaves its first
    pass on the last bar line at least 8 s before its end (with no tempo, exactly 8 s before), and the
    next pass enters a whole number of bars earlier, so the crossfade joins the same point in the bar;
    it jumps back just far enough, and loops again if one jump can't cover the ad."""
    if bed_len >= duration:
        return [(0.0, None)]
    bar = 240.0 / bpm if bpm else None
    up = (lambda x: math.ceil(x / bar - 1e-9) * bar) if bar else (lambda x: x)
    down = (lambda x: math.floor(x / bar + 1e-9) * bar) if bar else (lambda x: x)
    leave = down(bed_len - OUTRO)
    room = leave - XFADE  # the next pass can enter no earlier than the bed's start
    if room < (bar or 1.0):
        raise ValueError(f"The music bed is {bed_len:.0f} s long, too short to loop under a {duration:.0f} s ad. "
                         "Make a longer one with adeditor client music.")
    need = duration + LOOP_MARGIN - bed_len
    jump, passes = up(need), 1
    if jump > room:
        jump = down(room)
        passes = math.ceil(need / jump)
    enter = round(leave - XFADE - jump, 3)
    leave = round(leave, 3)
    return [(0.0, leave)] + [(enter, leave)] * (passes - 1) + [(enter, None)]


def _bed_chain(spans, duration: float, end_start: float, offset_db: float) -> str:
    """Input 1 as the bed: looped, flattened, offset, lifted on the end card and faded, as [bed]."""
    pre = f"[1:a]aresample={RATE},aformat=channel_layouts=stereo"
    parts = []
    if len(spans) == 1:
        head = pre + ","
    else:
        parts.append(pre + f",asplit={len(spans)}" + "".join(f"[b{i}]" for i in range(len(spans))))
        for i, (s, e) in enumerate(spans):
            parts.append(f"[b{i}]atrim=start={s:.3f}" + (f":end={e:.3f}" if e is not None else "")
                         + f",asetpts=PTS-STARTPTS[s{i}]")
        prev = "s0"
        for i in range(1, len(spans)):
            parts.append(f"[{prev}][s{i}]acrossfade=d={XFADE:g}:c1=tri:c2=tri[x{i}]")
            prev = f"x{i}"
        head = f"[{prev}]"
    offset = f",volume={offset_db:.2f}dB" if offset_db else ""
    lift = f"volume='if(lt(t,{end_start:.3f}),1,1+({LIFT}-1)*min(1,(t-{end_start:.3f})/{LIFT_TIME}))':eval=frame"
    parts.append(f"{head}{BED_FLAT},aresample={RATE}{offset},atrim=0:{duration:.3f},{lift},"
                 f"afade=t=in:d={FADE_IN},afade=t=out:st={duration - FADE_OUT:.3f}:d={FADE_OUT}[bed]")
    return ";".join(parts)


def stems(voice: Path, bed: Path | None, duration: float, end_start: float, bpm: float | None,
          bed_offset_db: float = 0.0, *, out_dir: Path, words: list[dict] | None = None) -> dict:
    """Render the levelled voice and the ducked bed alone, each the ad's length, to out_dir/voice.wav
    and out_dir/bed.wav (32-bit float, so nothing is lost before the final mix). The bed is None when
    there's no bed. With `words`, the bed is levelled once: the first render is measured against the
    voice, then its gain is moved so the median lands on BED_TARGET_DB under, and bed_offset_db (kept
    within +/-BED_OFFSET_LIMIT) moves it from there. Without words there is nothing to measure against,
    so only the offset applies."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    voice_out, bed_out = out_dir / "voice.wav", out_dir / "bed.wav"
    gain = _linear(VOICE_TARGET, _measured(_stderr(
        ["-i", voice, "-af", f"{VOICE_PRE},{VOICE_TARGET}:print_format=json", "-f", "null", "-"])))
    d = f"{duration:.3f}"
    levelled = f"[0:a]{VOICE_PRE},{gain},aresample={RATE},apad,atrim=0:{d}"
    wav = ["-c:a", "pcm_f32le", "-t", d]
    if bed is None:
        media.run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-filter_complex", f"{levelled}[v]",
                   "-map", "[v]", *wav, str(voice_out)])
        bed_out.unlink(missing_ok=True)
        return {"voice": voice_out, "bed": None, "median_db": None, "capped": False}
    spans = bed_spans(_length(bed), duration, bpm)
    offset = max(-BED_OFFSET_LIMIT, min(BED_OFFSET_LIMIT, bed_offset_db))

    def render(gain_db: float) -> None:
        graph = ";".join([f"{levelled},asplit[v][vsc]", _bed_chain(spans, duration, end_start, gain_db),
                          f"[bed][vsc]{DUCK}[duck]"])
        media.run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-i", str(bed), "-filter_complex", graph,
                   "-map", "[v]", *wav, str(voice_out), "-map", "[duck]", *wav, str(bed_out)])

    render(0.0 if words else offset)
    median, capped = None, False
    if words:
        median = bed_under_voice(voice_out, bed_out, words)["median_db"]
        if median is not None:
            want = median - BED_TARGET_DB
            capped = want > BED_MAX_BOOST_DB  # a nearly silent bed isn't lifted by tens of dB
            render(round(min(want, BED_MAX_BOOST_DB) + offset, 2))
            median = bed_under_voice(voice_out, bed_out, words)["median_db"]  # the final level, measured once
        else:  # a silent bed can't be levelled; the check reports it
            render(offset)
    return {"voice": voice_out, "bed": bed_out, "median_db": median, "capped": capped}


def measure(path: Path) -> tuple[float, float]:
    """(integrated loudness in LUFS, true peak in dBTP) of a file, by ffmpeg's ebur128."""
    err = _stderr(["-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    summary = err[err.rfind("Summary:"):]
    i = re.search(r"\bI:\s+(-?inf|-?[\d.]+) LUFS", summary)
    peak = re.search(r"\bPeak:\s+(-?inf|-?[\d.]+) dBFS", summary)
    if not (i and peak):
        raise media.ToolError(f"ffmpeg's ebur128 gave no loudness summary for {Path(path).name}")
    return float(i.group(1)), float(peak.group(1))


def momentary(path: Path) -> list[tuple[float, float]]:
    """(time, momentary loudness in LUFS) every 100 ms. Each reading covers 400 ms; the time given is
    its middle."""
    out = media.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", str(path), "-af",
                     "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file=-", "-f", "null", "-"]).stdout
    readings, t = [], None
    for line in out.splitlines():
        if line.startswith("frame:"):
            m = re.search(r"pts_time:(-?[\d.]+)", line)
            t = float(m.group(1)) if m else None
        elif line.startswith("lavfi.r128.M=") and t is not None:
            readings.append((round(t - 0.1, 3), float(line.split("=", 1)[1])))
    return readings


def bed_under_voice(voice_stem: Path, bed_stem: Path, words: list[dict]) -> dict:
    """How far the bed sits under the voice while they talk: the median of voice minus bed momentary
    loudness over the readings whose middle falls inside a word, overall and per 30 s stretch
    (None for a stretch with no words). A reading that isn't a finite number (ebur128 prints -inf for
    digital silence) is ignored; when none is left the bed is "not measurable" and median_db is None."""
    voice, bed = momentary(voice_stem), dict(momentary(bed_stem))
    spans = sorted((float(w["start"]), float(w["end"])) for w in words)
    diffs = []
    for t, v in voice:
        if v > VOICED and t in bed and math.isfinite(v) and math.isfinite(bed[t]) \
                and any(a <= t <= b for a, b in spans):
            diffs.append((t, v - bed[t]))
    if not diffs:
        return {"median_db": None, "by_30s": []}
    stretches = int(spans[-1][0] // 30) + 1
    by_30s = []
    for k in range(stretches):
        inside = [d for t, d in diffs if 30 * k <= t < 30 * (k + 1)]
        by_30s.append(round(statistics.median(inside), 1) if inside else None)
    return {"median_db": round(statistics.median(d for _, d in diffs), 1), "by_30s": by_30s}


def mix(voice: Path, bed: Path | None, silent: Path, out: Path, duration: float, end_start: float,
        bpm: float | None, bed_offset_db: float = 0.0, *, stems_dir: Path | None = None,
        words: list[dict] | None = None) -> dict:
    """Mix the voice (and the bed, if there is one) under the silent render into `out`, levelled to
    -14 LUFS. The stems go to stems_dir (beside `out` when not given); with `words`, the bed's level
    under the voice is measured on them. Returns {"voice_stem", "bed_stem", "lufs", "true_peak",
    "bed_median_db", "bed_capped"} (bed_capped: the bed was so quiet the auto level stopped at +12 dB)."""
    out = Path(out)
    st = stems(voice, bed, duration, end_start, bpm, bed_offset_db,
               out_dir=Path(stems_dir) if stems_dir else out.parent, words=words)
    inputs = ["-i", str(st["voice"])] + (["-i", str(st["bed"])] if st["bed"] else [])
    both = "[0:a][1:a]amix=inputs=2:normalize=0:duration=first[mix]" if st["bed"] else "[0:a]anull[mix]"
    level = _linear(FINAL_TARGET, _measured(_stderr(
        [*inputs, "-filter_complex", f"{both};[mix]{FINAL_TARGET}:print_format=json", "-f", "null", "-"])))
    video_input = len(inputs) // 2
    media.run(["ffmpeg", "-y", "-v", "error", *inputs, "-i", str(silent),
               "-filter_complex", f"{both};[mix]{level},aresample={RATE}[a]",
               "-map", f"{video_input}:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
               "-ar", str(RATE), "-ac", "2", "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out)])
    lufs, peak = measure(out)
    return {"voice_stem": st["voice"], "bed_stem": st["bed"], "lufs": lufs, "true_peak": peak,
            "bed_median_db": st["median_db"], "bed_capped": st["capped"]}
