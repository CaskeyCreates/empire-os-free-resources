import html
import json
import re

import pytest
from conftest import cut_words, make_speech

from adeditor import cli, graphic_page, layout, media, page, plan, render

TEXT = ("Hello and welcome to the clinic today. First we listen, then we test, next we plan, "
        "finally we move freely again. Most people wait until it really hurts. "
        "Book a call and we'll show you how it works.")
RATIOS = ("9x16", "4x5", "1x1")


def kit_and_measure(ad_job):
    words = cut_words(TEXT)
    job = ad_job(words, round(words[-1]["end"] + 0.5, 3))
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    return job, kit, page.Measure(kit, job.brand)


def items(*texts, start=3.0, step=0.9):
    return [{"text": t, "at": round(start + 0.6 + step * i, 3)} for i, t in enumerate(texts)]


def graphic(kind, **kw):
    base = {"kind": kind, "start": 3.0, "end": 9.0, "ground": "deep", "title": None, "items": [], "sfx": True}
    extra = {
        "cycle": {"items": items("She gets anxious", "She avoids it", "You step in", "A little relief")},
        "steps": {"items": items("First we listen", "then we test", "next we plan", "finally we move")},
        "checklist": {"items": items("A full assessment", "A plan that fits", "Follow up every week")},
        "versus": {"left": items("Waiting it out", "Hoping it goes", start=3.0),
                   "right": items("Moving freely", "Starting this week", start=5.0),
                   "left_header": {"text": "Before", "at": 3.5}, "right_header": {"text": "After", "at": 3.6},
                   "turn": {"text": "but now", "at": 4.8}},
        "stat": {"value": "30", "value_at": 4.4, "label": {"text": "appointments a month", "at": 5.0}},
        "quote": {"line": {"text": "Your back isn't broken, it's waiting for you", "at": 3.6, "end": 6.2},
                  "accent": "waiting"},
    }[kind]
    return {**base, **extra, **kw}


def readable(built) -> str:
    """What the graphic says, as the page shows it."""
    return html.unescape(re.sub(r"<[^>]+>", " ", built["html"].replace("<br>", " ")))


def squash(s: str) -> str:
    return " ".join(s.split())


def texts_of(g) -> list[str]:
    out = [i["text"] for i in g["items"]]
    for k in ("left", "right"):
        out += [i["text"] for i in g.get(k, [])]
    for k in ("title", "label", "left_header", "right_header"):
        if g.get(k):
            out.append(g[k]["text"])
    if g.get("line"):
        out.append(g["line"]["text"])
    return out


