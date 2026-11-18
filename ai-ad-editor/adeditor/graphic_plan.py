"""Graphics in the ad plan: motion graphics drawn from what the speaker says.

plan.json describes each graphic by phrase (its words are the speaker's own, never times). resolve()
puts each one on the cut's clock and refuses anything untrue or unsafe: an item that isn't a run of the
kept words, one that isn't said inside the graphic's window, a stat number nobody said, the wrong count
of items, a window too short or long. check_room() refuses graphics that crowd B-roll, a heading,
another graphic, the opening lockup or the end card. Every problem is listed, not just the first.
How well item text fits its slot is checked where the graphics are drawn, not here."""
from adeditor import plan, verify

# kind: (fewest items, most items). A versus counts each side; a stat and a quote have no items.
COUNTS = {"cycle": (3, 6), "steps": (2, 5), "checklist": (2, 6), "versus": (1, 4), "stat": (0, 0), "quote": (0, 0)}
MIN_LENGTH = 2.5
MAX_LENGTH = 8.0
MAX_GRAPHICS = 4
SPACING = 12.0  # at most one graphic starts in any 12 s
ENTRANCE = 0.4  # nothing lands in a graphic's first 0.4 s
IDLE_LIMIT = 20.0  # warn when this many seconds pass with no B-roll and no graphic
GROUNDS = ("deep", "paper")
COMMON = {"kind", "from", "to", "to_end", "hold", "after", "ground", "title", "sfx"}
EXTRA = {"cycle": {"items"}, "steps": {"items"}, "checklist": {"items"},
         "versus": {"left", "right", "left_header", "right_header", "turn"},
         "stat": {"value", "label"}, "quote": {"line", "accent"}}
SLACK = 0.01  # seconds a phrase may run past its window's end before it counts as outside it


def _say(span: tuple[float, float]) -> str:
    return f"{span[0]:.1f} and {span[1]:.1f} s"


def _phrase(kept, phrase, tok: int, window: tuple[float, float], label: str, what: str, errors: list[str]):
    """Find `phrase` from token `tok` on, inside the window. Returns (found, next token, end) or None after
    an error. `found` is {"text", "at"}; `at` is the phrase's first word's onset, held back to the end of the
    entrance so nothing lands in the graphic's first 0.4 s."""
    if not isinstance(phrase, str) or not verify.normalise(phrase):
        errors.append(f"{label}: {what} needs text, a run of the speaker's words.")
        return None
    got = kept.find(phrase, tok)
    if got is not None:
        first, last = kept.span(*got)
        if last <= window[1] + SLACK:
            return {"text": phrase, "at": round(max(first, window[0] + ENTRANCE), 3)}, got[0] + got[1], last
    errors.append(f'{label}: {what} "{phrase}" isn\'t said between {_say(window)}, in that order. '
                  f"The closest runs of kept words are: {plan._quoted(kept.closest(phrase))}.")
    return None


def _items(kept, phrases, tok: int, window, label: str, what: str, errors: list[str]) -> tuple[list[dict], int]:
    """Items in order: each is searched after the one before it. Returns the ones found and the next token."""
    out = []
    for n, p in enumerate(phrases, 1):
        got = _phrase(kept, p, tok, window, label, f"{what} {n}", errors)
        if got:
            found, tok, _ = got
            out.append(found)
    return out, tok


def _count(value, kind: str, name: str, label: str, errors: list[str], low: int, high: int) -> list | None:
    if not isinstance(value, list) or not all(isinstance(s, str) and s.strip() for s in value):
        errors.append(f"{label}: {name} must be a list of phrases.")
        return None
    if not low <= len(value) <= high:
        errors.append(f"{label}: a {kind} takes {low} to {high} {name} and this has {len(value)}.")
        return None
    return value


def _optional(kept, phrase, tok, window, label, what, errors):
    if phrase is None:
        return None
    got = _phrase(kept, phrase, tok, window, label, what, errors)
    return got[0] if got else None


