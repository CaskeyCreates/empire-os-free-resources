import json
import re

import pytest
from conftest import cut_words

from adeditor import page

TEXT = ("If you sit at a desk all day, your back is already telling you something. "
        "Most people wait until it really hurts. You don't have to wait. "
        "In six weeks you can move freely again, at work and at home. "
        "Book a call and we'll show you how it works.")


def words_and_length(text=TEXT):
    words = cut_words(text)
    return words, round(words[-1]["end"] + 0.5, 3)


def standard_plan():
    return {
        "headings": [{"lines": ["You don't have to", "*wait*"], "from": "you don't have", "to": "in six weeks"}],
        "key_words": ["desk", {"word": "wait", "only_in": "you don't have to wait"}],
        "broll": [{"clip": "pexels-1234567", "in": 1.0, "from": "most people", "to": "you don't have", "y": 40,
                   "why": "people waiting"}],
    }


def tops(html: str, cls: str) -> list[int]:
    return [int(t) for t in re.findall(rf'class="{cls}"[^>]*style="top:(\d+)px', html)]


def captions(html: str) -> list[tuple[int, float]]:
    """(top, font size) of every caption group on the page."""
    return [(int(top), float(size)) for top, size in
            re.findall(r'class="clip cap"[^>]*style="top:(\d+)px"><span class="pill"[^>]*font-size:([\d.]+)px', html)]


def hex_of(rgb: str) -> str:
    r, g, b = (int(v) for v in rgb.split(",")[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def test_page_uses_the_kit_and_no_reference_ad_values(ad_job):
    # Stronger than naming the reference ad's colours, fonts and words (which must stay out of this repo):
    # every colour, font and word on the page comes from the kit, the plan or the cut.
    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)])
    index = page.build(job, "4x5")
    html = index.read_text(encoding="utf-8")
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    assert kit["colours"]["primary"] in html and kit["colours"]["highlight"] in html
    assert '"Merriweather"' in html and '"Inter"' in html

    colours = set(kit["colours"].values())
    assert {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", html)} <= colours
    rgba = {hex_of(m) for m in re.findall(r"rgba\(([\d,\s.]+)\)", html)}
    assert rgba <= colours | {"#000000"}
    families = set(re.findall(r'font-family: "([^"]+)"', html)) | set(re.findall(r'font-family: ([^;]+);', html))
    named = {f.split(",")[0].strip().strip('"') for f in families}
    assert named == {kit["fonts"]["display"]["family"], kit["fonts"]["body"]["family"]}

    lockup = kit["lockup"]
    said = " ".join([w["text"] for w in words] + [lockup["callout"], *lockup["offer"], lockup["button"],
                                                  "You don't have to wait", kit["name"]])  # no logo: the name stands in
    allowed = set(re.findall(r"[a-z']+", said.lower()))
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    shown = re.findall(r"[a-z']+", re.sub(r"<[^>]+>", " ", body).replace("&#x27;", "'").lower())
    assert shown and set(shown) <= allowed
    for font in ("merriweather-regular.woff2", "merriweather-italic.woff2", "inter-bold.woff2"):
        assert f"assets/fonts/{font}" in html
        assert (index.parent / "assets" / "fonts" / font).is_file()
    assert (index.parent / "assets" / "aroll.mp4").is_file()
    assert (index.parent / "hyperframes.json").is_file() and (index.parent / "package.json").is_file()
    assert "hyperframes@0.7.5" in (index.parent / "package.json").read_text(encoding="utf-8")


def test_the_kit_words_land_on_the_page_escaped(ad_job):
    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)],
                 kit={"lockup": {"callout": "For desk workers <with> back pain", "offer": ["Six weeks to moving freely"],
                                 "detail": "In clinic & online", "button": "Book now"}})
    html = page.build(job, "9x16").read_text(encoding="utf-8")
    assert "For desk workers &lt;with&gt; back pain" in html
    assert "In clinic &amp; online" in html
    assert html.count(">Book now<") == 1  # the button is on the end card only
    assert '<div class="h" id="h1l1"><i>wait</i></div>' in html


