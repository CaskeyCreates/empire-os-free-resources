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


# fresh-user fixes

class _Reply:
    def __init__(self, size):
        self.size, self.headers = size, {"Content-Type": "text/html"}

    def read(self, n=-1):
        return b"x" * min(n if n >= 0 else self.size, self.size)

    def geturl(self):
        return "https://big.test/"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_page_over_five_megabytes_is_read_but_a_stylesheet_keeps_its_cap(monkeypatch):
    monkeypatch.setattr(brand.urllib.request, "urlopen", lambda req, timeout: _Reply(6 << 20))
    body, _, _ = brand.http_get("https://big.test/", "text/html")
    assert len(body) == 6 << 20
    with pytest.raises(ValueError, match="bigger than 4 MB"):
        brand.http_get("https://big.test/a.css", "text/css")
    monkeypatch.setattr(brand.urllib.request, "urlopen", lambda req, timeout: _Reply(16 << 20))
    with pytest.raises(ValueError, match="bigger than 15 MB"):
        brand.http_get("https://big.test/", "text/html")


def test_an_unreadable_site_still_makes_a_default_kit(root, web):
    def blocked(url, accept="*/*"):
        if "northbank.test" in url:
            raise ValueError("https://northbank.test/ is bigger than 15 MB")
        return web(url, accept)
    problems = []
    kit = brand.build("acme", "https://northbank.test/", fetch=blocked, problems=problems)
    assert kit["colours"] == {"primary": "#111111", "deep": brand.darken_to("#111111", 0.06).upper(),
                              "paper": "#FFFFFF", "accent": "#2563EB",
                              "accent_dark": kit["colours"]["accent_dark"], "highlight": kit["colours"]["highlight"],
                              "ink": "#111111"}
    assert brand.contrast(kit["colours"]["accent_dark"], "#FFFFFF") >= 4.5
    assert kit["fonts"]["display"]["family"] == "Libre Baskerville" and kit["fonts"]["body"]["family"] == "Inter"
    assert kit["logo"] is None and kit["approved"] is False
    assert (root / "clients/acme/brand/kit.json").is_file() and (root / "clients/acme/brand/swatch.html").is_file()
    assert problems and "bigger than 15 MB" in problems[0]


def test_client_new_says_plainly_when_it_could_not_read_the_site(root, web, monkeypatch, capsys):
    def blocked(url, accept="*/*"):
        if "northbank.test" in url:
            raise OSError("HTTP Error 403: Forbidden")
        return web(url, accept)
    monkeypatch.setattr(brand, "http_get", blocked)
    assert cli.main(["client", "new", "acme", "https://northbank.test/"]) == 0
    out = capsys.readouterr().out
    assert "We couldn't read their site (HTTP Error 403: Forbidden)." in out
    assert "adeditor client colours acme --primary ... --accent ..." in out
    assert "About page" in out and "logo" in out


def _roles(css, meta=None):
    return brand.derive_roles(css, meta or {})


def test_framework_default_colours_are_not_the_brand():
    css = (":root{--bs-primary:#0d6efd;--bs-danger:#dc3545;--brand-main:#7A2E3B}.logo{color:var(--brand-main)}"
           "body{color:#2A2A2A;background:#FFFFFF}.btn{background:#0d6efd}.a{color:#0d6efd}.b{color:#0099FF}")
    roles = _roles(css)
    assert roles["primary"] == "#7A2E3B"
    assert roles["accent"] not in ("#0D6EFD", "#0099FF", "#DC3545")
    for default in ("#0d6efd", "#6610f2", "#6f42c1", "#d63384", "#dc3545", "#fd7e14", "#ffc107", "#198754",
                    "#20c997", "#0dcaf0", "#007bff", "#3b82f6", "#2563eb", "#0099ff", "#0073aa", "#2271b1"):
        assert default in brand.FRAMEWORK_DEFAULTS


def test_a_framework_default_is_used_only_when_the_site_names_it_as_its_brand():
    assert _roles(":root{--brand-primary:#0d6efd}.a{color:var(--brand-primary)}")["primary"] == "#0D6EFD"
    # with nothing named, a framework blue is a guess at best, so the site's own darkest colour wins
    roles = _roles("body{color:#1f1f21;background:#fff}.a{color:#0d6efd}.b{color:#0d6efd}")
    assert roles["primary"] == "#1F1F21"


def test_framer_blue_is_skipped_on_a_framer_site():
    css = ":root{--framer-primary:#0099ff}.a{color:#0099ff}.b{background:#C2410C}.b2{background:#C2410C}"
    assert _roles(css)["primary"] == "#C2410C"


