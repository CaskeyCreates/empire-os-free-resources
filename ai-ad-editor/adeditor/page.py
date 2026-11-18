"""The page: one HyperFrames composition per ratio, from the cut, the face map, the client's approved
kit and the ad plan. Ported from the first hand-built ad's page builder.

On the page: the take, framed by layout.window (under a paper panel that holds the text, or in a
framed card under it, when the head sits too high for the headings to fade in over it), with a
punch-in on every other cut where it keeps the head and the captions clear; the B-roll
cut-ins, each staged at its exact length, graded a touch warmer and pushed in slowly; the offer
lockup on the first frame (it's the thumbnail); up to two headings in the speaker's words; captions
under the chin, a few words at a time, the key words in the display italic and the word being said
lit up; and the end card. Every colour and font comes from the kit. The page is silent: mix.py lays
the sound under the render.

Two HyperFrames 0.7.5 traps are kept clear of. A timeline built after an await renders no frames,
so the page builds it synchronously. A frame past a video's last extracted frame renders black, so
the take stops 0.04 s before the speech does, and each B-roll clip is staged 0.25 s longer than its
window and shown for at most its staged length minus 0.1 s."""
import html
import json
import os
import re
import shutil
import string
from pathlib import Path

from PIL import ImageFont

from adeditor import brand, faces, layout, media, plan, speed, verify
from adeditor.paths import Job

TEMPLATE = Path(__file__).resolve().parent.parent / "template"
GRADE = "eq=saturation=0.86:contrast=1.03:brightness=0.005,colorbalance=rs=0.025:gs=0.005:bs=-0.03"
STAGE_PAD = 0.25  # each B-roll clip is staged this much longer than its window
STAGE_TAIL = 0.1  # and shown for at most its staged length minus this
AROLL_TAIL = 0.04  # the take stops this long before the speech ends
MAX_WORDS, MAX_CHARS, GAP = 4, 19, 0.18  # a caption group: at most 4 words and 19 characters
BREAK_AFTER = re.compile(r"[.?!,:;]$")
CAP_IN, CAP_HOLD, CAP_FIRST = 0.05, 0.35, 0.08
COVER_IN, COVER_OUT = 0.05, 0.3  # a caption counts as over a cut-in with this much slack
CAP_WIDTH = 900  # px a caption group may take before it's scaled down
CAP_PAD = 0.43  # a caption pill's padding either side, in em
CARD_CAP_GAP = 24  # px a caption keeps inside a card's sides
FACES = brand.FACES
MAX_ROW = 1000  # px a text row may run before its font shrinks to keep it on one line
IDLE_MIN = 0.9  # s: a shorter gap between two text blocks leaves the panel as it is
# How each kind of text row is set: the kit font role, its face, letter-spacing in em, upper case.
ROW_FONT = {"eyebrow": ("display", "italic", -0.01, False), "h": ("display", "regular", -0.025, False),
            "caps": ("body", "bold", 0.22, True)}


def show(text: str) -> str:
    """A word as the caption shows it: no surrounding quotes, no trailing comma, full stop, colon or semicolon."""
    t = str(text).strip().strip('"“”')
    return re.sub(r"[,.;:]+$", "", t)


def groups(words: list[dict]) -> list[list[int]]:
    """Word indices in caption groups: a new group after 4 words, past 19 characters, after
    punctuation that ends a phrase, or after a pause of 0.18 s."""
    out, cur = [], []
    for i, w in enumerate(words):
        if cur:
            prev = words[cur[-1]]
            chars = sum(len(show(words[j]["text"])) for j in cur) + len(show(w["text"]))
            if (len(cur) >= MAX_WORDS or chars > MAX_CHARS or BREAK_AFTER.search(str(prev["text"]).strip())
                    or w["start"] - prev["end"] >= GAP):
                out.append(cur)
                cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return out


