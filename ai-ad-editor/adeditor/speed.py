"""Speed: play the cut take a little faster and keep the voice's pitch.

Every run starts from the 1x pair (cut.1x.json and aroll.1x.mp4, copied the first time), so a
note after a note never compounds: 1.25x then 1.5x is the same as 1.5x straight from 1x. The
picture is retimed with setpts and the audio with atempo, which keeps pitch. ffmpeg 8's atempo
takes 0.5 to 100 in one filter, so the 1.0 to 2.0 range needs no chain.
https://ffmpeg.org/ffmpeg-filters.html#atempo
Only output-clock times are divided; source-clock times never change."""
import json
import shutil
import sys
from pathlib import Path

from adeditor import media, paths
from adeditor.cli import command
from adeditor.paths import Job

MIN_SPEED, MAX_SPEED = 1.0, 2.0


def current(job: Job) -> float:
    """The speed the A-roll is at now (1.0 when none was set)."""
    path = job.work / "speed.json"
    if not path.exists():
        return 1.0
    return float(json.loads(path.read_text(encoding="utf-8"))["speed"])


def _divide(cut: dict, speed: float) -> dict:
    """`cut` with every output-clock time divided by `speed`."""
    f = lambda t: round(t / speed, 4)
    side = lambda w: None if w is None else {**w, "t": f(w["t"])}
    out = dict(cut)
    out["segments"] = [{**s, "out": [f(s["out"][0]), f(s["out"][1])]} for s in cut["segments"]]
    out["words"] = [{**w, "start": f(w["start"]), "end": f(w["end"])} for w in cut["words"]]
    out["duration"] = f(cut["duration"])
    out["tight_joins"] = [f(t) for t in cut.get("tight_joins", [])]
    out["tight_words"] = [
        {**tw, "t": f(tw["t"]), "before": side(tw.get("before")), "after": side(tw.get("after"))}
        for tw in cut.get("tight_words", [])
    ]
    out["struck"] = [{**s, "t": f(s["t"])} for s in cut.get("struck", [])]
    return out


# fps=30 after setpts lands the frame count on exactly input / speed; -r 30 alone ended 2 frames long.
def _encode(src: Path, out: Path, speed: float) -> None:
    media.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(src),
        "-filter_complex", f"[0:v]setpts=PTS/{speed},fps=30[v];[0:a]atempo={speed}[a]",
        "-map", "[v]", "-map", "[a]", "-r", "30",
        "-c:v", "libx264", "-preset", "slow", "-crf", "15", "-pix_fmt", "yuv420p",
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ])


def apply(job: Job, speed: float) -> float:
    """Rebuild cut.json and aroll.mp4 at `speed` from the 1x pair. Returns the speed."""
    speed = float(speed)
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise ValueError(f"The speed has to be between 1 and 2, and {speed:g} isn't.")
    work = job.work
    cut_1x, aroll_1x = work / "cut.1x.json", work / "aroll.1x.mp4"
    if not cut_1x.exists():
        shutil.copyfile(work / "cut.json", cut_1x)
    if not aroll_1x.exists():
        shutil.copyfile(work / "aroll.mp4", aroll_1x)

    if speed == 1.0:
        shutil.copyfile(cut_1x, work / "cut.json")
        shutil.copyfile(aroll_1x, work / "aroll.mp4")
    else:
        cut = json.loads(cut_1x.read_text(encoding="utf-8"))
        tmp = work / "aroll.tmp.mp4"
        _encode(aroll_1x, tmp, speed)
        tmp.replace(work / "aroll.mp4")
        (work / "cut.json").write_text(json.dumps(_divide(cut, speed), indent=1), encoding="utf-8")
    (work / "speed.json").write_text(json.dumps({"speed": speed}), encoding="utf-8")
    return speed


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _args(p):
    p.add_argument("client")
    p.add_argument("video")
    p.add_argument("factor", type=float, help="1 to 2. 1.25 suits a calm delivery; 1 restores the original pace.")


@command("speed", help="Play the cut take faster, keeping the pitch. Always rebuilt from the 1x cut.", args=_args)
def speed_cmd(ns):
    job = paths.job(ns.client, ns.video)
    if not (job.work / "cut.json").exists() or not (job.work / "aroll.mp4").exists():
        print("There's no cut to speed up yet. Run adeditor cut first.", file=sys.stderr)
        return 1
    try:
        speed = apply(job, ns.factor)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    one_x = json.loads((job.work / "cut.1x.json").read_text(encoding="utf-8"))["duration"]
    now = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))["duration"]
    print(f"{_clock(one_x)} at 1x is now {_clock(now)} at {speed:g}x")
    return 0