def test_ink_is_a_dark_everyday_colour_never_a_brand_colour():
    css = (":root{--bs-primary:#0d6efd}body{color:#0d6efd;background:#fff}"
           ".a{color:#1E4D6B}.b{color:#212529}")
    ink = _roles(css)["ink"]
    assert brand.rel_luminance(ink) <= 0.1
    assert ink == "#212529"
    only_brand = _roles("body{color:#1E4D6B;background:#fff}.a{background:#1E4D6B}")
    assert only_brand["ink"] == "#1A1A1A"


def test_the_swatch_never_carries_example_client_words(kit, root):
    swatch = root / "clients/northbank/brand/swatch.html"
    text = swatch.read_text()
    assert "Six weeks" not in text and "moving freely" not in text
    assert "Your offer line goes here" in text
    brand.set_lockup("northbank", callout="For desk workers", offer=["Back pain gone in 8 weeks"], detail=None)
    text = brand.write_swatch("northbank", brand.read("northbank")).read_text()
    assert "Back pain gone in 8 weeks" in text and "Your offer line goes here" not in text


def test_approve_warns_when_the_kit_has_no_logo(kit, capsys):
    path = brand.kit_path("northbank")
    saved = json.loads(path.read_text())
    saved["logo"] = None
    path.write_text(json.dumps(saved))
    assert cli.main(["client", "approve", "northbank"]) == 0
    out = capsys.readouterr().out
    assert "Warning: there's no logo" in out and "end card" in out and "clients/northbank/brand/" in out
    saved["logo"] = "logo.png"
    path.write_text(json.dumps(saved))
    assert cli.main(["client", "approve", "northbank"]) == 0
    assert "no logo" not in capsys.readouterr().out


def test_a_failed_re_run_leaves_a_good_kit_untouched(kit, root, web, monkeypatch, capsys):
    brand.set_colours("northbank", primary="#112233", accent="#AA5500")
    before = (root / "clients/northbank/brand/kit.json").read_text()

    def blocked(url, accept="*/*"):
        if "northbank.test" in url:
            raise OSError("HTTP Error 403: Forbidden")
        return web(url, accept)
    problems = []
    got = brand.build("northbank", SITE, fetch=blocked, problems=problems)
    assert (root / "clients/northbank/brand/kit.json").read_text() == before
    assert got["colours"]["primary"] == "#112233" and "403" in problems[0]
    monkeypatch.setattr(brand, "http_get", blocked)
    assert cli.main(["client", "new", "northbank", SITE]) == 0
    out = capsys.readouterr().out
    assert "couldn't read their site" in out and "existing kit is unchanged" in out
    assert (root / "clients/northbank/brand/kit.json").read_text() == before


BOOTSTRAP_ROOT = (":root{--bs-blue:#0d6efd;--bs-primary:#0d6efd;--bs-primary-text-emphasis:#052c65;"
                  "--bs-primary-bg-subtle:#cfe2ff;--bs-body-color:#212529;--bs-body-bg:#fff}")


def test_framework_prefixed_variables_never_count_as_a_named_brand_colour():
    css = BOOTSTRAP_ROOT + "body{color:#212529;background:#fff}.buy{background:#c87e1e}.buy:hover{background:#c87e1e}"
    found = brand.pick_colours(css, {})
    assert found["primary"] == "#c87e1e"
    for prefix in brand.FRAMEWORK_VAR_PREFIXES:
        css = f":root{{{prefix}primary:#123abc}}.buy{{background:#c87e1e}}"
        assert brand.pick_colours(css, {})["primary"] == "#c87e1e", prefix
    assert {"--bs-", "--tw-", "--wp--preset--", "--wp-admin-", "--chakra-", "--mantine-", "--mdc-",
            "--swiper-"} <= set(brand.FRAMEWORK_VAR_PREFIXES)


def test_a_colour_used_only_for_error_states_is_never_primary_or_accent():
    css = (".error{color:#cc2222;border-color:#cc2222}.form-success{color:#22aa44}.field:invalid{border:1px solid #cc2222}"
           ".alert-warning{background:#e0a800}.buy{background:#c87e1e}")
    found = brand.pick_colours(css, {})
    assert found["primary"] == "#c87e1e"
    assert found["accent"] not in ("#cc2222", "#22aa44", "#e0a800")


def test_a_colour_also_used_outside_a_state_rule_still_counts():
    css = ".error{color:#c87e1e}.buy{background:#c87e1e}"
    assert brand.pick_colours(css, {})["primary"] == "#c87e1e"


