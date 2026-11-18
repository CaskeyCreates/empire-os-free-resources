"""A client's brand kit, read from their live site: their colours, fonts and logo, never ours.

`build` reads the site's HTML and every stylesheet it links and finds:

- colours: the CSS custom properties named for the brand, the page's theme-color, then the
  saturated colours by how often the CSS uses them. The seven roles are derived from those.
- fonts: the family on their headings (display) and on their body text. A family Google Fonts
  serves is downloaded as woff2 from Google; any other family is matched to a Google family of
  the same class. A font file is never downloaded from the client's own site.
- logo: images and inline SVGs whose class, id, alt or file name says logo.

The kit is never used until the person approves it, and any change clears the approval.

Ported from a tested site reader (the fetch, the colour and font finders, the logo finder)."""
import argparse
import colorsys
import html
import html.parser
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from adeditor import paths
from adeditor.cli import command

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
MAX_BYTES = 4 << 20
MAX_SHEETS = 24
SLUG = re.compile(r"^[a-z0-9-]+$")
GENERIC_FONTS = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif",
                 "ui-serif", "ui-monospace", "-apple-system", "blinkmacsystemfont", "segoe ui", "roboto", "helvetica",
                 "helvetica neue", "arial", "sans", "inherit", "initial", "unset", "apple color emoji",
                 "segoe ui emoji", "segoe ui symbol", "noto color emoji", "emoji", "math", "var"}
# A family Google doesn't serve is matched by its generic class.
MATCH = {"serif": "Libre Baskerville", "sans-serif": "Inter", "display": "DM Serif Display",
         "monospace": "JetBrains Mono"}
CLASS_OF = {"serif": "serif", "sans-serif": "sans-serif", "monospace": "monospace",
            "cursive": "display", "fantasy": "display"}
FACES = {"regular": ("normal", 400), "italic": ("italic", 400), "bold": ("normal", 700)}
# A font file's format by its extension, for @font-face: Google's woff2, or the person's own licensed files.
FONT_FORMATS = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype"}
DEEP_MAX_LUMINANCE = 0.06
MIN_CONTRAST = 4.5
DEFAULT_PAPER = "#F7F5EF"
DEFAULT_INK = "#1A1A1A"


def font_format(name) -> str:
    """The @font-face format() for a font file, by its extension (woff2, woff, ttf or otf)."""
    suffix = Path(str(name)).suffix.lower()
    if suffix not in FONT_FORMATS:
        raise ValueError(f"{Path(str(name)).name} isn't a font file we can use: give a .woff2, .woff, .ttf or .otf file.")
    return FONT_FORMATS[suffix]


class NotApproved(Exception):
    def __init__(self, client: str):
        super().__init__(
            f"{client}'s brand kit isn't approved yet. Look at brand/swatch.html, then run: adeditor client approve {client}")


# kit files

def check_slug(client: str) -> str:
    if not SLUG.match(client or ""):
        raise ValueError("A client name is its folder name: lower-case letters, digits and hyphens only, like acme-physio.")
    return client


def brand_dir(client: str) -> Path:
    return paths.ROOT / "clients" / check_slug(client) / "brand"


def kit_path(client: str) -> Path:
    return brand_dir(client) / "kit.json"


class NoKit(ValueError):
    """There's no kit.json for the client yet."""


class BadKit(ValueError):
    """kit.json is there but can't be read. Never answered with `client new`, which would throw away
    the lockup, the key terms and the music the person set."""


