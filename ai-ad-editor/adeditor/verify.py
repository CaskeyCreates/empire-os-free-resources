"""The word check: the cut take, transcribed again, must hold every word the plan kept.

The same audio transcribed twice differs a little, so words are compared normalised (case,
punctuation, contractions, digits and number words) and aligned as a sequence, not diffed raw.
A difference within 1 s of a join may be a clipped word and stops the cut; one far from every
join is transcriber noise and is listed, not stopped on. A person who has listened at a near
difference and heard every word can accept it; work/verify.json records the cut it checked (its
fingerprint), its differences and the ones accepted, so a yes is only ever for that cut."""
import difflib
import hashlib
import json
import re
import string
from dataclasses import asdict, dataclass, field

from adeditor import retakes

NEAR = 1.0  # a difference this close to a join, in seconds, is treated as a possible clipped word

_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
          "nineteen"]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
# Number words 0 to 100 and their digits compare equal.
NUMBERS = {word: str(n) for n, word in enumerate(_UNITS)}
NUMBERS.update({word: str(20 + 10 * n) for n, word in enumerate(_TENS)})
_HYPHENS = "-\u2010\u2011\u2012\u2013\u2014"
_QUOTES = {"\u2018": "'", "\u2019": "'", "\u201b": "'", "\u201c": '"', "\u201d": '"'}
_QUOTE_CHARS = "".join(_QUOTES) + "\u2026"

Diff = dict  # {"kind": "missing" | "extra" | "changed", "kept", "heard", "t", "to_join"}


@dataclass
class Report:
    matched: int
    total: int
    near: list = field(default_factory=list)
    far: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_BASE_SYMBOLS = ((r"\$\s*", "dollars"), (r"\u00a3\s*", "pounds"), (r"\u20ac\s*", "euros"), (r"(?<![A-Za-z])R", "rand"))


def _base(text: str) -> list[str]:
    """Tokens of one word or phrase, before number words are merged."""
    # a currency symbol is its word after the number, as _spoken reads it: "$500 off" is "500 dollars off"
    for symbol, name in _BASE_SYMBOLS:
        text = re.sub(symbol + r"(\d[\d,]*(?:\.\d+)?)", r"\1 " + name + " ", text)
    text = text.lower()
    for curly, straight in _QUOTES.items():
        text = text.replace(curly, straight)
    text = text.replace("%", " percent ")
    for hyphen in _HYPHENS:
        text = text.replace(hyphen, " ")
    out = []
    for token in text.split():
        token = "".join(c for c in token if c.isalnum() or c == "'").strip("'").replace("'", "")
        if token:
            out.append(token)
    return out


def _merge(tokens: list[tuple[str, set]]) -> list[tuple[str, set]]:
    """Number words to digits: "ninety" "nine" is "99", "one hundred" and "a hundred" are "100".
    Each token carries the set of word positions it came from."""
    out: list[tuple[str, set]] = []
    i = 0
    while i < len(tokens):
        tok, src = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok in ("one", "a") and nxt and nxt[0] == "hundred":
            out.append(("100", src | nxt[1]))
            i += 2
        elif tok in _TENS and nxt and nxt[0] in _UNITS[1:10]:
            out.append((str(int(NUMBERS[tok]) + int(NUMBERS[nxt[0]])), src | nxt[1]))
            i += 2
        else:
            out.append((NUMBERS.get(tok, tok), src))
            i += 1
    return out


def normalise(text: str) -> list[str]:
    """Lower case, straight quotes, punctuation and apostrophes off, hyphens split, "%" read as
    "percent", and number words 0 to 100 as digits."""
    return [tok for tok, _ in _merge([(tok, {0}) for tok in _base(text)])]


