import json
import re
import urllib.parse
from pathlib import Path

import pytest

from adeditor import brand, cli, music, paths

SITE = "https://northbank.test/"
FIXTURE = Path(__file__).parent / "fixtures" / "site"
GSTATIC = "https://fonts.gstatic.com/s/"


def css2(family, style, weight):
    slug = family.lower().replace(" ", "")
    return (
        f"/* latin-ext */\n@font-face {{\n  font-family: '{family}';\n  font-style: {style};\n  font-weight: {weight};\n"
        f"  src: url({GSTATIC}{slug}/ext-{style}-{weight}.woff2) format('woff2');\n}}\n"
        f"/* latin */\n@font-face {{\n  font-family: '{family}';\n  font-style: {style};\n  font-weight: {weight};\n"
        f"  src: url({GSTATIC}{slug}/latin-{style}-{weight}.woff2) format('woff2');\n}}\n"
    )


class FakeWeb:
    """The fixture site plus a fake Google Fonts. Records every URL asked for."""

    # family -> faces Google has: (italic?, weight)
    GOOGLE = {"Merriweather": {("normal", 400), ("italic", 400), ("normal", 700)},
              "Inter": {("normal", 400), ("normal", 700)},
              "Libre Baskerville": {("normal", 400), ("italic", 400), ("normal", 700)}}

    def __init__(self):
        self.urls = []

    def __call__(self, url, accept="*/*"):
        self.urls.append(url)
        parts = urllib.parse.urlparse(url)
        if parts.netloc == "northbank.test":
            name = parts.path.lstrip("/") or "index.html"
            f = FIXTURE / name
            if not f.is_file():
                raise OSError(f"404 {url}")
            kind = {".html": "text/html", ".css": "text/css", ".png": "image/png"}[f.suffix]
            return f.read_bytes(), kind, url
        if parts.netloc == "fonts.googleapis.com":
            query = urllib.parse.parse_qs(parts.query)["family"][0]
            family, _, axes = query.partition(":")
            family = family.replace("+", " ")
            style, weight = ("italic", 400) if axes.startswith("ital,wght@1,") else ("normal", int(axes.split("@")[1]))
            if (style, weight) not in self.GOOGLE.get(family, set()):
                raise OSError(f"400 {url}")
            return css2(family, style, weight).encode(), "text/css", url
        if parts.netloc == "raw.githubusercontent.com":
            return b"Copyright The " + parts.path.split("/")[-2].encode() + b" Project Authors", "text/plain", url
        if parts.netloc == "fonts.gstatic.com":
            return b"wOF2" + url.encode(), "font/woff2", url
        raise OSError(f"no route {url}")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def kit(root, web):
    return brand.build("northbank", SITE, fetch=web)


def test_kit_reads_the_fixture_site(kit, root):
    assert kit["colours"]["primary"] == "#1E4D6B"
    display, body = kit["fonts"]["display"], kit["fonts"]["body"]
    assert (display["family"], display["source"], display["matched_from"]) == ("Merriweather", "google", None)
    assert (body["family"], body["source"], body["matched_from"]) == ("Inter", "matched", "Proxima Nova")
    assert kit["logo"] == "logo.png"
    assert (root / "clients/northbank/brand/logo.png").is_file()
    assert kit["name"] == "Northbank Physio" and kit["approved"] is False
    assert kit["lockup"]["button"] == "Learn more" and kit["keyterms"] == []
    saved = json.loads((root / "clients/northbank/brand/kit.json").read_text())
    assert saved == kit
    swatch = (root / "clients/northbank/brand/swatch.html").read_text()
    for role in kit["colours"]:
        assert role in swatch
    assert "Merriweather" in swatch and "logo.png" in swatch


def test_the_seven_roles_follow_the_brief(kit):
    c = kit["colours"]
    assert set(c) == {"primary", "deep", "paper", "accent", "accent_dark", "highlight", "ink"}
    assert brand.rel_luminance(c["deep"]) <= 0.06
    assert c["paper"] == "#F6F1E7" and c["accent"] == "#D9822B"
    assert brand.contrast(c["accent_dark"], c["paper"]) >= 4.5
    assert brand.contrast(c["ink"], c["paper"]) >= 4.5