def key_indices(words: list[dict], key_words: list[dict]) -> set[int]:
    """The indices of the words the captions set as key words. A key word with `only_in` counts only
    where it's said inside that phrase."""
    norm = [" ".join(verify.normalise(str(w["text"]))) for w in words]
    kept = plan.Kept(words)
    out: set[int] = set()
    for kw in key_words:
        target = " ".join(verify.normalise(kw["word"]))
        found = {i for i, n in enumerate(norm) if n == target}
        if kw.get("only_in"):
            inside: set[int] = set()
            start = 0
            while (got := kept.find(kw["only_in"], start)) is not None:
                i, n = got
                for t in range(i, i + n):
                    inside |= kept.toks[t][1]
                start = i + 1
            found &= inside
        out |= found
    return out


def _n(x: float) -> float:
    return round(float(x), 3)


def _rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _light(hex_colour: str) -> bool:
    """A colour dark text reads better on than light text does."""
    return brand.contrast(hex_colour, "#000000") >= brand.contrast(hex_colour, "#FFFFFF")


def _family(name: str) -> str:
    return '"' + re.sub(r'["\\\n\r]', "", str(name)) + '"'


def link(src: Path, dst: Path) -> None:
    """dst as a hard link to src, or a copy where links can't be made. Never a symlink (Windows)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def stage(src: Path, start: float, window: float, out: Path) -> Path:
    """The B-roll clip from `start` for its window plus STAGE_PAD, cover-cropped to 1080x1920 at 30 fps
    and graded, with no sound. Staged once; every ratio links the same file."""
    if out.exists() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.mp4")
    vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,{GRADE}"
    media.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{window + STAGE_PAD:.3f}",
               "-an", "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
               "-g", "15", "-keyint_min", "15", "-sc_threshold", "0", str(tmp)])
    tmp.replace(out)
    return out


def _inline(text: str) -> str:
    """Escaped text with *these words* in italic."""
    return re.sub(r"\*(.+?)\*", r"<i>\1</i>", html.escape(text))


def _plain(text: str) -> str:
    return html.escape(text.replace("*", ""))


class Measure:
    """Row widths and font metrics from the kit's own font files (Pillow reads woff2, woff, ttf and otf),
    so the layout knows where the text ends. Where a file can't be read, a plain estimate stands in."""

    def __init__(self, kit: dict, brand_dir: Path):
        self.kit, self.brand_dir, self.fonts = kit, brand_dir, {}

    def _font(self, role: str, face: str):
        if (role, face) not in self.fonts:
            files = self.kit["fonts"][role]["files"]
            rel = files.get(face) or files.get("regular")
            try:
                self.fonts[(role, face)] = ImageFont.truetype(str(self.brand_dir / rel), 100) if rel else None
            except (OSError, ValueError):
                self.fonts[(role, face)] = None
        return self.fonts[(role, face)]

    def metrics(self, kind: str) -> tuple[float, float] | None:
        """(ascent, descent) of the row's font as fractions of its size."""
        role, face, _, _ = ROW_FONT[kind]
        font = self._font(role, face)
        if font is None:
            return None
        ascent, descent = font.getmetrics()
        return ascent / 100, descent / 100

    def fit(self, kind: str, text: str, px: float, italic: bool = False) -> float:
        """How far the row's font shrinks (1 when it fits) to keep `text` on one line in MAX_ROW px.
        *Starred* words are set in the italic, as the page sets them."""
        role, face, spacing, upper = ROW_FONT[kind]
        width = 0.0
        for k, part in enumerate(re.split(r"\*(.+?)\*", text)):  # odd parts are the starred words
            part = part.upper() if upper else part
            font = self._font(role, "italic" if role == "display" and (italic or k % 2) else face)
            width += (font.getlength(part) / 100 * px if font else 0.6 * px * len(part)) + spacing * px * len(part)
        return min(1.0, MAX_ROW / width) if width > 0 else 1.0

    def caption_width(self, shown: list[tuple[int, str, bool]], px: float) -> float | None:
        """The width of a caption group's words at `px`, as the page sets them (the body bold, key words in
        the display italic at 1.12 times), without the pill's padding; None when a font can't be read."""
        body, key = self._font("body", "bold"), self._font("display", "italic")
        if body is None or key is None:
            return None
        width = body.getlength(" ") / 100 * px * (len(shown) - 1)
        for _, text, is_key in shown:
            if is_key:
                width += key.getlength(text) / 100 * px * 1.12 - 0.02 * px * 1.12 * len(text)
            else:
                width += body.getlength(text) / 100 * px - 0.005 * px * len(text)
        return width