def parse_kit(path: Path, client: str) -> dict:
    """A kit.json read, or NoKit when it isn't there, or BadKit (one plain sentence) when it's broken."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise NoKit(f"There's no brand kit for {client} yet. Run: adeditor client new {client} <their site>") from None
    where = f"clients/{client}/brand/kit.json"
    try:
        kit = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BadKit(f"{where} isn't valid JSON ({exc.msg} on line {exc.lineno}): fix that line, then run "
                     "the step again.") from None
    if not isinstance(kit, dict):
        raise BadKit(f"{where} isn't valid as a brand kit (it should be one JSON object): fix it, then run the step again.")
    return kit


def read(client: str) -> dict:
    return parse_kit(kit_path(client), client)


def save(client: str, kit: dict) -> dict:
    folder = brand_dir(client)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = kit_path(client).with_suffix(".tmp")
    tmp.write_text(json.dumps(kit, indent=2) + "\n", encoding="utf-8")
    tmp.replace(kit_path(client))
    return kit


def load(client: str) -> dict:
    """The approved kit. Nothing renders in a brand the person hasn't approved."""
    kit = read(client)
    if not kit.get("approved"):
        raise NotApproved(client)
    return kit


def approve(client: str) -> dict:
    kit = read(client)
    kit["approved"] = True
    return save(client, kit)


def set_lockup(client: str, callout: str, offer: list[str], detail: str | None, button: str = "Learn more") -> dict:
    """The offer lockup every ad carries. Any change clears the approval."""
    kit = read(client)
    offer = [line.strip() for line in offer if line and line.strip()][:2]
    if not callout.strip() or not offer:
        raise ValueError("A lockup needs a callout and at least one offer line.")
    kit["lockup"] = {"callout": callout.strip(), "offer": offer, "detail": (detail or "").strip() or None,
                     "button": (button or "Learn more").strip()}
    kit["approved"] = False
    return save(client, kit)


# reading their site

def http_get(url: str, accept: str = "*/*") -> tuple[bytes, str, str]:
    """(body, content type, final url). Only http and https, at most MAX_BYTES."""
    if not re.match(r"^https?://", url):
        raise ValueError(f"Not a web address: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError(f"{url} is bigger than {MAX_BYTES >> 20} MB")
        return body, r.headers.get("Content-Type", ""), r.geturl()


class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sheets, self.styles, self.inline = [], [], []
        self.images, self.icons, self.svgs = [], [], []
        self.meta = {}
        self.title = ""
        self._style = False
        self._title = False
        self._svg_depth = 0
        self._svg = None
        self._in_header = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("header", "nav"):
            self._in_header += 1
        if tag == "link":
            rel = a.get("rel", "").lower()
            if "stylesheet" in rel and a.get("href"):
                self.sheets.append(a["href"])
            if "icon" in rel and a.get("href"):
                self.icons.append({"src": a["href"], "rel": rel})
        elif tag == "style":
            self._style = True
        elif tag == "title":
            self._title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"]
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            text = " ".join([a.get("alt", ""), a.get("class", ""), a.get("id", ""), src]).lower()
            if src:
                self.images.append({"src": src, "logo": "logo" in text, "header": self._in_header > 0})
        if tag == "svg" and self._svg is None:
            text = " ".join([a.get("class", ""), a.get("id", ""), a.get("aria-label", "")]).lower()
            self._svg = {"logo": "logo" in text, "header": self._in_header > 0, "parts": [self.get_starttag_text()]}
            self._svg_depth = 1
        elif self._svg is not None:
            self._svg["parts"].append(self.get_starttag_text())
            self._svg_depth += 1
        if a.get("style"):
            self.inline.append(a["style"])

    def handle_endtag(self, tag):
        if tag in ("header", "nav") and self._in_header:
            self._in_header -= 1
        if tag == "style":
            self._style = False
        if tag == "title":
            self._title = False
        if self._svg is not None:
            self._svg["parts"].append(f"</{tag}>")
            self._svg_depth -= 1
            if self._svg_depth <= 0:
                svg = "".join(self._svg["parts"])
                if "xmlns=" not in svg[:400]:
                    svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                self.svgs.append({"svg": svg, "logo": self._svg["logo"], "header": self._svg["header"]})
                self._svg = None

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self._svg is not None and tag != "svg":
            self._svg_depth -= 1
            self._svg["parts"].append("")

    def handle_data(self, data):
        if self._style:
            self.styles.append(data)
        elif self._title:
            self.title += data
        elif self._svg is not None:
            self._svg["parts"].append(data)


HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
RGB = re.compile(r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})")
VAR = re.compile(r"(--[\w-]+)\s*:\s*([^;}{]+)")
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
IMPORT = re.compile(r"@import\s+(?:url\()?\s*['\"]?([^'\")\s;]+)", re.I)


