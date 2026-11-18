"""The ad plan: plan.json is what Claude writes, plan.load(job) is what the renderer trusts.

Claude names moments by phrase (its words are the speaker's own), never by time. This module
resolves each phrase onto the cut's clock from cut.json's kept words and refuses anything unsafe:
a phrase that isn't there (nothing is ever placed at time 0 on a miss), a heading that isn't the
speaker's own words, B-roll that overlaps, runs into the lockup or the end card, or has no file.
Every problem is collected and listed, not just the first."""
import argparse
import difflib
import json
import re
import sys

from adeditor import brand, media, paths, stock, verify
from adeditor.cli import command
from adeditor.paths import Job

LEAD = 0.04  # B-roll cuts in this long before the word it shows
MIN_LOCKUP = 2.5  # seconds the offer lockup keeps clear at the start
MAX_HEADINGS = 2
END_CARD = 3.2
CARD_LEAD = 0.2  # the end card starts this long before speech ends
DEFAULT_HOLD = 0.7
BED_LIMIT = 3.0  # bed_offset_db: -3 is 18 dB under the voice, 0 is 15, +3 is 12
SFX_RANGE = (-12.0, 6.0)  # sfx_offset_db: every sound effect moves together, in dB
KEYS = {"lockup", "headings", "key_words", "broll", "end_card", "bed_offset_db",
        "graphics", "sounds", "flash", "sfx_offset_db"}


class PlanError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


class Kept:
    """The kept words as normalised token runs, each token remembering which words made it."""

    def __init__(self, words: list[dict]):
        self.words = words
        self.toks = verify._tokens(words)
        self.text = [t for t, _ in self.toks]

    def find(self, phrase: str, start: int = 0) -> tuple[int, int] | None:
        """(first token, token count) of the phrase's first match at or after token `start`."""
        want = verify.normalise(phrase)
        n = len(want)
        if not n:
            return None
        for i in range(max(start, 0), len(self.text) - n + 1):
            if self.text[i:i + n] == want:
                return i, n
        return None

    def span(self, i: int, n: int) -> tuple[float, float]:
        first = min(self.toks[i][1])
        last = max(self.toks[i + n - 1][1])
        return float(self.words[first]["start"]), float(self.words[last]["end"])

    def closest(self, phrase: str, how_many: int = 3) -> list[str]:
        n = max(len(verify.normalise(phrase)), 1)
        windows: dict[str, str] = {}
        for i in range(len(self.text) - n + 1):
            key = " ".join(self.text[i:i + n])
            if key not in windows:
                lo, hi = min(self.toks[i][1]), max(self.toks[i + n - 1][1])
                windows[key] = " ".join(str(w.get("text", "")).strip() for w in self.words[lo:hi + 1])
        near = difflib.get_close_matches(" ".join(verify.normalise(phrase)), list(windows), n=how_many, cutoff=0.0)
        return [windows[k] for k in near]


def _quoted(items: list[str]) -> str:
    return ", ".join(f'"{s}"' for s in items)


def _number(value, name: str, errors: list[str], where: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}: {name} must be a number.")
        return default
    return float(value)


def _where(kept: Kept, spec: dict, key: str, label: str, errors: list[str], search_from: int):
    """Resolve spec[key] to (token index, count, start, end), or None after adding an error."""
    phrase = spec.get(key)
    if not isinstance(phrase, str) or not phrase.strip():
        errors.append(f'{label} needs a "{key}" phrase.')
        return None
    start = search_from
    after = spec.get("after") if key == "from" else None  # "to" searches on from the "from" match (_to_from)
    if after is not None:
        got = kept.find(str(after))
        if got is None:
            errors.append(f'{label}: I can\'t find "{after}" (its "after" phrase) in the words you kept. '
                          f"The closest are: {_quoted(kept.closest(str(after)))}.")
            return None
        start = got[0] + got[1]
    got = kept.find(phrase, start)
    if got is None:
        where = f' after "{after}"' if after is not None else ""
        errors.append(f'{label}: I can\'t find "{phrase}"{where} in the words you kept. '
                      f"The closest are: {_quoted(kept.closest(phrase))}.")
        return None
    return (*got, *kept.span(*got))