def test_a_missing_italic_falls_back_to_a_synthetic_slant(ad_job):
    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)])
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    kit["fonts"]["display"]["files"]["italic"] = None
    (job.brand / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    html = page.build(job, "1x1").read_text(encoding="utf-8")
    assert "merriweather-italic" not in html
    faces = re.findall(r'@font-face \{ font-family: "Merriweather";[^}]*\}', html)
    assert faces and all("font-style: italic" not in f for f in faces)
    assert "font-synthesis: style" in html


def test_text_stays_in_the_reels_safe_band(ad_job):
    words, length = words_and_length()
    # a chin low enough (1206 px) that the chin rule asks for the band's lowest caption, the lip still
    # above it, and B-roll that sends the captions over it to the band's top
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)],
                 face={"x": 0.4, "y": 0.428, "w": 0.2, "h": 0.2})
    html = page.build(job, "9x16").read_text(encoding="utf-8")
    caps = captions(html)
    assert len(caps) >= 10
    assert {top for top, _ in caps} >= {1212, 1233}
    for top, size in caps:
        assert top >= 1212
        assert top + size * 1.18 + size * (0.2 + 0.23) <= 1330
    heads = tops(html, "head")
    assert heads and all(t >= 270 for t in heads)


def test_end_card_timing(ad_job):
    speech = 135.333
    words = cut_words(" ".join(["steady"] * 300), step=0.44)
    assert words[-1]["end"] < speech
    job = ad_job(words, speech, plan={})
    html = page.build(job, "1x1").read_text(encoding="utf-8")
    assert re.search(r'id="end" class="clip" data-start="135.133" data-duration="3.2"', html)
    assert re.search(r'id="root"[^>]*data-duration="138.333"', html)
    assert re.search(r'id="aroll"[^>]*data-duration="135.293"', html)
    facts = json.loads((job.work / "pages" / "1x1" / "page.json").read_text(encoding="utf-8"))
    assert facts["duration"] == 138.333 and facts["end"] == [135.133, 138.333]


def test_captions_group_like_the_reference_ad():
    words = cut_words("You've tried every stretch, and it's still stiff.", step=0.3, length=0.25)
    got = [" ".join(words[i]["text"] for i in g) for g in page.groups(words)]
    # The reference ad's rule: at most 4 words and 19 characters, a break after .?!,:; or a 0.18 s gap.
    # The comma ends a group, and "stretch" is too long to join the first three words.
    assert got == ["You've tried every", "stretch,", "and it's still stiff."]


def test_a_pause_starts_a_new_caption():
    words = cut_words("and it's still stiff", step=0.3, length=0.25)
    words[3]["start"] += 0.2  # 0.25 s of quiet before "stiff"
    got = [" ".join(words[i]["text"] for i in g) for g in page.groups(words)]
    assert got == ["and it's still", "stiff"]


def test_key_words_and_only_in():
    words = cut_words("You don't have to wait. Wait for nothing, you can do it now.")
    key = page.key_indices(words, [{"word": "nothing", "only_in": None},
                                   {"word": "wait", "only_in": "you don't have to wait"}])
    assert {words[i]["text"] for i in key} == {"nothing,", "wait."}


def test_broll_is_staged_once_at_its_exact_length_and_shared(ad_job):
    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)])
    page.build(job, "9x16")
    page.build(job, "1x1")
    staged = list((job.work / "staged").glob("*.mp4"))
    assert len(staged) == 1
    facts = json.loads((job.work / "pages" / "1x1" / "page.json").read_text(encoding="utf-8"))
    [b] = facts["broll"]
    window = b["end"] - b["start"]
    from adeditor import media
    have = media.probe(staged[0]).duration
    assert have == pytest.approx(window + 0.25, abs=0.05)
    assert b["dur"] == round(min(window, have - 0.1), 3)
    linked = job.work / "pages" / "1x1" / "assets" / "broll" / staged[0].name
    assert linked.is_file() and linked.stat().st_size == staged[0].stat().st_size
    html = (job.work / "pages" / "1x1" / "index.html").read_text(encoding="utf-8")
    assert f'src="assets/broll/{staged[0].name}"' in html and "object-position:50% 40%" in html


def test_the_lockup_is_on_the_first_frame_until_the_first_cut_in(ad_job):
    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)])
    html = page.build(job, "9x16").read_text(encoding="utf-8")
    facts = json.loads((job.work / "pages" / "9x16" / "page.json").read_text(encoding="utf-8"))
    first_cut_in = facts["broll"][0]["start"]
    assert facts["lockup"] == [0.0, first_cut_in]
    assert re.search(rf'id="lock" class="clip" data-start="0" data-duration="{first_cut_in}"', html)
    data = json.loads(re.search(r"const DATA = (\{.*?\});\n", html, re.S).group(1))
    assert data["lockup"]["enter"] is False and all(h["enter"] for h in data["headings"])


