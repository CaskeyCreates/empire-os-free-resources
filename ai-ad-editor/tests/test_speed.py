import copy
import json
import math
import wave

import numpy as np
import pytest

from adeditor import cli, media, speed
from adeditor.paths import Job


def sample_cut() -> dict:
    return {
        "segments": [{"src": [1.0, 6.0], "out": [0.0, 5.0]}, {"src": [8.0, 13.0], "out": [5.0, 10.0]}],
        "words": [
            {"text": "hello", "start": 0.4, "end": 0.9, "src": 1.4},
            {"text": "there", "start": 10.0, "end": 10.6, "src": 13.0},
        ],
        "duration": 100.0,
        "floor_db": -60.1,
        "threshold_db": -48.1,
        "tight_joins": [5.0, 100.0],
        "tight_words": [
            {"t": 5.0, "before": {"text": "hello", "t": 0.4}, "after": {"text": "there", "t": 10.0}},
            {"t": 100.0, "before": {"text": "there", "t": 10.0}, "after": None},
        ],
        "struck": [{"text": "um", "src": 6.5, "t": 5.0}],
    }


@pytest.fixture
def job(tmp_path, video, speech, monkeypatch):
    monkeypatch.setattr("adeditor.paths.ROOT", tmp_path / "repo")
    j = Job("acme", tmp_path / "take.mp4")
    j.video.write_bytes(b"x")
    j.work.mkdir(parents=True)
    (j.work / "cut.json").write_text(json.dumps(sample_cut(), indent=1), encoding="utf-8")
    (j.work / "aroll.mp4").write_bytes(video(2, 160, 90, speech([("w", 2.0)])).read_bytes())
    return j


def cut_of(job) -> dict:
    return json.loads((job.work / "cut.json").read_text(encoding="utf-8"))


def test_speed_divides_every_time(job):
    speed.apply(job, 1.25)
    cut = cut_of(job)
    assert cut["words"][1]["start"] == 8.0
    assert cut["duration"] == 80.0
    assert speed.current(job) == 1.25


def test_speed_divides_only_output_clock_times(job):
    original = sample_cut()
    speed.apply(job, 1.25)
    cut = cut_of(job)
    f = lambda t: round(t / 1.25, 4)
    # Source clock: untouched.
    for new, old in zip(cut["segments"], original["segments"]):
        assert new["src"] == old["src"]
        assert new["out"] == [f(old["out"][0]), f(old["out"][1])]
    for new, old in zip(cut["words"], original["words"]):
        assert new["src"] == old["src"] and new["text"] == old["text"]
        assert (new["start"], new["end"]) == (f(old["start"]), f(old["end"]))
    assert cut["duration"] == f(original["duration"])
    assert cut["tight_joins"] == [f(t) for t in original["tight_joins"]]
    for new, old in zip(cut["tight_words"], original["tight_words"]):
        assert new["t"] == f(old["t"])
        for side in ("before", "after"):
            if old[side] is None:
                assert new[side] is None
            else:
                assert new[side] == {"text": old[side]["text"], "t": f(old[side]["t"])}
    for new, old in zip(cut["struck"], original["struck"]):
        assert new == {"text": old["text"], "src": old["src"], "t": f(old["t"])}
    # Everything else is carried over as it was.
    assert cut["floor_db"] == original["floor_db"] and cut["threshold_db"] == original["threshold_db"]
    assert set(cut) == set(original)


def test_speed_twice_equals_once_from_1x(job, tmp_path, video, speech, monkeypatch):
    speed.apply(job, 1.25)
    speed.apply(job, 1.5)
    twice = (job.work / "cut.json").read_bytes()

    monkeypatch.setattr("adeditor.paths.ROOT", tmp_path / "repo2")
    other = Job("acme", tmp_path / "take.mp4")
    other.work.mkdir(parents=True)
    (other.work / "cut.json").write_text(json.dumps(sample_cut(), indent=1), encoding="utf-8")
    (other.work / "aroll.mp4").write_bytes(video(2, 160, 90, speech([("w", 2.0)])).read_bytes())
    speed.apply(other, 1.5)
    assert (other.work / "cut.json").read_bytes() == twice


