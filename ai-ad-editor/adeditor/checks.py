"""`adeditor check`: the last gate before a person sees the ads.

Every finished ad is checked: every kept word still heard (the 9:16 ad's own audio, transcribed once
because the three ratios share one mix), loudness and true peak, the music under the voice, the files
themselves, and no black ending. Stills are pulled from the moments worth looking at, and
ads/<stem>/review.html is written for the person, summary first, with the B-roll credits.

The word check can't prove a cut that lands on two words running together; those joins are listed on
the sheet with their time and the words either side, and the person is asked to listen there."""
import html
import json
import math
import string
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import requests

from adeditor import brand, cutplan, faces, keys, layout, media, mix, paths, plan, render, retakes, speed, stock, transcribe, verify
from adeditor.cli import command
from adeditor.paths import Job

TEMPLATE = Path(__file__).resolve().parent.parent / "template" / "review.html"
LUFS, LUFS_TOLERANCE = -14.0, 1.0
PEAK_MAX = -1.3
BED_BAND = (12.0, 18.0)
FPS, FPS_TOLERANCE = 30.0, 0.1
DURATION_TOLERANCE = 0.15
MIN_BYTES = 5000
BLACK = 8.0  # mean luma (0 to 255) under this on the last frame is a black ending
CUT_AFTER = 0.2  # a B-roll still is taken this long after the cut
FACE_MOVES = 3
FACE_FAR, FAR_APART = 3, 2.0  # stills where the face sits furthest off the frame's centre, 2 s apart
STILL_WIDTH = 360
LAYOUT_NOTES = {"panel": "The take had no room above the head, so the text sits in a band at the top.",
                "card": "The take was framed tightly, so the picture sits as a framed card under the text band."}
RATIOS = render.RATIOS


class NothingToCheck(Exception):
    pass


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    problems: list[str] = field(default_factory=list)  # one plain sentence per failure


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    joins: list[dict] = field(default_factory=list)  # {"t", "before", "after"} to listen to
    warnings: dict[str, list[str]] = field(default_factory=dict)
    layout: dict[str, str] = field(default_factory=dict)  # ratio: "fade", "panel" or "card"
    stills: dict[str, list[tuple[str, float, str]]] = field(default_factory=dict)  # ratio: (label, time, file)
    credits: list[str] = field(default_factory=list)
    noise: list[str] = field(default_factory=list)  # far word differences: the transcriber, not the cut
    accepted: list[str] = field(default_factory=list)  # near differences the person listened to and accepted
    review: Path | None = None

    @property
    def failures(self) -> list[str]:
        return [p for c in self.checks if not c.ok for p in (c.problems or [f"{c.name}: {c.detail}"])]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def summary(self) -> str:
        return "All checks passed." if self.ok else " ".join(self.failures)


def _label(ratio: str) -> str:
    return ratio.replace("x", ":")


def plain(text) -> str:
    """Text for a person: no em dash, en dash or double hyphen, whatever the source held."""
    text = str(text).replace("—", " ").replace("–", " ")
    while "--" in text:
        text = text.replace("--", "-")
    return text


def esc(text) -> str:
    return html.escape(plain(text))


def href(url: str) -> str:
    """An address safe in an attribute; a double hyphen is written as its hyphen-minus escape."""
    return html.escape(str(url).replace("--", "-%2D"), quote=True)


def at(t: float) -> str:
    return f"{verify.clock(t)} ({t:.1f} s)"


# Stills ---------------------------------------------------------------------------------------------------

def _off_centre(face_samples: list[dict], spd: float, window: dict, ratio: str) -> list[float]:
    """Cut-clock times of the samples whose face centre sits furthest from the frame's centre in this
    ratio's window, at most FACE_FAR of them, FAR_APART seconds apart: a slow drift toward an edge.
    The take shows through its frame (the card, or the area under the panel), so each point shows
    that far across and down the canvas."""
    H = layout.RATIOS[ratio]
    fx, fy = (window.get("frame") or [0.0, window.get("panel", 0.0)])[:2]

    def off(f):
        x = fx + (f["x"] + f["w"] / 2) * window["src_w"] - window["x0"] - layout.WIDTH / 2
        y = fy + (f["y"] + f["h"] / 2) * window["src_h"] - window["y0"] - H / 2
        return math.hypot(x, y)

    picked: list[float] = []
    for f in sorted(face_samples, key=lambda f: (-off(f), f["t"])):
        t = f["t"] / spd
        if all(abs(t - p) >= FAR_APART for p in picked):
            picked.append(t)
        if len(picked) == FACE_FAR:
            break
    return picked