# Numbers a graphic shows ("$7,500", "90%", "3x") against the numbers the speaker said.
_SMALL = {w: n for n, w in enumerate(_UNITS)}
_TENS_VALUE = {w: 20 + 10 * n for n, w in enumerate(_TENS)}
_SCALES = {"thousand": 1_000, "million": 1_000_000}
_SPOKEN = {"%": ("percent",), "x": ("times", "x"), "$": ("dollars", "dollar", "bucks"), "r": ("rand",),
           "\u00a3": ("pounds", "pound"), "\u20ac": ("euros", "euro")}
_SYMBOLS = ((r"\$", "dollars"), (r"\u00a3", "pounds"), (r"\u20ac", "euros"), (r"\bR(?=\s*\d)", "rand"))
_SHOWN = re.compile(r"([$\u00a3\u20ac]|r)?\s*(\d[\d,]*(?:\.\d+)?)\s*(%|x|k|m)?", re.I)


def number_value(shown: str) -> float | None:
    """The value of a number as shown ("$7,500" is 7500, "90%" 90, "3x" 3, "R1.5m" 1500000), or None."""
    m = _SHOWN.fullmatch(str(shown).strip())
    if not m:
        return None
    value = float(m.group(2).replace(",", ""))
    return value * {"k": 1_000, "m": 1_000_000}.get((m.group(3) or "").lower(), 1)


def number_units(shown: str) -> tuple[str, ...]:
    """The spoken words that carry a shown number's symbol ("%" is percent, "$" dollars, "x" times, "R" rand,
    pounds, euros), or () when it has none. A "k" or "m" suffix has no spoken word of its own."""
    m = _SHOWN.fullmatch(str(shown).strip())
    if not m:
        return ()
    for symbol in (m.group(1), m.group(3)):
        if symbol and symbol.lower() in _SPOKEN:
            return _SPOKEN[symbol.lower()]
    return ()


_LONE = {"$": "$", "\u00a3": "\u00a3", "\u20ac": "\u20ac", "R": "R"}


def _texts(words: list[dict]) -> list[str]:
    """The words' texts, a lone currency symbol joined to the number word after it ("R" "5,000" is "R5,000")."""
    texts = [_text(w) for w in words]
    for i in range(len(texts) - 1):
        if texts[i].strip() in _LONE and re.match(r"\d", texts[i + 1].strip()):
            texts[i], texts[i + 1] = "", texts[i].strip() + texts[i + 1].strip()
    return texts


def _spoken(words: list[dict]) -> list[tuple[str, int]]:
    """Lower-case tokens of the words with the index of the word each came from; "%" is "percent", a
    currency symbol is its word after the number ("$7,500" is "7500 dollars"), also when the transcript
    splits it into two words ("R" "5,000"), and a comma between digit groups is dropped. A word that ends
    in any punctuation (a comma, full stop, ellipsis, dash, bracket or quote; "%" and currency symbols
    excepted), and a word that is only punctuation, is followed by a "|" token: a number never runs across
    a pause."""
    texts = _texts(words)
    out = []
    for i, raw in enumerate(texts):
        text = raw
        for symbol, name in _SYMBOLS:
            text = re.sub(symbol + r"\s*(\d[\d,]*(?:\.\d+)?)", r"\1 " + name + " ", text)
        text = re.sub(r"(?<=\d),(?=\d{3})", "", text.lower().replace("%", " percent "))
        out += [(tok, i) for tok in re.findall(r"\d+(?:\.\d+)?|[a-z]+", text)]
        end = raw.strip()[-1:]
        if end and not end.isalnum() and end not in "%$\u00a3\u20ac":
            out.append(("|", i))
    return out