def test_speed_one_restores_the_1x_pair(job):
    before = (job.work / "cut.json").read_bytes()
    speed.apply(job, 1.5)
    speed.apply(job, 1)
    assert (job.work / "cut.json").read_bytes() == before
    assert speed.current(job) == 1.0


def test_speed_current_is_1_when_absent(job):
    assert speed.current(job) == 1.0


@pytest.mark.parametrize("bad", [0.9, 2.1])
def test_speed_out_of_range_is_refused(job, bad):
    with pytest.raises(ValueError, match="between 1 and 2"):
        speed.apply(job, bad)
    assert not (job.work / "cut.1x.json").exists()


def test_speed_keeps_pitch(job, tmp_path):
    rate = 48000
    tone = tmp_path / "tone.wav"
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration=4",
               str(tone)])
    src = tmp_path / "tone.mp4"
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=4",
               "-i", str(tone), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)])
    (job.work / "aroll.mp4").write_bytes(src.read_bytes())
    speed.apply(job, 1.25)

    out = job.work / "aroll.mp4"
    wav = tmp_path / "out.wav"
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-vn", "-ac", "1", "-ar", str(rate), str(wav)])
    with wave.open(str(wav), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)
    spectrum = np.abs(np.fft.rfft(data * np.hanning(len(data))))
    peak = np.argmax(spectrum) * rate / len(data)
    assert abs(peak - 440) <= 5

    expected = media.probe(src).duration / 1.25
    assert abs(media.probe(out).duration - expected) <= 1 / 30


def test_speed_command_says_the_new_length(job, capsys):
    data = sample_cut()
    data["duration"] = 172.0
    (job.work / "cut.json").write_text(json.dumps(data), encoding="utf-8")
    code = cli.main(["speed", "acme", str(job.video), "1.25"])
    assert code == 0
    assert "2:52 at 1x is now 2:18 at 1.25x" in capsys.readouterr().out


def test_a_new_cut_drops_the_old_1x_pair_and_keeps_the_speed(tmp_path, monkeypatch, take, video):
    from adeditor import paths, transcribe

    three = [("s", 0.5), ("w", 0.5), ("s", 0.6), ("w", 0.4), ("s", 0.6), ("w", 0.5), ("s", 0.5)]
    monkeypatch.setattr(paths, "ROOT", tmp_path / "root")
    wav, words, env = take(three, texts=["one", "two", "three"])
    job = Job("acme", video(4, 320, 240, wav))
    job.work.mkdir(parents=True)
    (job.work / "transcript.json").write_text(json.dumps({"words": words}))
    (job.work / "envelope.json").write_text(json.dumps(env))
    job.brand.mkdir(parents=True)
    (job.brand / "kit.json").write_text(json.dumps({"keyterms": []}))

    def write_strikes(*spans):
        data = {"strikes": [{"from": a, "to": b, "why": "test"} for a, b in spans], "fillers": [], "approved": True}
        (job.work / "strikes.json").write_text(json.dumps(data))

    def hear(audio, keyterms, cache_dir):
        # Scribe hears the 1x cut: the word check always runs before the speed is applied.
        return [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in cut_of(job)["words"]]

    monkeypatch.setattr(transcribe, "transcribe", hear)
    run = lambda: cli.main(["cut", job.client, str(job.video)])

    write_strikes()
    assert run() == 0
    speed.apply(job, 1.25)
    old_1x = (job.work / "cut.1x.json").read_bytes()

    write_strikes((1, 1))  # strike the middle word
    assert run() == 0
    new_1x = (job.work / "cut.1x.json").read_bytes()
    assert new_1x != old_1x
    assert json.loads(new_1x)["duration"] < json.loads(old_1x)["duration"]
    assert speed.current(job) == 1.25
    assert cut_of(job) == speed._divide(json.loads(new_1x), 1.25)
    assert (job.work / "aroll.1x.mp4").exists()
    assert media.probe(job.work / "aroll.mp4").duration < media.probe(job.work / "aroll.1x.mp4").duration