def test_fonts_are_woff2_files_from_google(kit, root):
    fonts = root / "clients/northbank/brand/fonts"
    display, body = kit["fonts"]["display"]["files"], kit["fonts"]["body"]["files"]
    assert display["regular"] == "fonts/merriweather-regular.woff2" and (root / "clients/northbank/brand" / display["regular"]).is_file()
    assert display["italic"] == "fonts/merriweather-italic.woff2" and display["bold"] == "fonts/merriweather-bold.woff2"
    assert body["regular"] == "fonts/inter-regular.woff2" and body["bold"] == "fonts/inter-bold.woff2"
    assert len(list(fonts.glob("*.woff2"))) == 5


def test_a_missing_face_is_null_and_said_so(kit):
    body = kit["fonts"]["body"]
    assert body["files"]["italic"] is None
    assert "italic" in body["note"] and "fall back" in body["note"]
    assert kit["fonts"]["display"]["note"] is None


def test_caption_colours_are_readable(kit):
    c = kit["colours"]
    assert brand.contrast(c["paper"], c["deep"]) >= 4.5
    assert brand.contrast(c["highlight"], c["deep"]) >= 4.5


def test_a_dull_accent_is_lightened_for_the_highlight():
    deep = "#10202B"
    lifted = brand.lighten_until(deep, "#2A5F7A", 4.5)
    assert brand.contrast(lifted, deep) >= 4.5


def test_no_font_file_from_the_client_site(kit, web):
    for url in web.urls:
        parts = urllib.parse.urlparse(url)
        if parts.netloc == "northbank.test":
            assert not re.search(r"\.(woff2?|ttf|otf|eot)$", parts.path), url


def test_unapproved_kit_is_refused(kit, root, monkeypatch):
    with pytest.raises(brand.NotApproved):
        brand.load("northbank")
    brand.approve("northbank")
    assert brand.load("northbank")["approved"] is True
    monkeypatch.setattr(music.keys, "get", lambda name: "test-key")
    monkeypatch.setattr(music.requests, "post", lambda *a, **k: FakeAudio())
    music.make_bed("northbank", "calm piano")
    with pytest.raises(brand.NotApproved):
        brand.load("northbank")


def test_a_rebuild_keeps_the_lockup_and_asks_again(kit, web):
    brand.set_lockup("northbank", callout="For desk workers", offer=["Six weeks"], detail=None, button="Learn more")
    brand.approve("northbank")
    again = brand.build("northbank", SITE, fetch=web)
    assert again["lockup"]["callout"] == "For desk workers" and again["approved"] is False


def test_a_slug_is_lower_case_letters_digits_and_hyphens(root, web):
    for bad in ("Acme", "a b", "../x", "a_b", ""):
        with pytest.raises(ValueError):
            brand.build(bad, SITE, fetch=web)
    assert not (root / "clients").exists()


def test_lockup_clears_approval(kit):
    brand.approve("northbank")
    out = brand.set_lockup("northbank", callout="For desk workers with back pain", offer=["Six weeks to moving freely", "In clinic or online"],
                           detail="Learn more about the plan", button="Learn more")
    assert out["lockup"] == {"callout": "For desk workers with back pain",
                             "offer": ["Six weeks to moving freely", "In clinic or online"],
                             "detail": "Learn more about the plan", "button": "Learn more"}
    assert out["approved"] is False


def test_example_client_is_invented():
    clients = Path(__file__).resolve().parent.parent / "clients"
    saved = json.loads((clients / "example/brand/kit.json").read_text())
    assert saved["name"] == "Northbank Physio" and saved["logo"] is None and saved["approved"] is False
    assert saved["fonts"]["display"]["family"] == "Merriweather" and saved["fonts"]["body"]["family"] == "Inter"
    assert saved["lockup"] == {"callout": "For desk workers with back pain", "offer": ["Six weeks to moving freely", "In clinic or online"],
                               "detail": None, "button": "Learn more"}