def still_times(resolved: dict, face_samples: list[dict], spd: float, *, end_start: float,
                duration: float, window: dict | None = None, ratio: str | None = None) -> list[tuple[str, float]]:
    """(label, time on the cut's clock) for every moment worth a still: the first frame, each heading's
    middle, each B-roll cut plus 0.2 s, the end card's middle, the 3 moments the face centre moves
    most between samples and, given the ratio's window, the 3 where the face sits furthest from the
    frame's centre (a slow drift has no big move). Face samples are on the 1x clock, so their times are
    divided by the speed."""
    out = [("First frame", 0.0)]
    out += [(f"Heading {n}", (h["start"] + h["end"]) / 2) for n, h in enumerate(resolved["headings"], 1)]
    for b in resolved["broll"]:
        out.append((f"B-roll {b['clip']}", min(b["start"] + CUT_AFTER, max(b["end"] - 0.02, b["start"]))))
    out.append(("End card", (end_start + duration) / 2))
    moves = []
    for a, b in zip(face_samples, face_samples[1:]):
        dx = (b["x"] + b["w"] / 2) - (a["x"] + a["w"] / 2)
        dy = (b["y"] + b["h"] / 2) - (a["y"] + a["h"] / 2)
        moves.append((math.hypot(dx, dy), (a["t"] + b["t"]) / 2 / spd))
    biggest = sorted(moves, key=lambda m: (-m[0], m[1]))[:FACE_MOVES]
    out += [(f"Face move {n}", t) for n, (_, t) in enumerate(biggest, 1)]
    if window and ratio and face_samples:
        out += [(f"Face off centre {n}", t) for n, t in enumerate(_off_centre(face_samples, spd, window, ratio), 1)]
    last = max(duration - 0.05, 0.0)
    return [(label, round(min(max(t, 0.0), last), 3)) for label, t in out]


def _slug(label: str) -> str:
    return "".join(c if c in string.ascii_lowercase + string.digits else "_" for c in label.lower()).strip("_")


def _take_stills(job: Job, ratio: str, ad: Path, times: list[tuple[str, float]]) -> list[tuple[str, float, str]]:
    folder = job.ads / "stills"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{ratio}_*.jpg"):
        old.unlink()
    made = []
    for n, (label, t) in enumerate(times, 1):
        name = f"{ratio}_{n:02d}_{_slug(label)}.jpg"
        media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(ad), "-frames:v", "1",
                   "-vf", f"scale={STILL_WIDTH}:-2", "-q:v", "4", str(folder / name)])
        made.append((label, t, f"stills/{name}"))
    return made


# The checks -----------------------------------------------------------------------------------------------

