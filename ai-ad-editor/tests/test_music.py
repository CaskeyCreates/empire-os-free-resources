import json

import pytest

from adeditor import brand, music, paths


class Resp:
    def __init__(self, status=200, content=b"ID3generated", text=""):
        self.status_code, self.content, self.text = status, content, text


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    folder = tmp_path / "clients/acme/brand"
    folder.mkdir(parents=True)
    kit = {"name": "Acme", "music": None, "music_bpm": None, "approved": True}
    (folder / "kit.json").write_text(json.dumps(kit))
    monkeypatch.setattr(music.keys, "get", lambda name: "test-key")
    return folder


def test_music_request_shape(client, monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append((url, kw))
        return Resp()

    monkeypatch.setattr(music.requests, "post", fake)
    music.make_bed("acme", "warm piano, 72 bpm", seconds=240, bpm=72)
    url, kw = calls[0]
    assert url == "https://api.elevenlabs.io/v1/music"
    assert kw["headers"]["xi-api-key"] == "test-key"
    assert kw["json"] == {"prompt": "warm piano, 72 bpm", "music_length_ms": 240000, "force_instrumental": True}
    assert (client / "music/bed.mp3").read_bytes() == b"ID3generated"
    kit = json.loads((client / "kit.json").read_text())
    assert kit["music"] == "music/bed.mp3" and kit["music_bpm"] == 72 and kit["approved"] is False


def test_an_api_error_names_the_status_not_the_key(client, monkeypatch):
    monkeypatch.setattr(music.requests, "post", lambda url, **kw: Resp(422, text="bad prompt"))
    with pytest.raises(music.MusicError) as err:
        music.make_bed("acme", "x")
    assert "422" in str(err.value) and "test-key" not in str(err.value)
    assert not (client / "music/bed.mp3").exists()


def test_length_must_be_within_the_api_range(client):
    with pytest.raises(ValueError):
        music.make_bed("acme", "x", seconds=2)
    with pytest.raises(ValueError):
        music.make_bed("acme", "x", seconds=601)


def test_own_track_is_copied_in(client, tmp_path):
    track = tmp_path / "mine.mp3"
    track.write_bytes(b"ID3mine")
    music.use_file("acme", track, bpm=100)
    assert (client / "music/bed.mp3").read_bytes() == b"ID3mine"
    assert json.loads((client / "kit.json").read_text())["music_bpm"] == 100


def test_own_track_in_another_format_becomes_mp3(client, tmp_path):
    import json as js

    from adeditor import media

    wav = tmp_path / "mine.wav"
    media.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=1", str(wav)])
    music.use_file("acme", wav)
    out = client / "music/bed.mp3"
    codec = media.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "json", str(out)]).stdout
    assert js.loads(codec)["streams"][0]["codec_name"] == "mp3"


def test_no_kit_is_a_plain_error(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(music.keys, "get", lambda name: "k")
    with pytest.raises(ValueError, match="client new"):
        music.make_bed("nobody", "x")
    assert not brand.kit_path("nobody").exists()
