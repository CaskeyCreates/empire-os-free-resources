"""Per ratio: the window onto the source, the punch-ins and where the captions sit.

Ported from the numbers the first hand-built ad used, generalised to any source size. All the
maths is in scaled-source pixels: the source is scaled by `scale` and shown through the take's
frame, a rectangle on the canvas (Window.box), so a source point p shows at frame top + p - y0 (and
frame left + p - x0 across).

Fit, don't force. In order:
- fade: the first ad's layout. The take fills the canvas and each heading fades paper in over the
  wall above the head. It holds while the lowest text line of the lockup and of every heading sits
  at least TEXT_GAP above the head.
- panel: a solid paper panel from the top to the lowest text line plus PANEL_PAD, and the take scaled
  to cover the area below it, with no paper fade over it. Where the head or the chin doesn't fit,
  the head may meet the panel and the panel's text shrinks, to SHRINK_MIN at the least.
- card: the panel, and the take scaled below cover into a framed card on the paper under it, as large
  as keeps the head inside the card and the captions under the chin, never narrower than CARD_MIN.
- only then the card with no punch-ins and a warning.
Every punch-in keeps the head within its limit and the captions under the chin, or stays plain."""
from dataclasses import dataclass, replace

import numpy as np

from adeditor import verify
from adeditor.faces import Face, NoFace

WIDTH = 1080
RATIOS = {"9x16": 1920, "4x5": 1350, "1x1": 1080}
PUNCH = 1.12
CHIN_GAP = 28  # px between the chin and the top of a caption
NEAR = 0.3  # s either side of a segment whose face samples count
BLOWN_UP = 1.5
GAP_JOIN = 1.0  # 1x seconds between two out-of-frame samples that still count as one stretch
TEXT_GAP = 16  # px the lowest text line keeps above the head for the fade layout
PANEL_PAD = 48  # px the panel runs below the lowest text line
HEAD_GAP = 24  # px the head's top keeps below the panel
CARD_MIN = 756  # px: a card is never narrower than 0.7 of the canvas
CARD_MARGIN = 36  # px of paper either side of a card, and under it where the captions leave room
CARD_HEAD = 24  # px the head's top keeps inside a card's top edge
CARD_RADIUS = 28
LIP = 0.835  # the lower lip, as a fraction down the face box
CAPTION_BOX = 1.18 + 0.43  # a caption's height as a multiple of its font size (line height and padding)
BLEND = 40  # px of soft blend from the panel into the take's top edge
SHRINK_MIN = 0.8  # the panel's text may shrink this far to win the captions room under the chin
SHRINK_STEP = 0.01

# `cap_max` for 9x16 is 1233, not the first ad's 1290: nothing should sit below about 1330 and a
# full-size caption is 60 x (1.18 + 0.43) = 97 px tall. Its caption position is unchanged.
TEXT = {
    "9x16": dict(sc=1.0, cap=60, cap_min=1212, cap_max=1233, lock=300, heading=330, paper_h=720, end_top=470),
    "4x5": dict(sc=0.88, cap=56, cap_min=930, cap_max=1238, lock=58, heading=92, paper_h=430, end_top="centred"),
    "1x1": dict(sc=0.66, cap=48, cap_min=880, cap_max=985, lock=30, heading=62, paper_h=330, end_top="centred"),
}

# A text block's rows as the page sets them, in px at sc 1: font size, line height, space above. The
# rule between the callout and the offer is 2 px tall with ROW_RULE px (scaled) around it.
ROWS = {"eyebrow": (38, 1.25, 0), "h": (58, 1.12, 0), "caps": (24, 1.25, 20)}
ROW_RULE = 20 + 22
METRICS = (0.95, 0.25)  # a font's ascent and descent as fractions of its size, when its file can't be read


def text_bottom(top: float, rows: list[tuple[str, float]], sc: float, metrics: dict | None = None) -> float:
    """The baseline of a text block's lowest line, on the canvas: where the letters of its last row sit.
    `rows` are the block's rows from the top, each (kind, fit): kind is "eyebrow", "rule", "h" or "caps",
    and fit is how far that row's font was shrunk to keep it on one line. `metrics` maps a kind to its
    font's (ascent, descent) as fractions of the size."""
    y, base = float(top), float(top)
    for kind, fit in rows:
        if kind == "rule":
            y += 2 + ROW_RULE * sc
            continue
        size, line, above = ROWS[kind]
        px = size * sc * fit
        a, d = (metrics or {}).get(kind) or METRICS
        y += above * sc
        base = y + (line * px - (a + d) * px) / 2 + a * px  # half the leading above, then the ascent
        y += line * px
    return base


