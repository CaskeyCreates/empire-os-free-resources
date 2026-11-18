import json

import pytest

from adeditor import media, paths, transcribe
from adeditor.paths import Job

LONG = "a keyterm that is far too long to be accepted because it has many words"


class Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


BODY = {"words": [
    {"text": "Hello", "start": 0, "end": 1, "type": "word"},
    {"text": " ", "start": 1, "end": 1.1, "type": "spacing"},
    {"text": "(laughs)", "start": 1.1, "end": 1.5, "type": "audio_event"},
    {"text": "world", "start": 1.5, "end": 2, "type": "word"},
]}


@pytest.fixture
def post(monkeypatch):
    calls = []
    monkeypatch.setattr(transcribe.keys, "get", lambda name: "test-key")

    def fake(url, **kw):
        kw["file_name"] = kw["files"]["file"][0]
        calls.append((url, kw))
        return Resp(BODY)

    monkeypatch.setattr(transcribe.requests, "post", fake)
    return calls


def test_scribe_request_shape(post, speech, tmp_path):
    transcribe.transcribe(speech([("w", 0.5)]), ["Northbank", LONG, "Northbank", "bad<>{}[]\\term"], tmp_path / "c")
    url, kw = post[0]
    assert url == "https://api.elevenlabs.io/v1/speech-to-text"
    assert kw["headers"] == {"xi-api-key": "test-key"}
    fields = kw["data"]
    assert ("model_id", "scribe_v2") in fields
    assert ("timestamps_granularity", "word") in fields
    sent = [v for k, v in fields if k == "keyterms"]
    assert sent == ["Northbank", "badterm"]


def test_only_words_are_kept(post, speech, tmp_path):
    words = transcribe.transcribe(speech([("w", 0.5)]), [], tmp_path / "c")
    assert words == [{"word": "Hello", "start": 0.0, "end": 1.0}, {"word": "world", "start": 1.5, "end": 2.0}]
    assert all(isinstance(w["start"], float) and isinstance(w["end"], float) for w in words)


def test_cached_audio_is_not_sent_twice(post, speech, tmp_path):
    audio = speech([("w", 0.5)])
    first = transcribe.transcribe(audio, ["Northbank"], tmp_path / "c")
    second = transcribe.transcribe(audio, ["Northbank"], tmp_path / "c")
    assert len(post) == 1 and first == second


def test_silent_source_stops_before_any_call(post, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    src = tmp_path / "silent.mp4"
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
               "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-shortest", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)])
    job = Job("example", src)
    with pytest.raises(transcribe.NoSound) as err:
        transcribe.prepare(job, [])
    assert "no sound we can hear" in str(err.value) and "-100.0 dBFS" in str(err.value)
    assert post == []
    assert (job.work / "envelope.json").exists()


def test_prepare_writes_the_three_files(post, speech, video, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    src = video(2, 320, 240, speech([("w", 2.0)]))
    job = Job("example", src)
    words = transcribe.prepare(job, ["Northbank"])
    assert len(words) == 2
    assert json.loads((job.work / "transcript.json").read_text())["words"] == words
    assert (job.work / "audio.wav").exists() and (job.work / "envelope.json").exists()


def test_a_video_with_no_audio_track_stops_plainly_before_any_call(post, video, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    job = Job("example", video(2, 320, 240, audio=None))
    with pytest.raises(transcribe.NoSound) as err:
        transcribe.prepare(job, [])
    assert "no sound we can hear" in str(err.value) and "no sound track" in str(err.value)
    assert post == []


def test_prepare_on_a_video_with_no_audio_track_says_so_on_one_line(post, video, tmp_path, monkeypatch, capsys):
    from adeditor import cli

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    src = video(2, 320, 240, audio=None)
    assert cli.main(["prepare", "example", str(src)]) == 1
    err = capsys.readouterr().err
    assert "no sound we can hear" in err and "Traceback" not in err and "ffmpeg" not in err
