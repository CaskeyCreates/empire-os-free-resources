import json
import os
import time

import pytest
import requests

from adeditor import keys, pixabay

KEY = "pbx-SECRET-key-0123456789"


class Resp:
    def __init__(self, status=200, body=None, text="", chunks=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = text
        self._chunks = chunks or []

    def json(self):
        return self._body

    def iter_content(self, size):
        return iter(self._chunks)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def hit(vid, sizes=((1920, 1080), (1280, 720), (640, 360), (320, 180)), user="Ana Lee", uid=42):
    names = ["large", "medium", "small", "tiny"]
    return {"id": vid, "pageURL": f"https://pixabay.com/videos/office-{vid}/", "duration": 12, "user": user, "user_id": uid,
            "videos": {n: {"url": f"https://cdn.test/{vid}-{n}.mp4", "width": w, "height": h, "size": 1, "thumbnail": f"https://cdn.test/{vid}-{n}.jpg"}
                       for n, (w, h) in zip(names, sizes)}}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(keys, "get", lambda name: KEY)


def test_search_request_shape(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(pixabay.requests, "get", lambda url, **kw: seen.append((url, kw)) or Resp(200, {"hits": [hit(1)]}))
    pixabay.search("x" * 300, tmp_path)
    url, kw = seen[0]
    assert url == "https://pixabay.com/api/videos/" and KEY not in url
    assert kw["params"] == {"key": KEY, "q": "x" * 100, "per_page": 12, "min_height": 1080}


def test_search_without_1080_results_asks_again_without_min_height(monkeypatch, tmp_path):
    seen = []

    def get(url, **kw):
        seen.append(kw["params"])
        return Resp(200, {"hits": [] if "min_height" in kw["params"] else [hit(5)]})

    monkeypatch.setattr(pixabay.requests, "get", get)
    clips = pixabay.search("rare thing", tmp_path)
    assert [c["id"] for c in clips] == ["pixabay-5"]
    assert "min_height" in seen[0] and "min_height" not in seen[1]


def test_rendition_with_1080_short_side_beats_720_and_2160():
    f = pixabay._pick_file(hit(1, sizes=((3840, 2160), (1920, 1080), (1280, 720), (640, 360)))["videos"])
    assert (f["width"], f["height"]) == (1920, 1080)
    portrait = pixabay._pick_file(hit(1, sizes=((2160, 3840), (1080, 1920), (720, 1280), (360, 640)))["videos"])
    assert (portrait["width"], portrait["height"]) == (1080, 1920)


def test_without_a_1080_rendition_the_largest_is_used_and_an_empty_large_is_skipped():
    h = hit(1, sizes=((1920, 1080), (1280, 720), (640, 360), (320, 180)))
    h["videos"]["large"].update(url="", size=0)
    f = pixabay._pick_file(h["videos"])
    assert f["width"] == 1280


def test_candidate_shape_and_credit_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay.requests, "get", lambda *a, **k: Resp(200, {"hits": [hit(9, user="Ana Lee", uid=42)]}))
    [clip] = pixabay.search("office", tmp_path)
    assert clip["id"] == "pixabay-9" and clip["url"] == "https://pixabay.com/videos/office-9/"
    assert clip["user"] == "Ana Lee" and clip["user_url"] == "https://pixabay.com/users/Ana%20Lee-42/"
    assert clip["preview_file"].endswith("9-tiny.mp4") and clip["file"]["link"].endswith("9-large.mp4")


def test_a_bad_hit_id_is_dropped(monkeypatch, tmp_path):
    bad = {**hit(1), "id": "../../evil"}
    monkeypatch.setattr(pixabay.requests, "get", lambda *a, **k: Resp(200, {"hits": [bad, hit(2)]}))
    assert [c["id"] for c in pixabay.search("x", tmp_path)] == ["pixabay-2"]


def test_the_cache_serves_a_second_search_within_24_hours_and_expires_after(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(pixabay.requests, "get", lambda url, **kw: calls.append(kw["params"]) or Resp(200, {"hits": [hit(1)]}))
    pixabay.search("office", tmp_path)
    pixabay.search("office", tmp_path)
    assert len(calls) == 1
    pixabay.search("kitchen", tmp_path)  # another query is another request
    assert len(calls) == 2
    for f in tmp_path.glob("search-*.json"):  # make every cached search 25 hours old
        old = time.time() - 25 * 3600
        os.utime(f, (old, old))
    pixabay.search("office", tmp_path)
    assert len(calls) == 3


def test_the_cache_files_never_hold_the_key(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay.requests, "get", lambda *a, **k: Resp(200, {"hits": [hit(1)]}))
    pixabay.search("office", tmp_path)
    files = list(tmp_path.glob("search-*.json"))
    assert files and all(KEY not in f.name and KEY not in f.read_text(encoding="utf-8") for f in files)


def test_the_key_is_in_no_message_on_a_connection_error_a_400_a_429_or_a_403(monkeypatch, tmp_path, capsys):
    def boom(url, **kw):
        raise requests.ConnectionError(f"HTTPSConnectionPool: {url}?key={kw['params']['key']}&q=office failed")

    cases = [boom, lambda url, **kw: Resp(400, text=f'[ERROR 400] "key={KEY}"'), lambda url, **kw: Resp(429, text="API rate limit exceeded"),
             lambda url, **kw: Resp(403)]
    said = []
    for fake in cases:
        monkeypatch.setattr(pixabay.requests, "get", fake)
        with pytest.raises(pixabay.PixabayError) as err:
            pixabay.search("office", tmp_path)
        said.append(str(err.value))
        assert KEY not in str(err.value) and KEY not in repr(err.value) and err.value.__cause__ is None
    assert "Wait a minute" in said[2] and "HTTP 400" in said[1] and "PIXABAY_API_KEY" in said[1]
    seen = capsys.readouterr()
    assert KEY not in seen.out and KEY not in seen.err
    assert list(tmp_path.iterdir()) == []  # a failed search saves nothing


def test_lookup_by_id_and_a_missing_video(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(pixabay.requests, "get", lambda url, **kw: seen.append(kw["params"]) or Resp(200, {"hits": [hit(77)]}))
    assert pixabay.lookup("pixabay-77", tmp_path)["id"] == "pixabay-77"
    assert seen[0]["id"] == "77"
    monkeypatch.setattr(pixabay.requests, "get", lambda *a, **k: Resp(200, {"hits": []}))
    with pytest.raises(pixabay.PixabayError, match="no usable video pixabay-5"):
        pixabay.lookup("pixabay-5", tmp_path)


def test_orientation_puts_matching_clips_first(monkeypatch, tmp_path):
    tall = hit(2, sizes=((1080, 1920), (720, 1280), (360, 640), (180, 320)))
    monkeypatch.setattr(pixabay.requests, "get", lambda *a, **k: Resp(200, {"hits": [hit(1), tall]}))
    assert [c["id"] for c in pixabay.search("x", tmp_path, "portrait")] == ["pixabay-2", "pixabay-1"]
    assert [c["id"] for c in pixabay.search("x", tmp_path, "landscape")] == ["pixabay-1", "pixabay-2"]


def test_tiny_file_is_downloaded_once(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(pixabay.requests, "get", lambda url, **kw: calls.append(url) or Resp(200, chunks=[b"abc"]))
    clip = {"id": "pixabay-4", "preview_file": "https://cdn.test/4-tiny.mp4"}
    first = pixabay.tiny_file(clip, tmp_path)
    pixabay.tiny_file(clip, tmp_path)
    assert first.name == "pixabay-4-tiny.mp4" and first.read_bytes() == b"abc" and calls == ["https://cdn.test/4-tiny.mp4"]


def test_the_json_cache_is_plain_json(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay.requests, "get", lambda *a, **k: Resp(200, {"hits": [hit(1)]}))
    pixabay.search("office", tmp_path)
    [f] = [f for f in tmp_path.glob("search-*.json")]
    assert json.loads(f.read_text(encoding="utf-8"))["hits"][0]["id"] == 1


def test_a_hit_with_no_tiny_rendition_previews_with_small_then_medium_never_the_large_file():
    h = hit(1)
    h["videos"]["tiny"]["url"] = ""
    assert pixabay._clip(h)["preview_file"].endswith("1-small.mp4")
    h["videos"]["small"]["url"] = ""
    assert pixabay._clip(h)["preview_file"].endswith("1-medium.mp4")
    h["videos"]["medium"]["url"] = ""
    clip = pixabay._clip(h)
    assert clip["preview_file"] == "" and clip["file"]["link"].endswith("1-large.mp4")  # still fetchable by --get


def test_a_clip_with_no_preview_downloads_nothing_for_the_sheet(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay.requests, "get", lambda *a, **k: pytest.fail("no download"))
    with pytest.raises(pixabay.PixabayError, match="no small preview"):
        pixabay.tiny_file({"id": "pixabay-1", "preview_file": ""}, tmp_path)


@pytest.mark.parametrize("uid", ["abc", None, "4a", "\u0661\u0662", ""])
def test_a_bad_user_id_drops_the_profile_link_not_the_clip(uid):
    clip = pixabay._clip({**hit(3), "user_id": uid})
    assert clip["user_url"] == "" and clip["url"] == "https://pixabay.com/videos/office-3/"


def test_an_answer_that_is_not_an_object_is_a_plain_error(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay.requests, "get", lambda *a, **k: Resp(200, [1, 2]))
    with pytest.raises(pixabay.PixabayError, match="couldn't read"):
        pixabay.search("x", tmp_path)
    assert list(tmp_path.iterdir()) == []
