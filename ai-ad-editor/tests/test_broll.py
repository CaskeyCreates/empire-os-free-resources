import json
import shutil

import pytest
import requests
from conftest import put_in_raw
from PIL import Image

from adeditor import broll, cli, contactsheet, keys, paths, pexels, pixabay, stock

KEY = "pbx-SECRET-key-0123456789"


def env(tmp_path, monkeypatch, **vals):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in vals.items()), encoding="utf-8")
    put_in_raw()
    return tmp_path / "clients" / "acme"


def test_pixabay_is_the_default_when_its_key_is_set(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, PIXABAY_API_KEY="a", PEXELS_API_KEY="b")
    assert broll.choose_source(None) == "pixabay"


def test_pexels_is_used_when_only_its_key_is_set(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, PEXELS_API_KEY="b")
    assert broll.choose_source(None) == "pexels"


def test_source_flag_wins_when_its_key_is_set(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, PIXABAY_API_KEY="a", PEXELS_API_KEY="b")
    assert broll.choose_source("pexels") == "pexels"


def test_source_flag_without_its_key_names_the_key(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, PIXABAY_API_KEY="a")
    with pytest.raises(stock.StockError, match="PEXELS_API_KEY"):
        broll.choose_source("pexels")


def test_no_key_says_so_and_points_to_own_clips(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch, ELEVENLABS_API_KEY="e")
    assert cli.main(["broll", "acme", "take1.mp4", "desk"]) == 1
    err = capsys.readouterr().err
    assert "No stock video key is set" in err and "--own" in err and "PIXABAY_API_KEY" in err


def test_query_get_and_own_are_exclusive(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, PIXABAY_API_KEY="a")
    assert cli.main(["broll", "acme", "take1.mp4"]) == 1
    assert cli.main(["broll", "acme", "take1.mp4", "x", "--get", "pixabay-1"]) == 1
    assert cli.main(["broll", "acme", "take1.mp4", "x", "--own"]) == 1


def hit(vid, user="Ana Lee"):
    names = ["large", "medium", "small", "tiny"]
    sizes = [(1080, 1920), (720, 1280), (360, 640), (180, 320)]
    return {"id": vid, "pageURL": f"https://pixabay.com/videos/office-{vid}/", "duration": 4, "user": user, "user_id": 42,
            "videos": {n: {"url": f"https://cdn.test/{vid}-{n}.mp4", "width": w, "height": h, "size": 1} for n, (w, h) in zip(names, sizes)}}


class Resp:
    def __init__(self, status=200, body=None, content=b"", chunks=None):
        self.status_code, self._body, self.content, self._chunks = status, body or {}, content, chunks or []
        self.headers = {}

    def json(self):
        return self._body

    def iter_content(self, size):
        return iter(self._chunks)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def pixabay_net(tmp_path, monkeypatch, video):
    """Pixabay's API and CDN, faked: the API answers two hits, the CDN serves a real 4 s clip for both renditions."""
    clip = video(4, 360, 640).read_bytes()
    log = []

    def get(url, **kw):
        log.append((url, kw))
        if url == "https://pixabay.com/api/videos/":
            return Resp(200, {"hits": [hit(7), hit(8, user="Bo Kim")]})
        return Resp(200, chunks=[clip])

    monkeypatch.setattr(requests, "get", get)
    return log