def test_a_face_that_leaves_the_frame_is_a_warning_on_the_page(ad_job, video):
    from adeditor import faces

    words = cut_words(" ".join(["word"] * 80))
    job = ad_job(words, 30.0, aroll=video(1, 1920, 1080), segments=[{"src": [0, 30.0], "out": [0, 30.0]}])
    cut_fp, _ = faces.load(job.work / "faces.json")
    # the head low enough in the frame for the fade layout, where the take fills the 9:16 canvas, blown up
    lean = [{"t": i * 0.5, "x": (0.25 if 20 <= i < 40 else 0.5) - 0.06, "y": 0.45, "w": 0.12, "h": 0.2} for i in range(60)]
    (job.work / "faces.json").write_text(json.dumps({"cut": cut_fp, "samples": lean}), encoding="utf-8")
    page.build(job, "9x16")
    tall = json.loads((job.work / "pages" / "9x16" / "page.json").read_text(encoding="utf-8"))
    assert tall["window"]["mode"] == "fade"
    assert any("goes out of the frame from 0:10 to 0:19" in w for w in tall["warnings"])
    page.build(job, "4x5")
    four_five = json.loads((job.work / "pages" / "4x5" / "page.json").read_text(encoding="utf-8"))
    assert not any("out of the frame" in w for w in four_five["warnings"])


def test_the_page_and_the_plan_share_one_end_card_start(ad_job):
    from adeditor import plan

    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)])
    page.build(job, "1x1")
    facts = json.loads((job.work / "pages" / "1x1" / "page.json").read_text(encoding="utf-8"))
    cut = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))
    assert facts["end"][0] == plan.card_start(cut)


@pytest.mark.parametrize("name,fmt", [("a.woff2", "woff2"), ("a.woff", "woff"), ("a.TTF", "truetype"),
                                      ("a.otf", "opentype")])
def test_a_font_file_is_declared_in_its_own_format(name, fmt):
    from adeditor import brand

    assert brand.font_format(name) == fmt