def _word_number(toks: list[tuple[str, int]], i: int) -> tuple[float, int] | None:
    """A number spoken in words from token i: (value, next token), or None. "a hundred" is 100."""
    total = current = 0
    last = None
    j = i
    if toks[j][0] == "a" and j + 1 < len(toks) and toks[j + 1][0] in ("hundred", *_SCALES):
        current, last, j = 1, "small", j + 1
    while j < len(toks):
        w = toks[j][0]
        # a digit follows "hundred" or a magnitude only as the three digits that complete it ("7 thousand 500");
        # a bare "3" there is its own count ("five hundred 3 clients")
        if w[0].isdigit() and (last is None or (last == "scale" and len(w) == 3 and w.isdigit())):
            current, last = current + float(w), "digit"
        elif w in _SMALL and (last in (None, "hundred", "scale", "and") or (last == "tens" and 1 <= _SMALL[w] <= 9)):
            current, last = current + _SMALL[w], "small"
        elif w in _TENS_VALUE and last in (None, "hundred", "scale", "and"):
            current, last = current + _TENS_VALUE[w], "tens"
        elif w == "hundred" and last in ("small", "tens", "digit") and current < 100:
            current, last = current * 100, "hundred"
        elif w in _SCALES and last in ("small", "tens", "hundred", "digit"):
            total, current, last = total + current * _SCALES[w], 0, "scale"
        elif (w == "and" and last in ("hundred", "scale") and j + 1 < len(toks)
              and (toks[j + 1][0] in _SMALL or toks[j + 1][0] in _TENS_VALUE)):
            last = "and"
        else:
            break
        j += 1
    return (float(total + current), j) if j > i else None


def _said_after(words: list[dict], start: float, end: float) -> list[tuple[float, float, list[str]]]:
    """(value, onset, the up to two tokens right after the number, stopping at a pause) of every number
    spoken by the words overlapping start to end, as digits or words."""
    inside = [w for w in words if float(w["end"]) > start and float(w["start"]) < end]
    toks = _spoken(inside)
    out, i = [], 0
    while i < len(toks):
        tok, at = toks[i]
        got = _word_number(toks, i)
        if got is None:
            i += 1
            continue
        (value, i) = got
        after = []
        for t, _ in toks[i:i + 2]:
            if t == "|":
                break
            after.append(t)
        out.append((value, float(inside[at]["start"]), after))
    return out


def _said(words: list[dict], start: float, end: float) -> list[tuple[float, float]]:
    """(value, onset) of every number spoken by the words overlapping start to end, as digits or words."""
    return [(v, t) for v, t, _ in _said_after(words, start, end)]


def numbers_said(words: list[dict], start: float, end: float) -> list[float]:
    """The values of the numbers the words overlapping start to end say, as digits or as words."""
    return [v for v, _ in _said(words, start, end)]


def spoken_words(words: list[dict], start: float, end: float) -> set[str]:
    """The lower-case tokens the words overlapping start to end say ("%" as percent)."""
    inside = [w for w in words if float(w["end"]) > start and float(w["start"]) < end]
    return {t for t, _ in _spoken(inside) if t != "|"}


def _text(word: dict) -> str:
    return str(word.get("text", word.get("word", "")))


def _tokens(words: list[dict]) -> list[tuple[str, set]]:
    return _merge([(tok, {i}) for i, text in enumerate(_texts(words)) for tok in _base(text)])


def _distance(lo: float, hi: float, joins: list[float]) -> float | None:
    if not joins:
        return None
    return round(min(0.0 if lo <= j <= hi else min(abs(j - lo), abs(j - hi)) for j in joins), 3)