def _stat(kept, g, tok, window, label, errors) -> dict | None:
    shown = g.get("value")
    if isinstance(shown, (int, float)) and not isinstance(shown, bool):
        shown = f"{shown:g}"  # a JSON number is its string form
    value = verify.number_value(shown) if isinstance(shown, str) else None
    if value is None:
        errors.append(f'{label}: a stat\'s "value" is one number as shown, like "30", "$7,500" or "90%", '
                      f"and {shown!r} isn't.")
        return None
    heard = verify._said_after(kept.words, *window)
    matches = [(t, after) for v, t, after in heard if abs(v - value) < 1e-6]
    if not matches:
        said = ", ".join(f"{v:g}" for v, _, _ in heard) or "no numbers"
        errors.append(f'{label}: the stat "{shown}" isn\'t said between {_say(window)}. '
                      f"The speaker says {said} there. Show only a number they say.")
        return None
    units = verify.number_units(shown)
    if units:
        matches = [(t, after) for t, after in matches if set(units) & set(after)]
        if not matches:
            errors.append(f'{label}: the stat "{shown}" carries a unit, but the speaker never says '
                          f'{" or ".join(chr(34) + u + chr(34) for u in units)} right after the number '
                          f"between {_say(window)}. Say it with the number, or show the number without the unit.")
            return None
    at = matches[0][0]
    return {"value": shown, "value_at": round(max(at, window[0] + ENTRANCE), 3)}


def _versus(kept, g, tok, window, label, errors) -> dict | None:
    sides = {k: _count(g.get(k), "versus", f'"{k}" items', label, errors, 1, 4) for k in ("left", "right")}
    turn = g.get("turn")
    if turn is None:
        errors.append(f'{label}: a versus needs a "turn", the phrase where the speaker turns, like "but" or "instead".')
    if turn is None or None in sides.values():
        return None
    left, after_left = _items(kept, sides["left"], tok, window, label, "left item", errors)
    hit = _phrase(kept, turn, after_left, window, label, "the turn", errors)
    if not hit or len(left) != len(sides["left"]):
        return None
    right, _ = _items(kept, sides["right"], hit[1], window, label, "right item", errors)
    heads = [_optional(kept, g.get(k), tok, window, label, f'the {k.replace("_", " ")}', errors)
             for k in ("left_header", "right_header")]
    if len(right) != len(sides["right"]) or any(g.get(k) is not None and h is None
                                                 for k, h in zip(("left_header", "right_header"), heads)):
        return None
    return {"left": left, "right": right, "left_header": heads[0], "right_header": heads[1], "turn": hit[0]}


def _quote(kept, g, tok, window, label, errors) -> dict | None:
    got = _phrase(kept, g.get("line"), tok, window, label, "the line", errors)
    if not got:
        return None
    accent = g.get("accent")
    if accent is not None and (not isinstance(accent, str) or not verify.normalise(accent)
                               or not set(verify.normalise(accent)) <= set(verify.normalise(got[0]["text"]))):
        errors.append(f'{label}: the accent word {accent!r} isn\'t in the line "{got[0]["text"]}".')
        return None
    return {"line": {**got[0], "end": round(got[2], 3)}, "accent": accent}


def _one(g, n: int, kept, cut: dict, errors: list[str]) -> dict | None:
    label = f"Graphic {n}"
    before = len(errors)
    if not isinstance(g, dict):
        errors.append(f"{label} must be an object with a kind, from and to.")
        return None
    kind = g.get("kind")
    if not isinstance(kind, str) or kind not in COUNTS:
        errors.append(f'{label}: "kind" must be one of {", ".join(COUNTS)}, not "{kind}".')
        return None
    for k in sorted(set(g) - COMMON - EXTRA[kind]):
        errors.append(f'{label}: a {kind} has no "{k}". Its keys are {", ".join(sorted(COMMON | EXTRA[kind]))}.')
    ground = g.get("ground", "deep")
    if ground not in GROUNDS:
        errors.append(f'{label}: "ground" is "deep" or "paper", not "{ground}".')
    sfx = g.get("sfx", True)
    if not isinstance(sfx, bool):
        errors.append(f'{label}: "sfx" is true or false.')
    a = plan._where(kept, g, "from", label, errors, 0)
    b = plan._where(kept, g, "to", label, errors, plan._to_from(a, g))
    hold = plan._number(g.get("hold"), "hold", errors, label, plan.DEFAULT_HOLD)
    if not (a and b) or len(errors) > before:
        return None
    start = a[2]
    end = round(b[3] + hold, 3) if g.get("to_end") else b[2]
    if end <= start:
        errors.append(f"{label} would end at {end:.1f} s, before it starts at {start:.1f} s. Check its from and to.")
        return None
    if not MIN_LENGTH <= end - start <= MAX_LENGTH:
        errors.append(f"{label} lasts {end - start:.1f} s, from {start:.1f} to {end:.1f} s. "
                      f"A graphic lasts {MIN_LENGTH} to {MAX_LENGTH:g} s, so widen or narrow its from and to.")
        return None
    window, tok = (start, end), a[0]
    title = _optional(kept, g.get("title"), tok, window, label, "the title", errors)
    out = {"kind": kind, "start": start, "end": end, "ground": ground, "title": title, "items": [],
           "sfx": sfx, "_label": label}
    low, high = COUNTS[kind]
    if kind in ("cycle", "steps", "checklist"):
        phrases = _count(g.get("items"), kind, "items", label, errors, low, high)
        if phrases is not None:
            out["items"], _ = _items(kept, phrases, tok, window, label, "item", errors)
    elif kind == "versus":
        out.update(_versus(kept, g, tok, window, label, errors) or {})
    elif kind == "stat":
        out.update(_stat(kept, g, tok, window, label, errors) or {})
        out["label"] = _optional(kept, g.get("label"), tok, window, label, "the label", errors)
    else:
        out.update(_quote(kept, g, tok, window, label, errors) or {})
    return None if len(errors) > before else out