def test_the_persons_own_ttf_and_otf_fonts_reach_the_page(ad_job):
    from adeditor import brand

    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)])
    fonts = job.brand / "fonts"
    (fonts / "house-regular.ttf").write_bytes((fonts / "merriweather-regular.woff2").read_bytes())
    (fonts / "house-italic.otf").write_bytes((fonts / "merriweather-italic.woff2").read_bytes())
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    kit["fonts"]["display"] = {"family": "House Serif", "files": {"regular": "fonts/house-regular.ttf",
                                                                  "italic": "fonts/house-italic.otf", "bold": None},
                               "source": "own", "matched_from": None, "note": None}
    (job.brand / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    html = page.build(job, "1x1").read_text(encoding="utf-8")
    assert 'url("assets/fonts/house-regular.ttf") format("truetype")' in html
    assert 'url("assets/fonts/house-italic.otf") format("opentype")' in html
    assert 'url("assets/fonts/inter-regular.woff2") format("woff2")' in html
    swatch = brand.write_swatch("acme", kit).read_text(encoding="utf-8")
    assert "format('truetype')" in swatch and "format('opentype')" in swatch


def kit_with_logo(job, paper):
    from PIL import Image

    Image.new("RGB", (40, 20), "white").save(job.brand / "logo.png")  # a logo on its own white box
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    kit["logo"] = "logo.png"
    kit["colours"]["paper"] = paper
    (job.brand / "kit.json").write_text(json.dumps(kit), encoding="utf-8")


def test_a_logo_on_a_light_end_card_loses_its_white_box(ad_job):
    words, length = words_and_length()
    job = ad_job(words, length, plan={})
    kit_with_logo(job, "#FAFAF7")
    html = page.build(job, "9x16").read_text(encoding="utf-8")
    assert '<img id="endLogo" src="assets/logo.png"' in html
    assert re.search(r"#endLogo \{[^}]*mix-blend-mode: multiply", html)
    # multiply only reaches the card's own colour when nothing between them is a group of its own
    assert re.search(r"#endHead \{[^}]*z-index: auto", html)
    # on a dark card, multiply would sink the logo into it
    kit_with_logo(job, "#1B1B1B")
    assert "mix-blend-mode" not in page.build(job, "9x16").read_text(encoding="utf-8")


HIGH_HEAD = {"x": 0.43, "y": 0.205, "w": 0.14, "h": 0.25}  # the head's top at 8% of a landscape take
TWO_LINE_LOCKUP = {"lockup": {"callout": "For desk workers with back pain", "offer": ["Six weeks to moving", "freely"],
                              "detail": "In clinic", "button": "Book now"}}


def page_of(job, ratio):
    html = page.build(job, ratio).read_text(encoding="utf-8")
    facts = json.loads((job.work / "pages" / ratio / "page.json").read_text(encoding="utf-8"))
    data = json.loads(re.search(r"const DATA = (\{.*?\});\n", html, re.S).group(1))
    return html, facts, data


def test_a_high_head_takes_the_panel_layout_on_the_page(ad_job, video):
    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)], aroll=video(2, 2560, 1440),
                 face=HIGH_HEAD, kit=TWO_LINE_LOCKUP)
    kit_with_logo(job, "#F6F1E7")
    for ratio in ("9x16", "4x5", "1x1"):
        html, facts, data = page_of(job, ratio)
        win = facts["window"]
        assert win["mode"] == "panel" and win["panel"] > 0, ratio
        assert not any("head" in w for w in facts["warnings"]), facts["warnings"]
        # the take sits under the panel, covering the area below it
        H = {"9x16": 1920, "4x5": 1350, "1x1": 1080}[ratio]
        assert f"left: 0.0px; top: {win['panel']}px; width: 1080.0px; height: {H - win['panel']}px;" in take_style(html)
        assert re.search(rf"#win \{{ position: absolute; left: [-\d.]+px; top: {-win['y0'] + 0.0}px;", html)
        panel = re.search(r'<div id="panel"[^>]*style="([^"]*)"', html).group(1)
        assert "top:0" in panel and f"height:{win['panel']}px" in panel and "background:#F6F1E7" in panel
        blend = re.search(r'<div id="blend"[^>]*style="([^"]*)"', html).group(1)
        assert f"top:{win['panel']}px" in blend and "height:40px" in blend and "linear-gradient" in blend
        # no paper fade over the take: the headings' paper is drawn by the panel
        assert "background: none;" in re.search(r"\.paper \{[^}]*\}", html).group(0)
        # between the lockup and the headings the panel holds the logo and the callout
        idle = re.search(r'<div id="idle".*?</div>\s*</div>', html, re.S).group(0)
        assert 'id="idleLogo"' in idle and "For desk workers with back pain" in idle
        assert data["idle"] and all(b - a >= 0.9 for a, b in data["idle"])
        heading = facts["headings"][0]
        assert any(abs(a - facts["lockup"][1]) < 1e-6 for a, _ in data["idle"])  # after the lockup
        assert all(not (a < heading[1] and heading[0] < b) for a, b in data["idle"])  # never over a heading
        # the text bottom the layout used sits above the panel's foot
        assert facts["text_bottom"] + 48 == pytest.approx(win["panel"], abs=0.5)


def test_a_head_with_room_above_it_keeps_the_fade_layout(ad_job):
    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)],
                 face={"x": 0.369, "y": 0.403, "w": 0.351, "h": 0.197}, kit=TWO_LINE_LOCKUP)
    html, facts, data = page_of(job, "9x16")
    assert facts["window"]["mode"] == "fade" and facts["window"]["panel"] == 0
    assert 'id="panel"' not in html and 'id="blend"' not in html and 'id="idle"' not in html and data["idle"] == []
    assert "linear-gradient" in re.search(r"\.paper \{[^}]*\}", html).group(0)


def test_a_row_too_wide_for_the_canvas_shrinks_to_stay_on_one_line(ad_job):
    words, length = words_and_length()
    long_offer = "Six weeks to moving freely again at work and at home and on the weekend"
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)],
                 kit={"lockup": {"callout": "For desk workers", "offer": [long_offer], "detail": None, "button": "Go"}})
    html, facts, _ = page_of(job, "9x16")
    size = float(re.search(r'<div class="h" id="lockH1" style="font-size:([\d.]+)px">', html).group(1))
    assert 20 < size < 58
    short = re.search(r'<div class="eyebrow" id="lockEye"( style="[^"]*")?>', html)
    assert short and not short.group(1)  # a row that fits keeps its size
    for cls in (".eyebrow", ".h", ".caps"):
        assert "white-space: nowrap" in re.search(rf"\{cls} \{{[^}}]*\}}", html).group(0), cls


