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
                                                  "You don't have to wait"])
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
