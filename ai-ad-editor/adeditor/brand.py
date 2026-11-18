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
# A modern site's home page can be 5 MB of HTML alone; stylesheets and images keep MAX_BYTES.
MAX_HTML_BYTES = 15 << 20
MAX_SHEETS = 24
SLUG = re.compile(r"^[a-z0-9-]+$")
GENERIC_FONTS = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif",
                 "ui-serif", "ui-monospace", "-apple-system", "blinkmacsystemfont", "segoe ui", "roboto", "helvetica",
                 "helvetica neue", "arial", "sans", "inherit", "initial", "unset", "apple color emoji",
                 "segoe ui emoji", "segoe ui symbol", "noto color emoji", "emoji", "math", "var", "revert",
                 "revert-layer"}
# A family Google doesn't serve is matched by its generic class.
MATCH = {"serif": "Libre Baskerville", "sans-serif": "Inter", "display": "DM Serif Display",
         "monospace": "JetBrains Mono"}
CLASS_OF = {"serif": "serif", "sans-serif": "sans-serif", "monospace": "monospace",
            "cursive": "display", "fantasy": "display"}
FACES = {"regular": ("normal", 400), "italic": ("italic", 400), "bold": ("normal", 700)}
# A font file's format by its extension, for @font-face: Google's woff2, or the person's own licensed files.
FONT_FORMATS = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype"}
DEEP_MAX_LUMINANCE = 0.06
INK_MAX_LUMINANCE = 0.1
INK_MAX_SATURATION = 0.35
# CSS framework defaults that sit in a site's CSS whether or not the brand chose them: Bootstrap 5's named
# palette, Bootstrap 4's blue, Tailwind's blue-500 and blue-600, Framer's blue, WordPress's blues.
FRAMEWORK_DEFAULTS = frozenset({
    "#0d6efd", "#6610f2", "#6f42c1", "#d63384", "#dc3545", "#fd7e14", "#ffc107", "#198754", "#20c997", "#0dcaf0",
    "#007bff", "#3b82f6", "#2563eb", "#0099ff", "#0073aa", "#2271b1"})
# A custom property starting with one of these belongs to a CSS framework, not to the brand, however
# its name reads (Bootstrap's --bs-primary-text-emphasis is a navy no Bootstrap site chose).
FRAMEWORK_VAR_PREFIXES = ("--bs-", "--tw-", "--wp--preset--", "--wp-admin-", "--chakra-", "--mantine-", "--mdc-",
                          "--swiper-")
# A rule whose selector names one of these states styles an error or a notice, not the brand.
# Shopify's own payment buttons are skipped the same way: their blues are the platform's, not the shop's.
STATE_SELECTOR = re.compile(r"error|invalid|danger|success|valid|warning|alert|notice|shopify-payment", re.I)
GUESS_NOTE = ("The colours are a guess from their stylesheets, so check them against the logo and buttons on "
              "their site.")
# The end card changes to the deep ground when the logo would read under this against the paper.
LOGO_MIN_CONTRAST = 3.0
MIN_CONTRAST = 4.5
DEFAULT_PAPER = "#F7F5EF"
DEFAULT_INK = "#1A1A1A"
# The plain look a client gets when their site can't be read.
PLAIN = {"primary": "#111111", "accent": "#2563EB", "paper": "#FFFFFF", "ink": "#111111"}
KEPT = "kept"  # appended to `problems` when the existing kit was left as it was
PLAIN_FONTS = {"display": "Libre Baskerville", "body": "Inter"}


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
    save(client, kit)
    write_swatch(client, kit)
    return kit


# reading their site

def http_get(url: str, accept: str = "*/*") -> tuple[bytes, str, str]:
    """(body, content type, final url). Only http and https, at most MAX_BYTES (MAX_HTML_BYTES for a page)."""
    if not re.match(r"^https?://", url):
        raise ValueError(f"Not a web address: {url}")
    cap = MAX_HTML_BYTES if accept.startswith("text/html") else MAX_BYTES
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read(cap + 1)
        if len(body) > cap:
            raise ValueError(f"{url} is bigger than {cap >> 20} MB")
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


def prefer_real(colours: list[str]) -> list[str]:
    """The colours without the framework defaults, unless nothing else is left."""
    real = [c for c in colours if c not in FRAMEWORK_DEFAULTS]
    return real or colours


def without_framework_and_state(css: str) -> str:
    """The css the saturated colours are counted in: no framework custom properties, and no rule whose selector
    names a state (error, success, warning and the like)."""
    prefixes = "|".join(re.escape(p) for p in FRAMEWORK_VAR_PREFIXES)
    css = re.sub(rf"(?:{prefixes})[\w-]*\s*:\s*[^;}}{{]+;?", "", css)
    return RULE.sub(lambda m: "" if STATE_SELECTOR.search(m.group(1)) else m.group(0), css)