def compare(kept: list[dict], heard: list[dict], joins: list[float], struck: list[dict] = ()) -> Report:
    """Align the kept words (the plan's, on the cut clock) with the heard ones (the cut take
    transcribed). `matched` and `total` count kept words; each difference is `near` when it sits
    within 1 s of a join, else `far`. An extra heard word matching one of the `struck` words (the
    plan's, each with "t", where it was cut on the cut clock) within 1 s of where that word was cut
    is near even with no join close by: it may be that struck word left in, not transcriber noise."""
    k_tok, h_tok = _tokens(kept), _tokens(heard)
    gone = [(tok, float(w["t"])) for w in struck if w.get("t") is not None for tok in normalise(_text(w))]
    matcher = difflib.SequenceMatcher(None, [t for t, _ in k_tok], [t for t, _ in h_tok], autojunk=False)
    counted = {i for _, src in k_tok for i in src}
    unmatched: set[int] = set()
    report = Report(0, len(counted))
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        ki = sorted({i for _, src in k_tok[i1:i2] for i in src})
        hi = sorted({i for _, src in h_tok[j1:j2] for i in src})
        unmatched.update(ki)
        side = [kept[i] for i in ki] or [heard[i] for i in hi]
        lo, end = float(side[0]["start"]), float(side[-1]["end"])
        diff = {
            "kind": {"replace": "changed", "delete": "missing", "insert": "extra"}[tag],
            "kept": " ".join(_text(kept[i]).strip() for i in ki),
            "heard": " ".join(_text(heard[i]).strip() for i in hi),
            "t": round(lo, 3),
            "to_join": _distance(lo, end, joins),
        }
        near = diff["to_join"] is not None and diff["to_join"] <= NEAR
        if tag == "insert" and any(tok == g and _distance(lo, end, [t]) <= NEAR
                                   for tok, _ in h_tok[j1:j2] for g, t in gone):
            near = True
        (report.near if near else report.far).append(diff)
    report.matched = len(counted - unmatched)
    return report


def fingerprint(cut: dict) -> str:
    """Which cut this is: a short hash of its pieces on the source clock. A speed change leaves it as
    it is (only cut-clock times move); any new cut changes it."""
    pieces = [[round(float(a), 4), round(float(b), 4)] for a, b in (s["src"] for s in cut["segments"])]
    return hashlib.sha256(json.dumps(pieces).encode("utf-8")).hexdigest()[:16]


def unaccepted(record: dict) -> list[Diff]:
    """The near differences in a verify.json record that nobody has accepted yet."""
    accepted = record.get("accepted") or []
    return [d for d in record.get("near", []) if d not in accepted]


def same_spot(diff: Diff, accepted: Diff, speed: float = 1.0) -> bool:
    """Whether `diff`, found on a clock sped up by `speed`, is the difference `accepted` (found on the 1x
    cut): the same kind, the same words either way, within 1 s of the same moment."""
    return (diff["kind"] == accepted["kind"] and normalise(diff["kept"]) == normalise(accepted["kept"])
            and normalise(diff["heard"]) == normalise(accepted["heard"])
            and abs(float(diff["t"]) * speed - float(accepted["t"])) <= NEAR)


def clock(t: float) -> str:
    """Seconds as m:ss."""
    t = max(0, int(t))
    return f"{t // 60}:{t % 60:02d}"


def _line_at(kept: list[dict], t: float) -> str:
    """The line (as retakes reads lines) holding the kept word nearest `t`, at most 14 words."""
    if not kept:
        return ""
    words = [{"word": _text(w), "start": float(w["start"]), "end": float(w["end"])} for w in kept]
    at = min(range(len(words)), key=lambda i: abs(words[i]["start"] - t))
    line = next(group for group in retakes.lines(words) if at in group)
    if len(line) > 14:
        pos = line.index(at)
        lo = max(0, min(pos - 7, len(line) - 14))
        line = line[lo:lo + 14]
    return " ".join(words[i]["word"].strip() for i in line)


def _bare(text: str) -> str:
    return text.strip(string.punctuation + _QUOTE_CHARS)


def explain(diff: Diff, kept: list[dict]) -> str:
    """One plain sentence about a difference, naming the word, when, and the line it's in."""
    when = clock(diff["t"])
    if diff["kind"] == "extra":
        what = f"An extra '{_bare(diff['heard'])}' was heard near {when}, so part of a cut word may be left in"
    else:
        noun = "words" if " " in diff["kept"] else "word"
        what = f"The {noun} '{_bare(diff['kept'])}' near {when} may be clipped"
        if diff["kind"] == "changed":
            what += f" (it was heard as '{_bare(diff['heard'])}')"
    line = _line_at(kept, diff["t"])
    return f"{what}; it's in the line '{line}'" + ("" if line[-1:] in (".", "?", "!") else ".")
