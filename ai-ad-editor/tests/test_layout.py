import pytest

from adeditor import layout
from adeditor.layout import Window

REFERENCE_FACE = {"x": 0.369, "y": 0.403, "w": 0.351, "h": 0.197}


def track(face, n=10, step=0.5):
    return [{"t": i * step, **face} for i in range(n)]


def face_centred(cx, cy=0.4, w=0.2, h=0.2):
    return {"x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h}


def test_ratios_and_text_numbers_are_the_reference_ads_with_the_ruled_cap_max():
    assert layout.RATIOS == {"9x16": 1920, "4x5": 1350, "1x1": 1080}
    t = layout.TEXT
    assert [t[r]["sc"] for r in layout.RATIOS] == [1.0, 0.88, 0.66]
    assert [t[r]["cap"] for r in layout.RATIOS] == [60, 56, 48]
    assert [t[r]["cap_min"] for r in layout.RATIOS] == [1212, 930, 880]
    assert [t[r]["cap_max"] for r in layout.RATIOS] == [1233, 1238, 985]
    assert [t[r]["lock"] for r in layout.RATIOS] == [300, 58, 30]
    assert [t[r]["heading"] for r in layout.RATIOS] == [330, 92, 62]
    assert [t[r]["paper_h"] for r in layout.RATIOS] == [720, 430, 330]
    assert [t[r]["end_top"] for r in layout.RATIOS] == [470, "centred", "centred"]


LOCKUP_ROWS = [("eyebrow", 1), ("rule", 1), ("h", 1), ("h", 1), ("caps", 1)]  # callout, two offer lines, detail


def reference_text(ratio):
    """The first ad's text blocks on the canvas, (top, lowest line): its lockup (callout, two offer lines,
    detail) and a two-line heading."""
    t = layout.TEXT[ratio]
    return [(t["lock"], layout.text_bottom(t["lock"], LOCKUP_ROWS, t["sc"])),
            (t["heading"], layout.text_bottom(t["heading"], [("h", 1), ("h", 1)], t["sc"]))]


def lowest(ratio):
    return layout.lowest(reference_text(ratio))


def test_a_text_block_ends_where_its_last_line_sits():
    # The first ad's own 9:16 render: its lockup's detail line sits on 563 to 565 px.
    assert layout.text_bottom(300, LOCKUP_ROWS, 1.0) == pytest.approx(564, abs=1.5)
    # a row shrunk to fit on one line takes less height
    shrunk = LOCKUP_ROWS[:2] + [("h", 0.8)] + LOCKUP_ROWS[3:]
    assert layout.text_bottom(300, shrunk, 1.0) == pytest.approx(layout.text_bottom(300, LOCKUP_ROWS, 1.0) - 58 * 1.12 * 0.2)
    # a font's own ascent moves its baseline
    assert layout.text_bottom(0, [("h", 1)], 1.0, {"h": (1.0, 0.25)}) > layout.text_bottom(0, [("h", 1)], 1.0)


def test_the_reference_ad_windows_reproduce():
    faces = track(REFERENCE_FACE)
    got = {r: layout.window(1080, 1920, faces, r, reference_text(r)) for r in layout.RATIOS}
    assert [got[r].mode for r in ("9x16", "4x5", "1x1")] == ["fade"] * 3
    assert [got[r].y0 for r in ("9x16", "4x5", "1x1")] == [0, 250, 300]
    for r, win in got.items():
        assert win.scale == pytest.approx(1.0) and win.x0 == 0 and win.warn is None and win.panel == 0
        # the head (half a face box above the box) sits at least 16 px below the lowest text line
        assert layout.head_top(faces, 1920) - win.y0 >= lowest(r) + 16
        assert win.shrink == 1.0 and win.punch
        assert (win.src_w, win.src_h) == (1080, 1920)


def test_landscape_9x16_crop():
    win = layout.window(2560, 1440, track(face_centred(0.7)), "9x16")
    assert win.scale == pytest.approx(1.3333, abs=1e-3)
    assert win.y0 == 0 and win.warn is None
    assert (win.src_w, win.src_h) == (pytest.approx(3413.33, abs=0.01), pytest.approx(1920))
    # the face centre lands at 540 on the canvas, or the window hit the right edge
    assert abs(0.7 * win.src_w - win.x0 - 540) <= 1 or win.x0 == pytest.approx(win.src_w - 1080, abs=0.01)

    small = layout.window(1920, 1080, track(face_centred(0.5)), "9x16")
    assert small.scale == pytest.approx(1.7778, abs=1e-3)
    assert small.warn == "the 9:16 crop is blown up 1.8x and will look soft; ask the client for a vertical or 4K take"


def test_a_face_near_the_edge_clamps_the_window_inside_the_frame():
    right = layout.window(2560, 1440, track(face_centred(0.95)), "9x16")
    assert right.x0 == pytest.approx(right.src_w - 1080, abs=0.01)
    left = layout.window(2560, 1440, track(face_centred(0.05)), "9x16")
    assert left.x0 == 0


def test_a_landscape_source_in_1x1_needs_no_blow_up_warning():
    win = layout.window(1920, 1080, track(face_centred(0.5)), "1x1")
    assert win.scale == pytest.approx(1.0) and win.warn is None


CORNERS = [(0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)]


@pytest.mark.parametrize("ratio", list(layout.RATIOS))
@pytest.mark.parametrize("cx,cy", CORNERS)
@pytest.mark.parametrize("src", [(1080, 1920), (2560, 1440)])
def test_punch_never_shows_an_edge(ratio, cx, cy, src):
    faces = track(face_centred(cx, cy))
    win = layout.window(*src, faces, ratio)
    H = layout.RATIOS[ratio]
    segs = [{"out": [i * 2.0, i * 2.0 + 2.0]} for i in range(5)]
    st = layout.states(segs, faces, win, 1.0)
    assert [s[0] for s in st] == [0.0, 2.0, 4.0, 6.0, 8.0]
    for k, (t, s, tx, ty) in enumerate(st):
        if k % 2 == 0 or s == 1.0:  # a punch that can't keep the captions under the chin stays plain
            assert (s, tx, ty) == (1.0, 0.0, 0.0)
            continue
        assert s == layout.PUNCH == 1.12
        # the scaled source sits at (-x0, panel - y0), then translate(tx, ty) scale(s) from its top-left
        left, top = -win.x0 + tx, win.panel - win.y0 + ty
        assert left <= 0.01 and top <= win.panel + 0.01
        assert left + win.src_w * s >= 1080 - 0.01 and top + win.src_h * s >= H - 0.01


def test_a_punch_zooms_about_the_face_when_there_is_room():
    faces = track(face_centred(0.5, 0.5, h=0.1))
    win = layout.window(2560, 1440, faces, "9x16")
    st = layout.states([{"out": [0, 2]}, {"out": [2, 4]}], faces, win, 1.0)
    cx, cy = 0.5 * win.src_w, 0.5 * win.src_h
    _, s, tx, ty = st[1]
    # the face centre stays put on the canvas
    assert s * cx + tx - win.x0 == pytest.approx(cx - win.x0, abs=0.1)
    assert s * cy + ty - win.y0 == pytest.approx(cy - win.y0, abs=0.1)


def test_states_put_the_1x_face_clock_on_the_sped_clock():
    # The face sits left for the first 5 s of the 1x take and right after.
    left, right = face_centred(0.3), face_centred(0.7)
    faces = [{"t": i * 0.5, **(left if i * 0.5 < 5 else right)} for i in range(16)]
    win = layout.window(2560, 1440, faces, "9x16")
    segs = [{"out": [0.0, 1.0]}, {"out": [1.0, 1.9]}, {"out": [2.1, 3.0]}, {"out": [3.0, 3.9]}]

    def expect(cx):  # the punch about a face centre at cx, clamped to the frame
        return round(max(min(cx * (1 - 1.12), win.x0), 1080 + win.x0 - win.src_w * 1.12), 1)

    # the last segment is 3.0 to 3.9 s of the cut: the left face at 1x, the right face at 2x
    assert layout.states(segs, faces, win, 1.0)[3][2] == expect(0.3 * win.src_w)
    assert layout.states(segs, faces, win, 2.0)[3][2] == expect(0.7 * win.src_w)


def face_with_chin(chin_out):
    h = 0.2
    return track({"x": 0.4, "y": chin_out / 1920 - h, "w": 0.2, "h": h})


def test_captions_sit_under_the_chin_and_inside_the_band():
    states = [[0.0, 1.0, 0.0, 0.0]]
    for chin, want in [(1100, 1212), (1190, 1218), (1250, 1233)]:
        faces = face_with_chin(chin)
        win = layout.window(1080, 1920, faces, "9x16")
        assert layout.caption_top(1.0, 2.0, faces, win, states, covered=False, speed=1.0) == want
        assert layout.caption_top(1.0, 2.0, faces, win, states, covered=True, speed=1.0) == 1212


def test_a_punched_segment_moves_the_caption_with_the_chin():
    faces = face_with_chin(1000)
    win = layout.window(1080, 1920, faces, "9x16")
    states = [[0.0, 1.0, 0.0, 0.0], [2.0, 1.12, -100.0, -80.0]]
    plain = layout.caption_top(0.5, 1.5, faces, win, states, covered=False, speed=1.0)
    punched = layout.caption_top(2.5, 3.5, faces, win, states, covered=False, speed=1.0)
    assert plain == 1212  # 1000 + 28 is under cap_min
    assert punched == 1212  # 1000 * 1.12 - 80 = 1040 plus 28 is still under it
    states[1][3] = 80.0
    assert layout.caption_top(2.5, 3.5, faces, win, states, covered=False, speed=1.0) == int(1000 * 1.12 + 80 + 28)


def test_caption_top_reads_faces_on_the_sped_clock():
    low = {"x": 0.4, "y": 1250 / 1920 - 0.2, "w": 0.2, "h": 0.2}
    high = {"x": 0.4, "y": 900 / 1920 - 0.2, "w": 0.2, "h": 0.2}
    faces = [{"t": i * 0.5, **(high if i < 8 else low)} for i in range(16)]  # chin drops at 4 s of 1x
    win = layout.window(1080, 1920, faces, "9x16")
    states = [[0.0, 1.0, 0.0, 0.0]]
    # at 2x the 4 s of 1x is 2 s, so a caption at 3 s of the cut sees the low chin
    assert layout.caption_top(2.8, 3.2, faces, win, states, False, speed=2.0) == 1233
    assert layout.caption_top(2.8, 3.2, faces, win, states, False, speed=1.0) == 1212


def test_face_clear_warns_in_plain_words():
    ok = track(REFERENCE_FACE)
    for r in layout.RATIOS:
        assert layout.face_clear(layout.window(1080, 1920, ok, r, reference_text(r)), ok, r) == []
    # a head that reaches the top of a 9:16 frame takes the panel layout and meets the panel (Ruling 23
    # tries the first step when only the head's gap misses), with nothing left to warn of
    tall = track({"x": 0.3, "y": 0.1, "w": 0.3, "h": 0.2})
    win = layout.window(1080, 1920, tall, "9x16", reference_text("9x16"))
    assert win.mode == "panel" and win.head_gap == 0 and layout.face_clear(win, tall, "9x16") == []
    # a head already cut off at the top of the take: nothing fits it, so the card shows it from the top
    # and says so
    cut_off = track({"x": 0.3, "y": 0.02, "w": 0.3, "h": 0.2})
    win = layout.window(1080, 1920, cut_off, "9x16", reference_text("9x16"))
    msgs = layout.face_clear(win, cut_off, "9x16")
    assert win.mode == "card" and len(msgs) == 1 and "top of the speaker's head" in msgs[0] and "9:16" in msgs[0]
    # a chin so low captions would touch it
    low = track({"x": 0.3, "y": 0.62, "w": 0.3, "h": 0.2})
    msgs = layout.face_clear(layout.window(1080, 1920, low, "9x16"), low, "9x16")
    assert len(msgs) == 1 and "caption" in msgs[0]


def test_a_window_is_plain_data():
    win = layout.window(1080, 1920, track(REFERENCE_FACE), "9x16")
    assert isinstance(win, Window) and win.ratio == "9x16"


def lean_left(n=60, step=0.5, w=0.12, h=0.2):
    """A 1080p landscape take's face map: the speaker in the middle, leaning left (face centre at a
    quarter of the width) for the middle third of the ad."""
    return [{"t": i * step, "x": (0.25 if n // 3 <= i < 2 * n // 3 else 0.5) - w / 2, "y": 0.3, "w": w, "h": h}
            for i in range(n)]


def outside(win, faces):
    H = layout.RATIOS[win.ratio]
    return [f for f in faces if f["x"] * win.src_w < win.x0 - 0.5 or (f["x"] + f["w"]) * win.src_w > win.x0 + 1080.5
            or f["y"] * win.src_h < win.y0 - 0.5 or (f["y"] + f["h"]) * win.src_h > win.y0 + H + 0.5]


PLAIN = [[0.0, 1.0, 0.0, 0.0]]


def test_a_lean_that_fits_the_4x5_window_keeps_every_face_in_it():
    faces = lean_left()
    win = layout.window(1920, 1080, faces, "4x5")
    assert outside(win, faces) == []
    assert layout.face_out(win, faces, PLAIN, 1.0) == []


def test_a_lean_too_wide_for_9x16_is_warned_with_when():
    faces = lean_left()
    win = layout.window(1920, 1080, faces, "9x16")
    assert len(outside(win, faces)) == 20  # 1.78x blown up, the lean can't fit: it's said, not hidden
    [msg] = layout.face_out(win, faces, PLAIN, 1.0)
    assert "9:16" in msg and "from 0:10 to 0:19" in msg
    [msg] = layout.face_out(win, faces, PLAIN, 2.0)  # on a 2x cut, the same samples are half as far in
    assert "from 0:05 to 0:09" in msg
    assert layout.face_out(win, faces, PLAIN, 1.0, skip=[(9.9, 19.6)]) == []  # B-roll covers the lean


def test_a_face_the_punch_in_pushes_out_is_warned():
    win = Window(scale=1.0, x0=0.0, y0=0.0, src_w=1920.0, src_h=1080.0, warn=None, ratio="1x1")
    faces = [{"t": 0.0, "x": 970 / 1920, "y": 0.3, "w": 90 / 1920, "h": 0.2}]
    assert layout.face_out(win, faces, PLAIN, 1.0) == []
    [msg] = layout.face_out(win, faces, [[0.0, 1.12, 0.0, 0.0]], 1.0)
    assert "1:1" in msg and "at 0:00" in msg


def test_a_portrait_take_keeps_the_head_top_in_when_the_chin_rule_allows():
    # The head rises for a stretch: its top sits above where the chin rule alone would start the window.
    faces = [{"t": i * 0.5, "x": 0.35, "y": (0.05 if i < 4 else 0.40), "w": 0.3, "h": 0.17} for i in range(40)]
    win = layout.window(1080, 1920, faces, "4x5")
    assert outside(win, faces) == []
    chin = max((f["y"] + f["h"]) * 1920 for f in faces)
    assert chin + layout.CHIN_GAP - win.y0 <= layout.TEXT["4x5"]["cap_max"]  # the captions still fit under it


def on_canvas(p, win):
    """A scaled-source y on the canvas, with no punch-in."""
    return p - win.y0 + win.panel


# A landscape take with the head high in it: the head's top (half a box above the box) at 8% of the frame.
HIGH_HEAD = {"x": 0.5 - 0.07, "y": 0.205, "w": 0.14, "h": 0.25}


@pytest.mark.parametrize("ratio", list(layout.RATIOS))
def test_a_high_head_takes_the_panel_layout(ratio):
    faces = track(HIGH_HEAD)
    win = layout.window(2560, 1440, faces, ratio, reference_text(ratio))
    H, band = layout.RATIOS[ratio], layout.TEXT[ratio]
    assert win.mode == "panel" and win.panel == round(layout.lowest(reference_text(ratio), win.shrink) + 48)
    # the take covers the area under the panel and no more: the full width, H - panel tall
    assert win.src_h == pytest.approx(H - win.panel) and win.src_w >= 1080 and win.y0 == 0
    head = on_canvas(layout.head_top(faces, win.src_h), win)
    chin = on_canvas((HIGH_HEAD["y"] + HIGH_HEAD["h"]) * win.src_h, win)
    assert head >= win.panel + win.head_gap
    # the chin rule is kept everywhere: in 9:16 by shrinking the text a little (Ruling 22's first step)
    assert chin + 28 <= band["cap_max"] + 0.5 and layout.face_clear(win, faces, ratio) == [] and win.punch
    cap = layout.caption_top(1.0, 2.0, faces, win, PLAIN, covered=False, speed=1.0)
    assert cap >= min(chin + 28, band["cap_max"]) - 1 and band["cap_min"] <= cap <= band["cap_max"]


def test_a_portrait_take_in_the_panel_layout_keeps_the_head_under_the_panel_and_the_chin_rule():
    faces = track({"x": 0.35, "y": 0.25, "w": 0.3, "h": 0.15})  # head top at 336 px, chin at 768 px
    win = layout.window(1080, 1920, faces, "9x16", reference_text("9x16"))
    assert win.mode == "panel" and win.scale == pytest.approx(1.0)  # 1080 wide already covers the area
    assert 0 < win.y0 <= 1920 - (1920 - win.panel)
    assert on_canvas(336, win) >= win.panel + 24
    assert layout.caption_top(1.0, 2.0, faces, win, PLAIN, covered=False, speed=1.0) == int(on_canvas(768, win) + 28)
    assert layout.face_clear(win, faces, "9x16") == []


def test_the_fade_layout_holds_while_the_text_clears_the_head_by_16_px():
    faces = track(REFERENCE_FACE)  # head top at 584.6 px in 9:16
    assert layout.window(1080, 1920, faces, "9x16", [(300, 584.6 - 16)]).mode == "fade"
    assert layout.window(1080, 1920, faces, "9x16", [(300, 584.6 - 15)]).mode == "panel"
    assert layout.window(1080, 1920, faces, "9x16").mode == "fade"  # no text, nothing to clear


@pytest.mark.parametrize("ratio,src,face", [(r, (2560, 1440), HIGH_HEAD) for r in layout.RATIOS]
                         + [("9x16", (1080, 1920), {"x": 0.35, "y": 0.25, "w": 0.3, "h": 0.15})])
def test_a_punch_in_in_the_panel_layout_never_leaves_a_gap(ratio, src, face):
    faces = track(face)
    win = layout.window(*src, faces, ratio, reference_text(ratio))
    assert win.mode == "panel"
    H = layout.RATIOS[ratio]
    segs = [{"out": [i * 2.0, i * 2.0 + 2.0]} for i in range(4)]
    for _, s, tx, ty in layout.states(segs, faces, win, 1.0)[1::2]:
        top = win.panel - win.y0 + ty
        assert top <= win.panel + 0.01  # the take's top edge stays under the panel
        assert top + win.src_h * s >= H - 0.01 and -win.x0 + tx <= 0.01
        assert -win.x0 + tx + win.src_w * s >= 1080 - 0.01


def test_a_face_below_the_take_s_area_is_warned():
    win = Window(scale=1.0, x0=0.0, y0=0.0, src_w=1080.0, src_h=1920.0, warn=None, ratio="9x16", mode="panel",
                 panel=500.0)
    fine = [{"t": 0.0, "x": 0.3, "y": 0.2, "w": 0.3, "h": 0.2}]
    low = [{"t": 0.0, "x": 0.3, "y": 0.6, "w": 0.3, "h": 0.18}]  # chin at 1498, on the canvas at 1998
    assert layout.face_out(win, fine, PLAIN, 1.0) == []
    [msg] = layout.face_out(win, low, PLAIN, 1.0)
    assert "9:16" in msg and "at 0:00" in msg


def test_the_lowest_line_of_shrunk_text_blocks():
    blocks = [(300, 560.0), (330, 450.0)]
    assert layout.lowest(blocks) == 560.0
    assert layout.lowest(blocks, 0.8) == pytest.approx(300 + 0.8 * 260)


def test_a_chin_the_panel_pushes_low_is_won_back_by_shrinking_the_text():
    # The chin at 46% of a landscape take: at full size the 9:16 panel puts it 9 px too low for the captions.
    faces = track({"x": 0.43, "y": 0.26, "w": 0.14, "h": 0.2})
    full = layout._place(2560, 1440, faces, "9x16", round(lowest("9x16") + 48), layout.HEAD_GAP)
    assert full.panel + 0.46 * full.src_h + 28 > 1233 + 0.5
    win = layout.window(2560, 1440, faces, "9x16", reference_text("9x16"))
    assert win.mode == "panel" and 0.8 <= win.shrink < 1 and win.head_gap == 0 and win.punch
    assert win.panel == round(layout.lowest(reference_text("9x16"), win.shrink) + 48)
    assert on_canvas(0.46 * win.src_h, win) + 28 <= 1233 + 0.5
    assert layout.face_clear(win, faces, "9x16") == []
    # the least shrink that does it: one step less and the chin is too low again
    worse = layout._place(2560, 1440, faces, "9x16", round(layout.lowest(reference_text("9x16"), win.shrink + 0.01) + 48), 0)
    assert worse.panel + 0.46 * worse.src_h + 28 > 1233 + 0.5
    segs = [{"out": [0, 2]}, {"out": [2, 4]}]
    assert layout.states(segs, faces, win, 1.0)[1][1] == layout.PUNCH


# The real landscape take's face: the head's top at 6.6% of the frame and the chin at 51.9%.
REAL_TAKE = {"x": 0.445 - 0.078, "y": 0.217, "w": 0.156, "h": 0.302}
# The real vertical take's face (its median box): a close-up, half the frame wide, centred at 54% across,
# the chin at 51.4%.
VERTICAL_TAKE = {"x": 0.286, "y": 0.224, "w": 0.516, "h": 0.29}
SEGS = [{"out": [i * 2.0, i * 2.0 + 2.0]} for i in range(6)]


def lip_on_canvas(box, win):
    return win.box[1] + (box["y"] + 0.835 * box["h"]) * win.src_h - win.y0


def assert_punches_keep_the_limits(win, faces, box, text_times=None):
    """Every punched segment covers the take's frame, keeps the head at or under the window's limit
    and keeps the captions under the chin (no worse than the plain window)."""
    fx, fy, fw, fh = win.box
    cap = layout.TEXT[win.ratio]["cap_max"]
    head0, chin0 = (box["y"] - box["h"] / 2) * win.src_h, (box["y"] + box["h"]) * win.src_h
    for _, s, tx, ty in layout.states(SEGS, faces, win, 1.0, text_times):
        if s == 1.0:
            continue
        assert -win.x0 + tx <= 0.06 and -win.x0 + tx + win.src_w * s >= fw - 0.06  # no edge inside the frame
        assert -win.y0 + ty <= 0.06 and -win.y0 + ty + win.src_h * s >= fh - 0.06
        head, chin = fy + s * head0 + ty - win.y0, fy + s * chin0 + ty - win.y0
        if text_times is None or win.mode != "fade":
            assert head >= min(win.head_limit, fy + head0 - win.y0) - 0.06
        assert chin + 28 <= max(cap, fy + chin0 - win.y0 + 28) + 0.06


def test_the_reference_ad_s_punch_ins_keep_the_head_under_its_text():
    faces = track(REFERENCE_FACE)
    for r in layout.RATIOS:
        win = layout.window(1080, 1920, faces, r, reference_text(r))
        assert win.mode == "fade" and win.head_limit == pytest.approx(lowest(r) + 16)
        assert_punches_keep_the_limits(win, faces, REFERENCE_FACE)
    # 9:16: with text on screen, a 1.12 punch would lift the head 45 px into the lockup's 16 px: it stays plain
    win = layout.window(1080, 1920, faces, "9x16", reference_text("9x16"))
    assert all(st[1] == 1.0 for st in layout.states(SEGS, faces, win, 1.0, [(0.0, 12.0)]))
    # with no text on screen there is nothing to keep clear of, and the punch-ins are the first ad's
    free = layout.states(SEGS, faces, win, 1.0, [])
    assert [st[1] for st in free] == [1.0, 1.12] * 3
    assert free[1][2:] == layout.states(SEGS, faces, layout.window(1080, 1920, faces, "9x16"), 1.0)[1][2:]


def test_a_chin_low_in_a_landscape_take_gets_a_card_with_the_captions_under_the_lip():
    box = {"x": 0.5 - 0.09, "y": 0.26, "w": 0.18, "h": 0.30}  # the reviewer's case: the chin at 56%
    faces = track(box)
    text = [reference_text("9x16")[0]]
    win = layout.window(2560, 1440, faces, "9x16", text)
    fx, fy, fw, fh = win.box
    assert win.mode == "card" and win.punch and 756 <= fw <= 1080 - 72 and fx == pytest.approx((1080 - fw) / 2)
    assert fy == win.panel and fy + fh <= 1920 - 36 + 0.01
    caption = layout.caption_top(1.0, 2.0, faces, win, PLAIN, covered=False, speed=1.0)
    assert lip_on_canvas(box, win) < caption
    assert on_frame((box["y"] + box["h"]) * win.src_h, win) + 28 <= 1233 + 0.5
    assert on_frame((box["y"] - box["h"] / 2) * win.src_h, win) >= fy + 24 - 0.5  # the head inside the card
    assert layout.face_clear(win, faces, "9x16") == []
    assert_punches_keep_the_limits(win, faces, box)


def on_frame(p, win):
    return win.box[1] + p - win.y0


def test_the_real_landscape_take_gets_a_card_in_9x16():
    faces = track(REAL_TAKE)
    win = layout.window(2560, 1440, faces, "9x16", reference_text("9x16"))
    assert win.mode == "card" and win.punch and layout.face_clear(win, faces, "9x16") == []
    caption = layout.caption_top(1.0, 2.0, faces, win, PLAIN, covered=False, speed=1.0)
    assert lip_on_canvas(REAL_TAKE, win) < caption
    for ratio in ("4x5", "1x1"):  # these keep the panel, its full-size text and their punch-ins
        other = layout.window(2560, 1440, faces, ratio, reference_text(ratio))
        assert other.mode == "panel" and other.shrink == 1.0 and other.punch and layout.face_clear(other, faces, ratio) == []


def test_a_vertical_close_up_in_1x1_gets_a_card_with_the_whole_head_under_the_panel():
    faces = track(VERTICAL_TAKE)
    win = layout.window(1080, 1920, faces, "1x1", reference_text("1x1"))
    fx, fy, fw, fh = win.box
    assert win.mode == "card" and fw >= 756 and fy == win.panel
    assert on_frame((VERTICAL_TAKE["y"] - VERTICAL_TAKE["h"] / 2) * win.src_h, win) >= win.panel + 24 - 0.5
    assert layout.face_clear(win, faces, "1x1") == []
    assert_punches_keep_the_limits(win, faces, VERTICAL_TAKE)
    # the card reaches below the lowest caption, so no caption hangs over its edge
    band = layout.TEXT["1x1"]
    assert fy + fh >= band["cap_max"] + band["cap"] * (1.18 + 0.43) - 0.01 and fy + fh <= 1080


@pytest.mark.parametrize("ratio", list(layout.RATIOS))
@pytest.mark.parametrize("src,box", [((2560, 1440), HIGH_HEAD), ((2560, 1440), REAL_TAKE), ((1080, 1920), VERTICAL_TAKE),
                                     ((1080, 1920), {"x": 0.35, "y": 0.30, "w": 0.3, "h": 0.2})])
def test_a_punch_in_every_layout_keeps_the_head_and_chin_limits_and_its_frame_covered(ratio, src, box):
    faces = track(box)
    win = layout.window(*src, faces, ratio, reference_text(ratio))
    assert_punches_keep_the_limits(win, faces, box)


def test_a_close_up_too_tight_even_for_a_card_turns_the_punch_ins_off():
    # A vertical take with the face filling it: even the narrowest card (756 px) can't fit the head
    # and keep the captions under the chin.
    box = {"x": 0.1, "y": 0.28, "w": 0.8, "h": 0.5}
    faces = track(box)
    win = layout.window(1080, 1920, faces, "9x16", reference_text("9x16"))
    assert win.mode == "card" and not win.punch and win.box[2] == pytest.approx(756)
    assert all(st[1] == 1.0 for st in layout.states(SEGS, faces, win, 1.0))
    caption = layout.caption_top(1.0, 2.0, faces, win, PLAIN, covered=False, speed=1.0)
    # the face is so big the lip sits under the captions' top: the warning says the mouth is covered
    [msg] = layout.face_clear(win, faces, "9x16")
    assert lip_on_canvas(box, win) >= caption
    assert "9:16" in msg and "cover part of the speaker's mouth" in msg and "never cover" not in msg
    assert layout.mouth_covered(win, faces)  # the check fails this one (Ruling 28)
    # a face just too tall for the narrowest card: the captions sit on the chin, the lip clear above them
    near = {"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.31}
    faces = track(near)
    win = layout.window(1080, 1920, faces, "9x16", reference_text("9x16"))
    caption = layout.caption_top(1.0, 2.0, faces, win, PLAIN, covered=False, speed=1.0)
    assert win.mode == "card" and not win.punch and lip_on_canvas(near, win) < caption
    [msg] = layout.face_clear(win, faces, "9x16")
    assert "never cover the mouth" in msg and "low on the face" in msg
    assert not layout.mouth_covered(win, faces)
    # a take that fits never counts as covering the mouth
    assert not layout.mouth_covered(layout.window(1080, 1920, track(REFERENCE_FACE), "9x16", reference_text("9x16")),
                                    track(REFERENCE_FACE))



def caption_foot(ratio, top):
    return top + layout.TEXT[ratio]["cap"] * (1.18 + 0.43)


def test_a_low_face_leaves_the_fade_layout_rather_than_put_captions_over_the_lip():
    # A vertical take with the face low: the head's top at 31% (clear of the text), the chin at 70%.
    box = {"x": 0.35, "y": 0.44, "w": 0.3, "h": 0.26}
    faces = track(box)
    fade = layout._place(1080, 1920, faces, "9x16", 0, layout.HEAD_GAP)
    assert layout.head_top(faces, 1920) - fade.y0 >= lowest("9x16") + 16  # the text alone would allow fade
    caption = layout.caption_top(1.0, 2.0, faces, fade, PLAIN, covered=False, speed=1.0)
    assert lip_on_canvas(box, fade) >= caption  # and fade would put the captions over the lip
    win = layout.window(1080, 1920, faces, "9x16", reference_text("9x16"))
    assert win.mode in ("panel", "card") and win.punch
    fy = win.box[1]
    lip0 = (box["y"] + 0.835 * box["h"]) * win.src_h
    for st in (PLAIN, layout.states(SEGS, faces, win, 1.0)):
        for a in (0.5, 2.5, 4.5):
            _, s, _, ty = layout._state_at(a + 0.5, st)
            lip = fy + s * lip0 + ty - win.y0  # the lip where this segment's punch-in puts it
            assert lip < layout.caption_top(a, a + 1, faces, win, st, covered=False, speed=1.0)
    assert layout.face_clear(win, faces, "9x16") == []
    # the reference ad's lip sits well above its captions, so it keeps the fade layout
    ref = layout.window(1080, 1920, track(REFERENCE_FACE), "9x16", reference_text("9x16"))
    assert ref.mode == "fade" and lip_on_canvas(REFERENCE_FACE, ref) < 1212


@pytest.mark.parametrize("ratio", list(layout.RATIOS))
@pytest.mark.parametrize("src", [(2560, 1440), (1080, 1350), (1080, 1080)])
def test_a_card_always_reaches_below_the_lowest_caption(ratio, src):
    # The chin at 90% of the frame: lifting the take toward the head used to leave the card short.
    box = {"x": 0.5 - 0.12, "y": 0.42, "w": 0.24, "h": 0.48}
    faces = track(box)
    win = layout.window(*src, faces, ratio, reference_text(ratio))
    if win.mode != "card":
        return
    fx, fy, fw, fh = win.box
    for st in (PLAIN, layout.states(SEGS, faces, win, 1.0)):
        for a in (0.5, 2.5, 4.5, 6.5):
            top = layout.caption_top(a, a + 1, faces, win, st, covered=False, speed=1.0)
            assert caption_foot(ratio, top) <= fy + fh + 0.01, (src, ratio, top, win.box)
    assert fy + fh <= layout.RATIOS[ratio] + 0.01


def test_the_card_fit_counts_the_caption_s_reach():
    # 4:5, the head's top at 18% and the chin at 90% (the re-reviewer's case): the card's bottom reaches
    # the lowest caption's foot, or the take doesn't count as fitting a card.
    box = {"x": 0.5 - 0.12, "y": 0.42, "w": 0.24, "h": 0.48}
    faces = track(box)
    for src in ((2560, 1440), (1080, 1350)):
        card, fits = layout._card(*src, faces, "4x5", round(lowest("4x5") + 48), 1.0)
        assert not fits or card.box[1] + card.box[3] >= caption_foot("4x5", 1238) - 0.01