def pick_colours(css: str, meta: dict) -> dict:
    """The brand's primary, a second brand colour if the site has one, its everyday colours, and whether the
    primary is only a guess (no named brand colour and no theme-color)."""
    css = strip_comments(css)
    counts = Counter(colours_in(without_framework_and_state(css)))
    # A custom property counts only when a rule uses it: var(--name), or var(--other) where --other is itself
    # set to var(--name). A declared-but-unused one (a third-party widget's accent) is noise.
    decls = VAR.findall(css)
    refs = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    used = refs | {inner for n, v in decls if n in refs for inner in re.findall(r"var\(\s*(--[\w-]+)", v)}
    named = []
    for name, value in decls:
        c = norm_hex(value)
        if (c and name in used and not name.lower().startswith(FRAMEWORK_VAR_PREFIXES)
                and re.search(r"brand|primary|accent|main|theme", name, re.I) and hls(c)[2] > 0.25):
            named.append((name.lower(), c))
    theme = norm_hex(meta.get("theme-color", "")) if meta.get("theme-color") else None
    theme = theme if theme and hls(theme)[2] > 0.25 else None
    # A colour set in one custom property that nothing uses (an accessibility widget's focus ring) is noise, so
    # it needs a second use to count; a property a rule reads through var() is a use.
    outside = Counter(colours_in(VAR.sub(lambda m: m.group(0) if m.group(1) in used else "",
                                         without_framework_and_state(css))))
    saturated = [c for c, n in counts.most_common() if (n >= 2 or outside[c])
                 and hls(c)[2] > 0.35 and 0.18 < hls(c)[1] < 0.82]
    firsts = [c for n, c in named if "primary" in n] + [c for n, c in named if "accent" not in n] + ([theme] if theme else [])
    guessed = not named and not theme
    pool = [c for c in saturated if c not in FRAMEWORK_DEFAULTS] if guessed else saturated  # a guess never lands on a framework blue
    primary = (prefer_real(firsts + pool) + [None])[0]
    accents = prefer_real([c for n, c in named if "accent" in n] + pool)
    accent = next((c for c in accents if primary and c != primary and abs(hls(c)[0] - hls(primary)[0]) > 0.04), None)
    base = []
    for selector, block in RULE.findall(css):
        if re.search(r"(^|[\s,])(body|html)\b|:root", selector.lower()):
            base += colours_in(block)
    everyday = base + [c for c, _ in counts.most_common(40)]
    return {"primary": primary, "accent": accent, "everyday": everyday, "guessed": guessed}


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
    inks = [c for c in everyday if contrast(c, paper) >= MIN_CONTRAST and rel_luminance(c) <= INK_MAX_LUMINANCE
            and hls(c)[2] < INK_MAX_SATURATION and c not in FRAMEWORK_DEFAULTS]
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


SVG_PAINT = re.compile(r"(?:fill|stroke)\s*[=:]\s*[\"']?\s*(#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|white|black)", re.I)


def _svg_colour(token: str) -> tuple[int, int, int] | None:
    t = token.strip().lower()
    if t in ("white", "black"):
        return (255, 255, 255) if t == "white" else (0, 0, 0)
    if t.startswith("rgb"):
        nums = re.findall(r"[\d.]+", t)[:3]
        return tuple(min(255, round(float(n))) for n in nums) if len(nums) == 3 else None
    c = norm_hex(t)
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) if c else None


def logo_colour(path: Path | None) -> str | None:
    """The colour of the logo where it's drawn (#rrggbb), or None when it can't be measured. A PNG or WebP is
    averaged over its opaque pixels; an SVG over its fill and stroke colours. A picture with no transparent
    pixels sits in its own box, so it has no colour of its own to measure."""
    try:
        if path is None or not path.is_file():
            return None
        if path.suffix.lower() == ".svg":
            found = [c for c in map(_svg_colour, SVG_PAINT.findall(path.read_text(encoding="utf-8", errors="replace")))
                     if c]
            rgb = tuple(sum(c[i] for c in found) / len(found) for i in range(3)) if found else None
        else:
            from PIL import Image

            with Image.open(path) as img:
                img = img.convert("RGBA")
                img.thumbnail((160, 160))
                raw = img.tobytes()
                pixels = [raw[i:i + 4] for i in range(0, len(raw), 4)]
            solid = [p for p in pixels if p[3] >= 128]
            if not solid or len(solid) == len(pixels):
                return None
            rgb = tuple(sum(p[i] for p in solid) / len(solid) for i in range(3))
        return "#{:02x}{:02x}{:02x}".format(*(round(v) for v in rgb)) if rgb else None
    except Exception:
        return None


def logo_vanishes_on_paper(colours: dict, logo_file: Path | None) -> bool:
    """True when the logo would read under LOGO_MIN_CONTRAST on the paper, so wherever it sits on paper it's
    drawn as a dark silhouette instead."""
    ink = logo_colour(logo_file)
    return bool(ink and contrast(ink, colours["paper"]) < LOGO_MIN_CONTRAST)


def end_ground(colours: dict, logo_file: Path | None) -> str:
    """"paper" or "deep": the ground the end card sits on. The logo always shows, so a logo that reads under
    LOGO_MIN_CONTRAST against the paper (and does better on the deep ground) puts the card on the deep ground."""
    ink = logo_colour(logo_file)
    if ink and contrast(ink, colours["paper"]) < LOGO_MIN_CONTRAST and contrast(ink, colours["deep"]) > contrast(ink, colours["paper"]):
        return "deep"
    return "paper"


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
    kit.pop("colours_note", None)  # the person has set them: they're no longer a guess
    kit["approved"] = False
    save(client, kit)
    write_swatch(client, kit)
    return kit


