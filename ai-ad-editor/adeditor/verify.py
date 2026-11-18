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


def _base(text: str) -> list[str]:
    """Tokens of one word or phrase, before number words are merged."""
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


def _text(word: dict) -> str:
    return str(word.get("text", word.get("word", "")))


def _tokens(words: list[dict]) -> list[tuple[str, set]]:
    return _merge([(tok, {i}) for i, w in enumerate(words) for tok in _base(_text(w))])


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