@dataclass(frozen=True)
class Window:
    scale: float
    x0: float
    y0: float
    src_w: float  # the source's size after scaling
    src_h: float
    warn: str | None
    ratio: str
    mode: str = "fade"  # "fade", "panel" or "card"
    panel: float = 0.0  # the paper panel's height, where the text band ends; 0 in the fade layout
    head_gap: float = 24.0  # px the head's top keeps below the panel (0 once the text had to shrink)
    shrink: float = 1.0  # how far the panel's text blocks are scaled down
    punch: bool = True  # False when the take can't fit and no punch-in is safe
    frame: tuple[float, float, float, float] | None = None  # the take's (left, top, width, height) on the canvas
    head_min: float | None = None  # the highest the head's top may show on the canvas (None: no limit)

    @property
    def box(self) -> tuple[float, float, float, float]:
        """The take's frame on the canvas: the card, else the area under the panel (the whole canvas in
        the fade layout)."""
        if self.frame:
            return self.frame
        return 0.0, self.panel, float(WIDTH), RATIOS[self.ratio] - self.panel

    @property
    def head_limit(self) -> float:
        if self.head_min is not None:
            return self.head_min
        return self.panel + self.head_gap if self.mode == "panel" else float("-inf")


def lowest(blocks: list[tuple[float, float]], shrink: float = 1.0) -> float:
    """The lowest text line of the text blocks, each (top, lowest line) on the canvas, with every block
    scaled by `shrink` from its top."""
    return max(top + shrink * (bottom - top) for top, bottom in blocks)


def head_top(faces: list[Face], src_h: float) -> float:
    """The top of the head in scaled-source pixels. A face box starts at the forehead, so the head's top
    is taken as half a box height above it, at the 5th percentile over the take."""
    return float(np.percentile([(f["y"] - f["h"] / 2) * src_h for f in faces], 5))


def _chin(faces: list[Face], src_h: float) -> float:
    """The chin in scaled-source pixels: the 95th percentile of the face box bottoms."""
    return float(np.percentile([(f["y"] + f["h"]) * src_h for f in faces], 95))


def _lip(faces: list[Face], src_h: float) -> float:
    """The lower lip in scaled-source pixels, LIP down the face box, at its lowest over the take."""
    return float(np.percentile([(f["y"] + LIP * f["h"]) * src_h for f in faces], 95))


def _found(faces: list[Face]) -> list[Face]:
    got = [f for f in faces if f["x"] is not None]
    if not got:
        raise NoFace(0.0)
    return got


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def _label(ratio: str) -> str:
    return ratio.replace("x", ":")


def window(src_w: int, src_h: int, faces: list[Face], ratio: str,
           text: list[tuple[float, float]] | None = None) -> Window:
    """The window onto the scaled source, in the first layout that fits (see the module's note). `text`
    is the lockup's and the headings' blocks on the canvas, each (top, lowest line, from text_bottom()).

    Across, it centres on the face's median position, moved as little as it takes to hold the face's
    whole spread (its 5th to 95th percentile edges) where that fits, so a speaker who leans away for a
    stretch stays in. Down, the chin rule sets it (the chin just above the captions' floor), raised
    toward the head where the head would otherwise leave the take's frame, as far as the captions can
    still sit under the chin."""
    got = _found(faces)
    fade = _place(src_w, src_h, got, ratio, 0, HEAD_GAP)
    if not text:
        return fade
    if head_top(got, fade.src_h) - fade.y0 >= lowest(text) + TEXT_GAP and _lip_clear(fade, got):
        return replace(fade, head_min=lowest(text) + TEXT_GAP)
    win = _place(src_w, src_h, got, ratio, round(lowest(text) + PANEL_PAD), HEAD_GAP)
    if _fits(win, got):
        return win
    shrinks = [round(1 - i * SHRINK_STEP, 2) for i in range(round((1 - SHRINK_MIN) / SHRINK_STEP) + 1)]
    for k in shrinks:
        win = _place(src_w, src_h, got, ratio, round(lowest(text, k) + PANEL_PAD), 0, k)
        if _fits(win, got):
            return win
    for k in shrinks:
        card, fits = _card(src_w, src_h, got, ratio, round(lowest(text, k) + PANEL_PAD), k)
        if fits:
            return card
    return replace(card, punch=False)