def _sized(fit: float, px: float) -> str:
    return "" if fit >= 1 else f' style="font-size:{px * fit:.1f}px"'


def _offer_rows(lockup: dict, prefix: str, m: Measure, sc: float, h_px: float) -> list[tuple[str, str, str, float]]:
    """The callout, the rule, the offer line(s) and the detail, as (kind, id, html, fit) rows."""
    rows = []
    fit = m.fit("eyebrow", lockup["callout"], 38 * sc)
    rows.append(("eyebrow", f"{prefix}Eye", f'<div class="eyebrow" id="{prefix}Eye"{_sized(fit, 38 * sc)}>'
                 f'{_plain(lockup["callout"])}</div>', fit))
    rows.append(("rule", f"{prefix}Rule", f'<div class="rule" id="{prefix}Rule"></div>', 1.0))
    fit = m.fit("h", lockup["offer"][0], h_px)
    rows.append(("h", f"{prefix}H1", f'<div class="h" id="{prefix}H1"{_sized(fit, h_px)}>{_inline(lockup["offer"][0])}</div>',
                 fit))
    if len(lockup["offer"]) > 1:
        fit = m.fit("h", lockup["offer"][1], h_px, italic=True)
        rows.append(("h", f"{prefix}H2", f'<div class="h" id="{prefix}H2"{_sized(fit, h_px)}><i>{_plain(lockup["offer"][1])}'
                     "</i></div>", fit))
    if lockup.get("detail"):
        fit = m.fit("caps", lockup["detail"], 24 * sc)
        rows.append(("caps", f"{prefix}Caps", f'<div class="caps" id="{prefix}Caps"{_sized(fit, 24 * sc)}>'
                     f'{_plain(lockup["detail"])}</div>', fit))
    return rows


def _font_faces(kit: dict, job: Job, assets: Path) -> tuple[str, bool]:
    """The @font-face rules for both roles (each file linked into assets/fonts), and whether the
    display family has an italic face."""
    rules, seen = [], set()
    for role in ("display", "body"):
        font = kit["fonts"][role]
        for face, rel in font["files"].items():
            if not rel or (font["family"], face) in seen:
                continue
            seen.add((font["family"], face))
            name = Path(rel).name
            link(job.brand / rel, assets / "fonts" / name)
            style, weight = FACES[face]
            rules.append(f'      @font-face {{ font-family: {_family(font["family"])}; src: url("assets/fonts/{name}") '
                         f'format("{brand.font_format(name)}"); font-weight: {weight}; font-style: {style}; }}')
    return "\n".join(rules), bool(kit["fonts"]["display"]["files"].get("italic"))


def _panel_html(win: layout.Window, c: dict, lockup: dict, logo: str | None, alt: str, text: dict, m: Measure,
                blend_logo: bool) -> str:
    """The panel layout's paper panel from the top to the take, the soft blend into the take's top edge,
    and what the panel holds between the lockup and the headings: the logo, small, and the callout. In
    the card layout the paper runs the whole canvas, around the card, with no blend."""
    if win.mode == "fade":
        return ""
    sc, panel = text["sc"], _n(win.panel)
    paper_h = layout.RATIOS[win.ratio] if win.mode == "card" else panel
    fit = m.fit("eyebrow", lockup["callout"], 38 * sc)
    mark = (f'<img id="idleLogo" src="assets/{logo}" alt="{html.escape(alt)}" style="height:{_n(64 * sc)}px;'
            f'max-width:{_n(360 * sc)}px;object-fit:contain;margin-bottom:{_n(18 * sc)}px'
            f'{";mix-blend-mode:multiply" if blend_logo else ""}" />' if logo else "")
    return (f'      <div id="panel" data-layout-allow-occlusion data-layout-allow-overflow style="position:absolute;left:0;'
            f'top:0;width:1080px;height:{paper_h}px;background:{c["paper"]};z-index:2">\n'
            f'        <div id="idle" style="position:absolute;left:0;width:1080px;top:{text["lock"]}px;'
            f'height:{_n(win.panel - layout.PANEL_PAD / 2 - text["lock"])}px;display:flex;flex-direction:column;'
            f'align-items:center;justify-content:center;background:{c["paper"]};opacity:0">\n'
            f'          {mark}<div class="eyebrow" id="idleEye"{_sized(fit, 38 * sc)}>{_plain(lockup["callout"])}</div>\n'
            "        </div>\n      </div>"
            + ("" if win.mode == "card" else
               f'\n      <div id="blend" data-layout-allow-occlusion data-layout-allow-overflow style="position:absolute;'
               f'left:0;top:{panel}px;width:1080px;height:{layout.BLEND}px;'
               f'background:linear-gradient(to bottom, {_rgba(c["paper"], 1)}, {_rgba(c["paper"], 0)});z-index:2"></div>'))


