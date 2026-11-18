"""The six motion graphics, drawn on the page in the client's own colours and fonts.

Each graphic is one full-canvas clip on the kit's `deep` or `paper` ground, laid out inside the safe
area; it covers the captions while it shows. The motion is ported from
a reference ad's "pattern" circle: the ring draws on over 0.9 s (power2.inOut), labels land with opacity,
a 10 px rise and a 6 px blur over 0.35 s (expo.out), a dot travels from node to node and comes back to the
first, and the item being said takes the accent colour. Every other kind speaks the same vocabulary:
blur in on the speaker's word, accent on the active item, earlier items ticked or dimmed.

build() returns {"html", "js", "events", "full_at", "boxes"}: the clip's markup, its tweens on the page's
`tl`, its sound moments, when every item has landed (for the preview still) and the boxes it laid out
(for the safe-area check). Text is measured with the kit's own fonts and shrinks to fit its slot down to
0.75 of its size; past that it raises FitError, which plan check reports before anything renders.

Scale (ruling G): the block spans at least 75% of the canvas width in 9:16 and 70% in 4:5 and 1:1, item text
is 64 px, a title 46 px, a quote line 84 px and up, a stat value 220 px and up (all times the ratio's `sc`),
shrinking only to fit. The block sits at its optical centre. The ground is alive: two drifting accent glows
(deep) or washes (paper) under film grain, all driven by the timeline so a seek lands on the right frame."""
import html
import json
import math
import re

from adeditor import brand, layout, page, verify

SIDE = 60  # px of margin either side
TOP = {"9x16": 290, "4x5": 60, "1x1": 60}  # 9x16: 20 px under the reels' 270 px unsafe band
SAFE_TOP = {"9x16": 270, "4x5": 40, "1x1": 40}  # the optical centre is measured from here to the safe bottom
SAFE_BOTTOM = {"9x16": 1330, "4x5": 1310, "1x1": 1040}  # nothing below about 1330 in reels; 40 px from the foot elsewhere
# (a graphic covers the captions, so the caption band no longer bounds it)
SPAN = {"9x16": 0.75, "4x5": 0.70, "1x1": 0.70}  # the block spans this much of the canvas width
OPTICAL = 0.45  # the block's centre, as a share of the way down from the safe top to the safe bottom
GROW_CAP = 110 / 64  # a list or a column grows to at most this times its floor (110 px in 9:16)
FILL = 0.6  # ...until the block fills this share of the space from the safe top to the safe bottom
LINE_SPAN = 0.8  # ...or its widest line spans this much of the canvas
GLOW = {"alpha": 0.12, "size": 900, "drift": 140, "cycles": (11, 15), "grain_deep": 0.05, "grain_paper": 0.04,
        "tile": 512, "step": 1 / 15, "seed": 7}  # a living ground: the reference board stage values
MIN_FIT = 0.75
TRACK = 36  # graphics take tracks 36 and up; headings, the end card and the captions have the others
IN, OUT = 0.18, 0.14  # the fade in and out at the cut


class FitError(ValueError):
    """An item that can't be set at 0.75 of its size in its slot."""


class LoneWordError(FitError):
    """Text of three or more words that can only be set with one word alone on its last line (Ruling P)."""


def _greedy(m, role: str, face: str, words: list[str], size: float, width: float, spacing: float) -> list[str]:
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}" if cur else word
        if cur and m.width(role, face, trial, size, spacing) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def _wrap(m, role: str, face: str, text: str, size: float, width: float, spacing: float) -> list[str]:
    """Text broken into lines that fit `width`. Text of three or more words that wraps is balanced (Ruling P):
    on the same number of lines as a greedy wrap, the breaks that make the longest line shortest, and the last
    line never a single word. One and two word text, and text that fits on one line, wraps as it always did."""
    words = text.split()
    lines = _greedy(m, role, face, words, size, width, spacing)
    n = len(lines)
    if n < 2 or len(words) < 3 or len(words) <= n:
        return lines
    span = {}

    def w(i: int, j: int) -> float:  # the width of words[i:j] on one line
        if (i, j) not in span:
            span[(i, j)] = m.width(role, face, " ".join(words[i:j]), size, spacing)
        return span[(i, j)]

    # best[k][j]: the least (longest line, sum of squares) for the first j words on k lines, the last one
    # holding at least 2 words when it is the final line of the text
    inf = (float("inf"), float("inf"))
    best = [[inf] * (len(words) + 1) for _ in range(n + 1)]
    back = [[0] * (len(words) + 1) for _ in range(n + 1)]
    best[0][0] = (0.0, 0.0)
    for k in range(1, n + 1):
        for j in range(k, len(words) + 1):
            for i in range(k - 1, j):
                if k == n and j == len(words) and j - i < 2:
                    continue
                prev = best[k - 1][i]
                if prev == inf:
                    continue
                cost = w(i, j)
                cand = (max(prev[0], cost), prev[1] + cost * cost)
                if cand < best[k][j]:
                    best[k][j], back[k][j] = cand, i
    if best[n][len(words)] == inf or best[n][len(words)][0] > width + 1e-6:
        return lines
    out, j = [], len(words)
    for k in range(n, 0, -1):
        i = back[k][j]
        out.append(" ".join(words[i:j]))
        j = i
    return out[::-1]