def _to_from(a, spec: dict) -> int:
    """The token where the search for `to` starts. With to_end, `to` names the phrase whose end it holds
    after, so it may be the `from` phrase itself or sit inside it: the search starts where `from` starts.
    Without to_end, `to` is where it stops, so the search starts after the `from` match."""
    if not a:
        return 0
    return a[0] if spec.get("to_end") else a[0] + a[1]


def _lockup(value, job: Job, errors: list[str]) -> dict | None:
    if value == "kit":
        try:
            value = brand.read(job.client).get("lockup")  # the plan may be written before the kit is approved
        except ValueError as exc:
            errors.append(str(exc))
            return None
        empty = f"The lockup in {job.client}'s brand kit is empty. Run adeditor client lockup {job.client} with a callout and an offer line."
    else:
        empty = "The lockup in plan.json needs a callout and at least one offer line."
    if not isinstance(value, dict):
        errors.append(empty)
        return None
    callout = value.get("callout")
    offer = [s.strip() for s in value.get("offer") or [] if isinstance(s, str) and s.strip()]
    if not isinstance(callout, str) or not callout.strip() or not offer:
        errors.append(empty)
        return None
    detail = value.get("detail")
    button = value.get("button")
    return {"callout": callout.strip(), "offer": offer[:2],
            "detail": detail.strip() if isinstance(detail, str) and detail.strip() else None,
            "button": button.strip() if isinstance(button, str) and button.strip() else "Learn more"}


def card_start(cut: dict) -> float:
    """Where the end card starts on the cut's clock: CARD_LEAD before the cut take ends, as the reference
    ad placed it. The page and the plan check both use this one value."""
    return round(float(cut["duration"]) - CARD_LEAD, 3)


def _headings(items, kept: Kept, errors: list[str]) -> list[dict]:
    out = []
    if not isinstance(items, list):
        errors.append('"headings" must be a list.')
        return out
    if len(items) > MAX_HEADINGS:
        errors.append(f"An ad takes at most {MAX_HEADINGS} headings and the plan has {len(items)}.")
    for n, h in enumerate(items, 1):
        label = f"Heading {n}"
        if not isinstance(h, dict):
            errors.append(f"{label} must be an object with lines, from and to.")
            continue
        lines = h.get("lines")
        if not isinstance(lines, list) or not lines or not all(isinstance(s, str) and s.strip() for s in lines):
            errors.append(f"{label} needs its lines as a list of text.")
        else:
            for k, line in enumerate(lines, 1):
                if line.count("*") % 2:
                    what = ("closes an italic with * that was never opened"
                            if line.rstrip().endswith("*") and not line.lstrip().startswith("*")
                            else "opens an italic with * but doesn't close it")
                    errors.append(f"{label}, line {k} {what}. Each line needs its own pair.")
            said = " ".join(lines)
            if kept.find(said) is None:
                errors.append(f'{label} ("{said.replace("*", "")}") isn\'t a run of the speaker\'s kept words: '
                              f"headings use the speaker's own words. The closest runs are: {_quoted(kept.closest(said))}.")
        a = _where(kept, h, "from", label, errors, 0)
        b = _where(kept, h, "to", label, errors, _to_from(a, h))
        hold = _number(h.get("hold"), "hold", errors, label, DEFAULT_HOLD)
        sfx = _sfx(h, label, errors)
        if a and b and isinstance(lines, list):
            start = a[2]
            end = round(b[3] + hold, 3) if h.get("to_end") else b[2]
            if end <= start:
                errors.append(f"{label} would end at {end:.1f} s, before it starts at {start:.1f} s. Check its from and to.")
                continue
            out.append({"lines": [str(s) for s in lines], "start": start, "end": end, "sfx": sfx,
                        "words": _said_words(kept, " ".join(lines), start)})
    return out