def _idle(blocks: list[tuple[float, float]], card: float) -> list[list[float]]:
    """The gaps between the text blocks (and before the end card) long enough for the panel to show the
    logo and the callout in: [from, to] on the cut's clock."""
    ordered = sorted(blocks)
    ends = [b for _, b in ordered]
    starts = [a for a, _ in ordered[1:]] + [card]
    return [[_n(a), _n(b)] for a, b in zip(ends, starts) if b - a >= IDLE_MIN]


def _shrunk(shrink: float) -> str:
    """The style that scales a panel text block down from its top, when the layout shrank it."""
    return "" if shrink >= 1 else f";transform:scale({shrink:g});transform-origin:50% 0"


def _lockup_html(rows: list[tuple[str, str, str, float]], top: int, end: float, shrink: float = 1.0) -> tuple[str, list[str]]:
    items = [f"#{i}" for _, i, _, _ in rows]
    rows = [f"          {row}" for _, _, row, _ in rows]
    block = (f'      <div id="lock" class="clip" data-start="0" data-duration="{_n(end)}" data-track-index="30"\n'
             '        data-layout-allow-overflow style="position:absolute;inset:0;z-index:30">\n'
             '        <div class="paper" id="lockPaper"></div>\n'
             f'        <div class="head" id="lockHead" style="top:{top}px{_shrunk(shrink)}">\n' + "\n".join(rows) + "\n"
             "        </div>\n      </div>")
    return block, items


def _heading_rows(n: int, h: dict, m: Measure, sc: float) -> list[tuple[str, str, str, float]]:
    rows = []
    for j, line in enumerate(h["lines"]):
        fit = m.fit("h", line, 58 * sc)
        rows.append(("h", f"h{n}l{j}", f'<div class="h" id="h{n}l{j}"{_sized(fit, 58 * sc)}>{_inline(line)}</div>', fit))
    return rows


def _heading_html(n: int, h: dict, rows: list[tuple[str, str, str, float]], top: int,
                  shrink: float = 1.0) -> tuple[str, list[str]]:
    ids = [f"#{i}" for _, i, _, _ in rows]
    rows = "\n".join(f"          {row}" for _, _, row, _ in rows)
    block = (f'      <div id="h{n}" class="clip" data-start="{_n(h["start"])}" data-duration="{_n(h["end"] - h["start"])}" '
             f'data-track-index="{30 + n}"\n'
             '        data-layout-allow-overflow style="position:absolute;inset:0;z-index:30">\n'
             f'        <div class="paper" id="h{n}Paper"></div>\n'
             f'        <div class="head" id="h{n}Head" style="top:{top}px{_shrunk(shrink)}">\n{rows}\n        </div>\n      </div>')
    return block, ids


def _end_html(lockup: dict, offer_rows: list[tuple[str, str, str, float]], logo: str | None, alt: str, top,
              start: float, length: float) -> tuple[str, list[str]]:
    items, rows = [], []
    if logo:
        items.append("#endLogo")
        rows.append(f'          <img id="endLogo" src="assets/{logo}" alt="{html.escape(alt)}" />')
    items += [f"#{i}" for _, i, _, _ in offer_rows]
    rows += [f"          {row}" for _, _, row, _ in offer_rows]
    items.append("#endBtn")
    rows.append(f'          <div class="btn" id="endBtn">{_plain(lockup["button"])}</div>')
    head = (f'<div class="head" id="endHead" style="top:{top}px">' if isinstance(top, (int, float))
            else '<div class="head centred" id="endHead">')
    block = (f'      <div id="end" class="clip" data-start="{_n(start)}" data-duration="{_n(length)}" data-track-index="35"\n'
             '        data-layout-allow-overflow>\n        <div class="frame"></div>\n'
             f'        {head}\n' + "\n".join(rows) + "\n        </div>\n      </div>")
    return block, items