def fit_text(m, role: str, face: str, text: str, px: float, width: float, max_lines: int = 1,
             spacing: float = -0.015, upper: bool = False) -> tuple[float, list[str]]:
    """(factor, lines): `text` at `px` times factor in `width`, on at most `max_lines` lines, the factor 1 when
    it fits as it is and down to 0.75 when it doesn't. Past that, FitError. Several lines leave 3% so the
    browser never wraps a line again."""
    slack = width if max_lines == 1 else width * 0.97
    f, stub = 1.0, False
    count = len(text.split())
    while f >= MIN_FIT - 1e-9:
        lines = _wrap(m, role, face, text.upper() if upper else text, px * f, slack, spacing)
        if len(lines) <= max_lines and all(m.width(role, face, ln, px * f, spacing) <= slack + 1e-6 for ln in lines):
            if count >= 3 and len(lines) > 1 and len(lines[-1].split()) == 1:
                stub = True  # fits, but leaves one word alone: shrink to see if it balances
            else:
                return round(f, 3), lines
        f = round(f - 0.01, 4)
    if stub:
        raise LoneWordError(f'"{text}" would end on one word alone. Pick a shorter or longer run of the speaker\'s words.')
    raise FitError(f'"{text}" is too long to fit at a readable size. Shorten it to fewer of the speaker\'s words.')


def _num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


