"""`adeditor preview`: a still of each motion graphic at its fullest moment, before the full render.

Each ratio's page is built, then HyperFrames' snapshot step captures it at every graphic's `full_at`
time. The stills are copied to work/<stem>/preview/<ratio>-graphic-<n>.png so the plan can be looked at
and fixed for the cost of a few frames, not a render.

HyperFrames 0.7.5 writes the stills to <page folder>/snapshots/ as frame-NN-at-T.Ts.png. By default it
also sends them to a vision model whenever GEMINI_API_KEY is set; `--describe false` is always passed so
no frame of a client's video ever leaves this computer."""
import json
import os
import re
import shutil
import sys
from pathlib import Path

from adeditor import media, page, paths, render
from adeditor.cli import command
from adeditor.paths import Job

FRAME = re.compile(r"^frame-(\d+)-at-(\d+(?:\.\d+)?)s\.png$")


def snapshot_args(times: list[float]) -> list[str]:
    """The snapshot step's arguments. `--describe false` is never left out."""
    return ["snapshot", "--at", ",".join(f"{t:g}" for t in times), "--describe", "false"]


def _stills(folder: Path) -> list[tuple[float, Path]]:
    """(time, file) for each still the snapshot step wrote, in time order."""
    found = []
    for f in (folder / "snapshots").glob("frame-*.png"):
        m = FRAME.match(f.name)
        if m:
            found.append((float(m.group(2)), f))
    return sorted(found)


def preview(job: Job, ratios=paths.RATIOS) -> list[Path]:
    """Snapshot every graphic in every ratio. Returns the stills made, in ratio then graphic order."""
    render.gate(job)
    out_dir = job.work / "preview"
    made = []
    for ratio in ratios:
        index = page.build(job, ratio)
        folder = index.parent
        graphics = json.loads((folder / "page.json").read_text(encoding="utf-8"))["graphics"]
        if not graphics:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob(f"{ratio}-graphic-*.png"):
            old.unlink()
        snaps = folder / "snapshots"
        for old in snaps.glob("frame-*.png"):  # a still from an earlier run is never copied
            old.unlink()
        print(f"Looking at {len(graphics)} {'graphic' if len(graphics) == 1 else 'graphics'} in {ratio.replace('x', ':')}.",
              flush=True)
        # a second guard behind `--describe false`: the vision model's keys never reach the child
        env = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
        render._hyperframes(snapshot_args([g["full_at"] for g in graphics]), folder, env)
        stills = _stills(folder)
        if len(stills) != len(graphics):
            raise render.RenderError(f"HyperFrames wrote {len(stills)} stills on the {ratio} page for "
                                     f"{len(graphics)} graphics.")
        for g, (_, still) in zip(graphics, stills):
            dest = out_dir / f"{ratio}-graphic-{g['n']}.png"
            shutil.copyfile(still, dest)
            made.append(dest)
    return made


def _args(p) -> None:
    p.add_argument("client")
    p.add_argument("video")
    p.add_argument("--ratio", action="append", choices=paths.RATIOS,
                   help="preview only this ratio (give it again for another); all three by default")


@command("preview", help="Save a still of each motion graphic at its fullest, before the render.", args=_args)
def preview_cmd(ns):
    try:
        job = paths.job(ns.client, ns.video)
        ratios = tuple(dict.fromkeys(ns.ratio)) if ns.ratio else paths.RATIOS
        made = preview(job, ratios)
    except render.Refused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (render.RenderError, media.ToolError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not made:
        print("plan.json has no graphics to preview.")
        return 0
    print("Stills of each graphic at its fullest:")
    for path in made:
        print(f"  {path}")
    return 0