def build(job: Job, ratio: str) -> Path:
    """Write work/pages/<ratio>/index.html (and page.json, the facts the render and the checks read)
    with its assets linked beside it. Returns the index.html path."""
    if ratio not in layout.RATIOS:
        raise ValueError(f"A ratio is one of {', '.join(layout.RATIOS)}, and {ratio} isn't.")
    kit = brand.load(job.client)
    resolved = plan.load(job)
    cut = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))
    face_map = faces.filled(faces.load(job.work / "faces.json")[1])
    spd = speed.current(job)
    take = job.work / "aroll.mp4"
    probe = media.probe(take)
    text = layout.TEXT[ratio]
    H = layout.RATIOS[ratio]
    sc = text["sc"]

    # The text rows, each kept on one line, and where the lowest line of the lockup and the headings sits:
    # the layout keeps the head clear of it.
    m = Measure(kit, job.brand)
    headings = resolved["headings"]
    lock_rows = _offer_rows(resolved["lockup"], "lock", m, sc, 58 * sc)
    heading_rows = [_heading_rows(n, h, m, sc) for n, h in enumerate(headings, 1)]
    metrics = {kind: m.metrics(kind) for kind in ROW_FONT}
    blocks = ([(text["lock"], layout.text_bottom(text["lock"], [(k, f) for k, _, _, f in lock_rows], sc, metrics))]
              + [(text["heading"], layout.text_bottom(text["heading"], [(k, f) for k, _, _, f in rows], sc, metrics))
                 for rows in heading_rows])
    win = layout.window(probe.width, probe.height, face_map, ratio, blocks)
    lowest = layout.lowest(blocks, win.shrink)
    box = win.box

    words = cut["words"]
    speech = _n(cut["duration"])
    end0 = plan.card_start(cut)
    duration = _n(end0 + resolved["end_card"])

    out = job.work / "pages" / ratio
    assets = out / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    assets.mkdir(parents=True)
    for name in ("hyperframes.json", "package.json"):
        shutil.copyfile(TEMPLATE / name, out / name)
    link(take, assets / "aroll.mp4")

    # B-roll, staged once at its exact length and linked into this ratio's assets.
    broll = []
    for k, b in enumerate(resolved["broll"]):
        window = b["end"] - b["start"]
        name = f"{b['clip']}-{b['in']:.2f}-{window:.2f}.mp4"
        staged = stage(b["file"], b["in"], window, job.work / "staged" / name)
        link(staged, assets / "broll" / name)
        have = media.probe(staged).duration
        broll.append({"k": k, "file": f"assets/broll/{name}", "clip": b["clip"], "in": b["in"], "start": _n(b["start"]),
                      "end": _n(b["end"]), "dur": _n(min(window, have - STAGE_TAIL)), "y": b["y"]})

    warnings = ([win.warn] if win.warn else []) + layout.face_clear(win, face_map, ratio)
    lock_end = min([b["start"] for b in broll] + [h["start"] for h in headings] + [end0])
    # Punch-ins keep the head clear of the text while it shows.
    text_times = [(0.0, lock_end)] + [(h["start"], h["end"]) for h in headings]
    states = layout.states(cut["segments"], face_map, win, spd, text_times)
    early = [n for n, h in enumerate(headings, 1) if h["start"] == lock_end]
    if early and lock_end < plan.MIN_LOCKUP:
        warnings.append(f"The lockup shows for only {lock_end:.1f} s because heading {early[0]} starts then. Start it at "
                        f"{plan.MIN_LOCKUP} s or later so the offer reads first.")

    # Captions: grouped, timed and placed as the first ad's were.
    covers = [(b["start"], b["end"]) for b in broll] + [(end0, duration)]
    warnings += layout.face_out(win, face_map, states, spd, skip=covers)
    key = key_indices(words, resolved["key_words"])
    cap = text["cap"]
    grouped = groups(words)
    caps = []
    for gi, g in enumerate(grouped):
        a = max(CAP_FIRST, words[g[0]]["start"] - CAP_IN)
        nxt = words[grouped[gi + 1][0]]["start"] - CAP_IN if gi + 1 < len(grouped) else speech
        b = max(min(nxt, words[g[-1]]["end"] + CAP_HOLD), a + 0.04)
        shown = [(i, show(words[i]["text"]), i in key) for i in g]
        est = (sum(len(t) * (0.6 * cap * 1.12 if k else 0.56 * cap) for _, t, k in shown)
               + 0.28 * cap * (len(shown) - 1))
        scale = min(1.0, CAP_WIDTH / max(est, 1))
        # the pill's width at full size: the wider of the estimate and the kit's own fonts, plus its padding
        pill = max(est, m.caption_width(shown, cap) or 0.0) + 2 * CAP_PAD * cap
        if win.mode == "card":  # a caption stays inside the card, CARD_CAP_GAP from its sides
            scale = min(scale, (box[2] - 2 * CARD_CAP_GAP) / pill)
        covered = any(s <= a + COVER_IN and b <= e + COVER_OUT for s, e in covers)
        top = layout.caption_top(a, b, face_map, win, states, covered, speed=spd)
        caps.append({"a": _n(a), "b": _n(b), "top": top, "size": round(cap * scale, 1), "width": round(pill * scale, 1),
                     "words": shown, "covered": covered})
    if win.mode == "card" and any(cp["top"] + cp["size"] * layout.CAPTION_BOX > box[1] + box[3] + 0.5
                                  for cp in caps if not cp["covered"]):
        warnings.append(f"In {ratio.replace('x', ':')} some captions run past the bottom of the picture. A take with "
                        "the camera a little further back fixes it.")

    # The page.
    font_faces, has_italic = _font_faces(kit, job, assets)
    logo = None
    if kit.get("logo") and (job.brand / kit["logo"]).is_file():
        logo = Path(kit["logo"]).name
        link(job.brand / kit["logo"], assets / logo)
    c = kit["colours"]
    paper_h = text["paper_h"]
    paper_fade = (f"linear-gradient(to bottom, {_rgba(c['paper'], .97)} 0px, {_rgba(c['paper'], .93)} {int(paper_h * .46)}px, "
                  f"{_rgba(c['paper'], .55)} {int(paper_h * .72)}px, {_rgba(c['paper'], 0)} {int(paper_h * .97)}px)")
    if win.mode != "fade":
        paper_fade = "none"  # the panel is the paper; nothing fades over the take
    alt = f"{kit.get('name', '')} logo"
    lockup_block, lockup_items = _lockup_html(lock_rows, text["lock"], lock_end, win.shrink)
    heading_blocks = [_heading_html(n, h, rows, text["heading"], win.shrink)
                      for n, (h, rows) in enumerate(zip(headings, heading_rows), 1)]
    end_block, end_items = _end_html(resolved["lockup"], _offer_rows(resolved["lockup"], "end", m, sc, 66 * sc), logo, alt,
                                     text["end_top"], end0, resolved["end_card"])
    idle = _idle(text_times, end0) if win.mode != "fade" else []
    broll_html = "\n".join(
        f'      <div id="bw{b["k"]}" class="bw" data-layout-allow-occlusion data-layout-allow-overflow>'
        f'<video id="b{b["k"]}" src="{b["file"]}" muted playsinline data-start="{b["start"]}" data-duration="{b["dur"]}" '
        f'data-media-start="0" data-track-index="{10 + b["k"] % 2}" data-has-audio="false" data-volume="0" '
        f'style="object-position:50% {b["y"]}%" data-layout-allow-occlusion data-layout-allow-overflow></video></div>'
        for b in broll)
    caption_html = "\n".join(
        f'      <div id="cap{gi}" class="clip cap" data-start="{cp["a"]}" data-duration="{_n(cp["b"] - cp["a"])}" '
        f'data-track-index="{40 + gi % 2}" style="top:{cp["top"]}px"><span class="pill" id="pill{gi}" '
        f'style="font-size:{cp["size"]}px">'
        + " ".join(f'<span id="w{i}" class="{"w k" if k else "w"}">{html.escape(t)}</span>' for i, t, k in cp["words"])
        + "</span></div>"
        for gi, cp in enumerate(caps))
    data = {
        "duration": duration,
        "states": [[_n(t), s, tx, ty] for t, s, tx, ty in states],
        "broll": [[b["k"], b["start"], b["dur"]] for b in broll],
        "lockup": {"paper": "#lockPaper", "items": lockup_items, "start": 0, "end": lock_end, "enter": False},
        "headings": [{"paper": f"#h{n}Paper", "items": ids, "start": _n(h["start"]), "end": _n(h["end"]), "enter": True}
                     for n, (h, (_, ids)) in enumerate(zip(headings, heading_blocks), 1)],
        "end": {"start": end0, "items": end_items},
        "caps": [[gi, cp["a"], [[i, _n(words[i]["start"]), _n(words[i]["end"]), k] for i, _, k in cp["words"]]]
                 for gi, cp in enumerate(caps)],
        "colours": {"highlight": c["highlight"], "paper": c["paper"]},
        "idle": idle,
    }
    synthetic = ("" if has_italic else
                 "      /* The display family has no italic face, so the browser slants the regular one. */\n"
                 "      .eyebrow, .h i, .w.k { font-synthesis: style; }")
    page = string.Template((TEMPLATE / "page.html").read_text(encoding="utf-8")).substitute(
        height=H, sc=text["sc"], font_faces=font_faces, synthetic=synthetic,
        primary=c["primary"], deep=c["deep"], paper=c["paper"], accent=c["accent"], accent_dark=c["accent_dark"],
        highlight=c["highlight"], ink=c["ink"], plate=_rgba(c["deep"], .88), line=_rgba(c["accent"], .55),
        logo_blend=" mix-blend-mode: multiply;" if _light(c["paper"]) else "",
        display=_family(kit["fonts"]["display"]["family"]), body=_family(kit["fonts"]["body"]["family"]),
        take_left=float(round(box[0], 3)), take_top=float(round(box[1], 3)), take_w=float(round(box[2], 3)),
        take_h=float(round(box[3], 3)), take_z=3 if win.mode == "card" else 1,
        take_style=(f" border-radius: {layout.CARD_RADIUS}px; box-shadow: 0 22px 56px rgba(0,0,0,.2), "
                    "0 6px 16px rgba(0,0,0,.12);" if win.mode == "card" else ""),
        win_left=round(-win.x0, 3) + 0.0, win_top=round(-win.y0, 3) + 0.0, src_w=round(win.src_w, 3),
        src_h=round(win.src_h, 3), panel=_panel_html(win, c, resolved["lockup"], logo, alt, text, m, _light(c["paper"])),
        paper_h=paper_h, paper_fade=paper_fade, duration=duration, aroll_duration=_n(speech - AROLL_TAIL),
        broll=broll_html, lockup=lockup_block, headings="\n".join(b for b, _ in heading_blocks), end_card=end_block,
        captions=caption_html, data=json.dumps(data).replace("</", "<\\/"),
    )
    index = out / "index.html"
    index.write_text(page, encoding="utf-8")
    facts = {
        "ratio": ratio, "height": H, "speech": speech, "duration": duration, "end": [end0, duration],
        "window": {"scale": win.scale, "x0": win.x0, "y0": win.y0, "mode": win.mode, "panel": win.panel,
                   "head_gap": win.head_gap, "shrink": win.shrink, "punch": win.punch,
                   "frame": [float(round(v, 3)) for v in box], "head_limit": round(win.head_limit, 2)
                   if win.head_limit != float("-inf") else None,
                   "src_w": round(win.src_w, 3), "src_h": round(win.src_h, 3)},
        "text_bottom": round(lowest, 2), "mouth_covered": layout.mouth_covered(win, face_map),
        "warnings": warnings, "lockup": [0.0, lock_end],
        "headings": [[_n(h["start"]), _n(h["end"])] for h in headings],
        "broll": broll, "captions": [{k: v for k, v in cp.items() if k not in ("words", "covered")} for cp in caps],
        "states": len(states),
    }
    (out / "page.json").write_text(json.dumps(facts, indent=1), encoding="utf-8")
    return index