class _G:
    """One graphic being drawn: its elements, tweens, sound moments and boxes."""

    def __init__(self, g: dict, n: int, ratio: str, kit: dict, m):
        self.g, self.n, self.ratio, self.m = g, n, ratio, m
        self.sc = layout.TEXT[ratio]["sc"]
        self.H = layout.RATIOS[ratio]
        self.id = f"g{n}"
        self.top = TOP[ratio]
        self.bottom = SAFE_BOTTOM[ratio]
        self.span = SPAN[ratio] * 1080
        c = kit["colours"]
        if g["ground"] == "paper":
            self.P = dict(bg=c["paper"], text=c["primary"], active=c["accent_dark"], mark=c["accent"],
                          title=c["accent_dark"], on=c["paper"], dim=page._rgba(c["primary"], 0.5),
                          faint=page._rgba(c["primary"], 0.28), clear=page._rgba(c["accent"], 0),
                          plate=page._rgba(c["primary"], 0.06), edge=page._rgba(c["primary"], 0.14),
                          lit=page._rgba(c["accent"], 0.7))
        else:
            self.P = dict(bg=c["deep"], text=c["paper"], active=c["highlight"], mark=c["accent"],
                          title=c["accent"], on=c["deep"], dim=page._rgba(c["paper"], 0.5),
                          faint=page._rgba(c["paper"], 0.28), clear=page._rgba(c["accent"], 0),
                          plate=page._rgba(c["paper"], 0.06), edge=page._rgba(c["paper"], 0.14),
                          lit=page._rgba(c["accent"], 0.7))
        self.accent = c["accent"]
        # single quotes: the family sits inside a style="..." attribute
        self.fam = {"display": page._family(kit["fonts"]["display"]["family"]).replace('"', "'"),
                    "body": page._family(kit["fonts"]["body"]["family"]).replace('"', "'")}
        self.els: list[str] = []
        self.js: list[str] = []
        self.events: list[dict] = []
        self.boxes: list[tuple[float, float, float, float]] = []
        self.title_fit = None
        self.title_total = 0.0
        t = g.get("title")
        if t:
            px = 52 * self.sc
            f, lines = self.fit("display", "italic", t["text"], px, 960, 1, -0.01)
            self.title_fit = (px * f, lines)
            self.title_total = len(lines) * px * f * 1.25 + 14 * self.sc + 2 + 36 * self.sc

    # plumbing
    def ev(self, role: str, event: str, at: float) -> None:
        self.events.append({"role": role, "event": event, "at": round(at, 3), "sfx": self.g["sfx"]})

    def box(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.boxes.append((round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)))

    def fit(self, role, face, text, px, width, max_lines=1, spacing=-0.015, upper=False):
        try:
            return fit_text(self.m, role, face, text, px, width, max_lines, spacing, upper)
        except LoneWordError:
            raise LoneWordError(f'Graphic {self.n}: "{text}" would end on one word alone in {self.ratio.replace("x", ":")}. '
                                "Pick a shorter or longer run of the speaker's words.") from None
        except FitError:
            raise FitError(f'Graphic {self.n}: "{text}" is too long for {self.ratio.replace("x", ":")} even at 0.75 of '
                           "its size. Shorten it to fewer of the speaker's words.") from None

    def floor(self, k: float, fitted: list, texts: list[str]) -> None:
        """The 0.75 floor is against the unshrunk base size: a block shrunk to k to fit the height and a line
        shrunk by f to fit its slot may not together go below it."""
        for (f, _), text in zip(fitted, texts):
            if k * f < MIN_FIT - 1e-9:
                raise FitError(f'Graphic {self.n}: "{text}" is too long for {self.ratio.replace("x", ":")} even at '
                               "0.75 of its size. Shorten it to fewer of the speaker's words.")

    def fit_text_error(self, text: str) -> FitError:
        return FitError(f'Graphic {self.n}: "{text}" is too long for {self.ratio.replace("x", ":")} even at 0.75 of '
                        "its size. Shorten it to fewer of the speaker's words.")

    def too_tall(self, what: str) -> FitError:
        return FitError(f"Graphic {self.n}: {what} won't fit in {self.ratio.replace('x', ':')} even at 0.75 of its "
                        "size. Use fewer items or fewer of the speaker's words.")

    @property
    def avail(self) -> float:
        return self.bottom - self.top - self.title_total

    def text(self, ident: str, role: str, face: str, lines: list[str], size: float, x: float, y: float, w: float,
             align: str, colour: str, *, spacing: float = -0.015, lh: float = 1.18, upper: bool = False,
             extra: str = "") -> float:
        """A block of text on explicit lines, one `<div>`; returns its height."""
        style = {"regular": "", "italic": "font-style:italic;", "bold": "font-weight:700;"}[face]
        h = len(lines) * size * lh
        body = "<br>".join(html.escape(ln) for ln in lines)
        self.els.append(
            f'<div id="{ident}" style="position:absolute;left:{_num(x)}px;top:{_num(y)}px;width:{_num(w)}px;'
            f'text-align:{align};font-family:{self.fam[role]};{style}font-size:{_num(size)}px;line-height:{lh};'
            f'color:{colour};letter-spacing:{spacing}em;white-space:nowrap;'
            f'{"text-transform:uppercase;" if upper else ""}{extra}">{body}</div>')
        widest = max((self.m.width(role, face, ln.upper() if upper else ln, size, spacing) for ln in lines), default=0.0)
        widest = min(widest, w)
        left = x if align == "left" else x + w - widest if align == "right" else x + (w - widest) / 2
        self.box(left, y, left + widest, y + h)
        return h

    def shape(self, ident: str, x: float, y: float, w: float, h: float, style: str, inner: str = "") -> None:
        self.els.append(f'<div id="{ident}" style="position:absolute;left:{_num(x)}px;top:{_num(y)}px;'
                        f'width:{_num(w)}px;height:{_num(h)}px;{style}">{inner}</div>')
        self.box(x, y, x + w, y + h)

    def land(self, sel: str, t: float, dur: float = 0.35, settle: bool = False) -> None:
        extra = ", scale: 1.04" if settle else ""
        to = ", scale: 1" if settle else ""
        self.js.append(f'tl.fromTo("{sel}", {{ opacity: 0, y: 10, filter: "blur(6px)"{extra} }}, '
                       f'{{ opacity: 1, y: 0, filter: "blur(0px)"{to}, duration: {dur}, ease: "expo.out" }}, {_num(t)});')

    def tw(self, sel: str, props: str, t: float) -> None:
        self.js.append(f'tl.to("{sel}", {{ {props} }}, {_num(t)});')

    # the ground: glows or washes, then grain, all on the timeline
    def ground(self) -> None:
        g, sid = self.g, self.id
        deep = g["ground"] != "paper"
        size = GLOW["size"]
        spots = ((0.18, 0.28), (0.84, 0.72))
        wins = []
        for k, ((fx, fy), cycle) in enumerate(zip(spots, GLOW["cycles"])):
            amp = GLOW["drift"] / 2
            alpha = GLOW["alpha"] if deep else 0.10
            self.els.append(
                f'<div id="{sid}glow{k}" style="position:absolute;left:{_num(fx * 1080 - size / 2)}px;'
                f'top:{_num(fy * self.H - size / 2)}px;width:{size}px;height:{size}px;border-radius:50%;'
                f'background:radial-gradient(circle, {self.accent} 0%, {page._rgba(self.accent, 0)} 65%);opacity:{alpha}"></div>')
            life = g["end"] - g["start"]
            for axis, period in (("x", cycle), ("y", cycle * 0.8)):
                half = period / 2
                reps = max(0, int(life / half) - 1)  # whole yoyo legs only: the tween ends inside the window
                dur = half if reps else min(half, life)
                other = "y" if axis == "x" else "x"
                wins.append(f'tl.fromTo("#{sid}glow{k}", {{ {axis}: {-amp}, {other}: 0 }}, {{ {axis}: {amp}, '
                            f'duration: {_num(dur)}, ease: "sine.inOut", repeat: {reps}, yoyo: true }}, {_num(g["start"])});')
        self.js += wins
        opacity = GLOW["grain_deep"] if deep else GLOW["grain_paper"]
        self.els.append(f'<div id="{sid}grain" style="position:absolute;inset:0;opacity:{opacity};'
                        f'mix-blend-mode:overlay;background-size:{GLOW["tile"]}px {GLOW["tile"]}px"></div>')
        self.js.append(
            "window.__grainTile = window.__grainTile || (() => { const c = document.createElement(\"canvas\"); "
            f"c.width = c.height = {GLOW['tile']}; const x = c.getContext(\"2d\"); "
            f"const d = x.createImageData({GLOW['tile']}, {GLOW['tile']}); let s = {GLOW['seed']}; "
            "for (let i = 0; i < d.data.length; i += 4) { s = (s * 1664525 + 1013904223) >>> 0; "
            "const v = (s >>> 24); d.data[i] = d.data[i + 1] = d.data[i + 2] = v; d.data[i + 3] = 255; } "
            "x.putImageData(d, 0, 0); return c.toDataURL(); })();")
        self.js.append(f'document.getElementById("{sid}grain").style.backgroundImage = "url(" + window.__grainTile + ")";')
        seed, t = GLOW["seed"] + self.n, g["start"] + GLOW["step"]
        while t < g["end"] - 0.02:  # the grain's offset steps every other frame, drawn from the seed
            seed = (seed * 1664525 + 1013904223) % 2 ** 32
            ox, oy = (seed >> 8) % GLOW["tile"], (seed >> 20) % GLOW["tile"]
            self.js.append(f'tl.set("#{sid}grain", {{ backgroundPosition: "{ox}px {oy}px" }}, {_num(t)});')
            t += GLOW["step"]

    # the parts every kind shares
    def place(self, content_h: float) -> float:
        """Put the title and the content block at the optical centre; returns the content's top."""
        total = self.title_total + content_h
        centre = SAFE_TOP[self.ratio] + OPTICAL * (self.bottom - SAFE_TOP[self.ratio])
        y0 = max(self.top, min(centre - total / 2, self.bottom - total))
        t = self.g.get("title")
        if t:
            size, lines = self.title_fit
            h = self.text(f"{self.id}t", "display", "italic", lines, size, SIDE, y0, 960, "center", self.P["title"],
                          spacing=-0.01, lh=1.25)
            ry = y0 + h + 14 * self.sc
            self.shape(f"{self.id}r", 540 - 32 * self.sc, ry, 64 * self.sc, 2, f'background:{self.P["mark"]}')
            self.land(f"#{self.id}t", t["at"])
            self.land(f"#{self.id}r", t["at"] + 0.08, 0.4)
            self.ev("hit", "graphic title", t["at"])
        return y0 + self.title_total

    def entrance(self) -> None:
        g, sel = self.g, f"#{self.id}"
        self.js.append(f'tl.set("{sel}", {{ opacity: 0 }}, 0);')  # hidden until its own fade in (a full-frame overlay)
        self.js.append(f'tl.fromTo("{sel}", {{ opacity: 0, scale: 1.03 }}, '
                       f'{{ opacity: 1, scale: 1, duration: {IN}, ease: "power2.out" }}, {_num(g["start"])});')
        self.ev("cut", "graphic in", g["start"])
        self.ev("push", "graphic push", g["start"] + 0.2)

    def exit(self) -> None:
        g, sel = self.g, f"#{self.id}"
        self.js.append(f'tl.to("{sel}", {{ opacity: 0, duration: {OUT}, ease: "power2.in" }}, {_num(g["end"] - 0.16)});')
        self.js.append(f'tl.set("{sel}", {{ opacity: 0 }}, {_num(g["end"] - 0.02)});')
        self.ev("cut", "graphic out", g["end"])

    def done(self, last: float) -> float:
        """When every item has landed (0.4 s after the last one, before the fade out)."""
        return round(min(last + 0.4, self.g["end"] - 0.15), 3)

    def finish(self, last: float) -> dict:
        g = self.g
        self.entrance()
        self.exit()
        self.events.sort(key=lambda e: e["at"])
        z = 45  # every graphic covers the captions (z 30): its words are the speaker's own, so it is the caption
        root = (f'<div id="{self.id}" class="clip" data-start="{_num(g["start"])}" '
                f'data-duration="{_num(g["end"] - g["start"])}" data-track-index="{TRACK + self.n - 1}" '
                f'data-layout-allow-overflow data-layout-allow-occlusion '
                f'style="position:absolute;inset:0;z-index:{z};overflow:hidden;background:{self.P["bg"]}">\n'
                + "\n".join(f"        {e}" for e in self.els) + "\n      </div>")
        return {"html": root, "js": "\n".join(self.js), "events": self.events, "full_at": self.done(last),
                "boxes": self.boxes}