def test_shrunk_panel_text_scales_from_its_top(ad_job, video):
    words, length = words_and_length()
    # the chin at 46% of a landscape take: the 9:16 panel's text shrinks a little to keep the captions under it
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)], aroll=video(2, 2560, 1440),
                 face={"x": 0.43, "y": 0.26, "w": 0.14, "h": 0.2}, kit=TWO_LINE_LOCKUP)
    html, facts, data = page_of(job, "9x16")
    win = facts["window"]
    assert win["mode"] == "panel" and 0.8 <= win["shrink"] < 1 and win["punch"] is True and win["head_gap"] == 0
    for head in ("lockHead", "h1Head"):
        style = re.search(rf'id="{head}" style="([^"]*)"', html).group(1)
        assert f"transform:scale({win['shrink']:g})" in style and "transform-origin:50% 0" in style, head
    assert facts["text_bottom"] + 48 == pytest.approx(win["panel"], abs=0.5)
    assert not any("chin" in w or "close-up" in w for w in facts["warnings"])
    # 4:5 keeps its text at full size
    html, facts, data = page_of(job, "4x5")
    assert facts["window"]["shrink"] == 1.0 and "transform:scale" not in html


def take_style(html):
    return re.search(r"#take \{([^}]*)\}", html).group(1)


def test_a_chin_too_low_for_the_panel_puts_the_take_in_a_framed_card(ad_job, video):
    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)], aroll=video(2, 2560, 1440),
                 face={"x": 0.41, "y": 0.26, "w": 0.18, "h": 0.30}, kit=TWO_LINE_LOCKUP)
    kit_with_logo(job, "#F6F1E7")
    html, facts, data = page_of(job, "9x16")
    win = facts["window"]
    left, top, width, height = win["frame"]
    assert win["mode"] == "card" and 756 <= width <= 1008 and top == win["panel"]
    style = take_style(html)
    for part in (f"left: {left}px", f"top: {top}px", f"width: {width}px", f"height: {height}px", "overflow: hidden",
                 "border-radius: 28px", "box-shadow:"):
        assert part in style, part
    assert re.search(rf"#win \{{ position: absolute; left: {-win['x0'] + 0.0}px; top: {-win['y0'] + 0.0}px;", html)
    # the paper runs the whole canvas around the card, with no blend
    panel = re.search(r'<div id="panel"[^>]*style="([^"]*)"', html).group(1)
    assert "height:1920px" in panel and 'id="blend"' not in html and 'id="idle"' in html
    assert "background: none;" in re.search(r"\.paper \{[^}]*\}", html).group(0)


def test_the_page_tells_the_punch_ins_when_text_shows(ad_job, monkeypatch):
    from adeditor import layout

    seen = []
    real = layout.states
    monkeypatch.setattr(layout, "states", lambda *a, **k: seen.append(a[4] if len(a) > 4 else k.get("text_times"))
                        or real(*a, **k))
    words, length = words_and_length()
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)],
                 face={"x": 0.369, "y": 0.403, "w": 0.351, "h": 0.197})
    _, facts, _ = page_of(job, "9x16")
    assert seen == [[tuple(facts["lockup"])] + [tuple(h) for h in facts["headings"]]]



def test_a_caption_never_runs_wider_than_its_card(ad_job, video):
    # Long caption groups (19 characters, the most a group takes) on a narrow card: a vertical close-up in 9:16.
    text = " ".join(["Seventeen letters, absolutely flexible stretches."] * 6)
    words = cut_words(text)
    length = round(words[-1]["end"] + 0.5, 3)
    job = ad_job(words, length, plan={"key_words": ["absolutely"]}, aroll=video(2, 1080, 1920),
                 face={"x": 0.286, "y": 0.224, "w": 0.516, "h": 0.29})
    html, facts, data = page_of(job, "9x16")
    left, top, width, height = facts["window"]["frame"]
    assert facts["window"]["mode"] == "card" and width < 900
    assert facts["captions"] and all(c["width"] <= width - 48 + 0.5 for c in facts["captions"]), \
        [(c["width"], width) for c in facts["captions"]]



