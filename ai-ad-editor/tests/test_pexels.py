import time

import pytest

from adeditor import keys, pexels, stock


class Resp:
    def __init__(self, status=200, body=None, headers=None, chunks=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self._chunks = chunks or []

    def json(self):
        return self._body

    def iter_content(self, size):
        return iter(self._chunks)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise pexels.requests.HTTPError(str(self.status_code))


def item(vid, files, duration=12, user="Ana Lee"):
    return {"id": vid, "url": f"https://www.pexels.com/video/{vid}/", "duration": duration,
            "user": {"name": user, "url": "https://www.pexels.com/@ana"},
            "video_files": [{"width": w, "height": h, "file_type": t, "link": f"https://cdn.test/{vid}-{w}x{h}.mp4"}
                            for w, h, t in files]}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(keys, "get", lambda name: "pexels-secret")
    pexels.LIMIT.clear()


def test_search_picks_a_1080_file(monkeypatch):
    seen = {}

    def get(url, **kw):
        seen.update(url=url, **kw)
        return Resp(200, {"videos": [item(1, [(720, 1280, "video/mp4"), (2160, 3840, "video/mp4"),
                                               (1080, 1920, "video/mp4"), (1080, 1920, "video/webm")])]},
                    {"X-Ratelimit-Remaining": "19000"})

    monkeypatch.setattr(pexels.requests, "get", get)
    [clip] = pexels.search("desk worker", None, "portrait", 5)
    assert (clip["file"]["width"], clip["file"]["height"]) == (1080, 1920)
    assert clip["file"]["link"].endswith("1-1080x1920.mp4")
    assert clip["user"] == "Ana Lee" and clip["id"] == "pexels-1" and clip["duration"] == 12
    assert seen["url"] == "https://api.pexels.com/v1/videos/search"
    assert seen["headers"] == {"Authorization": "pexels-secret"}
    assert seen["params"]["orientation"] == "portrait" and seen["params"]["per_page"] == 5
    assert pexels.LIMIT["remaining"] == 19000


def test_search_falls_back_to_the_largest_file(monkeypatch):
    monkeypatch.setattr(pexels.requests, "get", lambda *a, **k: Resp(200, {"videos": [
        item(2, [(540, 960, "video/mp4"), (720, 1280, "video/mp4")])]}))
    [clip] = pexels.search("x")
    assert clip["file"]["height"] == 1280


def test_rate_limit_is_said_plainly(monkeypatch):
    reset = int(time.time()) + 1800
    monkeypatch.setattr(pexels.requests, "get", lambda *a, **k: Resp(429, headers={"X-Ratelimit-Reset": str(reset)}))
    with pytest.raises(pexels.PexelsError) as err:
        pexels.search("x")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(reset))
    assert str(err.value) == f"Pexels says we've hit the hourly limit; try again after {when}"


def test_rate_limit_without_a_reset_says_an_hour_never_the_monthly_reset(monkeypatch):
    pexels.LIMIT["reset"] = int(time.time()) + 20 * 86400  # the monthly reset from an earlier 2xx
    monkeypatch.setattr(pexels.requests, "get", lambda *a, **k: Resp(429))
    with pytest.raises(pexels.PexelsError) as err:
        pexels.search("x")
    said = str(err.value).rsplit("after ", 1)[1]
    got = time.mktime(time.strptime(said, "%Y-%m-%d %H:%M"))
    assert abs(got - (time.time() + 3600)) <= 120


def test_a_bad_key_names_the_key_not_its_value(monkeypatch):
    monkeypatch.setattr(pexels.requests, "get", lambda *a, **k: Resp(401))
    with pytest.raises(pexels.PexelsError) as err:
        pexels.search("x")
    assert "PEXELS_API_KEY" in str(err.value) and "pexels-secret" not in str(err.value)


def test_a_clip_id_that_is_not_digits_is_dropped_from_a_search(monkeypatch):
    good = item(7, [(1080, 1920, "video/mp4")])
    for bad in ["../../evil", "12a", "", "1/2"]:
        evil = {**item(8, [(1080, 1920, "video/mp4")]), "id": bad}
        monkeypatch.setattr(pexels.requests, "get", lambda url, **kw: Resp(200, {"videos": [evil, good]}))
        assert [c["id"] for c in pexels.search("desk")] == ["pexels-7"]


def test_search_keeps_the_preview_stills_in_order(monkeypatch):
    v = item(3, [(1080, 1920, "video/mp4")])
    v["video_pictures"] = [{"id": 2, "nr": 1, "picture": "https://s.test/b.jpg"}, {"id": 1, "nr": 0, "picture": "https://s.test/a.jpg"}]
    monkeypatch.setattr(pexels.requests, "get", lambda *a, **k: Resp(200, {"videos": [v]}))
    assert pexels.search("x")[0]["previews"] == ["https://s.test/a.jpg", "https://s.test/b.jpg"]


def test_lookup_fetches_one_clip_by_its_number(monkeypatch):
    seen = []

    def get(url, **kw):
        seen.append((url, kw.get("headers")))
        return Resp(200, item(55, [(720, 1280, "video/mp4"), (1080, 1920, "video/mp4")]))

    monkeypatch.setattr(pexels.requests, "get", get)
    clip = pexels.lookup("pexels-55")
    assert clip["id"] == "pexels-55" and clip["file"]["link"].endswith("55-1080x1920.mp4")
    assert seen == [("https://api.pexels.com/v1/videos/videos/55", {"Authorization": "pexels-secret"})]


def test_lookup_says_when_pexels_has_no_such_video(monkeypatch):
    monkeypatch.setattr(pexels.requests, "get", lambda *a, **k: Resp(404))
    with pytest.raises(stock.StockError, match="no video pexels-9"):
        pexels.lookup("pexels-9")