def balance(m, text: str, size: float, width: float, spacing: float) -> list[str]:
    """`text` on as few lines as it takes at `width`, then on the narrowest width that keeps that many, so the
    lines come out near-equal and none is left as a stub."""
    lines = _wrap(m, "display", "regular", text, size, width, spacing)
    if len(lines) < 2:
        return lines
    lo = max(m.width("display", "regular", w, size, spacing) for w in text.split())
    hi = width
    for _ in range(14):
        mid = (lo + hi) / 2
        if len(_wrap(m, "display", "regular", text, size, mid, spacing)) <= len(lines):
            hi = mid
        else:
            lo = mid
    return _wrap(m, "display", "regular", text, size, hi, spacing)


def _grow(s: _G, lay, wide) -> tuple:
    """`lay(k)` lays a block out with its text at k times the floor and returns (height, ...parts), or raises
    FitError. The block first shrinks until it fits (down to 0.75, then FitError), then grows while the text
    still fits unshrunk, up to GROW_CAP, stopping short of filling more than FILL of the space from the safe top
    to the safe bottom or of the room there is, and once its widest line (`wide(parts)`) spans LINE_SPAN. The last
    part is (unshrunk, line counts): growth stops before any item gains a line (a long item that already wraps
    at the starting size may keep wrapping)."""
    k = 1.0
    while True:
        got = lay(k)
        if got[0] <= s.avail:
            break
        k = round(k - 0.02, 3)
        if k < MIN_FIT:
            raise s.too_tall("the items")
    goal = FILL * (s.bottom - SAFE_TOP[s.ratio])
    lines = got[-1][1]  # growing never adds a line break (Ruling L): stop at the last size with these line counts
    while wide(got) < LINE_SPAN * 1080 and got[0] < goal and k + 0.04 <= GROW_CAP:
        try:
            nxt = lay(round(k + 0.04, 3))
        except FitError:
            break
        if nxt[0] > min(s.avail, goal) or not nxt[-1][0] or nxt[-1][1] != lines:
            break
        got, k = nxt, round(k + 0.04, 3)
    return got