def test_a_pixabay_search_makes_a_sheet_from_tiny_renditions_and_downloads_no_full_clip(tmp_path, monkeypatch, capsys, pixabay_net):
    work = env(tmp_path, monkeypatch, PIXABAY_API_KEY=KEY)
    assert cli.main(["broll", "acme", "take1.mp4", "tired desk worker"]) == 0
    out = capsys.readouterr()
    assert "pixabay-7" in out.out and "4.0 s" in out.out and "1080x1920" in out.out and "Bo Kim" in out.out
    assert "https://pixabay.com/users/Bo%20Kim-42/" in out.out and "Videos from Pixabay (https://pixabay.com)" in out.out
    assert "--get <id>" in out.out and KEY not in out.out + out.err
    downloaded = [u for u, _ in pixabay_net if "cdn.test" in u]
    assert sorted(downloaded) == ["https://cdn.test/7-tiny.mp4", "https://cdn.test/8-tiny.mp4"]  # no large, no medium
    assert (work / "broll" / "pixabay-7-tiny.mp4").exists() and not (work / "broll" / "pixabay-7.mp4").exists()
    sheet_path = work / "work" / "take1" / "broll" / "tired-desk-worker.jpg"
    with Image.open(sheet_path) as opened:
        img = opened.copy()
    cw, ch = contactsheet.CELL_PORTRAIT
    rows = 2 * (ch + contactsheet.LABEL_H)
    assert img.size == (4 * cw, rows + contactsheet.LABEL_H)
    grey = img.convert("L")
    for r in range(2):  # four different frames a clip, then a label
        cells = [grey.crop((c * cw, r * (ch + contactsheet.LABEL_H), (c + 1) * cw, r * (ch + contactsheet.LABEL_H) + ch)).tobytes() for c in range(4)]
        assert len(set(cells)) == 4
        assert grey.crop((0, r * (ch + 24) + ch, 4 * cw, (r + 1) * (ch + 24))).getextrema()[1] > 150
    assert grey.crop((0, rows, 4 * cw, rows + 24)).getextrema()[1] > 150  # the source line
    assert pixabay.CREDIT == "Videos from Pixabay, pixabay.com"
    assert set(json.loads((work / "broll" / "candidates.json").read_text(encoding="utf-8"))) == {"pixabay-7", "pixabay-8"}


def test_get_downloads_only_the_pick_and_credits_it(tmp_path, monkeypatch, capsys, pixabay_net):
    work = env(tmp_path, monkeypatch, PIXABAY_API_KEY=KEY)
    assert cli.main(["broll", "acme", "take1.mp4", "desk"]) == 0
    pixabay_net.clear()
    capsys.readouterr()
    assert cli.main(["broll", "acme", "take1.mp4", "--get", "pixabay-8"]) == 0
    assert [u for u, _ in pixabay_net] == ["https://cdn.test/8-large.mp4"]  # no second search
    cache = work / "broll"
    assert (cache / "pixabay-8.mp4").exists() and not (cache / "pixabay-7.mp4").exists()
    credit = json.loads((cache / "credits.json").read_text(encoding="utf-8"))["pixabay-8"]
    assert credit == {"source": "pixabay", "user": "Bo Kim", "user_url": "https://pixabay.com/users/Bo%20Kim-42/",
                      "url": "https://pixabay.com/videos/office-8/"}
    assert "Videos from Pixabay (https://pixabay.com)" in capsys.readouterr().out


def test_get_looks_a_pixabay_clip_up_when_no_search_remembers_it(tmp_path, monkeypatch, pixabay_net):
    work = env(tmp_path, monkeypatch, PIXABAY_API_KEY=KEY)
    assert cli.main(["broll", "acme", "take1.mp4", "--get", "pixabay-7"]) == 0
    assert pixabay_net[0][1]["params"]["id"] == "7" and (work / "broll" / "pixabay-7.mp4").exists()


def test_get_refuses_a_bare_number_and_anything_odd(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch, PIXABAY_API_KEY=KEY)
    for bad in ["1234567", "../../x", "pixabay-1/../2"]:
        assert cli.main(["broll", "acme", "take1.mp4", "--get", bad]) == 1
    assert "pixabay-1234567" in capsys.readouterr().err


def test_a_connection_error_and_a_400_and_a_429_print_no_key(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch, PIXABAY_API_KEY=KEY)

    def boom(url, **kw):
        raise requests.ConnectionError(f"failed {url}?key={kw['params']['key']}")

    for fake in (boom, lambda url, **kw: Resp(400), lambda url, **kw: Resp(429)):
        monkeypatch.setattr(requests, "get", fake)
        assert cli.main(["broll", "acme", "take1.mp4", "desk"]) == 1
        seen = capsys.readouterr()
        assert KEY not in seen.out + seen.err and "Error:" in seen.err
    assert KEY not in "".join(p.read_text(errors="ignore") for p in tmp_path.rglob("*") if p.is_file() and p.name != ".env")