def test_only_the_example_client_ships():
    """clients/example holds only its own known files, and every other client folder stays out of git."""
    here = Path(__file__).resolve().parent.parent
    example = here / "clients" / "example"
    fonts = [f"{family}-{face}.woff2" for family in ("inter", "merriweather") for face in ("regular", "italic", "bold")]
    known = {"brand/kit.json", "brand/fonts/OFL.txt", "raw/.gitkeep", *(f"brand/fonts/{name}" for name in fonts)}
    found = {p.relative_to(example).as_posix() for p in example.rglob("*") if p.is_file() and p.name != ".DS_Store"}
    assert found == known
    ignored = (here / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "clients/*/" in ignored and "!clients/example/" in ignored


def test_client_commands(root, web, monkeypatch, capsys):
    monkeypatch.setattr(brand, "http_get", web)
    assert cli.main(["client", "new", "Bad Slug", SITE]) == 1
    assert "lower-case" in capsys.readouterr().err
    assert cli.main(["client", "new", "northbank", SITE, "--name", "Northbank"]) == 0
    assert json.loads((root / "clients/northbank/brand/kit.json").read_text())["name"] == "Northbank"
    assert cli.main(["client", "lockup", "northbank", "--callout", "For desk workers", "--offer", "Six weeks",
                     "--offer2", "Online too", "--detail", "More"]) == 0
    assert json.loads((root / "clients/northbank/brand/kit.json").read_text())["lockup"]["offer"] == ["Six weeks", "Online too"]
    track = root / "track.mp3"
    track.write_bytes(b"ID3fake")
    assert cli.main(["client", "music", "northbank", "--file", str(track), "--bpm", "90"]) == 0
    saved = json.loads((root / "clients/northbank/brand/kit.json").read_text())
    assert saved["music"] == "music/bed.mp3" and saved["music_bpm"] == 90
    assert cli.main(["client", "approve", "northbank"]) == 0
    assert brand.load("northbank")["approved"] is True


class FakeAudio:
    status_code = 200
    content = b"ID3fake-mp3"
    text = ""


def test_example_fonts_carry_the_ofl():
    folder = Path(__file__).resolve().parent.parent / "clients/example/brand/fonts"
    text = (folder / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in text
    assert "Merriweather Project Authors" in text and "Inter Project Authors" in text
    notices = (Path(__file__).resolve().parent.parent / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    assert "Open Font License" in notices and "Merriweather" in notices and "Inter" in notices


def test_downloaded_fonts_get_their_licence(kit, root):
    fonts = root / "clients/northbank/brand/fonts"
    assert "Merriweather Project Authors".replace("Merriweather", "merriweather") in (fonts / "OFL-merriweather.txt").read_text()
    assert (fonts / "OFL-inter.txt").is_file()


def test_a_failed_licence_fetch_says_so_on_one_line(root, web, capsys):
    plain = web

    def no_licence(url, accept="*/*"):
        if "raw.githubusercontent.com" in url:
            raise OSError("404")
        return plain(url, accept)

    brand.build("northbank", SITE, fetch=no_licence)
    err = capsys.readouterr().err.strip().splitlines()
    assert err and all("open licence" in line for line in err)


def test_comments_are_not_selectors_or_colours():
    css = ("/* latin */ @font-face { font-family: 'Lora'; src: url(a.woff2); }\n"
           "/* #FF0000 */ body { font-family: 'Inter', sans-serif; }")
    display, body, used, _ = brand.font_roles(css)
    assert "Lora" not in used and (display, body) == ("Inter", "Inter")
    assert "#ff0000" not in brand.pick_colours(css, {})["everyday"]


def test_approve_warns_about_an_empty_lockup(kit, capsys):
    assert cli.main(["client", "approve", "northbank"]) == 0
    assert "lockup is empty" in capsys.readouterr().out
    brand.set_lockup("northbank", callout="For desk workers", offer=["Six weeks"], detail=None)
    assert cli.main(["client", "approve", "northbank"]) == 0
    assert "lockup is empty" not in capsys.readouterr().out


def test_a_broken_kit_says_it_is_broken_and_never_to_start_again(kit, root, capsys):
    path = root / "clients/northbank/brand/kit.json"
    path.write_text(path.read_text().replace('"approved": false', '"approved": false,,'))
    with pytest.raises(brand.BadKit) as err:
        brand.read("northbank")
    assert "isn't valid JSON" in str(err.value) and "line" in str(err.value) and "client new" not in str(err.value)
    assert cli.main(["client", "approve", "northbank"]) == 1
    out = capsys.readouterr().err
    assert "isn't valid JSON" in out and "client new" not in out


def test_a_missing_kit_still_says_how_to_make_one(root):
    with pytest.raises(brand.NoKit) as err:
        brand.read("nobody")
    assert "adeditor client new nobody" in str(err.value)


def test_colours_sets_what_is_given_and_works_out_the_rest(kit, root, capsys):
    brand.approve("northbank")
    before = brand.read("northbank")["colours"]
    assert cli.main(["client", "colours", "northbank", "--primary", "#1a1a1a", "--accent", "FF6B35"]) == 0
    saved = brand.read("northbank")
    c = saved["colours"]
    assert (c["primary"], c["accent"]) == ("#1A1A1A", "#FF6B35")
    assert (c["paper"], c["ink"]) == (before["paper"], before["ink"])  # not given, so kept
    # the rest by the rules build uses: deep from primary, accent_dark readable on paper, highlight on deep
    assert c == brand.complete_roles("#1A1A1A", "#FF6B35", before["paper"], before["ink"])
    assert brand.rel_luminance(c["deep"]) <= 0.06 and c["deep"] != before["deep"]
    assert brand.contrast(c["accent_dark"], c["paper"]) >= 4.5 and brand.contrast(c["highlight"], c["deep"]) >= 4.5
    assert saved["approved"] is False
    swatch = (root / "clients/northbank/brand/swatch.html").read_text()
    assert "#1A1A1A" in swatch and c["highlight"] in swatch and "approved: no" in swatch
    out = capsys.readouterr().out
    assert "swatch.html" in out and "adeditor client approve northbank" in out


def test_build_and_colours_share_one_set_of_rules(kit):
    c = kit["colours"]
    assert c == brand.complete_roles(c["primary"], c["accent"], c["paper"], c["ink"])


@pytest.mark.parametrize("bad", ["blue", "#12345", "#GGGGGG", "", "#1A1A1A1A"])
def test_colours_refuses_a_hex_it_cant_read(kit, root, capsys, bad):
    path = root / "clients/northbank/brand/kit.json"
    before = path.read_text()
    assert cli.main(["client", "colours", "northbank", "--primary", "#1A1A1A", "--accent", bad]) == 1
    err = capsys.readouterr().err
    assert f'accent colour "{bad}"' in err and "like #1A1A1A" in err and len(err.strip().splitlines()) == 1
    assert path.read_text() == before


def test_colours_names_the_caption_pair_that_doesnt_read(kit, root, capsys, monkeypatch):
    path = root / "clients/northbank/brand/kit.json"
    before = path.read_text()
    assert cli.main(["client", "colours", "northbank", "--paper", "#5A5A5A"]) == 1
    err = capsys.readouterr().err
    assert "caption words (paper #5A5A5A)" in err and "deep" in err and "4.5 to 1" in err
    assert "highlight" not in err and path.read_text() == before
    # the spoken word's highlight is checked on deep the same way
    monkeypatch.setattr(brand, "lighten_until", lambda on, c, ratio: c)
    assert cli.main(["client", "colours", "northbank", "--accent", "#203040"]) == 1
    err = capsys.readouterr().err
    assert "word being said (highlight #203040)" in err and "paper" not in err
    assert path.read_text() == before


def test_colours_needs_at_least_one_colour(kit, capsys):
    assert cli.main(["client", "colours", "northbank"]) == 1
    assert "at least one colour" in capsys.readouterr().err


def test_a_single_caption_colour_reads_in_the_singular(monkeypatch):
    monkeypatch.setattr(brand, "lighten_until", lambda on, c, ratio: c)
    colours = brand.complete_roles("#1A1A1A", "#203040", "#5A5A5A", "#111111")
    said = " ".join(brand.caption_problems(colours))
    assert "caption words (paper #5A5A5A) on the caption plate" in said and ") read at" in said
    assert "word being said (highlight #203040) on the caption plate" in said and ") reads at" in said
    assert "and needs at least" in said