# cycle
def _cycle(s: _G) -> float:
    g, sc, n = s.g, s.sc, len(s.g["items"])
    cx, pad = 540, 26 * sc

    def slot_of(i, R):
        a = math.radians(-90 + 360 * i / n)
        nx, c = cx + R * math.cos(a), math.cos(a)
        gap = 30 * sc
        if c > 0.3:
            return nx + gap, 1080 - SIDE - (nx + gap), "left"
        if c < -0.3:
            return SIDE, nx - gap - SIDE, "right"
        return 120, 840, "center"

    def lay(k):
        px = 64 * sc * k
        lab_h = px * 1.18
        R = max(90.0, min(170.0, s.avail / 2 - pad - 2 * lab_h - 14))
        fits = [s.fit("display", "regular", item["text"], px, slot_of(i, R)[1], 2) for i, item in enumerate(g["items"])]
        s.floor(k, fits, [i["text"] for i in g["items"]])
        return 2 * (R + pad + 2 * lab_h), px, R, fits, (all(f == 1 for f, _ in fits), [len(ln) for _, ln in fits])

    def wide(got):  # the ring and its widest label on a side
        _, px, R, fits, _ = got
        return 2 * R + 2 * (30 * sc + max(s.m.width("display", "regular", ln, px * f, -0.015)
                                        for f, lines in fits for ln in lines))

    content_h, px, R, fits, _ = _grow(s, lay, wide)
    lab_h = px * 1.18
    ct = s.place(content_h)
    cy = ct + R + pad + 2 * lab_h
    ring_len = round(2 * math.pi * R, 2)
    nodes, svg = [], []
    for i, item in enumerate(g["items"]):
        a = math.radians(-90 + 360 * i / n)
        nx, ny = cx + R * math.cos(a), cy + R * math.sin(a)
        nodes.append((nx, ny))
        c, sn = math.cos(a), math.sin(a)
        x, w, align = slot_of(i, R)
        f, lines = fits[i]
        size = px * f
        h = len(lines) * size * 1.18
        y = ny - h / 2 if abs(c) > 0.3 else (ny - pad - h if sn < 0 else ny + pad)
        s.text(f"{s.id}e{i}", "display", "regular", lines, size, x, y, w, align, s.P["text"])
        svg.append(f'<circle id="{s.id}n{i}" cx="{_num(nx)}" cy="{_num(ny)}" r="0" fill="{s.P["bg"]}" '
                   f'stroke="{s.P["mark"]}" stroke-width="2.5" />')
    ring = (f'<circle id="{s.id}ring" cx="{cx}" cy="{_num(cy)}" r="{_num(R)}" fill="none" stroke="{s.P["mark"]}" '
            f'stroke-opacity=".75" stroke-width="2.5" stroke-dasharray="{ring_len}" stroke-dashoffset="{ring_len}" '
            f'transform="rotate(-90 {cx} {_num(cy)})" />')
    dot = (f'<g id="{s.id}dg"><circle id="{s.id}dot" cx="{cx}" cy="{_num(cy - R)}" r="0" fill="{s.P["active"]}" /></g>')
    s.els.append(f'<svg width="1080" height="{s.H}" viewBox="0 0 1080 {s.H}" style="position:absolute;inset:0">'
                 f'{ring}{"".join(svg)}{dot}</svg>')
    s.box(cx - R - 13, cy - R - 13, cx + R + 13, cy + R + 13)

    at = [i["at"] for i in g["items"]]
    start, end = g["start"], g["end"]
    sid = s.id
    origin = f'svgOrigin: "{cx} {_num(cy)}"'
    s.js.append(f'tl.fromTo("#{sid}ring", {{ attr: {{ "stroke-dashoffset": {ring_len} }} }}, '
                f'{{ attr: {{ "stroke-dashoffset": 0 }}, duration: 0.9, ease: "power2.inOut" }}, {_num(start + 0.2)});')
    s.js.append(f'tl.set("#{sid}dg", {{ rotation: 0, {origin} }}, {_num(start)});')
    r_dot = _num(13 * sc)
    for i in range(n):
        s.land(f"#{sid}e{i}", at[i])
        s.js.append(f'tl.fromTo("#{sid}n{i}", {{ attr: {{ r: 0 }} }}, {{ attr: {{ r: {_num(10 * sc)} }}, duration: 0.3, '
                    f'ease: "back.out(2)" }}, {_num(at[i])});')
        s.tw(f"#{sid}n{i}", f'attr: {{ fill: "{s.P["mark"]}" }}, duration: 0.15', at[i])
        s.tw(f"#{sid}e{i}", f'color: "{s.P["active"]}", duration: 0.15', at[i])
        if i > 0:
            s.tw(f"#{sid}e{i}", f'color: "{s.P["text"]}", duration: 0.4', at[i] + 0.5)
    s.tw(f"#{sid}e0", f'color: "{s.P["text"]}", duration: 0.4', at[0] + 0.5)
    s.js.append(f'tl.fromTo("#{sid}dot", {{ attr: {{ r: 0 }} }}, {{ attr: {{ r: {r_dot} }}, duration: 0.3, '
                f'ease: "back.out(2)" }}, {_num(at[0])});')
    for k in range(1, n):
        d = max(0.12, min(0.5, at[k] - at[k - 1] - 0.05))
        t0 = at[k] - d
        s.js.append(f'tl.fromTo("#{sid}dg", {{ rotation: {_num(360 * (k - 1) / n)}, {origin} }}, '
                    f'{{ rotation: {_num(360 * k / n)}, duration: {_num(d)}, ease: "power2.inOut", {origin}, '
                    f'immediateRender: false }}, {_num(t0)});')
        s.ev("whip", f"cycle hop to item {k + 1}", t0)
    back, d_back = at[-1] + 0.5, 0.6
    if back + d_back <= end - 0.2:  # round again, to the first node, when there's room
        s.js.append(f'tl.fromTo("#{sid}dg", {{ rotation: {_num(360 * (n - 1) / n)}, {origin} }}, '
                    f'{{ rotation: 360, duration: {d_back}, ease: "power2.inOut", {origin}, immediateRender: false }}, '
                    f'{_num(back)});')
        s.ev("whip", "cycle hop back to item 1", back)
        s.tw(f"#{sid}e0", f'color: "{s.P["active"]}", duration: 0.25, ease: "power2.out"', back + d_back)
        s.tw(f"#{sid}ring", 'attr: { "stroke-opacity": 1 }, duration: 0.3', back + d_back)
    return at[-1]