def norm_hex(text: str) -> str | None:
    text = text.strip()
    m = HEX.fullmatch(text) if text.startswith("#") else None
    if m:
        h = m.group(1).lower()
        return "#" + ("".join(c * 2 for c in h) if len(h) == 3 else h)
    m = RGB.match(text)
    if m:
        r, g, b = (min(255, int(x)) for x in m.groups())
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def colours_in(css: str) -> list[str]:
    out = [norm_hex(m.group(0)) for m in HEX.finditer(css)]
    out += [norm_hex(m.group(0)) for m in RGB.finditer(css)]
    return [c for c in out if c]


def hls(c: str) -> tuple[float, float, float]:
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)


def shade(c: str, light: float) -> str:
    h, l, s = hls(c)
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l + light)), s)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def rel_luminance(c: str) -> float:
    """WCAG relative luminance."""
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two #rrggbb colours."""
    hi, lo = sorted((rel_luminance(a), rel_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def darken_to(c: str, max_luminance: float) -> str:
    while rel_luminance(c) > max_luminance and hls(c)[1] > 0.0:
        c = shade(c, -0.01)
    return c


def darken_until(on: str, c: str, ratio: float) -> str:
    """Darken `c` until it reads at `ratio` or better on `on`."""
    while contrast(c, on) < ratio and hls(c)[1] > 0.0:
        c = shade(c, -0.02)
    return c


def lighten_until(on: str, c: str, ratio: float) -> str:
    """Lighten `c` until it reads at `ratio` or better on `on`."""
    while contrast(c, on) < ratio and hls(c)[1] < 1.0:
        c = shade(c, 0.02)
    return c


def strip_comments(css: str) -> str:
    """CSS without /* comments */, so a comment never reads as a selector or a colour."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def pick_colours(css: str, meta: dict) -> dict:
    """The brand's primary, a second brand colour if the site has one, and its everyday colours."""
    css = strip_comments(css)
    counts = Counter(colours_in(css))
    named = []
    for name, value in VAR.findall(css):
        c = norm_hex(value)
        if c and re.search(r"brand|primary|accent|main|theme", name, re.I) and hls(c)[2] > 0.25:
            named.append((name.lower(), c))
    saturated = [c for c, _ in counts.most_common() if hls(c)[2] > 0.35 and 0.18 < hls(c)[1] < 0.82]
    theme = norm_hex(meta.get("theme-color", "")) if meta.get("theme-color") else None
    theme = theme if theme and hls(theme)[2] > 0.25 else None
    firsts = [c for n, c in named if "primary" in n] + [c for n, c in named if "accent" not in n] + ([theme] if theme else [])
    primary = (firsts + saturated + [None])[0]
    accents = [c for n, c in named if "accent" in n] + saturated
    accent = next((c for c in accents if primary and c != primary and abs(hls(c)[0] - hls(primary)[0]) > 0.04), None)
    base = []
    for selector, block in RULE.findall(css):
        if re.search(r"(^|[\s,])(body|html)\b|:root", selector.lower()):
            base += colours_in(block)
    everyday = base + [c for c, _ in counts.most_common(40)]
    return {"primary": primary, "accent": accent, "everyday": everyday}


def derive_roles(css: str, meta: dict) -> dict:
    """The seven roles: primary is headings and the button, deep the background and caption plate, paper
    the light cards and caption text, accent the rules, accent_dark the eyebrows and italic accent,
    highlight the spoken word, ink the body text on paper."""
    found = pick_colours(css, meta)
    everyday = found["everyday"]
    darks = [c for c in everyday if rel_luminance(c) < 0.2]
    primary = found["primary"] or (darks[0] if darks else "#1F2937")
    lights = [c for c in everyday if rel_luminance(c) > 0.8]
    paper = max(lights, key=rel_luminance) if lights else DEFAULT_PAPER
    accent = found["accent"] or shade(primary, 0.15)
    inks = [c for c in everyday if contrast(c, paper) >= MIN_CONTRAST and rel_luminance(c) < 0.2]
    ink = inks[0] if inks else DEFAULT_INK
    return complete_roles(primary, accent, paper, ink)


def complete_roles(primary: str, accent: str, paper: str, ink: str) -> dict:
    """The seven roles from the four a person can name. deep is the primary darkened into a background,
    accent_dark the accent darkened until it reads on paper, highlight the accent lightened until it
    reads on deep. Reading the site and setting colours by hand both come here."""
    deep = darken_to(primary, DEEP_MAX_LUMINANCE)
    accent_dark = darken_until(paper, shade(accent, -0.08), MIN_CONTRAST)
    highlight = lighten_until(deep, accent, MIN_CONTRAST)
    roles = {"primary": primary, "deep": deep, "paper": paper, "accent": accent, "accent_dark": accent_dark,
             "highlight": highlight, "ink": ink}
    return {k: v.upper() for k, v in roles.items()}


def parse_hex(text) -> str | None:
    """#RRGGBB from a hex colour as a person types it (#1a1a1a, 1A1A1A or #fff), or None."""
    t = str(text or "").strip()
    t = t if t.startswith("#") else "#" + t
    return norm_hex(t).upper() if HEX.fullmatch(t) else None


# What the captions need: (role, what it is on the page, its verbs, what to change when it doesn't read on deep).
CAPTION_PAIRS = (("paper", "caption words", ("read", "need"), "a lighter paper or a darker primary"),
                 ("highlight", "word being said", ("reads", "needs"), "a lighter accent or a darker primary"))


def caption_problems(colours: dict) -> list[str]:
    """One plain sentence per caption colour that doesn't read on the caption plate (deep)."""
    out = []
    for role, what, (reads, needs), fix in CAPTION_PAIRS:
        ratio = contrast(colours[role], colours["deep"])
        if ratio < MIN_CONTRAST:
            out.append(f"The {what} ({role} {colours[role]}) on the caption plate (deep {colours['deep']}, made from "
                       f"the primary) {reads} at {ratio:.1f} to 1 and {needs} at least {MIN_CONTRAST} to 1: give {fix}.")
    return out


def set_colours(client: str, primary=None, accent=None, paper=None, ink=None) -> dict:
    """Set the colours a person gives, work out the rest by complete_roles, check the captions read,
    then save the kit unapproved and rewrite the swatch. Nothing is saved when a check fails."""
    given = {role: text for role, text in (("primary", primary), ("accent", accent), ("paper", paper), ("ink", ink))
             if text is not None}
    if not given:
        raise ValueError("Give at least one colour to set, as a hex code like #1A1A1A: the primary, the accent, the "
                         "paper or the ink.")
    kit = read(client)
    base = {role: parse_hex(c) for role, c in (kit.get("colours") or {}).items()}
    for role, text in given.items():
        base[role] = parse_hex(text)
        if not base[role]:
            raise ValueError(f'The {role} colour "{text}" isn\'t a hex colour we can read. Give it like #1A1A1A.')
    for role in ("primary", "accent", "paper", "ink"):
        if not base.get(role):
            raise ValueError(f"kit.json has no {role} colour to keep, so give one too, like #1A1A1A.")
    colours = complete_roles(base["primary"], base["accent"], base["paper"], base["ink"])
    problems = caption_problems(colours)
    if problems:
        raise ValueError(" ".join(problems) + " Nothing was changed.")
    kit["colours"] = colours
    kit["approved"] = False
    save(client, kit)
    write_swatch(client, kit)
    return kit


def family_of(value: str) -> str | None:
    for part in value.split(","):
        name = part.strip().strip("'\"").strip()
        if name and name.lower() not in GENERIC_FONTS and not name.startswith("var("):
            return name
    return None


def font_roles(css: str) -> tuple[str | None, str | None, Counter, dict]:
    """(display family, body family, every family by use, each family's generic class)."""
    css = strip_comments(css)
    used, heads, body, classes = Counter(), Counter(), Counter(), {}
    for selector, block in RULE.findall(css):
        m = re.search(r"font-family\s*:\s*([^;]+)", block, re.I)
        if not m or selector.strip().startswith("@"):
            continue
        fam = family_of(m.group(1))
        if not fam:
            continue
        used[fam] += 1
        for part in m.group(1).split(","):
            kind = CLASS_OF.get(part.strip().strip("'\"").lower())
            if kind:
                classes.setdefault(fam, kind)
        sel = selector.lower()
        if re.search(r"\bh[1-3]\b|heading|title|display|hero", sel):
            heads[fam] += 1
        if re.search(r"(^|[\s,])(body|html|p)\b|:root", sel):
            body[fam] += 1
    display = heads.most_common(1)[0][0] if heads else (used.most_common(1)[0][0] if used else None)
    body_fam = body.most_common(1)[0][0] if body else (used.most_common(1)[0][0] if used else None)
    return display, body_fam, used, classes


# Google Fonts: https://developers.google.com/fonts/docs/css2
# Axes are listed alphabetically (ital before wght). One request per face, because a family with
# no italic or no bold answers the whole request with an error, and we want the faces it has.
CSS2 = "https://fonts.googleapis.com/css2?family="
FONT_FACE = re.compile(r"(?:/\*\s*([\w-]+)\s*\*/\s*)?@font-face\s*\{([^}]*)\}", re.I)


def css2_url(family: str, face: str) -> str:
    name = urllib.parse.quote(family).replace("%20", "+")
    style, weight = FACES[face]
    axes = f":ital,wght@1,{weight}" if style == "italic" else f":wght@{weight}"
    return CSS2 + name + axes


def google_face(family: str, face: str, fetch) -> str | None:
    """The woff2 url of one face of a Google family (its latin subset), or None when Google has no such face."""
    try:
        body, _, final = fetch(css2_url(family, face), "text/css")
    except Exception:
        return None
    options = []
    for subset, block in FONT_FACE.findall(body.decode("utf-8", "replace")):
        fam = re.search(r"font-family\s*:\s*['\"]?([^;'\"]+)", block, re.I)
        src = re.search(r"url\(\s*['\"]?([^'\")]+\.woff2)['\"]?\s*\)", block, re.I)
        if fam and src and fam.group(1).strip().lower() == family.lower():
            options.append((subset, urllib.parse.urljoin(final, src.group(1))))
    return next((u for s, u in options if s == "latin"), options[0][1] if options else None)


OFL_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/{}/OFL.txt"


def save_licence(family: str, fetch, out: Path) -> None:
    """Save the family's OFL.txt beside its fonts (the licence asks every copy to carry it)."""
    slug = re.sub(r"[^a-z0-9]", "", family.lower())
    try:
        text = fetch(OFL_URL.format(slug), "text/plain")[0]
        (out / "fonts" / f"OFL-{slug}.txt").write_bytes(text)
    except Exception:
        print(f"Note: {family} is under its own open licence (the SIL Open Font License); "
              f"see https://github.com/google/fonts/tree/main/ofl/{slug}", file=sys.stderr)


def get_font(family: str, fetch, out: Path, cache: dict) -> dict | None:
    """Download a Google family's regular, italic and bold into `out`/fonts. None when Google doesn't
    serve the family at all."""
    if family in cache:
        return cache[family]
    urls = {face: google_face(family, face, fetch) for face in FACES}
    if not urls["regular"]:
        cache[family] = None
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")
    files = {}
    for face, url in urls.items():
        if not url:
            files[face] = None
            continue
        name = f"fonts/{slug}-{face}.woff2"
        (out / "fonts").mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(fetch(url, "*/*")[0])
        files[face] = name
    save_licence(family, fetch, out)
    missing = [f for f in ("italic", "bold") if not files[f]]
    note = None
    if missing:
        note = f"{family} has no {' or '.join(missing)} face on Google Fonts, so the page will fall back for it."
    cache[family] = {"files": files, "note": note}
    return cache[family]


def choose_font(family: str | None, klass: str | None, fetch, out: Path, cache: dict) -> dict:
    """The kit entry for one role. A Google family is used as it is; any other is matched by class."""
    got = get_font(family, fetch, out, cache) if family else None
    if got:
        return {"family": family, "files": got["files"], "source": "google", "matched_from": None, "note": got["note"]}
    target = MATCH[klass or "sans-serif"]
    got = get_font(target, fetch, out, cache)
    if not got:
        raise ValueError(f"Couldn't download {target} from Google Fonts. Check your connection and try again.")
    return {"family": target, "files": got["files"], "source": "matched", "matched_from": family, "note": got["note"]}


# logo

def logo_ext(url: str, kind: str) -> str:
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if ext in (".svg", ".png"):
        return ext
    return {"image/svg+xml": ".svg", "image/png": ".png"}.get(kind.split(";")[0].strip(), "")


def find_logo(page: Page, base: str, fetch) -> tuple[bytes, str] | None:
    """The best logo (png or svg) the page offers: its bytes and extension."""
    found = []  # (score, bytes, ext)
    for svg in page.svgs:
        if svg["logo"]:
            found.append((3 if svg["header"] else 2, svg["svg"].encode(), ".svg"))
    for img in page.images[:30]:
        if not (img["logo"] or (img["header"] and img["src"].lower().split("?")[0].endswith(".svg"))):
            continue
        url = urllib.parse.urljoin(base, img["src"])
        try:
            data, kind, _ = fetch(url, "image/*")
        except Exception:
            continue
        ext = logo_ext(url, kind)
        if ext and data and len(data) <= MAX_BYTES:
            found.append((3 if img["header"] and img["logo"] else 2, data, ext))
    if not found:
        for icon in sorted(page.icons, key=lambda i: "apple-touch" not in i["rel"]):
            url = urllib.parse.urljoin(base, icon["src"])
            if "default-favicon" in url:
                continue  # a platform's stock icon is not their logo
            try:
                data, kind, _ = fetch(url, "image/*")
            except Exception:
                continue
            if logo_ext(url, kind) == ".png" and data:
                found.append((1, data, ".png"))
                break
    if not found:
        return None
    best = max(found, key=lambda f: f[0])
    return best[1], best[2]


# the kit

def build(client: str, url: str, name: str | None = None, fetch=None) -> dict:
    """Read the client's site and write brand/kit.json and brand/swatch.html. The kit is unapproved."""
    check_slug(client)
    fetch = fetch or http_get
    if not re.match(r"^https?://", url or ""):
        url = "https://" + str(url or "").strip().lstrip("/")
    body, _, final = fetch(url, "text/html")
    out = brand_dir(client)
    out.mkdir(parents=True, exist_ok=True)
    page = Page()
    page.feed(body.decode("utf-8", "replace"))
    css_parts = list(page.styles) + [f"x{{{s}}}" for s in page.inline]
    queue = [urllib.parse.urljoin(final, h) for h in page.sheets][:MAX_SHEETS]
    seen = set()
    while queue and len(seen) < MAX_SHEETS:
        sheet = queue.pop(0)
        if sheet in seen:
            continue
        seen.add(sheet)
        try:
            text = fetch(sheet, "text/css")[0].decode("utf-8", "replace")
        except Exception:
            continue
        css_parts.append(text)
        queue += [urllib.parse.urljoin(sheet, i) for i in IMPORT.findall(text)]
    css = "\n".join(css_parts)

    colours = derive_roles(css, page.meta)
    display, body_family, _, classes = font_roles(css)
    cache: dict = {}
    head = display or body_family
    fonts = {
        "display": choose_font(head, classes.get(head), fetch, out, cache),
        "body": choose_font(body_family, classes.get(body_family), fetch, out, cache),
    }
    logo = None
    got = find_logo(page, final, fetch)
    if got:
        logo = "logo" + got[1]
        for stale in out.glob("logo.*"):
            stale.unlink()
        (out / logo).write_bytes(got[0])

    host = urllib.parse.urlparse(final).hostname or client
    title = " ".join((name or page.meta.get("og:site_name") or page.title or host.removeprefix("www.")).split())[:80]
    kit = {
        "name": title, "site": final, "colours": colours, "fonts": fonts, "logo": logo,
        "lockup": {"callout": "", "offer": [""], "detail": None, "button": "Learn more"},
        "keyterms": [], "music": None, "music_bpm": None, "approved": False,
    }
    try:  # a rebuild keeps what the person already set
        old = json.loads(kit_path(client).read_text(encoding="utf-8"))
        for key in ("lockup", "keyterms"):
            kit[key] = old.get(key, kit[key])
        if old.get("music") and (out / old["music"]).is_file():
            kit["music"], kit["music_bpm"] = old["music"], old.get("music_bpm")
    except (OSError, ValueError):
        pass
    save(client, kit)
    write_swatch(client, kit)
    return kit


def write_swatch(client: str, kit: dict) -> Path:
    """brand/swatch.html: the colours with their role names, both fonts in a sample line, the logo."""
    faces = []
    for role, font in kit["fonts"].items():
        for face, file in font["files"].items():
            if file:
                style, weight = FACES[face]
                faces.append(f"@font-face{{font-family:'{role}';font-style:{style};font-weight:{weight};"
                             f"src:url('{html.escape(file)}') format('{font_format(file)}')}}")
    chips = "".join(
        f"<div class=chip style='background:{c};color:{'#fff' if contrast(c, '#FFFFFF') >= contrast(c, '#000000') else '#000'}'>"
        f"<b>{role}</b><span>{c}</span></div>" for role, c in kit["colours"].items())
    c = kit["colours"]
    notes = "".join(f"<p class=note>{html.escape(f['note'])}</p>" for f in kit["fonts"].values() if f.get("note"))
    logo = (f"<img class=logo src='{html.escape(kit['logo'])}' alt='logo'>" if kit["logo"]
            else "<p class=note>No logo found.</p>")
    sample = html.escape(f"{kit['name']}: Six weeks to moving freely.")
    body_family = html.escape(kit["fonts"]["body"]["family"])
    families = " · ".join(
        f"{role}: {font['family']} ({'matched from ' + font['matched_from'] if font['source'] == 'matched' else font['source']})"
        for role, font in kit["fonts"].items())
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(kit['name'])} brand kit</title>
<style>{''.join(faces)}
body{{margin:0;padding:32px;font:16px/1.5 body,sans-serif;background:#fff;color:#222}}
h1{{font:700 28px display,serif}} .chips{{display:flex;flex-wrap:wrap;gap:12px;margin:24px 0}}
.chip{{width:150px;padding:16px;border-radius:8px;display:flex;flex-direction:column;gap:4px;border:1px solid #0002}}
.card{{padding:24px;border-radius:8px;margin:16px 0;max-width:640px}}
.display{{font:400 32px display,serif}} .body{{font:400 18px body,sans-serif}}
.logo{{max-height:80px;background:#eee;padding:8px;border-radius:8px}} .note{{color:#a40}}</style></head>
<body><h1>{html.escape(kit['name'])}</h1><p>{html.escape(kit['site'])} · approved: {'yes' if kit['approved'] else 'no'}</p>
<div class=chips>{chips}</div>
<p>{html.escape(families)}</p>
<div class=card style="background:{c['deep']};color:{c['paper']}"><div class=display>{sample}</div>
<p class=body>The spoken word is <b style="color:{c['highlight']}">highlighted</b>.</p></div>
<div class=card style="background:{c['paper']};color:{c['ink']}"><div class=display style="color:{c['primary']}">{sample}</div>
<p class=body><span style="color:{c['accent_dark']};font-style:italic">An eyebrow in italic</span> and body text in {body_family}.</p></div>
{logo}{notes}</body></html>
"""
    path = brand_dir(client) / "swatch.html"
    path.write_text(page, encoding="utf-8")
    return path


# the client command

def _args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="action", metavar="action", required=True)
    new = sub.add_parser("new", help="Read a client's brand from their site into a kit to approve.")
    new.add_argument("client")
    new.add_argument("url")
    new.add_argument("--name", help="the client's display name")
    music = sub.add_parser("music", help="Make the client's music bed, or copy a licensed track in.")
    music.add_argument("client")
    music.add_argument("--prompt", help="describe the music to make with ElevenLabs Music")
    music.add_argument("--seconds", type=int, default=240)
    music.add_argument("--bpm", type=float, default=None, help="tempo (default 72 for a made bed)")
    music.add_argument("--file", help="a track you're licensed to use, copied in instead")
    approve_p = sub.add_parser("approve", help="Approve the kit after you've looked at brand/swatch.html.")
    approve_p.add_argument("client")
    colours = sub.add_parser("colours", help="Set the brand's colours by hand; the rest are worked out from them.")
    colours.add_argument("client")
    for role in ("primary", "accent", "paper", "ink"):
        colours.add_argument(f"--{role}", metavar="HEX", help=f"the {role} colour, like #1A1A1A")
    lockup = sub.add_parser("lockup", help="Set the offer lockup every ad carries.")
    lockup.add_argument("client")
    lockup.add_argument("--callout", required=True)
    lockup.add_argument("--offer", required=True)
    lockup.add_argument("--offer2")
    lockup.add_argument("--detail")
    lockup.add_argument("--button", default="Learn more")


@command("client", help="Set up a client: new, colours, music, approve, lockup.", args=_args)
def client_cmd(ns):
    from adeditor import music

    try:
        if ns.action == "new":
            kit = build(ns.client, ns.url, name=ns.name)
            print(f"Read {kit['name']} from {kit['site']}.")
            print(f"Look at clients/{ns.client}/brand/swatch.html, then set the lockup and run: adeditor client approve {ns.client}")
            for font in kit["fonts"].values():
                if font["note"]:
                    print(font["note"])
            if not kit["logo"]:
                print('No logo found on the site. Put logo.png or logo.svg in the brand folder and set "logo" in kit.json.')
        elif ns.action == "music":
            if bool(ns.file) == bool(ns.prompt):
                print("Give either --prompt (to make music) or --file (your own track).", file=sys.stderr)
                return 1
            if ns.file:
                music.use_file(ns.client, Path(ns.file), ns.bpm)
            else:
                music.make_bed(ns.client, ns.prompt, ns.seconds, ns.bpm if ns.bpm is not None else 72)
            print(f"The music bed is saved. The kit needs approving again: adeditor client approve {ns.client}")
        elif ns.action == "approve":
            kit = approve(ns.client)
            print(f"Approved {ns.client}'s brand kit.")
            lockup = kit["lockup"]
            if not lockup["callout"].strip() or not any(line.strip() for line in lockup["offer"]):
                print(f"Warning: the lockup is empty. Set it with: adeditor client lockup {ns.client} --callout ... --offer ...")
        elif ns.action == "colours":
            kit = set_colours(ns.client, ns.primary, ns.accent, ns.paper, ns.ink)
            print("The colours are set: " + ", ".join(f"{role} {c}" for role, c in kit["colours"].items()) + ".")
            print(f"Look at clients/{ns.client}/brand/swatch.html, then run: adeditor client approve {ns.client}")
        else:
            offers = [ns.offer] + ([ns.offer2] if ns.offer2 else [])
            set_lockup(ns.client, ns.callout, offers, ns.detail, ns.button)
            print(f"The lockup is set. The kit needs approving again: adeditor client approve {ns.client}")
    except (ValueError, urllib.error.URLError, OSError, music.MusicError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
