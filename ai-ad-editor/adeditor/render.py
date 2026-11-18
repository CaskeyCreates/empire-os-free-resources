"""`adeditor render`: build each ratio's page, render it silent with HyperFrames, and mix the sound
under it into ads/<stem>/<stem> (<ratio>).mp4.

Nothing renders until the person has approved the brand kit and the cut, and plan.json checks. The
HyperFrames run is lowered in priority (nice 10 on Mac and Linux, below normal on Windows) so the
computer stays usable while it works; this process itself is never lowered. Each run also updates
work/render.json: per ratio, the window onto the take, the layout's warnings, the page's length,
the mix's loudness and what the ad was made from (a fingerprint of the cut, the plan, the kit, the
music and the speed), and its layout (fade, panel or card), so the checks can name an ad made before the last
change and the review sheet can say where the text sits."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from adeditor import brand, faces, media, mix, page, paths, plan, speed, verify
from adeditor.cli import command
from adeditor.paths import Job

HYPERFRAMES = "hyperframes@0.7.5"
RATIOS = paths.RATIOS
RENDER = ["render", "-o", "renders/silent.mp4", "--fps", "30", "--quality", "high", "--crf", "16", "--workers", "4",
          "--quiet"]


def _parsed(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inputs(job: Job) -> str:
    """What an ad is made from, as a short hash: the cut (at its speed), the plan, the kit (its approval
    aside) and the music file's own bytes. Every ratio shares them, so a change to any one makes every
    ad made before it out of date."""
    kit = _parsed(job.brand / "kit.json")
    kit = {k: v for k, v in kit.items() if k != "approved"} if isinstance(kit, dict) else kit
    music = kit.get("music") if isinstance(kit, dict) else None
    made_from = {"cut": _parsed(job.work / "cut.json"), "plan": _parsed(job.work / "plan.json"), "kit": kit,
                 "speed": speed.current(job), "music": _file_hash(job.brand / music) if music else None}
    return hashlib.sha256(json.dumps(made_from, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class Refused(Exception):
    """The render can't start. The message is one plain sentence naming the command that fixes it."""


class RenderError(Exception):
    """HyperFrames stopped. The message carries the tail of what it said."""


def _nice() -> None:
    os.nice(10)


def low_priority() -> dict:
    """subprocess arguments that start a child at lower priority, leaving this process as it is."""
    if os.name == "nt":
        return {"creationflags": subprocess.BELOW_NORMAL_PRIORITY_CLASS}
    return {"preexec_fn": _nice}


# What HyperFrames says when headless Chrome is slow to load the page, as on a busy computer:
# "Navigation timeout of 10000 ms exceeded".
TIMED_OUT = re.compile(r"timeout of \d+ ?ms exceeded", re.I)


def _hyperframes(step: list[str], folder: Path) -> None:
    """Run one HyperFrames step on a page. A step that fails on a timeout is tried once more; any other
    failure stops the render with the tail of what HyperFrames said."""
    cmd = [media.tool("npx"), "-y", HYPERFRAMES, *step]
    for attempt in (1, 2):
        r = subprocess.run(cmd, cwd=folder, capture_output=True, encoding="utf-8", errors="replace", **low_priority())
        if r.returncode == 0:
            return
        said = "\n".join(f"{r.stdout or ''}\n{r.stderr or ''}".strip().splitlines()[-20:])
        if attempt == 1 and TIMED_OUT.search(said):
            print("HyperFrames timed out, trying once more.", flush=True)
            continue
        raise RenderError(f"HyperFrames {step[0]} stopped on the {folder.name} page (exit {r.returncode}):\n{said}")


def check_words(job: Job) -> None:
    """Refuse, in one sentence, unless the word check ran on this very cut (verify.json carries its
    fingerprint) and every spot it named has been listened to and accepted."""
    client, video = job.client, job.video.name
    path = job.work / "verify.json"
    cut = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))
    record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if record.get("cut") != verify.fingerprint(cut):
        raise Refused(f"The word check hasn't run on this cut of {video}: run adeditor cut {client} {video}.")
    waiting = verify.unaccepted(record)
    if waiting:
        times = ", ".join(dict.fromkeys(verify.clock(d["t"]) for d in waiting))
        spots = "a word" if len(waiting) == 1 else "words"
        take = "aroll.1x.mp4" if (job.work / "aroll.1x.mp4").is_file() else "aroll.mp4"
        raise Refused(f"The word check found {spots} that may be clipped at {times} in {video} and nobody has said "
                      f"they're there yet: listen there in {take}, and if every word is there, run "
                      f"adeditor accept-words {client} {video}.")