# steps and checklist: one plate a row, spanning the block
def _rows(s: _G, kind: str) -> float:
    g, sc = s.g, s.sc
    its = g["items"]
    n = len(its)
    W = LINE_SPAN * 1080
    x0 = (1080 - W) / 2

    def lay(k):
        px = 64 * sc * k
        md, pad = 0.9 * px, 0.45 * px
        tx = x0 + pad + md + 0.4 * px
        slot = x0 + W - pad - tx
        fitted = [s.fit("display", "regular", i["text"], px, slot, 2) for i in its]
        s.floor(k, fitted, [i["text"] for i in its])
        heights = [len(lines) * px * f * 1.18 + 0.7 * px for f, lines in fitted]  # 0.35 em above and below the text
        total = sum(heights) + 0.2 * px * (n - 1)
        return total, px, md, pad, tx, slot, fitted, heights, (all(f == 1 for f, _ in fitted),
                                                                [len(ln) for _, ln in fitted])

    def wide(got):
        _, px, md, pad, _, _, fitted, _, _ = got
        return max(pad + md + 0.4 * px + max(s.m.width("display", "regular", ln, px * f, -0.015) for ln in lines)
                   for f, lines in fitted) + pad

    total, px, md, pad, tx, slot, fitted, heights, _ = _grow(s, lay, wide)
    top = s.place(total)
    sid = s.id
    y = top
    for i, (item, (f, lines), h) in enumerate(zip(its, fitted, heights)):
        cyr = y + h / 2
        size = px * f
        t = item["at"]
        s.shape(f"{sid}p{i}", x0, y, W, h, f'background:{s.P["plate"]};border:1.5px solid {s.P["edge"]};'
                f'border-radius:{_num(0.3 * px)}px;box-sizing:border-box')
        s.js.append(f'tl.fromTo("#{sid}p{i}", {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.35, '
                    f'ease: "expo.out" }}, {_num(t)});')
        s.tw(f"#{sid}p{i}", f'borderColor: "{s.P["lit"]}", duration: 0.15', t)
        s.text(f"{sid}e{i}", "display", "regular", lines, size, tx, cyr - len(lines) * size * 1.18 / 2, slot, "left",
               s.P["text"], extra="transform-origin:0 50%;")
        mx = x0 + pad
        if kind == "steps":
            s.shape(f"{sid}c{i}", mx, cyr - md / 2, md, md,
                    f'border:3px solid {s.P["mark"]};border-radius:50%;display:flex;align-items:center;'
                    f'justify-content:center;font-family:{s.fam["display"]};font-size:{_num(md * 0.5)}px;'
                    f'color:{s.P["mark"]};background:{s.P["clear"]};box-sizing:border-box', str(i + 1))
            s.js.append(f'tl.fromTo("#{sid}c{i}", {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1, duration: 0.4, '
                        f'ease: "back.out(1.6)" }}, {_num(t)});')
            s.tw(f"#{sid}c{i}", f'backgroundColor: "{s.P["mark"]}", color: "{s.P["on"]}", duration: 0.2', t + 0.05)
            if i > 0:  # the earlier step steps back
                s.tw(f"#{sid}c{i - 1}", f'backgroundColor: "{s.P["clear"]}", color: "{s.P["mark"]}", duration: 0.3', t)
                s.tw(f"#{sid}e{i - 1}", f'color: "{s.P["dim"]}", duration: 0.4', t)
        else:
            check = (f'<svg viewBox="0 0 24 24" width="{_num(md * 0.7)}" height="{_num(md * 0.7)}">'
                     f'<path id="{sid}k{i}" d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="{s.P["on"]}" stroke-width="3" '
                     'stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="24" stroke-dashoffset="24" /></svg>')
            s.shape(f"{sid}c{i}", mx, cyr - md / 2, md, md,
                    f'border:3px solid {s.P["mark"]};border-radius:{_num(md * 0.26)}px;display:flex;'
                    f'align-items:center;justify-content:center;background:{s.P["clear"]};box-sizing:border-box', check)
            s.js.append(f'tl.fromTo("#{sid}c{i}", {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1, duration: 0.4, '
                        f'ease: "back.out(1.6)" }}, {_num(t)});')
            s.tw(f"#{sid}c{i}", f'backgroundColor: "{s.P["mark"]}", duration: 0.2', t + 0.1)
            s.tw(f"#{sid}k{i}", 'attr: { "stroke-dashoffset": 0 }, duration: 0.3, ease: "power2.out"', t + 0.1)
            if i > 0:
                s.tw(f"#{sid}e{i - 1}", f'color: "{s.P["text"]}", duration: 0.4', t)
        if i > 0:
            s.tw(f"#{sid}p{i - 1}", f'borderColor: "{s.P["edge"]}", duration: 0.4', t)
        s.land(f"#{sid}e{i}", t, settle=True)  # the newest item settles in, in the accent
        s.tw(f"#{sid}e{i}", f'color: "{s.P["active"]}", duration: 0.15', t)
        s.ev("tick", f"{kind} item {i + 1}", t)
        y += h + 0.2 * px
    return its[-1]["at"]


