"""ffmpeg and ffprobe helpers. Subprocesses take argument lists, never shell strings."""
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ToolError(Exception):
    pass


@dataclass
class Probe:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    rotation: int


def tool(name: str) -> str:
    """Full path of an executable. On Windows npx is npx.cmd, which CreateProcess
    cannot find from a bare name, so every subprocess goes through this."""
    found = shutil.which(name)
    if not found:
        raise ToolError(f"{name} was not found on your PATH. Run adeditor doctor for how to install it.")
    return found


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    args = [tool(args[0]), *args[1:]]
    kw.setdefault("capture_output", True)
    # Tools print UTF-8 (HyperFrames uses tick marks); never use the Windows locale encoding.
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    kw.setdefault("check", False)
    result = subprocess.run(args, **kw)
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-20:])
        raise ToolError(f"{Path(args[0]).name} failed (exit {result.returncode}):\n{tail}")
    return result


def _fraction(text: str) -> float:
    try:
        num, _, den = text.partition("/")
        return float(num) / float(den) if den and float(den) else float(num)
    except ValueError:
        return 0.0


def probe(path) -> Probe:
    out = run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    ).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ToolError(f"{path} has no video stream")
    rotation = 0
    for sd in video.get("side_data_list", []):
        if "rotation" in sd:
            rotation = int(round(float(sd["rotation"])))
    if not rotation:
        rotation = int(round(float(video.get("tags", {}).get("rotate", 0) or 0)))
    rotation %= 360
    width, height = int(video["width"]), int(video["height"])
    if rotation in (90, 270):
        width, height = height, width
    fps = _fraction(video.get("avg_frame_rate", "0/1")) or _fraction(video.get("r_frame_rate", "0/1"))
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return Probe(width, height, fps, duration, has_audio, rotation)


def extract_audio(src, out_wav, rate: int = 16000) -> Path:
    """Mono 32-bit float WAV on the file's own clock: when the audio stream starts after the
    picture (some cameras and phones write it so), silence fills the start, so a time in the WAV is
    the same time ffmpeg's trims and the picture use."""
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-af", "aresample=async=1:first_pts=0",
         "-ac", "1", "-ar", str(rate), "-c:a", "pcm_f32le", str(out_wav)])
    return out_wav


def frames(src, out_dir, fps: float = 2, width: int = 640) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", f"fps={fps},scale={width}:-2",
         str(out_dir / "frame_%05d.jpg")])
    return sorted(out_dir.glob("frame_*.jpg"))
