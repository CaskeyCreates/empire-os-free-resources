import json

import pytest

from adeditor import stock


@pytest.mark.parametrize("good", ["pixabay-1", "pexels-1234567", "own-my-clip", "own-a1-b2", "own-7"])
def test_valid_ids(good):
    assert stock.valid_id(good)


@pytest.mark.parametrize("bad", ["1234567", "pixabay-", "pixabay-12a", "pexels-../1", "own-", "own-My-Clip", "own--x", "own-x-", "own-a/b",
                                 "own-..", "pixabay-1.mp4", "other-1", "", "pixabay-1\n", "own-é", "pixabay-\u0661\u0662", "pexels-\u0661"])
def test_invalid_ids(bad):
    assert not stock.valid_id(bad)


def test_source_of_and_slug():
    assert [stock.source_of(i) for i in ("pixabay-1", "pexels-2", "own-x")] == ["pixabay", "pexels", "own"]
    assert stock.slug("Office Tour (final) v2") == "office-tour-final-v2" and stock.slug("日本") == "clip"


def test_own_clips_are_slugged_from_the_file_stem_and_only_video_files(tmp_path):
    own = tmp_path / "own"
    own.mkdir()
    for name in ["Office Tour.MP4", "team_photo.mov", "x.m4v", "notes.txt", "pic.jpg", "Office-Tour.mov"]:
        (own / name).write_bytes(b"x")
    (own / "sub").mkdir()
    found = stock.own_clips(tmp_path)
    assert sorted(found) == ["own-office-tour", "own-office-tour-2", "own-team-photo", "own-x"]
    assert found["own-team-photo"].name == "team_photo.mov"
    assert stock.file_for("own-x", tmp_path) == own / "x.m4v"


def test_file_for_refuses_anything_that_is_not_an_id_and_names_a_missing_own_clip(tmp_path):
    with pytest.raises(stock.StockError):
        stock.file_for("../../evil", tmp_path)
    with pytest.raises(stock.StockError, match="own-nope"):
        stock.file_for("own-nope", tmp_path)
    assert stock.file_for("pixabay-5", tmp_path) == tmp_path / "pixabay-5.mp4"


def clip(i, source="pixabay"):
    return {"id": f"{source}-{i}", "url": f"https://x.test/{i}/", "user": f"User {i}", "user_url": f"https://x.test/@{i}",
            "file": {"link": f"https://cdn.test/{i}.mp4", "width": 1080, "height": 1920}}


class Resp:
    def __init__(self, chunks):
        self._chunks = chunks

    def iter_content(self, size):
        return iter(self._chunks)

    def raise_for_status(self):
        pass


def test_fetch_caches_by_id_and_writes_credits_with_the_source(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(stock.requests, "get", lambda url, **kw: calls.append((url, kw)) or Resp([b"abc", b"def"]))
    cache = tmp_path / "broll"
    out = stock.fetch([clip(11), clip(22, "pexels")], cache)
    assert [p.name for p in out] == ["pixabay-11.mp4", "pexels-22.mp4"] and out[0].read_bytes() == b"abcdef"
    assert all("headers" not in kw and "params" not in kw for _, kw in calls)  # no key goes to a CDN
    stock.fetch([clip(11)], cache)
    assert len(calls) == 2
    credits = json.loads((cache / "credits.json").read_text(encoding="utf-8"))
    assert credits["pexels-22"] == {"source": "pexels", "user": "User 22", "user_url": "https://x.test/@22", "url": "https://x.test/22/"}


@pytest.mark.parametrize("bad", ["../../evil", "12a", "", "1/2", "pixabay-../x", "own-clip", "7"])
def test_a_stock_id_that_is_not_prefixed_digits_never_becomes_a_file_name(tmp_path, bad):
    with pytest.raises(stock.StockError):
        stock.fetch([{**clip(7), "id": bad}], tmp_path)
    assert list(tmp_path.iterdir()) == []  # nothing written, not even credits.json