# versus
def _versus(s: _G) -> float:
    g, sc, sid = s.g, s.sc, s.id
    colw, gutter = 440.0, 80.0
    xs = {"left": SIDE, "right": SIDE + colw + gutter}

    def lay(k):
        px = 64 * sc * k
        head_px = 28 * sc * k
        gap = 0.4 * px
        cols, heights = {}, {}
        for side in ("left", "right"):
            rows = [s.fit("display", "regular", i["text"], px, colw, 2) for i in g[side]]
            s.floor(k, rows, [i["text"] for i in g[side]])
            head = g.get(f"{side}_header")
            hh, hfit = 0.0, None
            if head:
                hfit = s.fit("body", "bold", head["text"], head_px, colw, 1, 0.2, True)
                s.floor(k, [hfit], [head["text"]])
                hh = head_px * hfit[0] * 1.3 + 0.5 * px
            body = sum(len(ln) * px * f * 1.18 for f, ln in rows) + gap * (len(rows) - 1)
            cols[side], heights[side] = (rows, head, hfit, hh), hh + body
        ok = all(f == 1 for side in cols for f, _ in cols[side][0])
        counts = [len(ln) for side in cols for _, ln in cols[side][0]]
        return max(heights.values()), px, head_px, gap, cols, (ok, counts)

    block, px, head_px, gap, cols, _ = _grow(s, lay, lambda got: 0.0)
    top = s.place(block)
    s.shape(f"{sid}d", 540 - 1, top, 2, block, f'background:{s.P["faint"]};transform-origin:50% 0')
    s.js.append(f'tl.fromTo("#{sid}d", {{ scaleY: 0 }}, {{ scaleY: 1, duration: 0.5, ease: "power2.out" }}, '
                f'{_num(g["start"] + 0.4)});')
    vs = 1.0 * px
    s.shape(f"{sid}vs", 540 - vs / 2, top + block / 2 - vs / 2, vs, vs,
            f'border:2px solid {s.P["faint"]};border-radius:50%;background:{s.P["bg"]};display:flex;'
            f'align-items:center;justify-content:center;font-family:{s.fam["display"]};font-style:italic;'
            f'font-size:{_num(vs * 0.42)}px;color:{s.P["dim"]};box-sizing:border-box', "vs")
    s.js.append(f'tl.fromTo("#{sid}vs", {{ opacity: 0, scale: 0.7 }}, {{ opacity: 1, scale: 1, duration: 0.4, '
                f'ease: "back.out(1.6)" }}, {_num(g["start"] + 0.4)});')
    strikes, idx, last = [], 0, 0.0
    turn = g["turn"]["at"]
    for side in ("left", "right"):
        rows, head, hfit, hh = cols[side]
        x, y = xs[side], top
        if head:
            s.text(f"{sid}h{side[0]}", "body", "bold", [head["text"]], head_px * hfit[0], x, y, colw, "center",
                   s.P["dim"] if side == "left" else s.P["active"], spacing=0.2, lh=1.3, upper=True)
            s.land(f"#{sid}h{side[0]}", head["at"])
        y += hh
        for item, (f, lines) in zip(g[side], rows):
            size = px * f
            h = s.text(f"{sid}e{idx}", "display", "regular", lines, size, x, y, colw, "center", s.P["text"])
            s.land(f"#{sid}e{idx}", item["at"])
            s.ev("tick", f"versus {side} item", item["at"])
            last = max(last, item["at"])
            if side == "left":
                for j, ln in enumerate(lines):
                    wl = s.m.width("display", "regular", ln, size, -0.015)
                    ly = y + (j + 0.55) * size * 1.18
                    sk = f"{sid}s{len(strikes)}"
                    s.shape(sk, x + (colw - wl) / 2 - 6, ly - 1.5, wl + 12, 3,
                            f'background:{s.P["active"]};transform-origin:0 50%;opacity:0')
                    strikes.append(sk)
                s.tw(f"#{sid}e{idx}", f'color: "{s.P["dim"]}", duration: 0.4', turn + 0.1)
            idx += 1
            y += h + gap
    for j, sk in enumerate(strikes):
        s.js.append(f'tl.fromTo("#{sk}", {{ scaleX: 0, opacity: 1 }}, {{ scaleX: 1, opacity: 1, duration: 0.3, '
                    f'ease: "power2.out" }}, {_num(turn + 0.07 * j)});')
    s.ev("hit", "versus strike", turn)
    return last


# stat
def _stat(s: _G) -> float:
    g, sc, sid = s.g, s.sc, s.id
    shown = g["value"]
    match = re.match(r"^([^\d.]*)([\d,]*\.?\d+)(.*)$", shown.strip())
    pre, num, suf = (match.group(1), match.group(2), match.group(3)) if match else ("", shown, "")
    target = float(num.replace(",", "")) if match else 0.0
    decimals = len(num.split(".")[1]) if "." in num else 0
    label = g.get("label")
    lab_px = 64 * sc
    lab_fit = s.fit("display", "italic", label["text"], lab_px, 900, 2) if label else None
    h_lab = len(lab_fit[1]) * lab_px * lab_fit[0] * 1.2 if label else 0.0
    gap = 34 * sc
    # the value grows to fill the block, as far as the width and a share of the height allow, never below 220
    base = 220 * sc
    size = base
    for step in range(0, 31):
        trial = base * (1 + step * 0.05)
        wide = s.m.width("display", "regular", shown, trial, -0.03)
        if wide > s.span * 1.1 or trial * 1.05 + (gap * 2 + 2 + h_lab) > s.avail * 0.85:
            break
        size = trial
    if s.m.width("display", "regular", shown, size, -0.03) > 960:
        f, _ = s.fit("display", "regular", shown, size, 960, 1, -0.03)
        if size * f < MIN_FIT * base:
            raise s.fit_text_error(shown)
        size *= f
    h_num = size * 1.05
    block = h_num + gap + 2 + (gap + h_lab if label else 0)
    top = s.place(block)
    s.text(f"{sid}v", "display", "regular", [pre + "0" + suf], size, SIDE, top, 960, "center", s.P["active"],
           spacing=-0.03, lh=1.05)
    ry = top + h_num + gap
    s.shape(f"{sid}hl", 540 - s.span / 2, ry, s.span, 2, f'background:{s.P["faint"]};transform-origin:50% 50%')
    s.shape(f"{sid}r", 540 - 48 * sc, ry - 1, 96 * sc, 4, f'background:{s.P["mark"]};transform-origin:50% 50%')
    t_hit = g["value_at"]
    t_in = g["start"] + 0.15
    t_count = max(t_in, t_hit - 0.9)
    s.js.append(f'tl.fromTo("#{sid}v", {{ opacity: 0, y: 10, filter: "blur(6px)" }}, '
                f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.35, ease: "expo.out" }}, {_num(t_in)});')
    # the digits are driven by the timeline itself: one set a frame, no callback (a seek suppresses callbacks)
    frames = max(1, round((t_hit - t_count) * 30))
    for i in range(frames + 1):
        p = i / frames
        v = target * (1 - (1 - p) ** 2)  # power2.out
        shown_now = f"{v:,.{decimals}f}" if "," in num else f"{v:.{decimals}f}"
        if i == frames:
            shown_now = num
        s.js.append(f'tl.set("#{sid}v", {{ textContent: {json.dumps(pre + shown_now + suf)} }}, '
                    f'{_num(t_count + (t_hit - t_count) * p)});')
    s.js.append(f'tl.to("#{sid}v", {{ scale: 1.05, duration: 0.12, ease: "power2.out" }}, {_num(t_hit)});')
    s.js.append(f'tl.to("#{sid}v", {{ scale: 1, duration: 0.3, ease: "expo.out" }}, {_num(t_hit + 0.12)});')
    s.js.append(f'tl.fromTo("#{sid}hl", {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.6, ease: "power2.out" }}, {_num(t_hit)});')
    s.js.append(f'tl.fromTo("#{sid}r", {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.4, ease: "power2.out" }}, {_num(t_hit)});')
    s.ev("hit", "stat value", t_hit)
    last = t_hit
    if label:
        s.text(f"{sid}e0", "display", "italic", lab_fit[1], lab_px * lab_fit[0], SIDE + 30, ry + 2 + gap, 900, "center",
               s.P["text"], lh=1.2)
        s.land(f"#{sid}e0", label["at"])
        last = max(last, label["at"])
    return last


