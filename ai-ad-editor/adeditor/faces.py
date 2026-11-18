"""Where the speaker's face is, sampled through the cut take.

MediaPipe's Tasks API FaceDetector with the BlazeFace short-range model, as Google's guide shows:
https://ai.google.dev/edge/mediapipe/solutions/vision/face_detector/python
A sample is the largest face in a frame as fractions of the frame (origin top-left), or None when
the detector found nothing. Times are on the 1x clock: they never change when the speed does."""
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import TypedDict

from adeditor import media
from adeditor.paths import ROOT

MODEL_PATH = ROOT / "models" / "blaze_face_short_range.tflite"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
             "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite")
MIN_SHARE = 0.5


class Face(TypedDict):
    t: float
    x: float | None
    y: float | None
    w: float | None
    h: float | None


class ModelMissing(Exception):
    pass


class NoFace(Exception):
    """Too few samples found a face to plan a crop from."""

    def __init__(self, share: float):
        self.share = share
        super().__init__(f"We found the speaker's face in only {share:.0%} of the video. "
                         "Check the speaker is on camera and facing it, then run the step again.")


def _clamp01(v: float) -> float:
    return round(min(1.0, max(0.0, v)), 4)


def _largest(detections, width: int, height: int):
    best = max(detections, key=lambda d: d.bounding_box.width * d.bounding_box.height, default=None)
    if best is None:
        return None
    b = best.bounding_box
    x0, y0 = _clamp01(b.origin_x / width), _clamp01(b.origin_y / height)
    x1, y1 = _clamp01((b.origin_x + b.width) / width), _clamp01((b.origin_y + b.height) / height)
    return {"x": x0, "y": y0, "w": round(x1 - x0, 4), "h": round(y1 - y0, 4)}


@contextlib.contextmanager
def quiet_native():
    """MediaPipe and LiteRT write INFO lines from C++ straight to the process's stderr (file 2), and
    no logging setting stops them, so file 2 points at the null device while the detector runs. With
    ADEDITOR_DEBUG=1 set, they're kept, for whoever is fixing the tool. The cost: if the detector's
    C++ aborts the process, its last words go to the null device too, so the run just ends with
    nothing said; run it again with ADEDITOR_DEBUG=1 to see why."""
    if os.environ.get("ADEDITOR_DEBUG", "").strip() not in ("", "0"):
        yield
        return
    sys.stderr.flush()
    saved = os.dup(2)
    try:
        with open(os.devnull, "wb") as null:
            os.dup2(null.fileno(), 2)
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved, 2)
        os.close(saved)


def _detect(frames: list[Path], fps: float) -> list[Face]:
    """One sample per frame file. MediaPipe and cv2 are never handed a path: a non-ASCII user
    name in a Windows profile breaks their C++ file open, so the model and every frame are read
    in Python and passed as bytes."""
    with quiet_native():
        return _detect_quietly(frames, fps)


def _detect_quietly(frames: list[Path], fps: float) -> list[Face]:
    import cv2
    import mediapipe as mp  # slow to import, so only when a video is tracked
    import numpy as np

    vision = mp.tasks.vision
    options = vision.FaceDetectorOptions(
        base_options=mp.tasks.BaseOptions(model_asset_buffer=MODEL_PATH.read_bytes()),
        running_mode=vision.RunningMode.IMAGE,
    )
    out: list[Face] = []
    with vision.FaceDetector.create_from_options(options) as detector:
        for i, frame in enumerate(frames):
            bgr = cv2.imdecode(np.frombuffer(Path(frame).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
            rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            box = _largest(detector.detect(image).detections, image.width, image.height)
            out.append({"t": round(i / fps, 4), **(box or {"x": None, "y": None, "w": None, "h": None})})
    return out


def track(video: Path, fps: float = 2, cut: str | None = None) -> list[Face]:
    """The largest face in each sampled frame of `video`. Writes faces.json beside the video as
    {"cut": the fingerprint of the cut it was tracked on, "samples": [...]}."""
    video = Path(video)
    if not MODEL_PATH.exists():
        raise ModelMissing("The face model isn't downloaded yet. Run: adeditor doctor --fix")
    with tempfile.TemporaryDirectory() as tmp:
        out = _detect(media.frames(video, tmp, fps=fps, width=640), fps)
    (video.parent / "faces.json").write_text(json.dumps({"cut": cut, "samples": out}, indent=1), encoding="utf-8")
    return out


def load(path: Path) -> tuple[str | None, list[Face]]:
    """(the cut it was tracked on, the samples) from a faces.json. A map written before maps named
    their cut is a bare list, and belongs to no cut."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return None, data
    return data.get("cut"), list(data.get("samples") or [])


def filled(faces: list[Face]) -> list[Face]:
    """Every sample with a face: a lost one takes the nearest found sample's box (the earlier one
    on a tie), keeping its own time. Raises NoFace when under half the samples found a face."""
    found = [f for f in faces if f["x"] is not None]
    share = len(found) / len(faces) if faces else 0.0
    if share < MIN_SHARE:
        raise NoFace(share)
    out: list[Face] = []
    for f in faces:
        if f["x"] is None:
            near = min(found, key=lambda g: (abs(g["t"] - f["t"]), g["t"]))
            f = {**near, "t": f["t"]}
        out.append(dict(f))
    return out