def _lip_clear(win: Window, got: list[Face]) -> bool:
    """The lower lip above the captions' top, with no punch-in."""
    band, fy = TEXT[win.ratio], win.box[1]
    chin = fy + _chin(got, win.src_h) - win.y0
    return fy + _lip(got, win.src_h) - win.y0 < min(band["cap_max"], max(band["cap_min"], chin + CHIN_GAP))


def _fits(win: Window, got: list[Face]) -> bool:
    """The head's top below its limit and the captions under the chin, with no punch-in."""
    fy = win.box[1]
    head = fy + head_top(got, win.src_h) - win.y0
    chin = fy + _chin(got, win.src_h) - win.y0
    return head >= win.head_limit - 0.5 and chin + CHIN_GAP <= TEXT[win.ratio]["cap_max"] + 0.5


def _across(got: list[Face], sw: float, width: float) -> float:
    """x0 for a frame `width` wide onto a source `sw` wide: centred on the face, holding its spread."""
    cx = float(np.median([(f["x"] + f["w"] / 2) * sw for f in got]))
    x0 = cx - width / 2
    left = float(np.percentile([f["x"] * sw for f in got], 5))
    right = float(np.percentile([(f["x"] + f["w"]) * sw for f in got], 95))
    if right - left <= width:
        x0 = _clamp(x0, right - width, left)
    return _clamp(x0, 0, sw - width)


def _blown_up(scale: float, ratio: str) -> str | None:
    if scale > BLOWN_UP:
        return (f"the {_label(ratio)} crop is blown up {scale:.1f}x and will look soft; "
                "ask the client for a vertical or 4K take")
    return None


def _card(src_w: int, src_h: int, got: list[Face], ratio: str, panel: int, shrink: float) -> tuple[Window, bool]:
    """The card layout under a panel `panel` px tall: the take scaled down to the largest scale that keeps
    the head CARD_HEAD inside the card's top edge and the chin CHIN_GAP above cap_max, into a card
    centred across the canvas, its top at the panel's foot, CARD_MARGIN of paper either side and
    under it (less under it where the lowest caption needs the room). The card reaches below the
    lowest caption, so no caption leaves it. A card never goes narrower than CARD_MIN. The second
    value is False when the card can't keep all of that."""
    H, band = RATIOS[ratio], TEXT[ratio]
    head0, chin0 = head_top(got, src_h), _chin(got, src_h)
    room = band["cap_max"] - CHIN_GAP - (panel + CARD_HEAD)
    cover = max(WIDTH / src_w, (H - panel) / src_h)
    scale = min(cover, room / max(chin0 - head0, 1e-6))
    floor = CARD_MIN / src_w
    fits = scale >= floor - 1e-9
    scale = max(scale, floor)
    sw, sh = src_w * scale, src_h * scale
    cw = min(sw, WIDTH - 2 * CARD_MARGIN)
    x0 = _across(got, sw, cw)
    chin = panel + chin0 * scale
    limit = head0 * scale - CARD_HEAD
    caption_foot = band["cap_max"] + band["cap"] * CAPTION_BOX  # the lowest a caption reaches
    reach = panel + sh - caption_foot  # the take must run on below it, so y0 stays at or under this
    lowest_y0 = chin + CHIN_GAP - band["cap_max"]
    y0 = max(lowest_y0, min(chin + CHIN_GAP - band["cap_min"], limit))
    y0 = _clamp(y0, 0, max(0.0, min(limit, reach)))  # the head inside and the caption over the take come first
    fits = fits and lowest_y0 <= y0 + 0.5 and y0 <= reach + 0.5
    # paper under the card, but never so much that the lowest caption hangs over the card's edge
    ch = min(sh - y0, H - min(CARD_MARGIN, H - caption_foot) - panel)
    frame = (round((WIDTH - cw) / 2, 3), float(panel), round(cw, 3), round(ch, 3))
    plain_head = panel + head0 * scale - y0
    return Window(round(scale, 6), round(x0, 3), round(y0, 3), sw, sh, _blown_up(scale, ratio), ratio, "card",
                  float(panel), 0.0, shrink, True, frame, min(panel + CARD_HEAD, plain_head)), fits