def test_the_page_records_a_covered_mouth_and_warns_of_a_caption_past_its_card(ad_job, video, monkeypatch):
    from adeditor import layout

    words, length = words_and_length()
    # a face too big for any card: the last-resort layout
    job = ad_job(words, length, plan=standard_plan(), clips=[("pexels-1234567", 10)], aroll=video(2, 1080, 1920),
                 face={"x": 0.1, "y": 0.28, "w": 0.8, "h": 0.5})
    _, facts, _ = page_of(job, "9x16")
    assert facts["window"]["punch"] is False and facts["mouth_covered"] is True
    # and a card whose captions run past its bottom is warned of
    real = layout.window

    def short_card(*a, **k):
        win = real(*a, **k)
        fx, fy, fw, fh = win.box
        return layout.replace(win, frame=(fx, fy, fw, 1100 - fy))

    monkeypatch.setattr(layout, "window", short_card)
    _, facts, _ = page_of(job, "9x16")
    assert any("run past the bottom of the picture" in w for w in facts["warnings"])


def test_an_end_card_with_no_logo_sets_the_clients_name_in_the_display_font(ad_job):
    words, length = words_and_length()
    job = ad_job(words, length, plan={})
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    kit["logo"] = None
    (job.brand / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    html = page.build(job, "9x16").read_text(encoding="utf-8")
    assert "<img id=\"endLogo\"" not in html
    assert re.search(r'<div id="endLogo" class="wordmark"[^>]*>%s</div>' % re.escape(kit["name"]), html)
    assert re.search(r"#endLogo\.wordmark \{[^}]*font-family: [^;]*;", html)
    assert '"#endLogo"' in html  # it still animates in with the card


# Task 3: the shutter flash and every picture change as a sound event.
FACE = {"x": 0.369, "y": 0.403, "w": 0.351, "h": 0.197}


def flash_plan(**extra):
    return {"headings": [], "key_words": [], "broll": [], **extra}


def punch_job(ad_job, plan, segments=None):
    words, length = words_and_length()
    return ad_job(words, length, plan=plan, clips=[("pexels-1234567", 10)], face=FACE, segments=segments)


def changes(facts):
    return [e for e in facts["events"] if e["event"] == "camera change"]


def test_visible_punch_changes_flash_and_sound(ad_job):
    # 4 segments: plain, punch, plain, punch is 3 changes, each with a flash and a cut on the 0.9 frame.
    job = punch_job(ad_job, flash_plan())
    html, facts, data = page_of(job, "9x16")
    cuts = changes(facts)
    assert len(cuts) == 3 and all(e["role"] == "cut" and e["sfx"] is True for e in cuts)
    keys = dict((f, o) for f, o in data["flash"]["white"])
    assert [round(f / 30, 3) for f, o in data["flash"]["white"] if o == 0.9] == [e["at"] for e in cuts]
    for f, o in data["flash"]["white"]:
        if o == 0.9:  # the run before it is 0.3 and the run after it 0.55, 0.25, 0
            assert [keys[f - 1], keys[f + 1], keys[f + 2], keys[f + 3]] == [0.3, 0.55, 0.25, 0.0]
    assert 'id="flash"' in html and "#flash" in html
    punch = dict(data["flash"]["punch"])
    first = round(cuts[0]["at"] * 30)
    assert punch[first] == 1.045 and punch[first + 8] == 1.0 and 1.0 < punch[first + 4] < 1.045


def test_hidden_punch_changes_are_silent(ad_job):
    # The B-roll covers some of the changes; only those outside it are seen and heard.
    broll = [{"clip": "pexels-1234567", "in": 1.0, "from": "your back is already", "to": "most people wait until", "y": 40}]
    job = punch_job(ad_job, flash_plan(broll=broll))
    _, facts, data = page_of(job, "9x16")
    [b] = facts["broll"]
    all_changes = [s[0] for prev, s in zip(data["states"], data["states"][1:]) if s[1:] != prev[1:]]
    inside = [t for t in all_changes if b["start"] <= t < b["end"]]
    assert inside and len(all_changes) == 3  # the fixture really puts a change under the B-roll
    seen = [e["at"] for e in changes(facts)]
    assert len(seen) == len(all_changes) - len(inside)
    assert not any(b["start"] - 0.05 <= t < b["end"] for t in seen)
    whitest = [round(f / 30, 3) for f, o in data["flash"]["white"] if o == 0.9]
    assert whitest == seen  # no flash where there is no sound


def test_broll_and_headings_make_events(ad_job):
    job = punch_job(ad_job, standard_plan())
    _, facts, _ = page_of(job, "9x16")
    [b] = facts["broll"]
    [h] = facts["headings"]
    ev = facts["events"]
    assert [e["at"] for e in ev if e["event"] in ("b-roll in", "b-roll out")] == [b["start"], b["end"]]
    assert all(e["role"] == "cut" for e in ev if e["event"].startswith("b-roll"))
    hits = [e for e in ev if e["role"] == "hit"]
    assert [e["at"] for e in hits] == [round(h[0] + 0.12, 3)]  # the opening lockup makes none
    assert not any(e["at"] == 0 for e in ev)


def test_flash_off_keeps_the_sound(ad_job):
    job = punch_job(ad_job, flash_plan())
    on = page_of(job, "9x16")
    (job.work / "plan.json").write_text(json.dumps({"lockup": "kit", **flash_plan(flash=False)}), encoding="utf-8")
    off = page_of(job, "9x16")
    assert changes(on[1]) == changes(off[1]) and len(changes(off[1])) == 3
    assert 'id="flash"' not in off[0] and off[2]["flash"] == {"white": [], "punch": []}
    assert "id=\"flash\"" in on[0]


def test_events_are_sorted_and_carry_sfx_flags(ad_job):
    plan = standard_plan()
    plan["broll"][0]["sfx"] = False
    plan["headings"][0]["sfx"] = False
    _, facts, _ = page_of(punch_job(ad_job, plan), "9x16")
    ev = facts["events"]
    assert [e["at"] for e in ev] == sorted(e["at"] for e in ev)
    assert all(isinstance(e["sfx"], bool) for e in ev)
    assert {e["sfx"] for e in ev if e["event"].startswith("b-roll") or e["role"] == "hit"} == {False}
    assert all(e["sfx"] for e in ev if e["event"] == "camera change")


def test_a_stretch_that_stays_plain_makes_no_change(ad_job):
    from adeditor import shutter

    states = [[0.0, 1.0, 0.0, 0.0], [1.0, 1.0, 0.0, 0.0], [2.0, 1.12, 5.0, 3.0], [3.0, 1.12, 5.0, 3.0]]
    assert shutter.punch_changes(states, []) == [2.0]
    assert shutter.punch_changes(states, [(1.5, 2.0)]) == [2.0]  # the end is outside the window
    assert shutter.punch_changes(states, [(1.5, 2.5)]) == []


def test_a_change_keys_on_the_frame_its_rounded_time_lands_on():
    from adeditor import shutter

    states = [[0.0, 1.0, 0.0, 0.0], [21.43345, 1.12, 5.0, 3.0]]  # the page sets this state at 21.433, frame 643
    [t] = shutter.punch_changes(states, [])
    assert t == 21.433 and dict(shutter.flash_keys([t])["white"])[643] == 0.9


def logo_job(ad_job, make, name="logo.png", paper="#FAFAF7", **job_args):
    words, length = words_and_length()
    job = ad_job(words, length, plan=job_args.pop("plan", {}), **job_args)
    make(job.brand / name)
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    kit["logo"] = name
    kit["colours"]["paper"] = paper
    (job.brand / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    return job, kit


def white_png(path):
    from PIL import Image

    img = Image.new("RGBA", (60, 30), (0, 0, 0, 0))
    img.paste((255, 255, 255, 255), (10, 8, 50, 22))
    img.save(path)


def dark_png(path):
    from PIL import Image

    img = Image.new("RGBA", (60, 30), (0, 0, 0, 0))
    img.paste((20, 20, 20, 255), (10, 8, 50, 22))
    img.save(path)


def end_ground(job):
    html = page.build(job, "9x16").read_text(encoding="utf-8")
    facts = json.loads((job.work / "pages" / "9x16" / "page.json").read_text(encoding="utf-8"))
    return html, facts["end_ground"]


def test_a_white_logo_on_transparent_gives_the_deep_end_card(ad_job):
    job, kit = logo_job(ad_job, white_png)
    html, ground = end_ground(job)
    assert ground == "deep"
    assert re.search(rf"#end \{{[^}}]*background: {kit['colours']['deep']}", html)
    assert not re.search(r"#endLogo \{[^}]*mix-blend-mode", html)


def test_a_dark_logo_gives_the_paper_end_card(ad_job):
    job, kit = logo_job(ad_job, dark_png)
    html, ground = end_ground(job)
    assert ground == "paper"
    assert re.search(rf"#end \{{[^}}]*background: {kit['colours']['paper']}", html)


def test_an_unreadable_logo_gives_the_paper_end_card(ad_job):
    job, _ = logo_job(ad_job, lambda p: p.write_bytes(b"not an image"))
    assert end_ground(job)[1] == "paper"


SVG = ('<svg xmlns="http://www.w3.org/2000/svg"><path fill="#fff" d="M0 0h9v9z"/>'
       '<circle style="fill:#FFFFFF" r="3"/></svg>')


def test_an_svg_with_only_white_fills_gives_the_deep_end_card(ad_job):
    job, _ = logo_job(ad_job, lambda p: p.write_text(SVG), name="logo.svg")
    assert end_ground(job)[1] == "deep"


def test_an_svg_with_dark_fills_gives_the_paper_end_card(ad_job):
    dark = SVG.replace("#fff", "#1a1a1a").replace("#FFFFFF", "#1a1a1a")
    job, _ = logo_job(ad_job, lambda p: p.write_text(dark), name="logo.svg")
    assert end_ground(job)[1] == "paper"


def test_the_swatch_shows_the_logo_on_the_ground_the_end_card_uses(ad_job):
    from adeditor import brand

    job, kit = logo_job(ad_job, white_png)
    swatch = brand.write_swatch(job.client, kit).read_text(encoding="utf-8")
    assert f"alt='logo' style='background:{kit['colours']['deep']}'" in swatch


def panel_logo_style(job, ratio="9x16"):
    html = page.build(job, ratio).read_text(encoding="utf-8")
    facts = json.loads((job.work / "pages" / ratio / "page.json").read_text(encoding="utf-8"))
    m = re.search(r'<img id="idleLogo"[^>]*style="([^"]*)"', html)
    return (m.group(1) if m else None), facts["panel_logo_silhouette"]


def panel_job(ad_job, video, make):
    # a high head forces the panel layout, where the logo sits on the paper panel
    return logo_job(ad_job, make, paper="#F6F1E7", plan=standard_plan(), clips=[("pexels-1234567", 10)],
                    aroll=video(2, 2560, 1440), face=HIGH_HEAD, kit=TWO_LINE_LOCKUP)[0]


def test_a_white_logo_in_the_panel_is_drawn_as_a_dark_silhouette(ad_job, video):
    style, flag = panel_logo_style(panel_job(ad_job, video, white_png))
    assert flag is True and style and "filter:brightness(0)" in style and "multiply" not in style


def test_a_dark_logo_in_the_panel_gets_no_filter(ad_job, video):
    style, flag = panel_logo_style(panel_job(ad_job, video, dark_png))
    assert flag is False and style and "brightness" not in style


def test_captions_leave_out_the_words_a_heading_shows():
    from adeditor import plan as planmod

    texts = ["market", "after", "market", "we", "got", "calendars", "booked", "and", "then", "booked", "again"]
    words = [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, t in enumerate(texts)]
    kept = planmod.Kept(words)
    idx = planmod._said_words(kept, "calendars booked", 0.0)
    assert idx == [5, 6]
    heading = {"start": 0.0, "end": 3.9, "words": idx}  # shows from the start to just after "booked"
    got = page.hide_heading_words([[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10]], words, [heading])
    shown = [[words[i]["text"] for i in g] for g in got]
    assert shown == [["market", "after", "market", "we"], ["got", "and"], ["then", "booked", "again"]]
    # a group left with no words isn't drawn
    assert page.hide_heading_words([[5, 6], [7]], words, [heading]) == [[7]]
    # words said outside the window stay
    late = {"start": 0.0, "end": 2.0, "words": idx}
    assert page.hide_heading_words([[5, 6]], words, [late]) == [[5, 6]]


def test_a_headings_words_are_absent_from_the_pages_captions_in_its_window(ad_job):
    words, _ = words_and_length()
    with_heading = punch_job(ad_job, standard_plan())
    _, facts, data = page_of(with_heading, "9x16")
    [h] = facts["headings"]
    shown = {i for _, _, ws in data["caps"] for i, *_ in ws}
    in_window = {i for i, w in enumerate(words) if w["start"] < h[1] and w["end"] > h[0]}
    said = {i for i in in_window if words[i]["text"].strip(".,'").lower() in ("you", "don't", "have", "to", "wait")}
    assert said and not said & shown
    # without the heading the same words are captioned
    bare = {**standard_plan(), "headings": []}
    (with_heading.work / "plan.json").write_text(json.dumps({"lockup": "kit", **bare}), encoding="utf-8")
    _, _, data2 = page_of(with_heading, "9x16")
    assert said <= {i for _, _, ws in data2["caps"] for i, *_ in ws}