def _said_words(kept: Kept, said: str, start: float) -> list[int]:
    """The indices (into the cut's words) of the run of words a heading's text was resolved to: the first
    match said at or after the heading's start, else the first match anywhere."""
    first, at = None, 0
    while (got := kept.find(said, at)) is not None:
        idx = sorted({w for t in range(got[0], got[0] + got[1]) for w in kept.toks[t][1]})
        if first is None:
            first = idx
        if kept.span(*got)[0] >= start - 0.05:
            return idx
        at = got[0] + 1
    return first or []


def _key_words(items, kept: Kept, errors: list[str]) -> list[dict]:
    out = []
    if not isinstance(items, list):
        errors.append('"key_words" must be a list.')
        return out
    for item in items:
        word, only_in = (item, None) if isinstance(item, str) else (item.get("word"), item.get("only_in")) if isinstance(item, dict) else (None, None)
        if not isinstance(word, str) or not verify.normalise(word):
            errors.append(f"A key word needs to be a word or a {{word, only_in}} object, and {item!r} isn't.")
            continue
        if not any(t in kept.text for t in verify.normalise(word)):
            errors.append(f'Key word "{word}" isn\'t said anywhere in the kept words.')
            continue
        if only_in is not None:
            got = kept.find(str(only_in))
            if got is None:
                errors.append(f'Key word "{word}": I can\'t find its only_in phrase "{only_in}" in the kept words. '
                              f"The closest are: {_quoted(kept.closest(str(only_in)))}.")
                continue
            if verify.normalise(word)[0] not in kept.text[got[0]:got[0] + got[1]]:
                errors.append(f'Key word "{word}" isn\'t in its only_in phrase "{only_in}".')
                continue
        out.append({"word": word, "only_in": only_in})
    return out


def _broll(items, job: Job, kept: Kept, errors: list[str]) -> list[dict]:
    out = []
    if not isinstance(items, list):
        errors.append('"broll" must be a list.')
        return out
    for n, b in enumerate(items, 1):
        label = f"B-roll {n}"
        if not isinstance(b, dict):
            errors.append(f"{label} must be an object with clip, from and to.")
            continue
        clip = str(b.get("clip", "")).strip()
        file = None
        if not stock.valid_id(clip):
            errors.append(f'{label} needs a "clip" id {stock.ID_HELP}.')
        elif stock.source_of(clip) == "own":
            file = stock.own_clips(job.broll_cache).get(clip)
            if file is None:
                errors.append(f"{label} uses clip {clip} but there's no such file in clients/{job.client}/broll/own/. "
                              f"Run: adeditor broll {job.client} {job.video.name} --own to see the ids.")
        else:
            file = stock.file_for(clip, job.broll_cache)
            if not file.is_file():
                errors.append(f"{label} uses clip {clip} but there's no cached file for it. Run: adeditor broll {job.client} {job.video.name} --get {clip}")
                file = None
        a = _where(kept, b, "from", label, errors, 0)
        e = _where(kept, b, "to", label, errors, _to_from(a, b))
        into = _number(b.get("in"), "in", errors, label, 0.0)
        hold = _number(b.get("hold"), "hold", errors, label, DEFAULT_HOLD)
        y = _number(b.get("y"), "y", errors, label, 50)
        sfx = _sfx(b, label, errors)
        if not 0 <= y <= 100:
            errors.append(f"{label}: y is a percentage from 0 to 100.")
        if not (a and e):
            continue
        start = max(round(a[2] - LEAD, 3), 0.0)
        end = round((e[3] + hold if b.get("to_end") else e[2]) - LEAD, 3)
        if end <= start:
            errors.append(f"{label} would end at {end:.1f} s, before it starts at {start:.1f} s. Check its from and to.")
            continue
        if file is not None:
            try:
                length = media.probe(file).duration
            except media.ToolError:
                errors.append(f"{label}: clip {clip} can't be read. Delete {file.name} from the broll folder and fetch it again.")
                continue
            if into + (end - start) > length + 0.05:
                errors.append(f"Clip {clip} isn't long enough for {label}: it needs {end - start:.1f} s from {into:.1f} s in "
                              f"and the clip is only {length:.1f} s long.")
                continue
        out.append({"clip": clip, "file": file, "in": into, "start": start, "end": end, "y": int(y),
                    "why": str(b.get("why", "")), "sfx": sfx, "_label": label})
    return out


