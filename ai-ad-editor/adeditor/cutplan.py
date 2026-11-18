"""The cut: only silence comes out, plus the retakes and fillers the person approved.

Word times from a transcript are not enough to cut on: a word's sound starts before the
transcriber says it does, and a cut placed by word times alone lands on sound. So the cut reads
the audio. In 10 ms frames, the noise floor (the 10th percentile) plus 12 dB is the line between
sound and silence. Between two kept words it removes only stretches silent for at least MIN_RUN,
keeping KEEP of silence beside every sound and staying WORD_PAD clear of the transcript's own word
times. A struck retake or filler comes out from a cut point in the gap before it to one in the gap
after it, each placed by the same rule against the kept word beside it. Where a kept word runs
into a struck one with no such silence between them, the cut goes on the quietest 10 ms frame in
the gap, the kept word keeping that whole frame, and the join is listed in `tight_joins`; when no
frame there is clearly quieter than the kept word, the cut stops (CannotCut) rather than clip it,
and a struck span is never quietly left in. Cut points snap to whole source frames, and every
other join is checked: 30 ms either side of it must sit below the line."""
import json
import math
import sys

import requests

from adeditor import aroll, brand, envelope, faces, media, paths, retakes, speed, transcribe, verify
from adeditor.cli import command
from adeditor.paths import Job

MIN_GAP = 0.30  # a gap between two words shorter than this is never touched
MIN_RUN = 0.22  # only a silence at least this long can be cut into
KEEP = 0.10  # silence kept beside every sound
KEEP_WIDE = 0.16  # silence kept at a join the word check flagged, after one retry
WORD_PAD = 0.15  # a cut stays this far from the transcript's own word times
MIN_CUT = 0.06  # a silence cut shorter than this isn't worth a join
LEAD_LOOK, LEAD_PAD, TAIL_PAD = 0.6, 0.15, 0.35  # lead-in and tail: first and last sound, padded
JOIN = 0.03  # every join is silent this far either side
FRAME = 0.01  # the envelope's frame, in seconds
MIN_PIECE = 0.02  # a kept piece this short or shorter is dropped
MAP_SLACK = 0.15  # a word whose time starts up to this much before a piece still belongs to it
DIP = 6.0  # a tight cut goes only on a frame at least this many dB under the kept word beside it


class CutOnSound(Exception):
    """A join lands on sound. `times` are the joins on the cut clock."""

    def __init__(self, times: list[float]):
        self.times = list(times)
        where = ", ".join(verify.clock(t) for t in self.times)
        super().__init__(
            f"A cut would land on sound at {where} on the cut take, so nothing was rendered."
        )


class CannotCut(ValueError):
    """A struck span that can't come out without cutting into a kept word."""

    def __init__(self, said: str, at: float):
        noun, verb = ("words", "run") if " " in said else ("word", "runs")
        super().__init__(
            f"The struck {noun} '{said}' at {verify.clock(at)} in the take {verb} straight into the words "
            "beside it, with no quiet moment to cut on, so taking it out would clip a kept word. Put it "
            "back in strikes.json, or record that line again."
        )