def last_frame_luma(path: Path) -> float:
    """Mean brightness (0 to 255) of the file's last frame."""
    w, h = 64, 36
    raw = subprocess.run([media.tool("ffmpeg"), "-v", "error", "-sseof", "-0.5", "-i", str(path), "-an",
                          "-vf", f"scale={w}:{h}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    frame = raw[-w * h:]
    if len(frame) < w * h:
        raise media.ToolError(f"We couldn't read the last frame of {Path(path).name}.")
    return sum(frame) / len(frame)


def _said_yes(job: Job, cut: dict) -> list[dict]:
    """The near differences the person listened to and accepted after the cut (adeditor accept-words),
    when verify.json is for this cut; they're on the 1x clock."""
    path = job.work / "verify.json"
    record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return list(record.get("accepted") or []) if record.get("cut") == verify.fingerprint(cut) else []


def _word_check(job: Job, ad: Path, cut: dict, report: Report, spd: float) -> Check:
    wav = media.extract_audio(ad, job.work / "ad.wav")
    heard = transcribe.transcribe(wav, retakes.keyterms(job), job.work / "cache")
    result = verify.compare(cut["words"], heard, cutplan.joins(cut), cut.get("struck", []))
    report.noise = [_noise(d) for d in result.far]
    yes = _said_yes(job, cut)
    known = [d for d in result.near if any(verify.same_spot(d, a, spd) for a in yes)]
    still = [d for d in result.near if d not in known]
    report.accepted = [_noise(d) for d in known]
    if still:
        problems = [verify.explain(d, cut["words"]) for d in still]
        return Check("Words", False, f"{result.matched} of {result.total} kept words were heard.", problems)
    if known:
        n = len(known)
        return Check("Words", True, f"Every kept word was heard ({result.matched} of {result.total}), apart from "
                                    f"{n} {'spot' if n == 1 else 'spots'} you listened to and accepted.")
    return Check("Words", True, f"Every kept word was heard ({result.matched} of {result.total}).")


def _noise(d: dict) -> str:
    if d["kind"] == "changed":
        return f"'{d['kept']}' was heard as '{d['heard']}' near {verify.clock(d['t'])}"
    if d["kind"] == "missing":
        return f"'{d['kept']}' wasn't heard near {verify.clock(d['t'])}"
    return f"an extra '{d['heard']}' was heard near {verify.clock(d['t'])}"


def _loudness(ratio: str, ad: Path) -> Check:
    lufs, peak = mix.measure(ad)
    name = f"Loudness {_label(ratio)}"
    problems = []
    if abs(lufs - LUFS) > LUFS_TOLERANCE:
        problems.append(f"The {_label(ratio)} ad measures {lufs:.1f} LUFS and it should be {LUFS:.0f}, give or take 1.")
    if peak > PEAK_MAX:
        problems.append(f"The {_label(ratio)} ad peaks at {peak:.1f} dBTP and the limit is {PEAK_MAX} dBTP.")
    return Check(name, not problems, f"{lufs:.1f} LUFS, true peak {peak:.1f} dBTP.", problems)


def _bed(job: Job, cut: dict, entries: dict) -> Check:
    voice, bed = job.work / "mix" / "voice.wav", job.work / "mix" / "bed.wav"
    if bed.is_file() and voice.is_file():
        median = mix.bed_under_voice(voice, bed, cut["words"])["median_db"]
        if median is None:
            said = ("The music can't be heard under the voice. Check that the music file has sound in it. "
                    "If bed_offset_db in plan.json is negative, set it back toward 0.")
            return Check("Music under the voice", False, "Not measurable: the bed is silent wherever the voice talks.",
                         [said])
    else:
        median = next((e["bed_median_db"] for e in entries.values() if e.get("bed_median_db") is not None), None)
        if median is None:
            return Check("Music under the voice", True, "No music bed.")
    lo, hi = BED_BAND
    if lo <= median <= hi:
        return Check("Music under the voice", True, f"The music sits {median:.1f} dB under the voice.")
    side = "too loud under the voice" if median < lo else "too quiet to hear on a phone"
    more = (" The mix lifts a very quiet music file by at most 12 dB by itself, so try a louder music file."
            if median > hi else "")
    return Check("Music under the voice", False, f"{median:.1f} dB under the voice.",
                 [f"The music sits {median:.1f} dB under the voice, which is {side}; it should be 12 to 18 dB.{more}"])


def _file(ratio: str, ad: Path, entry: dict) -> Check:
    name, label = f"File {_label(ratio)}", _label(ratio)
    if not ad.is_file():
        return Check(name, False, "The file is missing.", [f"The {label} ad file {ad.name} is missing."])
    problems, size = [], ad.stat().st_size
    if size < MIN_BYTES:
        problems.append(f"The {label} ad file is only {size} bytes, so it didn't finish writing.")
    p = media.probe(ad)
    if not p.has_audio:
        problems.append(f"The {label} ad has no sound.")
    if abs(p.duration - entry["duration"]) > DURATION_TOLERANCE:
        problems.append(f"The {label} ad runs {p.duration:.1f} s and its page was {entry['duration']:.1f} s.")
    if abs(p.fps - FPS) > FPS_TOLERANCE:
        problems.append(f"The {label} ad runs at {p.fps:.1f} frames a second and it should be 30.")
    luma = last_frame_luma(ad) if not problems else None
    if luma is not None and luma < BLACK:
        problems.append(f"The {label} ad ends on a black frame.")
    detail = f"{size / 1e6:.1f} MB, {verify.clock(p.duration)}, {p.fps:.0f} fps, {p.width} by {p.height}."
    return Check(name, not problems, detail, problems)


TOO_CLOSE = "This take is too close for the captions to clear the mouth. Ask the client for a wider take."


def _mouth(entries: dict) -> Check:
    """Fails an ad whose last-resort layout leaves the captions over the mouth (render.json's
    mouth_covered): no layout could clear it, so the person needs a wider take."""
    covered = [r for r in RATIOS if r in entries and entries[r].get("mouth_covered")]
    if not covered:
        return Check("Captions clear of the mouth", True, "No caption covers the speaker's mouth.")
    return Check("Captions clear of the mouth", False, f"{len(covered)} of {len(entries)} ads have captions over the mouth.",
                 [f"{_label(r)}: {TOO_CLOSE}" for r in covered])


def _credits(job: Job, resolved: dict, report: Report) -> Check:
    ids = list(dict.fromkeys(b["clip"] for b in resolved["broll"]))
    report.credits = ids
    if not ids:
        return Check("Credits", True, "No B-roll was used, so no credits are due.")
    path = job.broll_cache / "credits.json"
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    missing = [i for i in ids if stock.source_of(i) != "own" and i not in known]
    problems = [f"There's no {stock.SOURCES[stock.source_of(i)][0]} credit on record for clip {i}; fetch it again with the get option of "
                f"adeditor broll {job.client} {job.video.name} for clip {i}." for i in missing]
    return Check("Credits", not missing, f"{len(ids) - len(missing)} of {len(ids)} clips have a credit.", problems)


def _up_to_date(job: Job, entries: dict, made_from: str) -> Check:
    """Every ratio made, and made from the cut, plan, kit, music and speed there are now."""
    problems, again = [], f"adeditor render {job.client} {job.video.name}"
    for r in RATIOS:
        if r not in entries:
            problems.append(f"The {_label(r)} ad hasn't been made: run {again} to make all three.")
        elif entries[r].get("inputs") != made_from:
            problems.append(f"The {_label(r)} ad was made before the last change to the cut, the plan, the kit, the "
                            f"music or the speed: run {again} to make all three again.")
    if problems:
        return Check("Up to date", False, f"{len(problems)} of {len(RATIOS)} ads aren't up to date.", problems)
    return Check("Up to date", True, "All three ads were made from the cut, plan, kit and speed there are now.")


# The sheet ------------------------------------------------------------------------------------------------

def _joins(cut: dict) -> list[dict]:
    spots = cut.get("tight_words")
    if spots:
        return [{"t": s["t"], "before": (s.get("before") or {}).get("text"), "after": (s.get("after") or {}).get("text")}
                for s in spots]
    return [{"t": t, "before": None, "after": None} for t in cut.get("tight_joins", [])]


def join_line(j: dict) -> str:
    before, after = (f"'{str(w).strip()}'" if w else None for w in (j["before"], j["after"]))
    where = (f"Between {before} and {after}" if before and after else f"Before {after}" if after
             else f"After {before}" if before else "At a cut")
    return f"{where} at {at(j['t'])}"


def _li(ok: bool, name: str, detail: str) -> str:
    return f'<li class="{"pass" if ok else "fail"}"><span class="tag">{"Pass" if ok else "Fail"}</span> ' \
           f"<strong>{esc(name)}.</strong> {esc(detail)}</li>"


def _section(title: str, body: str) -> str:
    return f"<section>\n<h2>{esc(title)}</h2>\n{body}\n</section>"


def web(url) -> str | None:
    """The address when it's an http or https link, else None: nothing else goes in an href."""
    url = str(url or "").strip()
    return url if url.lower().startswith(("https://", "http://")) else None


def _credit_line(clip_id: str, credit: dict | None) -> str:
    source = stock.source_of(clip_id) if stock.valid_id(clip_id) else "pexels"  # credits from before ids carried a source
    if source == "own":
        return f"<li>{esc(stock.OWN_CREDIT)} ({esc(clip_id)})</li>"
    if not credit:
        return f"<li>Clip {esc(clip_id)}: no credit on record.</li>"
    name, home = stock.SOURCES[source]
    who = web(credit.get("user_url"))
    maker = f'<a href="{href(who)}">{esc(credit.get("user", ""))}</a>' if who else esc(credit.get("user", ""))
    return f'<li>Video by {maker} on <a href="{href(web(credit.get("url")) or home)}">{name}</a></li>'


def write_review(job: Job, report: Report, cut: dict) -> Path:
    from string import Template

    ok = report.ok
    if ok:
        summary = '<span class="tag">Pass</span> <strong>All checks passed.</strong>'
        if report.joins:
            summary += " Please listen at the tight joins below before you send the ads."
    else:
        items = "\n".join(f"<li>{esc(f)}</li>" for f in report.failures)
        summary = f'<span class="tag">Fail</span> <strong>These need a look before the ads go out.</strong>\n<ul>\n{items}\n</ul>'
    checks = "\n".join(_li(c.ok, c.name, c.detail) for c in report.checks)
    if report.noise:
        checks += "\n" + "\n".join(f"<li>Far from any cut, so it's the transcriber and not the cut: {esc(n)}.</li>"
                                   for n in report.noise)
    if report.accepted:
        checks += "\n" + "\n".join(f"<li>Accepted after you listened and every word was there: {esc(n)}.</li>"
                                   for n in report.accepted)
    joins = warnings = layout_note = stills = credits = ""
    if report.joins:
        lines = "\n".join(f"<li>{esc(join_line(j))}</li>" for j in report.joins)
        joins = _section("Listen here", "<p>These cuts land between words that run together, on the quietest moment "
                         "between them. The word check heard every word, but only your ear can tell whether one is "
                         f"clipped. Play the ad at each time.</p>\n<ul>\n{lines}\n</ul>")
    notes = [(r, LAYOUT_NOTES[mode]) for r, mode in report.layout.items() if mode in LAYOUT_NOTES]
    if notes:
        rows = "\n".join(f"<li>{esc(_label(r))}: {esc(note)}</li>" for r, note in notes)
        layout_note = _section("Layout", f"<ul>\n{rows}\n</ul>")
    if any(report.warnings.values()):
        rows = "\n".join(f"<li>{esc(_label(r))}: {esc(w)}</li>" for r, ws in report.warnings.items() for w in ws)
        warnings = _section("Warnings", f"<ul>\n{rows}\n</ul>")
    for ratio, items in report.stills.items():
        figs = "\n".join(f'<figure><img src="{href(path)}" alt="{esc(label)} at {t:.1f} s" loading="lazy">'
                         f"<figcaption>{esc(label)}, {t:.1f} s</figcaption></figure>" for label, t, path in items)
        stills += _section(f"Stills, {_label(ratio)}", f'<div class="grid">\n{figs}\n</div>')
    if report.credits:
        path = job.broll_cache / "credits.json"
        known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        lines = "\n".join(_credit_line(i, known.get(i)) for i in report.credits)
        used = [stock.source_of(i) if stock.valid_id(i) else "pexels" for i in report.credits]
        froms = ", ".join(f'<a href="{stock.SOURCES[s][1]}">{stock.SOURCES[s][0]}</a>' for s in stock.SOURCES if s in used)
        where = f"Videos from {froms}. " if froms else ""
        credits = _section("Credits", f'<ul>\n{lines}\n</ul>\n<p>{where}Keep these credits with the ads when you post them.</p>')
    else:
        credits = _section("Credits", "<p>No B-roll in this ad.</p>")
    page = Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        title=esc(job.video.stem), summary_class="pass" if ok else "fail", summary=summary, checks=checks,
        joins=joins, warnings=layout_note + warnings, stills=stills, credits=credits)
    out = job.ads / "review.html"
    out.write_text(page, encoding="utf-8")
    return out


# Running --------------------------------------------------------------------------------------------------

def run(job: Job) -> Report:
    """Check every finished ad, take the stills and write review.html."""
    path = job.work / "render.json"
    if not path.is_file():
        raise NothingToCheck(f"There are no finished ads for {job.video.name} yet: run adeditor render {job.client} "
                             f"{job.video.name}.")
    entries = json.loads(path.read_text(encoding="utf-8"))
    cut = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))
    brand.read(job.client)  # a broken kit is named as broken, not as a plan problem
    resolved = plan.load(job)
    spd = speed.current(job)
    report = Report(joins=_joins(cut))
    ratios = [r for r in RATIOS if r in entries] or list(entries)
    ads = {r: job.ads / entries[r]["file"] for r in ratios}
    first = next((r for r in ratios if ads[r].is_file()), None)
    if first is None:
        raise NothingToCheck(f"The finished ads for {job.video.name} aren't in {job.ads}: run adeditor render "
                             f"{job.client} {job.video.name}.")
    made_from = render.inputs(job)
    current = [r for r in ratios if ads[r].is_file() and entries[r].get("inputs") == made_from]
    # The words are checked on one ad (the three share one mix): the 9:16 unless it's out of date.
    heard_on = current[0] if current else first
    report.checks.append(_word_check(job, ads[heard_on], cut, report, spd))
    report.checks.append(_up_to_date(job, entries, made_from))
    for r in ratios:
        if ads[r].is_file():
            report.checks.append(_loudness(r, ads[r]))
    report.checks.append(_bed(job, cut, entries))
    for r in ratios:
        report.checks.append(_file(r, ads[r], entries[r]))
    report.checks.append(_mouth(entries))
    report.checks.append(_credits(job, resolved, report))
    try:
        face_samples = faces.filled(faces.load(job.work / "faces.json")[1])
    except (faces.NoFace, FileNotFoundError):
        face_samples = []
    for r in ratios:
        report.warnings[r] = list(entries[r].get("warnings", []))
        report.layout[r] = entries[r].get("mode", "fade")
        if ads[r].is_file():
            times = still_times(resolved, face_samples, spd, end_start=entries[r]["end_start"],
                                duration=entries[r]["duration"], window=entries[r].get("window"), ratio=r)
            report.stills[r] = _take_stills(job, r, ads[r], times)
    report.review = write_review(job, report, cut)
    return report