def _place(src_w: int, src_h: int, got: list[Face], ratio: str, panel: int, head_gap: float,
           shrink: float = 1.0) -> Window:
    """The window for a take whose area runs from `panel` to the canvas's foot (the whole canvas when
    panel is 0), the source scaled to cover that area. In the fade layout the head's limit is the face
    box's top, as the first ad had it; in the panel layout it's the head's top, head_gap below the panel."""
    H = RATIOS[ratio]
    area = H - panel
    scale = WIDTH / min(src_w, src_h * WIDTH / area)  # max(1080 / src_w, area / src_h)
    sw, sh = src_w * scale, src_h * scale
    x0 = _across(got, sw, WIDTH)
    band = TEXT[ratio]
    chin = panel + _chin(got, sh)  # on the canvas, before the window moves
    limit = (float(np.percentile([f["y"] * sh for f in got], 5)) if not panel
             else head_top(got, sh) - head_gap)
    y0 = max(chin + CHIN_GAP - band["cap_max"], min(chin + CHIN_GAP - band["cap_min"], limit))
    y0 = _clamp(y0, 0, sh - area)
    return Window(round(scale, 6), round(x0, 3), round(y0, 3), sw, sh, _blown_up(scale, ratio), ratio,
                  "panel" if panel else "fade", float(panel), float(head_gap), shrink)


def _face_at(a: float, b: float, faces: list[Face], win: Window, speed: float):
    """Centre, chin and head's top of the face over a stretch of the cut (times on the cut's clock), in
    scaled-source pixels: the mean centre, the lowest chin and the highest head. Face times are 1x, so
    they are divided by the speed."""
    rows = [(f["t"] / speed, (f["x"] + f["w"] / 2) * win.src_w, (f["y"] + f["h"] / 2) * win.src_h,
             (f["y"] + f["h"]) * win.src_h, (f["y"] - f["h"] / 2) * win.src_h) for f in _found(faces)]
    near = [r for r in rows if a - NEAR <= r[0] <= b + NEAR] or [min(rows, key=lambda r: abs(r[0] - (a + b) / 2))]
    return (sum(r[1] for r in near) / len(near), sum(r[2] for r in near) / len(near), max(r[3] for r in near),
            min(r[4] for r in near))


def states(segments: list[dict], faces: list[Face], win: Window, speed: float,
           text_times: list[tuple[float, float]] | None = None) -> list[list[float]]:
    """[t, scale, tx, ty] per cut segment, t its start on the cut's clock. Even segments are plain
    (1.0). Odd ones punch in PUNCH about the face. The page shows the scaled source through the take's
    frame at left -x0, top -y0, then applies translate(tx, ty) scale(s) from its top-left, so a point p
    lands at frame + s * p + (tx, ty) - (x0, y0).

    A punch-in always covers the frame (no edge shows inside it), keeps the head's top at or below the
    window's head limit and the captions under the chin, never worse than the plain window for that
    segment; it moves off the face's centre as little as that takes, and a segment where it can't
    keep all of them stays plain. In the fade layout the head's limit is the text's, so it counts only
    while text shows (`text_times` on the cut's clock; always, when not given). A window with its
    punch-ins off keeps every segment plain."""
    fx, fy, fw, fh = win.box
    cap = TEXT[win.ratio]["cap_max"] - CHIN_GAP
    out = []
    for k, seg in enumerate(segments):
        a, b = seg["out"]
        if k % 2 == 0 or not win.punch:
            out.append([a, 1.0, 0.0, 0.0])
            continue
        cx, cy, chin, head = _face_at(a, b, faces, win, speed)
        s = PUNCH
        tx = min(win.x0, max(fw + win.x0 - win.src_w * s, cx - cx * s))
        limit = win.head_limit
        if win.mode == "fade" and text_times is not None and not any(ta < b and a < tb for ta, tb in text_times):
            limit = float("-inf")
        # on the canvas p shows at fy + s * p + ty - y0; the take covers the frame while
        # fh + y0 - sh * s <= ty <= y0
        lowest_ty = max(fh + win.y0 - win.src_h * s, min(limit, fy + head - win.y0) - fy - s * head + win.y0)
        highest_ty = min(win.y0, max(cap, fy + chin - win.y0) - fy - s * chin + win.y0)
        if lowest_ty > highest_ty + 1e-6:
            out.append([a, 1.0, 0.0, 0.0])
            continue
        ty = _clamp(cy - cy * s, lowest_ty, highest_ty)
        out.append([a, s, round(tx, 1), round(ty, 1)])
    return out


def _state_at(t: float, states: list[list[float]]) -> list[float]:
    return next((s for s in reversed(states) if s[0] - 1e-6 <= t), states[0])


def caption_top(a: float, b: float, faces: list[Face], win: Window, states: list[list[float]],
                covered: bool, *, speed: float) -> int:
    """Top of the caption for the words from a to b on the cut's clock. `speed` (required) puts the 1x
    face times on that clock. Over B-roll the caption sits at its floor; otherwise just under the
    chin as the punch-in has moved it, inside the ratio's band."""
    band = TEXT[win.ratio]
    if covered:
        return band["cap_min"]
    _, s, _, ty = _state_at((a + b) / 2, states)
    _, _, chin, _ = _face_at(a, b, faces, win, speed)
    chin_out = win.box[1] + chin * s + ty - win.y0
    return int(min(band["cap_max"], max(band["cap_min"], chin_out + CHIN_GAP)))