# quote
def _quote(s: _G) -> float:
    g, sc, sid = s.g, s.sc, s.id
    line = g["line"]
    width = 940
    base = 84 * sc
    best = None
    for step in range(0, 14):  # grow the line toward the block's width: as big as keeps it to four lines and in the space
        px = base * (1 + step * 0.05)
        try:
            f, lines = fit_text(s.m, "display", "regular", line["text"], px, width, 4, -0.02)
        except FitError:
            if best is None:
                s.fit("display", "regular", line["text"], px, width, 4, -0.02)  # raises, naming the line
            break
        size = px * f
        if size < MIN_FIT * base:
            if best is None:
                raise s.fit_text_error(line["text"])
            break
        lines = balance(s.m, line["text"], size, width, -0.02)
        h = len(lines) * size * 1.16
        if h + 40 * sc > s.avail and best is not None:
            break
        best = (size, lines, h)
        if max(s.m.width("display", "regular", ln, size, -0.02) for ln in lines) >= width * 0.97 and step >= 3:
            break
    size, lines, h = best
    lh = 1.16
    bar = 40 * sc
    top = s.place(h + bar)
    if not g.get("title"):
        s.shape(f"{sid}r", 540 - 32 * sc, top, 64 * sc, 3, f'background:{s.P["mark"]}')
        s.land(f"#{sid}r", g["start"] + 0.4, 0.4)
    top += bar
    accent = set(verify.normalise(g["accent"])) if g.get("accent") else set()
    words = [w for ln in lines for w in ln.split()]
    total = sum(len(w) for w in words)
    t0, t1 = line["at"], max(line["end"], line["at"] + 0.1)
    n, cum, last = 0, 0, t0
    for r, ln in enumerate(lines):
        spans = []
        for w in ln.split():
            hot = bool(accent & set(verify.normalise(w)))
            colour = s.P["active"] if hot else s.P["text"]
            style = f'color:{colour};{"font-style:italic;" if hot else ""}'
            spans.append(f'<span id="{sid}w{n}" style="display:inline-block;{style}">{html.escape(w)}</span>')
            t = t0 + (t1 - t0) * cum / total
            s.land(f"#{sid}w{n}", t)
            last = max(last, t)
            cum += len(w)
            n += 1
        y = top + r * size * lh
        s.els.append(f'<div style="position:absolute;left:{(1080 - width) / 2}px;top:{_num(y)}px;width:{width}px;'
                     f'text-align:center;font-family:{s.fam["display"]};font-size:{_num(size)}px;line-height:{lh};'
                     f'letter-spacing:-0.02em;white-space:nowrap">{" ".join(spans)}</div>')
        wl = s.m.width("display", "regular", ln, size, -0.02)
        s.box(540 - wl / 2, y, 540 + wl / 2, y + size * lh)
    return last


KINDS = {"cycle": _cycle, "steps": lambda s: _rows(s, "steps"), "checklist": lambda s: _rows(s, "checklist"),
         "versus": _versus, "stat": _stat, "quote": _quote}


def build(g: dict, n: int, ratio: str, kit: dict, m) -> dict:
    """One graphic as markup, tweens, sound moments and the boxes it laid out. `n` counts from 1."""
    s = _G(g, n, ratio, kit, m)
    s.ground()
    last = KINDS[g["kind"]](s)
    return s.finish(last)


def fit_errors(job, graphics: list[dict]) -> list[str]:
    """Every graphic in every ratio that has text too long to fit, as plain sentences, for plan check. A
    client with no readable kit yet has nothing to measure with, so it reports none."""
    if not graphics:
        return []
    try:
        kit = brand.read(job.client)
    except (brand.NoKit, brand.BadKit):
        return []
    if not {"colours", "fonts"} <= set(kit):  # a kit not filled in yet has nothing to measure with
        return []
    m = page.Measure(kit, job.brand)
    out = []
    for n, g in enumerate(graphics, 1):  # anything else that goes wrong in build is a bug, so it is not caught
        for ratio in layout.RATIOS:
            try:
                build(g, n, ratio, kit, m)
            except FitError as exc:
                out.append(str(exc))
    return list(dict.fromkeys(out))