def test_the_pexels_search_still_makes_its_sheet_from_stills(tmp_path, monkeypatch, capsys):
    import io

    work = env(tmp_path, monkeypatch, PEXELS_API_KEY="pxk")
    buf = io.BytesIO()
    Image.new("RGB", (180, 320), (60, 60, 60)).save(buf, "JPEG")
    v = {"id": 7, "url": "https://www.pexels.com/video/7/", "duration": 4, "user": {"name": "Ana", "url": "https://www.pexels.com/@ana"},
         "video_files": [{"width": 1080, "height": 1920, "file_type": "video/mp4", "link": "https://cdn.test/7.mp4"}],
         "video_pictures": [{"nr": i, "picture": f"https://static.test/7/{i}.jpg"} for i in range(15)]}
    asked = []

    def get(url, **kw):
        asked.append(url)
        return Resp(200, {"videos": [v]}, content=buf.getvalue()) if "api.pexels.com" in url else Resp(200, content=buf.getvalue())

    monkeypatch.setattr(requests, "get", get)
    assert cli.main(["broll", "acme", "take1.mp4", "desk", "--source", "pexels"]) == 0
    out = capsys.readouterr().out
    assert "pexels-7" in out and "Videos from Pexels (https://www.pexels.com)" in out and "--get <id>" in out
    assert [u.rsplit("/", 1)[1] for u in asked if "static.test" in u] == ["1.jpg", "5.jpg", "9.jpg", "12.jpg"]
    assert not [u for u in asked if u.endswith("7.mp4")]
    assert (work / "work" / "take1" / "broll" / "desk.jpg").exists()


def test_own_clips_make_a_sheet_and_need_no_download(tmp_path, monkeypatch, capsys, video):
    work = env(tmp_path, monkeypatch)  # no keys at all
    own = work / "broll" / "own"
    own.mkdir(parents=True)
    shutil.copy(video(4, 360, 640), own / "Front Desk.MOV")
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("own clips touch no network"))
    assert cli.main(["broll", "acme", "take1.mp4", "--own"]) == 0
    out = capsys.readouterr().out
    assert "own-front-desk" in out and "4.0 s" in out and "Front Desk.MOV" in out and "The client's own footage" in out
    with Image.open(work / "work" / "take1" / "broll" / "own.jpg") as img:
        cw, ch = contactsheet.CELL_PORTRAIT
        assert img.size == (4 * cw, ch + 24 + 24)
        grey = img.convert("L")
        assert len({grey.crop((c * cw, 0, (c + 1) * cw, ch)).tobytes() for c in range(4)}) == 4
        assert grey.crop((0, ch, 4 * cw, ch + 24)).getextrema()[1] > 150  # the label
        assert grey.crop((0, ch + 24, 4 * cw, ch + 48)).getextrema()[1] > 150  # the source line
    assert cli.main(["broll", "acme", "take1.mp4", "--get", "own-front-desk"]) == 0
    assert "already local" in capsys.readouterr().out



    env(tmp_path, monkeypatch)


def test_own_with_no_files_says_where_to_put_them(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    assert cli.main(["broll", "acme", "take1.mp4", "--own"]) == 1
    err = capsys.readouterr().err
    assert "broll" in err and "MP4, MOV or M4V" in err




def test_a_pixabay_clip_with_no_small_preview_stays_off_the_sheet_and_nothing_large_is_downloaded(tmp_path, monkeypatch, capsys, video):
    work = env(tmp_path, monkeypatch, PIXABAY_API_KEY=KEY)
    clip = video(4, 360, 640).read_bytes()
    bare = hit(9)
    for n in ("tiny", "small", "medium"):
        bare["videos"][n]["url"] = ""
    asked = []

    def get(url, **kw):
        asked.append(url)
        return Resp(200, {"hits": [hit(7), bare]}) if "api/videos" in url else Resp(200, chunks=[clip])

    monkeypatch.setattr(requests, "get", get)
    assert cli.main(["broll", "acme", "take1.mp4", "desk"]) == 0
    assert [u for u in asked if "cdn.test" in u] == ["https://cdn.test/7-tiny.mp4"]
    with Image.open(work / "work" / "take1" / "broll" / "desk.jpg") as img:
        assert img.size[1] == contactsheet.CELL_PORTRAIT[1] + 24 + 24  # one row