def _span(t0: float, t1: float) -> str:
    a, b = verify.clock(t0), verify.clock(t1)
    return f"at {a}" if a == b else f"from {a} to {b}"


def face_out(win: Window, faces: list[Face], states: list[list[float]], speed: float, skip=()) -> list[str]:
    """A plain warning naming when (on the cut's clock) any face box leaves the frame, with each
    stretch's punch-in counted. Face times are 1x, so they're divided by the speed. A moment inside
    a `skip` span (B-roll, the end card) is covered, so it doesn't count."""
    _, _, fw, fh = win.box
    out_at = []
    for f in _found(faces):
        t = f["t"] / speed
        if any(a <= t <= b for a, b in skip):
            continue
        _, s, tx, ty = _state_at(t, states)
        # a scaled-source point p shows at frame + s * p + (tx, ty) - (x0, y0), inside the frame
        lo_x, hi_x = (win.x0 - tx) / s, (win.x0 - tx + fw) / s
        lo_y, hi_y = (win.y0 - ty) / s, (win.y0 - ty + fh) / s
        x0, x1 = f["x"] * win.src_w, (f["x"] + f["w"]) * win.src_w
        y0, y1 = f["y"] * win.src_h, (f["y"] + f["h"]) * win.src_h
        if x0 < lo_x - 0.5 or x1 > hi_x + 0.5 or y0 < lo_y - 0.5 or y1 > hi_y + 0.5:
            out_at.append(t)
    if not out_at:
        return []
    spans = [[out_at[0], out_at[0]]]
    for t in out_at[1:]:
        if t - spans[-1][1] <= GAP_JOIN / speed:
            spans[-1][1] = t
        else:
            spans.append([t, t])
    when = ", ".join(_span(a, b) for a, b in spans)
    return [f"In {_label(win.ratio)} the speaker's face goes out of the frame {when}. Look at those moments, "
            "and ask for a take where the speaker stays nearer the middle if it shows."]


def mouth_covered(win: Window, faces: list[Face]) -> bool:
    """True in the last-resort layout (punch-ins off) when the captions sit over the lower lip: the check
    fails it, since no layout could clear the mouth."""
    if win.punch:
        return False
    got, band, fy = _found(faces), TEXT[win.ratio], win.box[1]
    chin = fy + _chin(got, win.src_h) - win.y0
    return fy + _lip(got, win.src_h) - win.y0 >= min(band["cap_max"], max(band["cap_min"], chin + CHIN_GAP))


def face_clear(win: Window, faces: list[Face], ratio: str) -> list[str]:
    """Plain warnings when the layout can't keep the text and the captions off the speaker. The fade
    layout is only chosen with the head clear of the text. A warning says the mouth is clear only when
    the lower lip sits above the captions' top."""
    band, got, out = TEXT[ratio], _found(faces), []
    label = _label(ratio)
    fy = win.box[1]
    head = fy + head_top(got, win.src_h) - win.y0
    if win.mode == "panel" and head < win.panel + win.head_gap - 0.5:
        out.append(f"In {label} the speaker's head reaches up to {head:.0f} px from the top, right under "
                   "the band the text sits in. Ask for a take with more room above the head.")
    if win.mode == "card" and head < fy + CARD_HEAD - 0.5:
        out.append(f"In {label} the top of the speaker's head meets the top of the picture. Ask for a take with "
                   "more room above the head.")
    chin = fy + _chin(got, win.src_h) - win.y0
    lip = fy + _lip(got, win.src_h) - win.y0
    caption = min(band["cap_max"], max(band["cap_min"], chin + CHIN_GAP))
    if not win.punch:
        if lip < caption:
            out.append(f"In {label} the take is a close-up, so the captions sit low on the face, over the chin. "
                       "We turned the punch-ins off there so they never cover the mouth. A take with the camera "
                       "a little further back fixes it.")
        else:
            out.append(f"In {label} the take is a close-up, so the captions cover part of the speaker's mouth. "
                       "Ask for a take with the camera a little further back.")
    elif chin + CHIN_GAP > band["cap_max"] + 0.5:
        out.append(f"In {label} the speaker's chin is low enough that captions would touch it. "
                   "Ask for a take with the camera pointed a little lower.")
    return out