def run_and_tell(job: Job) -> int:
    """Run the checks, say what they found in plain words and return the exit code (1 on a failure)."""
    try:
        report = run(job)
    except (NothingToCheck, brand.NoKit, brand.BadKit) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except plan.PlanError as exc:
        n = len(exc.errors)
        print(f"plan.json has {n} {'problem' if n == 1 else 'problems'} since the ads were made: run adeditor plan check "
              f"{job.client} {job.video.name} to see {'it' if n == 1 else 'them'}, fix plan.json and render again.",
              file=sys.stderr)
        return 1
    except (transcribe.ScribeError, requests.RequestException, keys.MissingKey, media.ToolError) as exc:
        print(f"The checks couldn't run: {transcribe.why(exc)}. Run adeditor check {job.client} {job.video.name} "
              "to try again.", file=sys.stderr)
        return 1
    print(plain(report.summary))
    for n in report.noise:
        print(f"  Far from any cut, so it's the transcriber and not the cut: {plain(n)}.")
    for n in report.accepted:
        print(f"  Accepted after you listened and every word was there: {plain(n)}.")
    if report.joins:
        print("Listen at these cuts, where two words run together:")
        for j in report.joins:
            print(f"  {plain(join_line(j))}")
    print(f"The review sheet is {report.review}")
    return 0 if report.ok else 1


def _args(p) -> None:
    p.add_argument("client")
    p.add_argument("video")


@command("check", help="Check the finished ads and write the review sheet (ads/<video>/review.html).", args=_args)
def check_cmd(ns):
    return run_and_tell(paths.job(ns.client, ns.video))