def _sfx(spec: dict, label: str, errors: list[str]) -> bool:
    value = spec.get("sfx", True)
    if not isinstance(value, bool):
        errors.append(f'{label}: "sfx" is true or false.')
        return True
    return value


def _span_text(s: dict) -> str:
    return f"{s['start']:.1f} to {s['end']:.1f} s"


def _overlap(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def _check_timeline(headings: list[dict], broll: list[dict], cut: dict, errors: list[str],
                    graphics: list[dict] = ()) -> None:
    ordered = sorted(broll, key=lambda b: b["start"])
    for a, b in zip(ordered, ordered[1:]):
        if b["start"] < a["end"]:
            errors.append(f"{a['_label']} ({_span_text(a)}) and {b['_label']} ({_span_text(b)}) overlap. Give each a window of its own.")
    if ordered and ordered[0]["start"] < MIN_LOCKUP:
        errors.append(f"{ordered[0]['_label']} starts at {ordered[0]['start']:.1f} s, inside the lockup. "
                      f"The lockup needs the first {MIN_LOCKUP} s clear, so start the first B-roll at {MIN_LOCKUP} s or later.")
    card = card_start(cut)
    for b in ordered:
        if b["end"] > card:
            errors.append(f"{b['_label']} runs to {b['end']:.1f} s, into the end card that starts at {card:.1f} s. End it sooner.")
    for n, h in enumerate(headings, 1):
        if h["end"] > card:
            errors.append(f"Heading {n} runs to {h['end']:.1f} s, into the end card that starts at {card:.1f} s. End it sooner.")
        for b in ordered:
            if _overlap(h, b):
                errors.append(f"Heading {n} ({_span_text(h)}) overlaps {b['_label']} ({_span_text(b)}). A heading and B-roll can't share a moment.")
    for (i, a), (j, b) in zip(enumerate(headings, 1), enumerate(headings[1:], 2)):
        if _overlap(a, b):
            errors.append(f"Heading {i} and heading {j} overlap. Only one heading shows at a time.")
    from adeditor import graphic_plan  # imported here: graphic_plan uses this module's helpers

    graphic_plan.check_room(list(graphics), headings, ordered, cut, errors)


def _flag(raw: dict, name: str, errors: list[str]) -> bool:
    value = raw.get(name, True)
    if not isinstance(value, bool):
        errors.append(f'"{name}" is true or false.')
        return True
    return value


def load(job: Job) -> dict:
    """The plan resolved onto the cut's clock. Raises PlanError listing every problem."""
    try:
        cut = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlanError([f"There's no cut for {job.video.name} yet. Run adeditor cut {job.client} {job.video.name} first."]) from None
    try:
        raw = json.loads((job.work / "plan.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlanError([f"There's no plan.json for {job.video.name} yet. It goes in clients/{job.client}/work/{job.video.stem}/."]) from None
    except json.JSONDecodeError as exc:
        raise PlanError([f"plan.json isn't valid JSON: {exc.msg} on line {exc.lineno}."]) from None
    if not isinstance(raw, dict):
        raise PlanError(["plan.json should be one JSON object."])
    errors: list[str] = []
    for k in sorted(set(raw) - KEYS):
        errors.append(f'plan.json has a key we don\'t use: "{k}". The keys are {", ".join(sorted(KEYS))}.')
    kept = Kept(cut["words"])
    lockup = _lockup(raw.get("lockup", "kit"), job, errors)
    headings = _headings(raw.get("headings", []), kept, errors)
    key_words = _key_words(raw.get("key_words", []), kept, errors)
    broll = _broll(raw.get("broll", []), job, kept, errors)
    end_card = _number(raw.get("end_card"), "end_card", errors, "plan.json", END_CARD)
    if not 0 < end_card <= 10:
        errors.append("end_card is a length in seconds, more than 0 and at most 10.")
    bed = _number(raw.get("bed_offset_db"), "bed_offset_db", errors, "plan.json", 0.0)
    sfx = _number(raw.get("sfx_offset_db"), "sfx_offset_db", errors, "plan.json", 0.0)
    sounds, flash = _flag(raw, "sounds", errors), _flag(raw, "flash", errors)
    from adeditor import graphic_plan  # imported here: graphic_plan uses this module's helpers

    graphics = graphic_plan.resolve(raw.get("graphics", []), kept, cut, errors)
    _check_timeline(headings, broll, cut, errors, graphics)
    if errors:
        raise PlanError(errors)
    for b in broll + graphics:
        del b["_label"]
    return {"lockup": lockup, "headings": headings, "key_words": key_words,
            "broll": sorted(broll, key=lambda b: b["start"]), "end_card": end_card,
            "bed_offset_db": max(-BED_LIMIT, min(BED_LIMIT, bed)),
            "graphics": sorted(graphics, key=lambda g: g["start"]), "sounds": sounds, "flash": flash,
            "sfx_offset_db": max(SFX_RANGE[0], min(SFX_RANGE[1], sfx)),
            "warnings": graphic_plan.idle_warnings(broll, graphics, cut)}


def format_table(r: dict, card_at: float) -> str:
    rows = [(0.0, "lockup", r["lockup"]["callout"])]
    for h in r["headings"]:
        rows.append((h["start"], f"heading to {h['end']:.2f} s", " ".join(h["lines"]).replace("*", "")))
    for b in r["broll"]:
        rows.append((b["start"], f"b-roll {b['clip']} to {b['end']:.2f} s", b["why"]))
    for g in r.get("graphics", []):
        rows.append((g["start"], f"graphic {g['kind']} to {g['end']:.2f} s", " | ".join(i["text"] for i in g["items"])
                     or str(g.get("value") or (g.get("line") or {}).get("text") or "")))
    rows.append((card_at, "end card", f"{r['end_card']:.1f} s"))
    lines = [f"{'time':>8}  {'what':<28}  words"]
    lines += [f"{t:7.2f}s  {what:<28}  {words}" for t, what, words in sorted(rows, key=lambda x: x[0])]
    return "\n".join(lines)


def _args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="action", metavar="action", required=True)
    check = sub.add_parser("check", help="Resolve plan.json onto the cut and list what's wrong.")
    check.add_argument("client")
    check.add_argument("video")


@command("plan", help="Check the ad plan Claude wrote: plan check.", args=_args)
def plan_cmd(ns):
    job = paths.job(ns.client, ns.video)
    try:
        r = load(job)
    except PlanError as exc:
        word = "problem" if len(exc.errors) == 1 else "problems"
        print(f"plan.json has {len(exc.errors)} {word}:", file=sys.stderr)
        for e in exc.errors:
            print(f"- {e}", file=sys.stderr)
        return 1
    from adeditor import graphic_page  # imported here: graphic_page uses this module's helpers

    too_long = graphic_page.fit_errors(job, r["graphics"])  # every graphic in every ratio, as the page will set it
    if too_long:
        print(f"plan.json has {len(too_long)} {'problem' if len(too_long) == 1 else 'problems'}:", file=sys.stderr)
        for e in too_long:
            print(f"- {e}", file=sys.stderr)
        return 1
    cut = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))
    print(format_table(r, card_start(cut)))
    for w in r["warnings"]:
        print(f"Warning: {w}", file=sys.stderr)
    return 0