@pytest.mark.parametrize("ratio", RATIOS)
@pytest.mark.parametrize("kind", ["cycle", "steps", "versus", "stat", "checklist", "quote"])
def test_each_kind_builds_in_every_ratio(ad_job, kind, ratio):
    _, kit, m = kit_and_measure(ad_job)
    for ground in ("deep", "paper"):
        g = graphic(kind, ground=ground, title={"text": "How it works", "at": 3.5})
        built = graphic_page.build(g, 1, ratio, kit, m)
        body = squash(readable(built))
        for t in texts_of(g):
            assert squash(t) in body, t
        shown = built["html"] + built["js"]
        colours = {c.upper() for c in kit["colours"].values()}
        assert {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", shown)} <= colours
        for rgb in re.findall(r"rgba\(([\d,\s.]+)\)", shown):
            r, gr, b = (int(v) for v in rgb.split(",")[:3])
            assert f"#{r:02X}{gr:02X}{b:02X}" in colours | {"#000000"}
        families = {f.strip().strip("'\"") for f in re.findall(r"font-family:([^;\"]+)", built["html"])}
        assert families and families <= {kit["fonts"]["display"]["family"], kit["fonts"]["body"]["family"]}
        assert 'id="g1"' in built["html"] and 'class="clip"' in built["html"]
        assert 0 < built["full_at"] <= g["end"]


@pytest.mark.parametrize("ratio", RATIOS)
@pytest.mark.parametrize("kind", ["cycle", "steps", "versus", "stat", "checklist", "quote"])
def test_graphics_stay_inside_the_safe_area(ad_job, kind, ratio):
    _, kit, m = kit_and_measure(ad_job)
    band = layout.TEXT[ratio]
    for with_title in (False, True):
        g = graphic(kind, title={"text": "How it works", "at": 3.5} if with_title else None)
        built = graphic_page.build(g, 1, ratio, kit, m)
        assert built["boxes"]
        for x0, y0, x1, y1 in built["boxes"]:
            assert x0 >= 40 and x1 <= 1080 - 40 and y1 <= graphic_page.SAFE_BOTTOM[ratio], (x0, y0, x1, y1)
            assert y0 >= (270 if ratio == "9x16" else 40), (x0, y0, x1, y1)


def land_times(js: str, n=1) -> dict[int, float]:
    """The time each item's own tween is held at, from its fromTo line."""
    return {int(i): float(t) for i, t in re.findall(rf'tl\.fromTo\("#g{n}e(\d+)",[^\n]*, ([\d.]+)\);', js)}


@pytest.mark.parametrize("kind", ["cycle", "steps", "checklist", "versus", "stat"])
def test_items_tween_at_their_words(ad_job, kind):
    _, kit, m = kit_and_measure(ad_job)
    g = graphic(kind)
    built = graphic_page.build(g, 1, "9x16", kit, m)
    got = land_times(built["js"])
    flat = g["items"] or [*g.get("left", []), *g.get("right", [])] or [g["label"]]
    assert len(got) == len(flat)
    # the versus numbers its items left then right; every other kind in order
    for i, item in enumerate(flat):
        assert got[i] == pytest.approx(item["at"], abs=1e-3)
    assert min(got.values()) >= g["start"] + 0.4
    for t in re.findall(r"tl\.\w+\([^\n]*, ([\d.]+)\);", built["js"].replace('{ opacity: 0 }, 0);', '')):
        assert float(t) >= g["start"] - 1e-6


def test_the_page_script_has_no_tween_before_the_entrance_but_the_fade_in(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    g = graphic("cycle")
    built = graphic_page.build(g, 1, "9x16", kit, m)
    early = [float(t) for t in re.findall(r"tl\.\w+\([^\n]*, ([\d.]+)\);", built["js"])
             if float(t) < g["start"] + 0.4]
    assert early, "the ground fades in and the ring starts drawing"
    assert max(early) < g["start"] + 0.4


def test_long_items_shrink_then_refuse(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    one = "Moving freely again at work"
    width = m.width("display", "regular", one, 50, -0.015)
    f, lines = graphic_page.fit_text(m, "display", "regular", one, 50, width / 1.3, 1)
    assert lines == [one] and 0.75 <= f < 1 and f == pytest.approx(1 / 1.3, abs=0.02)
    with pytest.raises(graphic_page.FitError) as err:
        graphic_page.fit_text(m, "display", "regular", one, 50, width / 1.5, 1)
    assert one in str(err.value)
    # through a whole graphic: the message names the graphic, the item and the ratio
    long = ("Waiting around for months and months while it only gets worse and worse every single day and "
            "nobody does a thing about it at all")
    g = graphic("checklist", items=items("A full assessment", long, "Follow up"))
    with pytest.raises(graphic_page.FitError) as err:
        graphic_page.build(g, 2, "1x1", kit, m)
    text = str(err.value)
    assert "Graphic 2" in text and long in text and "1:1" in text
    assert "-" * 2 not in text and chr(0x2014) not in text


def test_graphic_events(ad_job):
    _, kit, m = kit_and_measure(ad_job)

    def got(g, ratio="9x16"):
        return [(e["role"], e["at"]) for e in graphic_page.build(g, 1, ratio, kit, m)["events"]]

    cyc = graphic("cycle")
    ev = got(cyc)
    roles = [r for r, _ in ev]
    assert roles.count("cut") == 2 and roles.count("push") == 1 and roles.count("whip") == 4
    assert ev[0] == ("cut", 3.0) and ev[-1] == ("cut", 9.0)
    assert ("push", pytest.approx(3.2)) in ev
    assert [t for _, t in ev] == sorted(t for _, t in ev)
    whips = [t for r, t in ev if r == "whip"]
    assert all(cyc["start"] < t < cyc["end"] for t in whips)

    assert [r for r, _ in got(graphic("checklist"))].count("tick") == 3
    assert [r for r, _ in got(graphic("steps"))].count("tick") == 4
    stat = got(graphic("stat"))
    assert ("hit", pytest.approx(4.4)) in stat and [r for r, _ in stat].count("hit") == 1
    titled = got(graphic("checklist", title={"text": "What you get", "at": 3.5}))
    assert ("hit", pytest.approx(3.5)) in titled
    vs = got(graphic("versus"))
    assert ("hit", pytest.approx(4.8)) in vs and [r for r, _ in vs].count("tick") == 4

    silent = graphic_page.build(graphic("cycle", sfx=False), 1, "9x16", kit, m)["events"]
    assert silent and all(e["sfx"] is False for e in silent)
    assert all(e["sfx"] is True for e in graphic_page.build(cyc, 1, "9x16", kit, m)["events"])
    assert all({"role", "event", "at", "sfx"} <= set(e) for e in silent)


def test_a_cycle_that_runs_out_of_room_skips_its_last_hop(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    g = graphic("cycle", end=7.2, items=items("One", "Two", "Three", "Four", start=3.0, step=1.0))
    whips = [e for e in graphic_page.build(g, 1, "9x16", kit, m)["events"] if e["role"] == "whip"]
    assert len(whips) == 3


def plan_with_graphic(**spec):
    return {"graphics": [{"kind": "steps", "from": "first we listen", "to": "finally we move", "to_end": True,
                          "items": ["first we listen", "then we test", "next we plan", "finally we move"], **spec}]}


def job_with(ad_job, plan):
    words = cut_words(TEXT)
    return ad_job(words, round(words[-1]["end"] + 0.5, 3), plan=plan)


KINDS = ["cycle", "steps", "versus", "stat", "checklist", "quote"]


@pytest.mark.parametrize("ratio", RATIOS)
@pytest.mark.parametrize("kind", KINDS)
def test_the_block_spans_the_canvas_and_sits_at_its_optical_centre(ad_job, kind, ratio):
    _, kit, m = kit_and_measure(ad_job)
    built = graphic_page.build(graphic(kind), 1, ratio, kit, m)
    x0 = min(b[0] for b in built["boxes"])
    x1 = max(b[2] for b in built["boxes"])
    y0 = min(b[1] for b in built["boxes"])
    y1 = max(b[3] for b in built["boxes"])
    assert x1 - x0 >= (0.75 if ratio == "9x16" else 0.70) * 1080 - 1, (x0, x1)
    bottom = {"9x16": 1330, "4x5": 1310, "1x1": 1040}[ratio]
    safe = 270 if ratio == "9x16" else 40
    want = safe + 0.45 * (bottom - safe)
    assert y1 <= bottom
    assert abs((y0 + y1) / 2 - want) <= 45, ((y0 + y1) / 2, want)  # ink, not the line boxes, so a little loose


@pytest.mark.parametrize("kind,least", [("checklist", 64), ("steps", 64), ("quote", 84), ("stat", 220)])
def test_text_sizes_in_9x16(ad_job, kind, least):
    _, kit, m = kit_and_measure(ad_job)
    g = graphic(kind, title={"text": "How it works", "at": 3.5})
    body = graphic_page.build(g, 1, "9x16", kit, m)["html"]
    sizes = [float(v) for v in re.findall(r"font-size:([\d.]+)px", body)]
    assert max(sizes) >= least
    title = re.search(r'id="g1t"[^>]*font-size:([\d.]+)px', body)
    assert float(title.group(1)) >= 52


@pytest.mark.parametrize("ground,grain", [("deep", "0.05"), ("paper", "0.04")])
def test_the_ground_is_alive_and_driven_by_the_timeline(ad_job, ground, grain):
    _, kit, m = kit_and_measure(ad_job)
    built = graphic_page.build(graphic("stat", ground=ground), 1, "9x16", kit, m)
    body, js = built["html"], built["js"]
    assert body.count('id="g1glow') == 2 and f"opacity:{grain};mix-blend-mode:overlay" in body
    assert ("opacity:0.12" in body) == (ground == "deep") and ("opacity:0.1\"" in body) == (ground == "paper")
    assert "animation" not in body and "@keyframes" not in body
    assert '"#g1glow0"' in js and '"#g1glow1"' in js and "sine.inOut" in js and "yoyo: true" in js
    assert js.count('tl.set("#g1grain"') > 20  # the grain's offset steps through the window
    assert not any(c in body for c in ("#FFFFFF", "#000000"))


@pytest.mark.parametrize("kind", KINDS)
def test_every_graphic_covers_the_captions(ad_job, kind):
    _, kit, m = kit_and_measure(ad_job)
    html = graphic_page.build(graphic(kind), 1, "9x16", kit, m)["html"]
    z = int(re.search(r"z-index:(\d+)", html).group(1))
    assert z > 30  # the caption layer (page.py) is z-index 30


def test_a_three_item_checklist_fills_9x16_down_to_the_safe_bottom(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    boxes = graphic_page.build(graphic("checklist"), 1, "9x16", kit, m)["boxes"]
    y0, y1 = min(b[1] for b in boxes), max(b[3] for b in boxes)
    assert abs((y0 + y1) / 2 - (270 + 0.45 * (1330 - 270))) <= 2, ((y0 + y1) / 2)
    assert y1 <= 1330 and y0 >= 270


@pytest.mark.parametrize("ratio", RATIOS)
def test_growing_never_adds_a_line_break(ad_job, ratio):
    _, kit, m = kit_and_measure(ad_job)
    g = graphic("checklist", items=items("qualify", "show up", "ready to go solar"))
    body = graphic_page.build(g, 1, ratio, kit, m)["html"]
    for i in range(3):
        row = re.search(rf'id="g1e{i}"[^>]*>(.*?)</div>', body).group(1)
        assert "<br>" not in row, (ratio, i, row)
    assert max(float(v) for v in re.findall(r"font-size:([\d.]+)px", body)) > 64 * layout.TEXT[ratio]["sc"]  # it did grow


def test_checklist_boxes_rows_and_the_newest_item_settling(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    built = graphic_page.build(graphic("checklist"), 1, "9x16", kit, m)
    body = built["html"]
    size = float(re.search(r'id="g1e0"[^>]*font-size:([\d.]+)px', body).group(1))
    box = re.search(r'id="g1c0" style="[^"]*width:([\d.]+)px', body)
    assert float(box.group(1)) == pytest.approx(0.9 * size, abs=0.5)
    tops = [float(t) for t in re.findall(r'id="g1p\d" style="[^"]*top:([\d.]+)px', body)]
    heights = [float(h) for h in re.findall(r'id="g1p\d" style="[^"]*height:([\d.]+)px', body)]
    assert heights[0] == pytest.approx(1.88 * size, abs=0.5)  # 0.35 em above and below a 1.18 em line
    assert [round(b - a, 1) for a, b in zip(tops, tops[1:])] == [pytest.approx(heights[0] + 0.2 * size, abs=0.5)] * 2
    assert re.search(r'tl\.fromTo\("#g1e1", \{ opacity: 0, y: 10, filter: "blur\(6px\)", scale: 1\.04 \}', built["js"])


@pytest.mark.parametrize("ratio", RATIOS)
def test_captions_sit_at_the_band_top_during_a_graphic(ad_job, ratio):
    job = job_with(ad_job, plan_with_graphic())
    out = page.build(job, ratio)
    facts = json.loads((out.parent / "page.json").read_text(encoding="utf-8"))
    (g,) = facts["graphics"]
    band = layout.TEXT[ratio]
    inside = [c for c in facts["captions"] if g["start"] + 0.1 <= c["a"] and c["b"] <= g["end"]]
    assert inside and all(c["top"] == band["cap_min"] for c in inside)
    outside = [c for c in facts["captions"] if c["b"] < g["start"] - 0.1]
    assert outside


def test_page_json_carries_the_graphics_and_their_events(ad_job):
    job = job_with(ad_job, plan_with_graphic())
    out = page.build(job, "9x16")
    facts = json.loads((out.parent / "page.json").read_text(encoding="utf-8"))
    (g,) = facts["graphics"]
    assert g["n"] == 1 and g["kind"] == "steps" and g["start"] < g["full_at"] <= g["end"]
    roles = [e["role"] for e in facts["events"] if e["event"] != "camera change"]  # the graphics' own
    assert roles.count("cut") == 2 and roles.count("tick") == 4 and roles.count("push") == 1
    html_text = out.read_text(encoding="utf-8")
    assert 'id="g1"' in html_text and "First we listen".lower() in html_text.lower()
    # the lockup gives way to a graphic that starts before any heading or B-roll
    assert facts["lockup"][1] <= g["start"]


def test_a_page_with_no_graphics_is_unchanged_in_facts(ad_job):
    job = job_with(ad_job, {})
    facts = json.loads((page.build(job, "1x1").parent / "page.json").read_text(encoding="utf-8"))
    assert facts["graphics"] == [] and {e["event"] for e in facts["events"]} <= {"camera change"}


def test_plan_check_refuses_an_item_too_long_to_fit(ad_job, capsys):
    long = ("first we listen to absolutely everything you could possibly ever want to tell us about your back pain "
            "and your work")
    text = f"Hello and welcome to the clinic today and thank you for being here. {long} then we test. Book a call now."
    words = cut_words(text, step=0.2, length=0.15)
    plan = {"graphics": [{"kind": "checklist", "from": "first we listen", "to": "then we test", "to_end": True,
                          "items": [long, "then we test"]}]}
    job = ad_job(words, round(words[-1]["end"] + 0.5, 3), plan=plan)
    assert cli.main(["plan", "check", "acme", job.video.name]) == 1
    err = capsys.readouterr().err
    assert "Graphic 1" in err and long in err and "9:16" in err


def test_plan_check_passes_graphics_that_fit(ad_job):
    job = job_with(ad_job, plan_with_graphic())
    assert cli.main(["plan", "check", "acme", job.video.name]) == 0


def test_the_floor_does_not_compound_in_a_tall_block(ad_job):
    # six rows shrink the block (k) and one long item shrinks its own line (f): together they never go below
    # 0.75 of the 64 px floor, and past that the graphic is refused
    _, kit, m = kit_and_measure(ad_job)
    refused = built_ok = 0
    for extra in range(0, 40, 4):
        long = "Waiting around for months while it gets worse " + "and worse " * (extra // 4) + "every day"
        g = graphic("checklist", items=items("One", "Two", "Three", "Four", "Five", long))
        for ratio in RATIOS:
            try:
                body = graphic_page.build(g, 1, ratio, kit, m)["html"]
            except graphic_page.FitError as err:
                refused += 1
                assert long in str(err)
                continue
            built_ok += 1
            sizes = [float(v) for v in re.findall(r'id="g1e\d"[^>]*font-size:([\d.]+)px', body)]
            assert min(sizes) >= 0.75 * 64 * layout.TEXT[ratio]["sc"] - 0.01, (ratio, sizes)
    assert refused and built_ok


def test_fit_errors_do_not_hide_a_broken_graphic(ad_job):
    job, kit, m = kit_and_measure(ad_job)
    assert graphic_page.fit_errors(job, [graphic("cycle")]) == []
    broken = {k: v for k, v in graphic("cycle").items() if k != "items"}
    with pytest.raises(KeyError):
        graphic_page.fit_errors(job, [broken])


def test_the_stat_counts_up_on_the_timeline_with_no_callback(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    g = graphic("stat", value="$7,500")
    g["value_at"] = 5.0
    js = graphic_page.build(g, 1, "9x16", kit, m)["js"]
    assert "onUpdate" not in js and "onComplete" not in js
    sets = re.findall(r'tl\.set\("#g1v", \{ textContent: "([^"]+)" \}, ([\d.]+)\);', js)
    values = [float(v.strip("$").replace(",", "")) for v, _ in sets]
    assert sets[0][0] == "$0" and sets[-1][0] == "$7,500" and float(sets[-1][1]) == pytest.approx(5.0)
    assert values == sorted(values) and len(sets) >= 20
    assert all(a <= b for a, b in zip([float(t) for _, t in sets], [float(t) for _, t in sets][1:]))


def test_the_glow_tweens_end_inside_the_graphics_window(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    for end in (3.4, 5.0, 9.0, 30.0):
        g = graphic("stat", end=end, value_at=3.5)
        js = graphic_page.build(g, 1, "9x16", kit, m)["js"]
        found = re.findall(r'"#g1glow\d".*?duration: ([\d.]+), ease: "sine.inOut", repeat: (\d+)', js)
        assert len(found) == 4
        for dur, reps in found:
            assert float(dur) * (int(reps) + 1) <= end - g["start"] + 1e-6


@pytest.mark.render
def test_render_a_cycle(ad_job, tmp_path, video):
    texts = ("Hello there my good friend. First she worries, then she avoids, next you step in, finally relief "
             "comes. Book a call now.").split()
    pattern = [("s", 0.4)] + [("w", 0.4), ("s", 0.15)] * len(texts) + [("s", 0.2)]
    speech = round(sum(d for _, d in pattern), 3)
    take = video(speech, 1080, 1920, audio=make_speech(tmp_path, pattern))
    words, t = [], 0.0
    for kind, dur in pattern:
        if kind == "w":
            words.append({"text": texts[len(words)], "start": round(t, 3), "end": round(t + dur, 3), "src": round(t, 3)})
        t += dur
    plan = {"graphics": [{"kind": "cycle", "from": "first", "to": "relief", "to_end": True, "title": "worries",
                          "items": ["first she worries", "then she avoids", "next you step in", "finally relief"]}]}
    job = ad_job(words, speech, plan=plan, aroll=take)
    done = render.render(job)
    assert set(done) == set(RATIOS)
    for ratio in RATIOS:
        facts = json.loads((job.work / "pages" / ratio / "page.json").read_text(encoding="utf-8"))
        (g,) = facts["graphics"]
        frame = tmp_path / f"cycle-{ratio}.png"
        media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{g['full_at']:.3f}", "-i", str(done[ratio]),
                   "-frames:v", "1", str(frame)])
        assert frame.stat().st_size > 5000


def test_lists_grow_to_fill_but_never_past_110_px(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    for ratio in RATIOS:
        sc = layout.TEXT[ratio]["sc"]
        g = graphic("checklist", items=items("Qualify", "Show up", "Ready to go solar"))
        body = graphic_page.build(g, 1, ratio, kit, m)["html"]
        size = float(re.search(r'id="g1e0"[^>]*font-size:([\d.]+)px', body).group(1))
        assert 64 * sc < size <= 110 * sc + 0.01, (ratio, size)
    # a long list still fits, at or above the floor
    six = graphic("checklist", items=items("One thing", "Two things", "Three things", "Four things", "Five things",
                                           "Six things"))
    body = graphic_page.build(six, 1, "9x16", kit, m)["html"]
    assert float(re.search(r'id="g1e0"[^>]*font-size:([\d.]+)px', body).group(1)) >= 0.75 * 64


def test_a_quotes_lines_are_near_equal(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    g = graphic("quote", line={"text": "keep their closers calendars booked, market after market", "at": 3.6, "end": 6.2},
                accent="booked")
    for ratio in RATIOS:
        built = graphic_page.build(g, 1, ratio, kit, m)
        widths = [b[2] - b[0] for b in built["boxes"] if b[3] - b[1] > 20 and b[2] - b[0] > 100]
        if len(widths) > 1:
            assert min(widths) >= 0.55 * max(widths), (ratio, widths)


@pytest.mark.render
def test_render_a_stat_shows_its_number(ad_job, tmp_path, video):
    from PIL import Image

    texts = "Hello there my good friend. In thirty days you get results. Book a call now.".split()
    pattern = [("s", 0.4)] + [("w", 0.4), ("s", 0.15)] * len(texts) + [("s", 0.2)]
    speech = round(sum(d for _, d in pattern), 3)
    take = video(speech, 1080, 1920, audio=make_speech(tmp_path, pattern))
    words, t = [], 0.0
    for kind, dur in pattern:
        if kind == "w":
            words.append({"text": texts[len(words)], "start": round(t, 3), "end": round(t + dur, 3), "src": round(t, 3)})
        t += dur
    spec = {"graphics": [{"kind": "stat", "from": "in thirty days", "to": "results", "to_end": True, "value": "30",
                          "label": "days"}]}
    job = ad_job(words, speech, plan=spec, aroll=take)
    out = render.render(job, ("9x16",))["9x16"]
    (g,) = json.loads((job.work / "pages" / "9x16" / "page.json").read_text(encoding="utf-8"))["graphics"]
    hit = plan.load(job)["graphics"][0]["value_at"]
    mid, end = (max(g["start"] + 0.15, hit - 0.9) + hit) / 2 - 0.1, g["full_at"]  # mid-count, and fully landed
    frames = []
    for name, at in (("mid", mid), ("end", end)):
        f = tmp_path / f"stat-{name}.png"
        media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(out), "-frames:v", "1", str(f)])
        frames.append(Image.open(f).convert("L").crop((0, 400, 1080, 1100)))
    # the number is the biggest ink on the frame: the end frame has plenty, and it differs from mid count
    from PIL import ImageChops
    assert ImageChops.difference(frames[0], frames[1]).getbbox() is not None
    hist = frames[1].histogram()
    assert sum(hist[201:]) > 2000 or sum(hist[:60]) > 2000


def test_wrapped_text_is_balanced_with_no_single_word_last_line(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    text = "pay very close attention"
    three = max(m.width("display", "regular", s, 50, -0.015) for s in ("pay very close", "close attention")) * 1.01
    assert len(graphic_page._greedy(m, "display", "regular", text.split(), 50, three, -0.015)) == 2  # 3 words + 1
    lines = graphic_page._wrap(m, "display", "regular", text, 50, three, -0.015)
    assert lines == ["pay very", "close attention"]
    # a two word item may still wrap one word a line, and one that fits is left alone
    narrow = m.width("display", "regular", "calendars", 50, -0.015) * 1.05
    assert graphic_page._wrap(m, "display", "regular", "calendars booked", 50, narrow, -0.015) == ["calendars", "booked"]
    f, got = graphic_page.fit_text(m, "display", "regular", text, 50, 10000, 2)
    assert f == 1.0 and got == [text]


def test_a_stub_last_line_shrinks_the_text_until_it_balances(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    text = "pay very close attention"
    three = m.width("display", "regular", "pay very close", 50, -0.015) * 1.01
    f, lines = graphic_page.fit_text(m, "display", "regular", text, 50, three / 0.97, 3)
    assert len(lines[-1].split()) > 1 and f < 1


def test_text_that_can_only_end_on_one_word_is_refused(ad_job):
    _, kit, m = kit_and_measure(ad_job)
    text = "go go supercalifragilisticexpialidocious"
    wide = m.width("display", "regular", "supercalifragilisticexpialidocious", 50, -0.015) * 0.8  # fits alone only at 0.8
    with pytest.raises(graphic_page.LoneWordError) as err:
        graphic_page.fit_text(m, "display", "regular", text, 50, wide, 3)
    assert text in str(err.value) and "one word alone" in str(err.value)
    # through a graphic, the message names the graphic and the ratio
    s = graphic_page._G(graphic("checklist"), 2, "9x16", kit, m)
    with pytest.raises(graphic_page.FitError) as err:
        s.fit("display", "regular", text, 50, wide, 3)
    msg = str(err.value)
    assert msg.startswith('Graphic 2: "') and "9:16" in msg and "shorter or longer run" in msg
    assert "-" * 2 not in msg and chr(0x2014) not in msg and chr(0x2013) not in msg