def gate(job: Job) -> None:
    """Refuse, in one sentence, unless the kit and the cut are approved, the word check and the face map
    belong to this cut, and the plan checks."""
    client, video = job.client, job.video.name
    try:
        brand.load(client)
    except brand.NotApproved:
        raise Refused(f"{client}'s brand kit isn't approved yet, so nothing renders: look at brand/swatch.html "
                      f"and run adeditor client approve {client}.") from None
    except brand.BadKit as exc:
        raise Refused(str(exc)) from None
    except brand.NoKit:
        raise Refused(f"There's no brand kit for {client} yet: run adeditor client new {client} <their site>.") from None
    strikes = job.work / "strikes.json"
    if not strikes.exists():
        raise Refused(f"There's no cut for {video} yet: run adeditor prepare {client} {video}.")
    if not json.loads(strikes.read_text(encoding="utf-8")).get("approved"):
        raise Refused(f"The cut for {video} isn't approved yet: read the strikes with the person, then run "
                      f"adeditor approve-cut {client} {video}.")
    if not (job.work / "cut.json").exists() or not (job.work / "aroll.mp4").exists():
        raise Refused(f"There's no cut take for {video} yet: run adeditor cut {client} {video}.")
    check_words(job)
    try:
        plan.load(job)
    except plan.PlanError as exc:
        n = len(exc.errors)
        raise Refused(f"plan.json has {n} {'problem' if n == 1 else 'problems'}: run adeditor plan check {client} "
                      f"{video} to see {'it' if n == 1 else 'them'}.") from None
    face_map = job.work / "faces.json"
    if not face_map.exists():
        raise Refused(f"There's no face map for {video} yet: run adeditor cut {client} {video}.")
    tracked_on, samples = faces.load(face_map)
    if tracked_on != verify.fingerprint(json.loads((job.work / "cut.json").read_text(encoding="utf-8"))):
        raise Refused(f"The face map for {video} was made on an earlier cut: run adeditor cut {client} {video}.")
    try:
        faces.filled(samples)
    except faces.NoFace as exc:
        raise Refused(f"We found the speaker's face in only {exc.share:.0%} of {video}, so the ads can't be framed: "
                      f"check the speaker is on camera and facing it, then run adeditor cut {client} {video}.") from None
    kit = brand.load(client)
    if kit.get("music") and not (job.brand / kit["music"]).is_file():
        raise Refused(f"The kit names a music bed that isn't in the brand folder: run adeditor client music {client}.")


def render(job: Job, ratios=RATIOS) -> dict[str, Path]:
    """Render each ratio and mix it. Returns {ratio: finished file}."""
    gate(job)
    kit = brand.load(job.client)
    resolved = plan.load(job)
    words = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))["words"]
    bed = job.brand / kit["music"] if kit.get("music") else None
    report_path = job.work / "render.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    made_from = inputs(job)
    # The ads are being made again, so the old review sheet no longer speaks for them; check writes a
    # new one. Until then the video isn't done (paths.done), even if a ratio fails part way.
    (job.ads / "review.html").unlink(missing_ok=True)
    done = {}
    for ratio in ratios:
        print(f"Rendering {ratio.replace('x', ':')}.", flush=True)
        folder = page.build(job, ratio).parent
        silent = folder / "renders" / "silent.mp4"
        silent.unlink(missing_ok=True)
        for step in (["lint"], ["validate"], RENDER):
            _hyperframes(step, folder)
        if not silent.is_file():
            raise RenderError(f"HyperFrames finished without writing renders/silent.mp4 on the {ratio} page.")
        facts = json.loads((folder / "page.json").read_text(encoding="utf-8"))
        job.ads.mkdir(parents=True, exist_ok=True)
        out = job.ads / f"{job.video.stem} ({ratio}).mp4"
        tmp = out.with_name(f"{out.stem}.part.mp4")
        try:
            got = mix.mix(job.work / "aroll.mp4", bed, silent, tmp, facts["duration"], facts["end"][0],
                          kit.get("music_bpm"), resolved["bed_offset_db"], stems_dir=job.work / "mix", words=words)
            tmp.replace(out)
        except BaseException:
            tmp.unlink(missing_ok=True)
            if not any(job.ads.iterdir()):  # an empty ads folder would mark the video as done
                job.ads.rmdir()
            raise
        report[ratio] = {"window": facts["window"], "mode": facts["window"]["mode"], "warnings": facts["warnings"],
                         "duration": facts["duration"],
                         "end_start": facts["end"][0], "file": out.name, "lufs": got["lufs"],
                         "true_peak": got["true_peak"], "bed_median_db": got["bed_median_db"], "inputs": made_from,
                         "mouth_covered": bool(facts.get("mouth_covered"))}
        report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        done[ratio] = out
    return done


def _args(p) -> None:
    p.add_argument("client")
    p.add_argument("video")
    p.add_argument("--ratio", action="append", choices=RATIOS,
                   help="render only this ratio (give it again for another); all three by default")


@command("render", help="Render the ad in 9:16, 4:5 and 1:1 and mix the sound under it.", args=_args)
def render_cmd(ns):
    try:
        job = paths.job(ns.client, ns.video)
        ratios = tuple(dict.fromkeys(ns.ratio)) if ns.ratio else RATIOS
        done = render(job, ratios)
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (RenderError, media.ToolError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not done:
        return 0
    report = json.loads((job.work / "render.json").read_text(encoding="utf-8"))
    print(f"Done. The ads are in clients/{job.client}/ads/{job.video.stem}/:")
    for ratio, path in done.items():
        r = report[ratio]
        under = f", the music {r['bed_median_db']:.1f} dB under the voice" if r["bed_median_db"] is not None else ""
        print(f"  {path.name}: {verify.clock(r['duration'])}, {r['lufs']:.1f} LUFS{under}")
    warnings = list(dict.fromkeys(w for ratio in done for w in report[ratio]["warnings"]))
    for w in warnings:
        print(f"Warning: {w}")
    from adeditor import checks  # the last gate: the ads are checked before anyone sees them

    return checks.run_and_tell(job)
