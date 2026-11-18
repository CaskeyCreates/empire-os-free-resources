"""The A-roll: the source cut to the plan's pieces and joined, picture and sound together.

Each piece is trimmed from the source, its audio faded in and out over 8 ms so no join clicks,
and the pieces are concatenated. The picture is trimmed by whole frames: each trim runs from half
a frame before the piece's first frame to half a frame before the frame after its last, so a time
rounded in the graph can never gain or lose a frame and pull the sound out of sync."""
from pathlib import Path

from adeditor import media

FADE = 0.008  # seconds of audio fade at each side of every piece
# The source audio on the file's own clock before any trim, as media.extract_audio reads it: when
# the audio starts after the picture, silence fills the start, so a piece that begins before the
# first sample keeps its sound where the plan put it instead of pulling it early.
CLOCK = "aresample=async=1:first_pts=0"


def filter_graph(segments: list[dict], fps: float) -> str:
    """The ffmpeg filter graph for `segments` ([{"src": [a, b], ...}]) from input 0."""
    half = 0.5 / (fps if fps and fps > 0 else 30.0)
    parts, labels = [], []
    for k, seg in enumerate(segments):
        a, b = (float(x) for x in seg["src"])
        parts.append(f"[0:v]trim=start={max(0.0, a - half):.6f}:end={b - half:.6f},setpts=PTS-STARTPTS[v{k}]")
        parts.append(f"[0:a]{CLOCK},atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={FADE},afade=t=out:st={b - a - FADE:.6f}:d={FADE}[a{k}]")
        labels.append(f"[v{k}][a{k}]")
    return ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segments)}:v=1:a=1[v][a]"


def build(src: Path, cut: dict, out: Path) -> Path:
    """Write the A-roll for `cut` (cut.json) from `src` to `out`. The filter graph goes to a file
    beside it (`aroll.fc`), since a long take's graph is too long for a command line."""
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    script = out.with_suffix(".fc")
    script.write_text(filter_graph(cut["segments"], media.probe(src).fps), encoding="utf-8")

    def encode(graph_option: str):
        media.run([
            "ffmpeg", "-y", "-v", "error", "-i", str(src),
            graph_option, str(script), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", "15", "-preset", "slow", "-r", "30", "-g", "30", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(out),
        ])

    # ffmpeg 7 and later read the graph file with -/filter_complex and call -filter_complex_script
    # deprecated; ffmpeg 6 knows only the old option, so it gets that one.
    try:
        encode("-/filter_complex")
    except media.ToolError as exc:
        if "/filter_complex" not in str(exc):
            raise
        encode("-filter_complex_script")
    return out