def font_part(part: str) -> str:
    """One comma-separated piece of a font-family value, cleaned: no !important, var( wrapping, brackets or quotes."""
    part = re.sub(r"!\s*important", "", part, flags=re.I).replace("var(", "")
    return part.replace("(", "").replace(")", "").strip().strip("'\"").strip()


def family_of(value: str) -> str | None:
    for part in value.split(","):
        name = font_part(part)
        if name and not name.startswith("--") and name.lower() not in GENERIC_FONTS:
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
            kind = CLASS_OF.get(font_part(part).lower())
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

def build(client: str, url: str, name: str | None = None, fetch=None, problems: list | None = None) -> dict:
    """Read the client's site and write brand/kit.json and brand/swatch.html. The kit is unapproved.
    When the site can't be read (too big, blocked, 404, no network) the kit gets plain defaults instead, and
    the reason is appended to `problems` so the caller can say so."""
    check_slug(client)
    fetch = fetch or http_get
    if not re.match(r"^https?://", url or ""):
        url = "https://" + str(url or "").strip().lstrip("/")
    out = brand_dir(client)
    try:
        body, _, final = fetch(url, "text/html")
    except Exception as exc:
        if problems is not None:
            problems.append(str(exc))
        if kit_path(client).is_file():  # a kit already exists: a failed re-run never replaces it
            if problems is not None:
                problems.append(KEPT)
            return read(client)
        return build_plain(client, url, name, fetch, out)
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
    guessed = pick_colours(css, page.meta)["guessed"]
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
    return finish_kit(client, out, title, final, colours, fonts, logo, GUESS_NOTE if guessed else None)


def build_plain(client: str, url: str, name: str | None, fetch, out: Path) -> dict:
    """The kit for a site we couldn't read: plain colours, Libre Baskerville and Inter, no logo."""
    out.mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    fonts = {role: choose_font(family, None, fetch, out, cache) for role, family in PLAIN_FONTS.items()}
    host = urllib.parse.urlparse(url).hostname or client
    title = " ".join((name or host.removeprefix("www.")).split())[:80]
    colours = complete_roles(PLAIN["primary"], PLAIN["accent"], PLAIN["paper"], PLAIN["ink"])
    return finish_kit(client, out, title, url, colours, fonts, None)


def finish_kit(client: str, out: Path, title: str, site: str, colours: dict, fonts: dict, logo: str | None,
               colours_note: str | None = None) -> dict:
    kit = {
        "name": title, "site": site, "colours": colours, "fonts": fonts, "logo": logo,
        "lockup": {"callout": "", "offer": [""], "detail": None, "button": "Learn more"},
        "keyterms": [], "music": None, "music_bpm": None, "approved": False,
    }
    if colours_note:
        kit["colours_note"] = colours_note
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


def font_source(font: dict) -> str:
    """Where a font came from, for the swatch: its Google source, the family it was matched from, or a plain default."""
    if font["source"] != "matched":
        return font["source"]
    return f"matched from {font['matched_from']}" if font.get("matched_from") else "a plain default, the site names no usable font"


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
    ground = c[end_ground(c, brand_dir(client) / kit["logo"] if kit["logo"] else None)]
    logo = (f"<img class=logo src='{html.escape(kit['logo'])}' alt='logo' style='background:{ground}'>" if kit["logo"]
            else "<p class=note>No logo found.</p>")
    colours_note = f"<p class=note>{html.escape(kit['colours_note'])}</p>" if kit.get("colours_note") else ""
    offer = next((line.strip() for line in (kit.get("lockup") or {}).get("offer", []) if line and line.strip()), "")
    sample = html.escape(f"{kit['name']}: {offer or 'Your offer line goes here'}")
    body_family = html.escape(kit["fonts"]["body"]["family"])
    families = " · ".join(
        f"{role}: {font['family']} ({font_source(font)})"
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
{logo}{colours_note}{notes}</body></html>
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
            problems: list = []
            kit = build(ns.client, ns.url, name=ns.name, problems=problems)
            if problems:
                print(f"We couldn't read their site ({problems[0]}). Set their colours with `adeditor client colours "
                      f"{ns.client} --primary ... --accent ...`, drop their logo in the brand folder, or try another "
                      "page of their site, like their About page.")
                if KEPT in problems:
                    problems.remove(KEPT)
                    print(f"{kit['name']}'s existing kit is unchanged.")
                else:
                    print(f"Made {kit['name']}'s kit with plain colours and fonts for now.")
            else:
                print(f"Read {kit['name']} from {kit['site']}.")
                if kit.get("colours_note"):
                    print(kit["colours_note"])
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
            if not kit.get("logo"):
                print("Warning: there's no logo, so the end card will carry the client's name in its place. Put "
                      f"logo.png or logo.svg in clients/{ns.client}/brand/ and set \"logo\" in kit.json to use theirs.")
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
