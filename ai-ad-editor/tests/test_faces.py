import json

import pytest

from adeditor import faces, media
from adeditor.faces import NoFace

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def sample(t, found=True):
    return {"t": t, "x": 0.3, "y": 0.2, "w": 0.2, "h": 0.25} if found else {"t": t, "x": None, "y": None, "w": None, "h": None}


def test_lost_face_is_filled_and_too_few_fails():
    got = [sample(i * 0.5, found=i not in (0, 4, 5)) for i in range(10)]
    got[1] = {**got[1], "x": 0.31}  # a distinct value to prove which neighbour filled each gap
    got[6] = {**got[6], "x": 0.66}
    filled = faces.filled(got)
    assert len(filled) == 10 and all(f["x"] is not None for f in filled)
    assert filled[0]["x"] == 0.31  # nearest found is sample 1
    assert filled[4]["x"] == 0.3 and filled[5]["x"] == 0.66  # sample 3 is nearer 4, sample 6 is nearer 5
    assert [f["t"] for f in filled] == [i * 0.5 for i in range(10)]  # times never move
    assert got[0]["x"] is None  # the input is left alone

    too_few = [sample(i * 0.5, found=i >= 6) for i in range(10)]
    with pytest.raises(NoFace) as err:
        faces.filled(too_few)
    assert err.value.share == pytest.approx(0.4)
    assert "40%" in str(err.value)


def test_exactly_half_is_enough_and_none_found_fails():
    half = [sample(i * 0.5, found=i % 2 == 0) for i in range(10)]
    assert len(faces.filled(half)) == 10
    with pytest.raises(NoFace) as err:
        faces.filled([sample(0, found=False)])
    assert err.value.share == 0
    with pytest.raises(NoFace):
        faces.filled([])


def test_track_needs_the_model_and_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setattr(faces, "MODEL_PATH", tmp_path / "nope.tflite")
    with pytest.raises(faces.ModelMissing) as err:
        faces.track(tmp_path / "x.mp4")
    assert "adeditor doctor --fix" in str(err.value)


needs_model = pytest.mark.skipif(not faces.MODEL_PATH.exists(),
                                 reason="run adeditor doctor --fix to fetch the face model")


@needs_model
@pytest.mark.slow
def test_detector_finds_a_face(tmp_path):
    # tests/fixtures/face.jpg is 960x1306 (see tests/fixtures/README.md). Hand-measured box, as
    # fractions of the frame: forehead to chin and cheek to cheek.
    want = {"x": 0.370, "y": 0.233, "w": 0.235, "h": 0.226}
    clip = tmp_path / "work" / "aroll.1x.mp4"
    clip.parent.mkdir()
    media.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "30", "-i", str(FIXTURES / "face.jpg"),
               "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)])
    got = faces.track(clip, fps=2, cut="the-cut")
    assert len(got) >= 3
    assert got[0]["t"] == 0 and got[1]["t"] == pytest.approx(0.5)
    for f in got:
        assert f["x"] is not None
        assert abs(f["x"] - want["x"]) < 0.10 and abs(f["y"] - want["y"]) < 0.10
        assert abs(f["w"] - want["w"]) < 0.10 and abs(f["h"] - want["h"]) < 0.10
    assert faces.load(clip.parent / "faces.json") == ("the-cut", got)


@needs_model
@pytest.mark.slow
def test_frames_in_a_non_ascii_folder_are_read(tmp_path):
    folder = tmp_path / "Zoë café"
    folder.mkdir()
    frame = folder / "frame_00001.jpg"
    frame.write_bytes((FIXTURES / "face.jpg").read_bytes())
    got = faces._detect([frame], fps=2)
    assert len(got) == 1 and got[0]["x"] is not None and abs(got[0]["x"] - 0.370) < 0.10


def test_the_face_map_carries_the_cut_it_was_tracked_on(tmp_path, monkeypatch):
    monkeypatch.setattr(faces, "MODEL_PATH", tmp_path / "model.tflite")
    (tmp_path / "model.tflite").write_bytes(b"model")
    monkeypatch.setattr(faces.media, "frames", lambda video, out, fps, width: ["a.jpg", "b.jpg"])
    samples = [{"t": 0.0, "x": 0.4, "y": 0.3, "w": 0.2, "h": 0.2}, {"t": 0.5, "x": None, "y": None, "w": None, "h": None}]
    monkeypatch.setattr(faces, "_detect", lambda frames, fps: samples)
    clip = tmp_path / "aroll.mp4"
    assert faces.track(clip, cut="abc123") == samples
    saved = json.loads((tmp_path / "faces.json").read_text(encoding="utf-8"))
    assert saved == {"cut": "abc123", "samples": samples}
    assert faces.load(tmp_path / "faces.json") == ("abc123", samples)


def test_an_old_face_map_reads_as_belonging_to_no_cut(tmp_path):
    samples = [{"t": 0.0, "x": 0.4, "y": 0.3, "w": 0.2, "h": 0.2}]
    (tmp_path / "faces.json").write_text(json.dumps(samples), encoding="utf-8")
    assert faces.load(tmp_path / "faces.json") == (None, samples)


def test_native_log_lines_are_silenced_unless_debugging(capfd, monkeypatch):
    import os

    monkeypatch.delenv("ADEDITOR_DEBUG", raising=False)
    with faces.quiet_native():
        os.write(2, b"INFO: [environment.cc:36] Creating LiteRT environment\n")
    os.write(2, b"after\n")
    assert capfd.readouterr().err == "after\n"  # silenced inside, and stderr works again after
    monkeypatch.setenv("ADEDITOR_DEBUG", "1")
    with faces.quiet_native():
        os.write(2, b"INFO: kept for whoever is fixing the tool\n")
    assert "kept for whoever" in capfd.readouterr().err


def test_stderr_comes_back_when_the_detector_fails(capfd, monkeypatch):
    import os

    monkeypatch.delenv("ADEDITOR_DEBUG", raising=False)
    with pytest.raises(RuntimeError):
        with faces.quiet_native():
            raise RuntimeError("boom")
    os.write(2, b"still here\n")
    assert capfd.readouterr().err == "still here\n"


@needs_model
@pytest.mark.slow
def test_tracking_prints_no_mediapipe_info_lines():
    # A fresh process, since MediaPipe may log its setup only once per process.
    import os
    import subprocess
    import sys

    code = ("from pathlib import Path; from adeditor import faces; "
            f"print(faces._detect([Path({str(FIXTURES / 'face.jpg')!r})], 2)[0]['x'] is not None)")
    env = {k: v for k, v in os.environ.items() if k != "ADEDITOR_DEBUG"}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0 and r.stdout.strip() == "True", r.stderr
    assert "INFO" not in r.stderr and r.stderr.strip() == "", r.stderr