def resolve(graphics: list, kept, cut: dict, errors: list[str]) -> list[dict]:
    """The plan's graphics on the cut's clock, each {"kind", "start", "end", "ground", "title", "items",
    "sfx", ...fields of its kind}. A problem adds a sentence to `errors` and drops that graphic."""
    if not isinstance(graphics, list):
        errors.append('"graphics" must be a list.')
        return []
    if len(graphics) > MAX_GRAPHICS:
        errors.append(f"An ad takes at most {MAX_GRAPHICS} graphics and the plan has {len(graphics)}.")
    done = [_one(g, n, kept, cut, errors) for n, g in enumerate(graphics, 1)]
    return [g for g in done if g]


def _span_text(s: dict) -> str:
    return f"{s['start']:.1f} to {s['end']:.1f} s"


def check_room(graphics: list[dict], headings: list[dict], broll: list[dict], cut: dict, errors: list[str]) -> None:
    ordered = sorted(graphics, key=lambda g: g["start"])
    card = plan.card_start(cut)
    for g in ordered:
        label = f"{g['_label']} ({_span_text(g)})"
        if g["start"] < plan.MIN_LOCKUP:
            errors.append(f"{label} starts inside the lockup, which needs the first {plan.MIN_LOCKUP} s clear. "
                          f"Start it at {plan.MIN_LOCKUP} s or later.")
        if g["end"] > card:
            errors.append(f"{label} runs into the end card that starts at {card:.1f} s. End it sooner.")
        for b in broll:
            if plan._overlap(g, b):
                errors.append(f"{label} overlaps {b['_label']} ({_span_text(b)}). A graphic and B-roll can't share a moment.")
        for n, h in enumerate(headings, 1):
            if plan._overlap(g, h):
                errors.append(f"{label} overlaps heading {n} ({_span_text(h)}). A graphic and a heading can't share a moment.")
    for a, b in zip(ordered, ordered[1:]):
        if plan._overlap(a, b):
            errors.append(f"{a['_label']} and {b['_label']} overlap. Only one graphic shows at a time.")
        elif b["start"] - a["start"] < SPACING:
            errors.append(f"{a['_label']} starts at {a['start']:.1f} s and {b['_label']} at {b['start']:.1f} s. "
                          f"Only one graphic may start in any {SPACING:g} s, so the speaker stays the star.")


def idle_warnings(broll: list[dict], graphics: list[dict], cut: dict) -> list[str]:
    """One sentence for each stretch over 20 s that shows neither B-roll nor a graphic. The opening lockup's
    first 2.5 s and the end card are not empty, so only the stretch between them is measured."""
    first, last = plan.MIN_LOCKUP, plan.card_start(cut)
    gaps, cursor = [], first
    for s, e in sorted((x["start"], x["end"]) for x in [*broll, *graphics]):
        if s - cursor > IDLE_LIMIT:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if last - cursor > IDLE_LIMIT:
        gaps.append((cursor, last))
    return [f"From {a:.1f} to {b:.1f} s ({b - a:.0f} s) nothing shows but the speaker. If they say something "
            f"you could show there, add a graphic or one of the client's own clips." for a, b in gaps]