def guess_site(css, meta=""):
    class Web(FakeWeb):
        def __call__(self, url, accept="*/*"):
            if url.endswith("/guess"):
                return (f"<html><head><title>Guess</title>{meta}<style>{css}</style></head><body></body></html>".encode(),
                        "text/html", url)
            return super().__call__(url, accept)
    return Web()


def test_a_read_with_no_named_colour_and_no_theme_colour_says_it_is_a_guess(root, capsys, monkeypatch):
    monkeypatch.setattr(brand, "http_get", guess_site(".buy{background:#c87e1e}.a{color:#c87e1e}"))
    kit = brand.build("guess", "https://northbank.test/guess", fetch=guess_site(".buy{background:#c87e1e}"))
    assert kit["colours_note"] == brand.GUESS_NOTE
    assert "check them against the logo and buttons" in brand.GUESS_NOTE
    assert brand.GUESS_NOTE in (root / "clients/guess/brand/swatch.html").read_text()
    assert cli.main(["client", "new", "guess2", "https://northbank.test/guess"]) == 0
    out = capsys.readouterr().out
    assert out.index("Read ") < out.index(brand.GUESS_NOTE)


def test_a_named_brand_colour_or_a_theme_colour_is_not_a_guess(root):
    named = brand.build("named", "https://northbank.test/guess", fetch=guess_site(":root{--brand-primary:#c87e1e}.a{color:var(--brand-primary)}"))
    assert not named.get("colours_note")
    themed = brand.build("themed", "https://northbank.test/guess",
                         fetch=guess_site(".buy{background:#c87e1e}", '<meta name="theme-color" content="#c87e1e">'))
    assert not themed.get("colours_note")


def test_a_framework_only_variable_is_still_a_guess(root):
    kit = brand.build("fw", "https://northbank.test/guess", fetch=guess_site(BOOTSTRAP_ROOT + ".buy{background:#c87e1e}"))
    assert kit["colours"]["primary"] == "#C87E1E" and kit["colours_note"] == brand.GUESS_NOTE


@pytest.mark.parametrize("value, family", [
    ("var(--x, inherit))", None), ("PitchSans !important", "PitchSans"), ("'Lora', serif", "Lora"),
    ("var(--font, 'Lora', serif)", "Lora"), ("inherit", None), ("var(--a)", None), ("system-ui, 'DM Sans'", "DM Sans"),
])
def test_family_of_cleans_each_part_of_a_font_value(value, family):
    assert brand.family_of(value) == family


def test_with_nothing_named_a_colour_set_in_one_variable_and_used_nowhere_is_noise():
    css = ("body{color:#1f1f21;background:#fff}:root{--wcag-focus-ring-color:#8FEB25}"
           ".a{color:#1f1f21}.b{color:#1f1f21}")
    found = brand.pick_colours(css, {})
    assert found["primary"] is None and found["guessed"] is True
    assert brand.pick_colours(css + ".shop{background:#1990c6}", {})["primary"] == "#1990c6"


def test_a_palette_colour_used_through_var_counts_as_used():
    css = ":root{--color-orange:#e8590c;--color-ink:#1b1b1b}.btn{background:var(--color-orange)}body{color:var(--color-ink)}"
    assert brand.pick_colours(css, {})["primary"] == "#e8590c"


def test_an_unused_named_variable_loses_and_a_used_one_still_wins():
    css = ":root{--cyl-eln-accent:#1a4d8f}.buy{background:#c87e1e}.buy2{border-color:#c87e1e}"
    found = brand.pick_colours(css, {})
    assert found["primary"] == "#c87e1e" and found["accent"] != "#1a4d8f" and found["guessed"] is True
    used = css + ".widget{color:var(--cyl-eln-accent)}"
    found = brand.pick_colours(used, {})
    assert found["accent"] == "#1a4d8f" and found["guessed"] is False


def test_a_named_variable_used_through_another_variable_counts():
    css = ":root{--brand-primary:#7a2e3b;--btn:var(--brand-primary)}.buy{background:var(--btn)}"
    found = brand.pick_colours(css, {})
    assert found["primary"] == "#7a2e3b" and found["guessed"] is False


def test_setting_the_colours_by_hand_clears_the_guess_note(root, web):
    kit = brand.build("northbank", SITE, fetch=web)
    kit["colours_note"] = brand.GUESS_NOTE
    brand.save("northbank", kit)
    brand.write_swatch("northbank", kit)
    brand.set_colours("northbank", primary="#1E4D6B")
    assert "colours_note" not in brand.read("northbank")
    assert brand.GUESS_NOTE not in (root / "clients/northbank/brand/swatch.html").read_text()