class _Take:
    """The envelope read as sound and silence, and the source's frame grid."""

    def __init__(self, env: list[float], fps: float):
        self.env = env
        self.fps = fps
        self.floor, self.line = envelope.floor_and_line(env)
        self.loud = [v > self.line for v in env]
        self.end = len(env) * FRAME

    def up(self, x: float) -> float:
        """A kept piece starts on a frame at or after x."""
        return math.ceil(x * self.fps - 1e-6) / self.fps

    def down(self, x: float) -> float:
        """A kept piece ends on a frame at or before x."""
        return math.floor(x * self.fps + 1e-6) / self.fps

    def runs(self, a: float, b: float) -> list[tuple[float, float]]:
        """Stretches of silence between a and b, as (start, end)."""
        loud = self.loud
        i0, i1 = max(0, int(a * 100)), min(len(loud), int(math.ceil(b * 100)))
        out, start = [], None
        for i in range(i0, i1):
            if not loud[i] and start is None:
                start = i
            if loud[i] and start is not None:
                out.append((start / 100, i / 100))
                start = None
        if start is not None:
            out.append((start / 100, i1 / 100))
        return out

    def first_sound(self, after: float) -> float:
        i = int(after * 100)
        while i < len(self.loud) and not self.loud[i]:
            i += 1
        return i / 100

    def last_sound(self, before: float) -> float:
        i = min(len(self.loud) - 1, int(before * 100))
        while i > 0 and not self.loud[i]:
            i -= 1
        return (i + 1) / 100

    def quiet_at(self, x: float) -> bool:
        """Every frame within JOIN of x sits below the line."""
        return not _sound_near(self.loud, x)

    def quietest(self, lo: float, hi: float, kept_first: bool) -> int:
        """The quietest 10 ms frame between lo and hi (at least one frame), for two words that run
        together with no silence of MIN_RUN between them. A tie goes to the frame nearest the kept word."""
        i0 = max(0, min(len(self.env) - 1, int(lo * 100)))
        frames = range(i0, max(i0 + 1, min(len(self.env), math.ceil(hi * 100))))
        return min(frames if kept_first else reversed(frames), key=lambda i: self.env[i])

    def level(self, word: dict) -> float:
        """A word's typical loudness: the median 10 ms frame over its transcript times."""
        i0 = max(0, min(len(self.env) - 1, int(word["start"] * 100)))
        i1 = max(i0 + 1, min(len(self.env), math.ceil(word["end"] * 100)))
        return float(sorted(self.env[i0:i1])[(i1 - i0) // 2])

    def tight(self, lo: float, hi: float, kept: dict, kept_first: bool) -> float | None:
        """A cut between a kept word and a struck one that run together: on the quietest frame
        between lo and hi (callers keep the window outside the kept word's own transcript times), the
        kept piece keeping that whole frame (its fade sits there), so the cut moves toward the
        struck word. None when that frame isn't DIP under the kept word: there's no quiet moment,
        only the words' own sound, and cutting there could clip a kept word."""
        if hi <= lo:  # the window is empty: the transcript has the words overlap past it
            return None
        q = self.quietest(lo, hi, kept_first)
        if self.env[q] > self.level(kept) - DIP:
            return None
        return self.up((q + 1) * FRAME) if kept_first else self.down(q * FRAME)


def _sound_near(loud: list[bool], x: float) -> bool:
    lo, hi = x - JOIN, x + JOIN
    return any(loud[i] for i in range(max(0, int(lo * 100)), min(len(loud), int(hi * 100) + 1)))


def _silence_after(take: _Take, kept: dict, struck: dict, keep: float):
    """Where the kept piece ending in `kept` stops before the struck word after it, in the first
    silence of MIN_RUN in the gap: KEEP past the sound and WORD_PAD past the word time. (time, False),
    or None when the gap has no such silence."""
    for s0, s1 in take.runs(kept["end"] - 0.05, struck["start"] + 0.05):
        if s1 - s0 < MIN_RUN:
            continue
        x = take.down(max(s0 + keep, kept["end"] + WORD_PAD))
        if x <= s1 - JOIN and take.quiet_at(x):
            return x, False
    return None


def _silence_before(take: _Take, struck: dict, kept: dict, keep: float):
    """_silence_after, mirrored: where the kept piece starting with `kept` begins, in the last
    silence of MIN_RUN in the gap, KEEP before the sound and WORD_PAD before the word time."""
    for s0, s1 in reversed(take.runs(struck["end"] - 0.05, kept["start"] + 0.05)):
        if s1 - s0 < MIN_RUN:
            continue
        x = take.up(min(s1 - keep, kept["start"] - WORD_PAD))
        if x >= s0 + JOIN and take.quiet_at(x):
            return x, False
    return None


def _ranked(options) -> list[tuple[float, bool, int]]:
    """(time, tight, rank) for each option that exists; a bare time is a tight cut."""
    out = []
    for rank, option in enumerate(options):
        if option is not None:
            x, tight = option if isinstance(option, tuple) else (option, True)
            out.append((x, tight, rank))
    return out


def _lefts(take: _Take, kept: dict, struck: dict, keep: float, limit: float) -> list[tuple[float, bool, int]]:
    """Every way the kept piece ending in `kept` can stop before the struck word, best first: in a
    silence of MIN_RUN; on the quietest frame in the transcript's own gap between the two; and on
    the quietest frame reaching 50 ms on into the struck word (its transcript start can be late).
    Every tight window starts at the kept word's own transcript end, whatever the struck word's
    times say, so a tight cut never lands inside the kept word's own times (a quiet frame there may
    be the word's own closure). It looks no further than `limit`, the middle of the struck span,
    and needs a real quiet moment (_Take.tight); with neither, there's no option. When the
    transcript's times are right, at most a frame of the struck word stays in. When the kept word's
    end time is early, the quietest frame can still clip it by up to about 30 ms; every tight join
    is listed with the words either side (`tight_words`) so the person can listen there, and the
    word check looks hardest there."""
    gap = struck["start"] - kept["end"] >= FRAME
    return _ranked([
        _silence_after(take, kept, struck, keep),
        take.tight(kept["end"], min(struck["start"], limit), kept, True) if gap else None,
        take.tight(kept["end"], min(struck["start"] + 0.05, limit), kept, True),
    ])


def _rights(take: _Take, struck: dict, kept: dict, keep: float, limit: float) -> list[tuple[float, bool, int]]:
    """_lefts, mirrored: every way the kept piece starting with `kept` can begin after the struck
    word. Every tight window ends at the kept word's own transcript start."""
    gap = kept["start"] - struck["end"] >= FRAME
    return _ranked([
        _silence_before(take, struck, kept, keep),
        take.tight(max(struck["end"], limit), kept["start"], kept, False) if gap else None,
        take.tight(max(struck["end"] - 0.05, limit), kept["start"], kept, False),
    ])


def _span_cut(take: _Take, before: dict, first: dict, last: dict, after: dict, keep: float,
              said: str) -> tuple[float, float, bool, bool]:
    """The stretch a struck span between two kept words comes out by: (from, to, from is tight,
    to is tight). It never skips the span: it takes the best pair of edges (_lefts and _rights,
    each looking only in its own half of the span) that leaves room on whole frames, and when no
    pair does, any cut would go through a kept word, so it raises CannotCut instead."""
    mid = (first["start"] + last["end"]) / 2
    pairs = [(left, right) for left in _lefts(take, before, first, keep, mid)
             for right in _rights(take, last, after, keep, mid) if right[0] > left[0]]
    if not pairs:
        raise CannotCut(said, first["start"])
    left, right = min(pairs, key=lambda pair: (pair[0][2] + pair[1][2], pair[0][2]))
    return left[0], right[0], left[1], right[1]


def _silence_cut(s0: float, s1: float, a: dict, b: dict, keep: float):
    """The cut inside one silence between two kept words, or None when it's too short to make."""
    c0, c1 = max(s0 + keep, a["end"] + WORD_PAD), min(s1 - keep, b["start"] - WORD_PAD)
    return (c0, c1) if c1 - c0 >= MIN_CUT else None


def _struck(strikes: dict | None, count: int) -> set[int]:
    strikes = strikes or {}
    out: set[int] = set()
    for s in strikes.get("strikes", []):
        out.update(range(int(s["from"]), int(s["to"]) + 1))
    out.update(int(i) for i in strikes.get("fillers", []))
    bad = sorted(i for i in out if not 0 <= i < count)
    if bad:
        raise ValueError(f"A strike points past the words: word {bad[0]}, and there are only {count} words.")
    return out


def plan(words: list[dict], env: list[float], strikes: dict | None, fps: float, wide=()) -> dict:
    """The cut plan for one take. `words` are the transcript's ({"word", "start", "end"}), `env` the
    10 ms envelope, `strikes` the approved strikes.json, `fps` the source's frame rate. `wide` holds
    source times inside joins the word check flagged: the cut there keeps KEEP_WIDE instead of KEEP.

    Returns {"segments": [{"src", "out"}], "words": [{"text", "start", "end", "src"}], "duration",
    "floor_db", "threshold_db", "tight_joins": [t], "tight_words": [{"t", "before", "after"}],
    "struck": [{"text", "src", "t"}]}, where a struck word's "t" is where it was cut on the cut
    clock and "before"/"after" are the kept words either side of a tight join ({"text", "t"}, or
    None). Raises CannotCut when a struck span can't come out without cutting into a kept word,
    and CutOnSound when a join that isn't tight has sound within 30 ms of it."""
    if not env:
        raise ValueError("The envelope is empty, so there's no audio to cut by.")
    fps = float(fps) if fps and fps > 0 else 30.0
    take = _Take(env, fps)
    order = [i for i, w in enumerate(words) if str(w.get("word", "")).strip()]
    if not order:
        raise ValueError("The transcript has no words, so there's nothing to cut.")
    struck = _struck(strikes, len(words))
    kept = [i for i in order if i not in struck]
    if not kept:
        raise ValueError("Every word is struck, so there's nothing left to keep.")

    def widened(lo: float, hi: float) -> bool:
        return any(lo - 1e-6 <= t <= hi + 1e-6 for t in wide)

    removals = []  # (cut from, cut to, from is tight, to is tight)
    start = start_tight = end = end_tight = None
    n = 0
    while n < len(order):
        if order[n] not in struck:
            if n + 1 < len(order) and order[n + 1] not in struck:
                a, b = words[order[n]], words[order[n + 1]]
                if b["start"] - a["end"] > MIN_GAP:
                    for s0, s1 in take.runs(a["end"] - 0.05, b["start"] + 0.05):
                        if s1 - s0 < MIN_RUN:
                            continue
                        cut = _silence_cut(s0, s1, a, b, KEEP)
                        if cut and widened(take.down(cut[0]), take.up(cut[1])):
                            cut = _silence_cut(s0, s1, a, b, KEEP_WIDE)
                        if cut:
                            removals.append((cut[0], cut[1], False, False))
            n += 1
            continue
        m = n  # a removed span: the maximal run of struck words from n to m
        while m + 1 < len(order) and order[m + 1] in struck:
            m += 1
        first, last = words[order[n]], words[order[m]]
        before = words[order[n - 1]] if n > 0 else None
        after = words[order[m + 1]] if m + 1 < len(order) else None
        mid = (first["start"] + last["end"]) / 2
        said = " ".join(str(words[order[k]]["word"]).strip() for k in range(n, m + 1))
        if before is None:
            edges = _rights(take, last, after, KEEP, mid)
            if not edges:
                raise CannotCut(said, first["start"])
            start, start_tight, _ = edges[0]
        elif after is None:
            edges = _lefts(take, before, first, KEEP, mid)
            if not edges:
                raise CannotCut(said, first["start"])
            end, end_tight, _ = edges[0]
        else:
            cut = _span_cut(take, before, first, last, after, KEEP, said)
            if widened(cut[0], cut[1]):
                cut = _span_cut(take, before, first, last, after, KEEP_WIDE, said)
            removals.append(cut)
        n = m + 1

    if start is None:
        lead = take.first_sound(max(0.0, words[order[0]]["start"] - LEAD_LOOK)) - LEAD_PAD
        start, start_tight = take.down(max(0.0, lead)), False
    if end is None:
        tail = take.last_sound(words[order[-1]]["end"] + LEAD_LOOK) + TAIL_PAD
        # A take that stops mid-sound keeps every sample to its end.
        end, end_tight = (take.end if tail >= take.end else take.down(tail)), False

    pieces, cur, cur_tight = [], start, start_tight
    for c0, c1, t0, t1 in sorted(removals):
        if c1 <= start or c0 >= end:
            continue
        pieces.append([cur, take.down(c0), cur_tight, t0])
        cur, cur_tight = take.up(c1), t1
    pieces.append([cur, end, cur_tight, end_tight])
    pieces = [[round(x, 4), round(y, 4), tx, ty] for x, y, tx, ty in pieces if y - x > MIN_PIECE]

    out, segments, tight = 0.0, [], []  # tight: the piece index each tight join comes before
    for k, (x, y, tx, ty) in enumerate(pieces):
        segments.append({"src": [x, y], "out": [round(out, 4), round(out + y - x, 4)]})
        if (k and (tx or pieces[k - 1][3])) or (k == 0 and tx):
            tight.append(k)
        out += y - x
    duration = round(out, 4)
    if pieces and pieces[-1][3]:
        tight.append(len(pieces))

    mapped, piece_of = [], []
    for i in kept:
        w = words[i]
        seg = next((s for s in segments if s["src"][0] - MAP_SLACK <= w["start"] < s["src"][1]), None)
        if seg is None:  # the transcript's start sits outside every piece: take the one it overlaps most
            overlap = [(min(s["src"][1], w["end"]) - max(s["src"][0], w["start"]), s) for s in segments]
            best = max(overlap, key=lambda o: o[0], default=(0, None))
            if best[0] <= 0:
                raise ValueError(f"The cut lost the word '{w['word']}' at {w['start']:.2f} s in the source.")
            seg = best[1]
        (x, y), o = seg["src"], seg["out"][0]
        mapped.append({"text": w["word"], "start": round(max(x, w["start"]) - x + o, 3),
                       "end": round(min(y, w["end"]) - x + o, 3), "src": w["start"]})
        piece_of.append(segments.index(seg))

    result = {
        "segments": segments,
        "words": mapped,
        "duration": duration,
        "floor_db": round(take.floor, 1),
        "threshold_db": round(take.line, 1),
        "tight_joins": [_join_at(segments, k) for k in tight],
        "tight_words": [_beside(mapped, piece_of, segments, k) for k in tight],
        "struck": [{"text": words[i]["word"], "src": words[i]["start"], "t": _cut_at(segments, words[i]["start"])}
                   for i in order if i in struck],
    }
    check(result, env)
    return result


def _cut_at(segments: list[dict], src: float) -> float:
    """Where a source time sits on the cut clock: inside a kept piece, its own time; inside a
    stretch that was cut, the join it was cut at."""
    for seg in segments:
        (x, y), (o, p) = seg["src"], seg["out"]
        if src < x:
            return o
        if src < y:
            return round(src - x + o, 4)
    return segments[-1]["out"][1] if segments else 0.0


def _join_at(segments: list[dict], k: int) -> float:
    """The cut-clock time of the join before piece k (0.0 before the first, the duration after the last)."""
    return segments[k]["out"][0] if k < len(segments) else segments[-1]["out"][1]


def _beside(kept: list[dict], piece_of: list[int], segments: list[dict], k: int) -> dict:
    """The kept words either side of the tight join before piece k, told apart by the piece each
    word plays in (not by its rounded time), as {"t", "before": {"text", "t"}, "after": {"text", "t"}}
    with None for the side that has no word (a tight lead-in or tail)."""
    before = [w for w, n in zip(kept, piece_of) if n < k]
    after = [w for w, n in zip(kept, piece_of) if n >= k]
    word = lambda w: {"text": w["text"], "t": w["start"]} if w else None
    return {"t": _join_at(segments, k), "before": word(before[-1] if before else None),
            "after": word(after[0] if after else None)}


def check(cut: dict, env: list[float]) -> None:
    """Raise CutOnSound if any join, other than those in `tight_joins`, has a frame above the line
    within 30 ms either side."""
    _, line = envelope.floor_and_line(env)
    loud = [v > line for v in env]
    tight = cut.get("tight_joins", [])
    bad = []
    segs = cut["segments"]
    for left, right in zip(segs, segs[1:]):
        t = left["out"][1]
        if any(abs(t - x) <= 1e-3 for x in tight):
            continue
        if _sound_near(loud, left["src"][1]) or _sound_near(loud, right["src"][0]):
            bad.append(t)
    if bad:
        raise CutOnSound(bad)


def joins(cut: dict) -> list[float]:
    """Every place the word check looks hardest, on the cut clock: each join, and a lead-in or
    tail that had to sit on the quietest moment between two words."""
    out = [left["out"][1] for left in cut["segments"][:-1]]
    out += [t for t in cut.get("tight_joins", []) if all(abs(t - j) > 1e-3 for j in out)]
    return sorted(out)


def _wide_points(cut: dict, near: list[dict]) -> list[float]:
    """For each near difference, a source time inside the stretch cut at the join nearest it. A
    difference nearest a tight lead-in or tail, or with no join within a second (near only because
    a struck word was heard), has no join to widen."""
    segs = cut["segments"]
    places = [(left["out"][1], (left["src"][1] + right["src"][0]) / 2) for left, right in zip(segs, segs[1:])]
    places += [(t, None) for t in cut.get("tight_joins", []) if t in (0.0, cut["duration"])]
    points = set()
    for diff in near:
        if places and diff["to_join"] is not None and diff["to_join"] <= verify.NEAR:
            point = min(places, key=lambda j: abs(j[0] - diff["t"]))[1]
            if point is not None:
                points.add(point)
    return sorted(points)


def _heard(diff: dict) -> str:
    if diff["kind"] == "changed":
        return f"'{diff['kept']}' heard as '{diff['heard']}'"
    if diff["kind"] == "missing":
        return f"'{diff['kept']}' not heard"
    return f"an extra '{diff['heard']}' heard"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _write(path, data) -> None:
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _rebuild_and_check(job: Job, cut: dict, keyterms, before: dict | None) -> tuple[verify.Report, list]:
    """Build the A-roll from cut.json, transcribe it and compare. Writes aroll.mp4,
    transcript.cut.json and verify.json ({"cut": its fingerprint, the report, "accepted"}). A
    difference the person already accepted in `before` (the verify.json from before this run) stays
    accepted only when this is the same cut and the same difference. Returns (report, accepted)."""
    work = job.work
    aroll.build(job.video, cut, work / "aroll.mp4")
    wav = media.extract_audio(work / "aroll.mp4", work / "aroll.wav")
    heard = transcribe.transcribe(wav, keyterms, work / "cache")
    _write(work / "transcript.cut.json", {"words": heard})
    report = verify.compare(cut["words"], heard, joins(cut), cut["struck"])
    fp, before = verify.fingerprint(cut), before or {}
    said_yes = (before.get("accepted") or []) if before.get("cut") == fp else []
    accepted = [d for d in report.near if d in said_yes]
    _write(work / "verify.json", {"cut": fp, **report.to_dict(), "accepted": accepted})
    return report, accepted


def _bare(text: str) -> str:
    return str(text).strip().strip(".,!?;:\"'")


def _stop_advice(job: Job, cut: dict, near: list[dict], listen, widened: bool) -> str:
    """What to do about near differences: listen there; take a strike out only when a cut word sits
    within 1 s of one; and if every word is there, say so with accept-words."""
    times = ", ".join(dict.fromkeys(verify.clock(d["t"]) for d in near))
    accept = f"adeditor accept-words {job.client} \"{job.video}\""
    out = "We widened the kept silence there and checked again, and it's still different. " if widened else ""
    struck = [s for s in cut.get("struck", []) if any(abs(float(s["t"]) - float(d["t"])) <= verify.NEAR for d in near)]
    if not struck:
        return out + f"Listen to {listen.name} in {listen.parent} at {times}; if every word is there, run {accept}."
    names = ", ".join(dict.fromkeys(f"'{_bare(s['text'])}'" for s in struck))
    return (out + f"Listen to {listen.name} in {listen.parent} at {times}. If a word is clipped, put back the cut "
            f"{names} beside it: take it out of strikes.json (its strike, or its number in fillers), then run "
            f"adeditor approve-cut and adeditor cut again. If every word is there, run {accept}.")


def _one_x(work):
    """The A-roll on the 1x clock: aroll.1x.mp4 once a speed was set, else aroll.mp4."""
    return work / "aroll.1x.mp4" if (work / "aroll.1x.mp4").exists() else work / "aroll.mp4"


def _args(p):
    p.add_argument("client")
    p.add_argument("video")


@command("cut", help="Cut the approved take, build the A-roll and check every kept word is still there.", args=_args)
def cut_cmd(ns):
    job = paths.job(ns.client, ns.video, need_raw=True)
    work = job.work
    proposal = retakes.proposal_path(job)
    if not proposal.exists() or not (work / "transcript.json").exists() or not (work / "envelope.json").exists():
        print("There's nothing to cut yet. Run adeditor prepare, then adeditor approve-cut.", file=sys.stderr)
        return 1
    try:
        strikes = retakes.read_proposal(proposal)
        keyterms = retakes.keyterms(job)
    except (retakes.BadProposal, brand.BadKit) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not strikes.get("approved"):
        print("The cut isn't approved yet. Read the strikes with the person, then run "
              f"adeditor approve-cut {job.client} \"{job.video}\".", file=sys.stderr)
        return 1
    words = json.loads((work / "transcript.json").read_text(encoding="utf-8"))["words"]
    env = json.loads((work / "envelope.json").read_text(encoding="utf-8"))
    problem = retakes.check_proposal(strikes, len(words))
    if problem:
        print(problem, file=sys.stderr)
        return 1
    probe = media.probe(job.video)
    try:
        cut = plan(words, env, strikes, probe.fps)
    except (CutOnSound, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    kept_speed = speed.current(job)
    for stale in ("cut.1x.json", "aroll.1x.mp4"):  # a new cut makes the old 1x pair wrong
        (work / stale).unlink(missing_ok=True)
    before = None  # the last word check, kept only for the person's yes on the same cut
    if (work / "verify.json").exists():
        try:
            before = json.loads((work / "verify.json").read_text(encoding="utf-8"))
        except ValueError:
            before = None
    # Until this run writes them again, there's no word check and no face map: if it stops part way,
    # nothing old can pass for the new cut.
    for stale in ("verify.json", "faces.json"):
        (work / stale).unlink(missing_ok=True)
    _write(work / "cut.json", cut)
    stops, widened, report, accepted = [], [], None, []
    try:
        report, accepted = _rebuild_and_check(job, cut, keyterms, before)
        open_near = [d for d in report.near if d not in accepted]
        if open_near:
            try:
                again = plan(words, env, strikes, probe.fps, wide=_wide_points(cut, open_near))
            except (CutOnSound, ValueError):
                again = cut  # no wider cut fits there, so the first one stands
            if again != cut:
                widened = list(dict.fromkeys(verify.clock(d["t"]) for d in open_near))
                cut = again
                _write(work / "cut.json", cut)
                report, accepted = _rebuild_and_check(job, cut, keyterms, before)
    except (transcribe.ScribeError, requests.RequestException) as exc:
        # The key never appears here: Scribe errors carry the status and a short reason only.
        report = None
        stops.append(f"The word check couldn't run: {transcribe.why(exc)}. The cut is saved in cut.json and "
                     "aroll.mp4; run adeditor cut again to retry.")
    # Whatever the word check said, cut.json and aroll.mp4 now hold this cut: it takes the person's
    # speed again, and the face map is made on it, so every file belongs to the same cut.
    if kept_speed != 1.0:
        speed.apply(job, kept_speed)
    try:  # where the face is, on the 1x clip (the layout divides by the speed itself)
        faces.filled(faces.track(_one_x(work), cut=verify.fingerprint(cut)))
    except faces.NoFace as exc:
        print(f"{exc} The cut itself is fine, but the ads can't be framed until a face is found.", file=sys.stderr)
    except faces.ModelMissing as exc:
        stops.append(f"{exc}. The cut is saved in cut.json and aroll.mp4; run adeditor cut again after it.")
    open_near = [d for d in report.near if d not in accepted] if report else []
    if open_near:
        lines = [verify.explain(d, cut["words"]) for d in open_near]
        lines.append(_stop_advice(job, cut, open_near, _one_x(work), bool(widened)))
        stops.insert(0, "\n".join(lines))
    if stops:
        print("\n".join(stops), file=sys.stderr)
        return 1
    struck = len(cut["struck"])
    print(f"Cut the take from {verify.clock(probe.duration)} to {verify.clock(cut['duration'])} in "
          f"{_count(len(cut['segments']), 'piece')}"
          + (f", with {_count(struck, 'struck word')} out." if struck else "."))
    if widened:
        print(f"We widened the kept silence at the cut near {', '.join(widened)} and checked again.")
    print(f"Word check: {report.matched} of {report.total} kept words heard in the cut take.")
    if accepted:
        times = ", ".join(dict.fromkeys(verify.clock(d["t"]) for d in accepted))
        print(f"  You listened at {times} and said every word is there.")
    for d in report.far:
        print(f"  Far from any cut, so it's the transcriber: {_heard(d)} near {verify.clock(d['t'])}.")
    if cut["tight_joins"]:
        tight = len(cut["tight_joins"])
        print(f"{_count(tight, 'cut')} {'sits' if tight == 1 else 'sit'} between words that run together, on "
              "the quietest moment between them. The check heard every word, but listen there yourself:")
        for spot in cut.get("tight_words", []):
            before, after = (f"'{w['text'].strip()}'" if w else None for w in (spot["before"], spot["after"]))
            where = (f"between {before} and {after}" if before and after
                     else f"before {after}" if after else f"after {before}")
            print(f"  {where} near {verify.clock(spot['t'])}")
    return 0


@command("accept-words", help="After listening where the cut's word check stopped: record that every word is there.",
         args=_args)
def accept_cmd(ns):
    job = paths.job(ns.client, ns.video)
    work = job.work
    if not (work / "cut.json").exists() or not (work / "verify.json").exists():
        print(f"There's no word check to answer yet. Run adeditor cut {job.client} \"{job.video}\".", file=sys.stderr)
        return 1
    cut = json.loads((work / "cut.json").read_text(encoding="utf-8"))
    record = json.loads((work / "verify.json").read_text(encoding="utf-8"))
    if record.get("cut") != verify.fingerprint(cut):
        print(f"The word check was made on an earlier cut. Run adeditor cut {job.client} \"{job.video}\" again, "
              "then listen where it stops.", file=sys.stderr)
        return 1
    waiting = verify.unaccepted(record)
    if not waiting:
        print("There's nothing to accept: the word check has no open spot for this cut.")
        return 0
    record["accepted"] = (record.get("accepted") or []) + waiting
    _write(work / "verify.json", record)
    times = ", ".join(dict.fromkeys(verify.clock(d["t"]) for d in waiting))
    print(f"Noted: you listened at {times} and every word is there. The word check won't stop there again "
          "for this cut.")
    return 0
