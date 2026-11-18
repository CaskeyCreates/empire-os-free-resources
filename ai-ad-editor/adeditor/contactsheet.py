"""One JPEG to choose B-roll by eye: a row a clip, four frames spread through it, the clip id, its
length and who it's from under the row, and a last line saying where the videos come from."""
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from adeditor import media

FRAME_POINTS = (0.10, 0.35, 0.60, 0.85)
CELL_PORTRAIT = (150, 267)
CELL_LANDSCAPE = (267, 150)
LABEL_H = 24


def spread(items: list, count: int = len(FRAME_POINTS)) -> list:
    """`count` items spread through the list at 10, 35, 60 and 85% (or all of them if fewer)."""
    if len(items) <= count:
        return items
    return [items[min(int(p * len(items)), len(items) - 1)] for p in FRAME_POINTS[:count]]


def video_frames(src: Path, duration: float | None = None) -> list[Image.Image]:
    """Four frames from a video file at 10, 35, 60 and 85% of its length."""
    length = duration if duration else media.probe(src).duration
    tmp = Path(tempfile.mkdtemp(prefix="adeditor-frames-"))
    try:
        out = []
        for n, p in enumerate(FRAME_POINTS):
            jpg = tmp / f"f{n}.jpg"
            media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(length * p, 0):.3f}", "-i", str(src),
                       "-frames:v", "1", "-q:v", "3", str(jpg)])
            if jpg.exists():
                with Image.open(jpg) as im:
                    out.append(im.convert("RGB"))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sheet(rows: list[tuple[str, list[Image.Image]]], source_line: str, out: Path, portrait: bool = True) -> Path:
    """Rows are (label, frames). `source_line` goes on the last line, where the videos are from."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cw, ch = CELL_PORTRAIT if portrait else CELL_LANDSCAPE
    row_h = ch + LABEL_H
    img = Image.new("RGB", (len(FRAME_POINTS) * cw, row_h * len(rows) + LABEL_H), (16, 16, 16))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=14)  # Pillow's bundled font: no system path, same on Mac and Windows
    for r, (label, frames) in enumerate(rows):
        for c, frame in enumerate(frames[:len(FRAME_POINTS)]):
            frame = frame.copy()
            frame.thumbnail((cw, ch))
            img.paste(frame, (c * cw + (cw - frame.width) // 2, r * row_h + (ch - frame.height) // 2))
        draw.text((6, r * row_h + ch + 4), label, fill=(255, 255, 255), font=font)
    draw.text((6, row_h * len(rows) + 4), source_line, fill=(255, 255, 255), font=font)
    img.save(out, "JPEG", quality=88)
    return out
