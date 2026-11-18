"""Retakes and fillers: find the earlier attempts at a line so the cut keeps the last full take.

Ported from a tested rough-cut module. Three rules find retakes: a false start that trails off
and restarts after a pause, the same line said twice, and a phrase said twice straight off.
Claude reads the transcript too, in the conversation, and adds strikes to work/strikes.json
with a "why" that starts "Claude: ". Nothing is cut until `adeditor approve-cut`."""
import difflib
import json
import re
import string
import sys

import requests

from adeditor import brand, keys, paths, transcribe
from adeditor.cli import command
from adeditor.paths import Job

RESTART_PAUSE = 0.8  # a pause this long after a line that trails off is where a restart begins
SENTENCE_PAUSE = 3.0  # a pause this long also ends a sentence
# Two consecutive lines sharing this much of the shorter one are two takes of one line.
REPEAT_RATIO = 0.8
REPEAT_MIN_WORDS = 3
# The later take must hold at least this share of the earlier's words, or it is an echo.
REPEAT_MIN_SHARE = 0.5
# A phrase this long said twice straight off is a stumble; longer runs are looked for first.
PHRASE_MIN, PHRASE_MAX = 3, 12
EPS = 1e-6

SENTENCE_END = re.compile(r"[.?!][\"'”’)\]]*$")
FILLER = re.compile(r"^(?:u+m+|u+h+m*|e+r+m*|a+h+|h+m+|m{2,})$")
_STRIP = string.punctuation + "“”…–—"

Strike = dict  # {"from": int, "to": int, "why": str}, inclusive word indices


def norm(text: str) -> str:
    """A word as compared: lower case, punctuation off its ends, curly apostrophes made straight."""
    return text.lower().replace("’", "'").replace("‘", "'").strip(_STRIP)


def stem(text: str) -> str:
    """A contraction's first part, so "you're" restarts as "you"."""
    return norm(text).split("'")[0]


def lines(words) -> list[list[int]]:
    """Word indices grouped by line: a sentence ends at . ? ! or a pause of 3 s or more, and a
    line also ends at a pause of 0.8 s or more."""
    out: list[list[int]] = []
    for i, w in enumerate(words):
        if not out:
            out.append([])
        out[-1].append(i)
        if i + 1 < len(words):
            gap = words[i + 1]["start"] - w["end"]
            if SENTENCE_END.search(w["word"]) or gap >= RESTART_PAUSE - EPS or gap >= SENTENCE_PAUSE - EPS:
                out.append([])
    return out


def _rule_retakes(words, grouped):
    """(first, last, why) word positions of each earlier attempt the rules find."""
    found = []
    for a, b in zip(grouped, grouped[1:]):
        last, first = words[a[-1]], words[b[0]]
        # 1. A trail-off, a pause, and a restart on the same two words.
        if (not SENTENCE_END.search(last["word"]) and len(b) >= 2
                and first["start"] - last["end"] >= RESTART_PAUSE - EPS):
            head = [stem(words[k]["word"]) for k in b[:2]]
            stems = [stem(words[k]["word"]) for k in a]
            if all(head):
                for p in range(len(a) - 1):
                    if stems[p:p + 2] == head:
                        found.append((a[p], a[-1], "false start, said again after a pause"))
                        break
        # 2. The same line said twice.
        x = [n for n in (norm(words[k]["word"]) for k in a) if n]
        y = [n for n in (norm(words[k]["word"]) for k in b) if n]
        short = min(len(x), len(y))
        if short >= REPEAT_MIN_WORDS and len(y) >= REPEAT_MIN_SHARE * len(x):
            blocks = [m for m in difflib.SequenceMatcher(None, x, y, autojunk=False).get_matching_blocks() if m.size]
            matched = sum(m.size for m in blocks)
            # The shorter line's first word is part of the match: a retake starts the same way.
            opens = any((m.a if len(x) <= len(y) else m.b) == 0 for m in blocks)
            if opens and matched / short >= REPEAT_RATIO - EPS:
                found.append((a[0], a[-1], "the same line, said again"))
    # 3. A phrase said twice straight off.
    normed = [norm(w["word"]) for w in words]
    i = 0
    while i < len(words):
        hit = 0
        for k in range(min(PHRASE_MAX, (len(words) - i) // 2), PHRASE_MIN - 1, -1):
            if all(normed[i:i + k]) and normed[i:i + k] == normed[i + k:i + 2 * k]:
                hit = k
                break
        if hit:
            found.append((i, i + hit - 1, "a phrase said twice"))
            i += hit
        else:
            i += 1
    return found


def find(words) -> list[Strike]:
    """The earlier attempts the three rules find, as inclusive word ranges. Flagged words become
    one strike per run within a line, with every reason that touched it, so strikes never overlap."""
    if not words:
        return []
    grouped = lines(words)
    line_of = {i: n for n, line in enumerate(grouped) for i in line}
    reasons: dict[int, list[str]] = {}
    for start, end, why in _rule_retakes(words, grouped):
        for k in range(start, end + 1):
            reasons.setdefault(k, []).append(why)
    runs: list[list[int]] = []
    for k in sorted(reasons):
        if runs and k == runs[-1][-1] + 1 and line_of[k] == line_of[k - 1]:
            runs[-1].append(k)
        else:
            runs.append([k])
    out = []
    for run in runs:
        whys: list[str] = []
        for k in run:
            for why in reasons[k]:
                if why not in whys:
                    whys.append(why)
        out.append({"from": run[0], "to": run[-1], "why": "; ".join(whys)})
    return out


def fillers(words) -> list[int]:
    """Indices of filler words (um, uh, erm, er, ah, hmm, mm, uhm, stretched or punctuated) and of
    a cut-off word: one ending in a single hyphen whose next word starts with the same letters
    ("pre-" then "pre-qualified"). Never "like" or "you know"."""
    out = []
    for i, w in enumerate(words):
        text = w["word"].strip()
        if FILLER.match(norm(text)):
            out.append(i)
        elif text.endswith("-") and not text.endswith("-" * 2) and i + 1 < len(words):
            head = norm(text)
            if head and norm(words[i + 1]["word"]).startswith(head):
                out.append(i)
    return out


def proposal_path(job: Job):
    return job.work / "strikes.json"


class BadProposal(ValueError):
    """strikes.json can't be read. The message says where, in one plain sentence."""


def read_proposal(path) -> dict:
    """strikes.json read, or BadProposal naming the line when it isn't valid JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BadProposal(f"strikes.json isn't valid JSON ({exc.msg} on line {exc.lineno}): fix that line, "
                          "then run the step again.") from None
    if not isinstance(data, dict):
        raise BadProposal('strikes.json should be one JSON object, like {"strikes": [], "fillers": [], "approved": false}.')
    return data


def write_proposal(job: Job, words) -> dict:
    """Write work/strikes.json for Claude and the owner to read, edit and approve."""
    proposal = {"strikes": find(words), "fillers": fillers(words), "approved": False}
    job.work.mkdir(parents=True, exist_ok=True)
    proposal_path(job).write_text(json.dumps(proposal, indent=1), encoding="utf-8")
    return proposal


def _plain(text: str) -> str:
    return text.replace("—", ",").replace("–", ",").replace("-" * 2, ",")


def describe(words, proposal) -> str:
    """One plain line per strike and one line listing the fillers with their count."""
    out = []
    for s in proposal["strikes"]:
        said = " ".join(w["word"] for w in words[s["from"]:s["to"] + 1])
        out.append(_plain(f"Cut the first try at: '{said}' ({s['why']})"))
    idx = proposal["fillers"]
    if idx:
        noun = "filler" if len(idx) == 1 else "fillers"
        out.append(_plain(f"Cut {len(idx)} {noun}: " + ", ".join(words[i]["word"].strip(_STRIP) for i in idx)))
    else:
        out.append("No fillers found.")
    return "\n".join(out)


def _number(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check_proposal(proposal: dict, count: int) -> str | None:
    """A plain sentence naming the first problem with the strikes, or None when they are fine."""
    if not isinstance(proposal, dict):
        return 'strikes.json should be one JSON object, like {"strikes": [], "fillers": [], "approved": false}.'
    strikes, fillers = proposal.get("strikes", []), proposal.get("fillers", [])
    if not isinstance(strikes, list):
        return 'In strikes.json, "strikes" should be a list, like [{"from": 3, "to": 5, "why": "..."}].'
    for s in strikes:
        if not isinstance(s, dict) or "from" not in s or "to" not in s:
            return ('Each strike in strikes.json needs "from" and "to", the first and last word numbers it cuts, '
                    'like {"from": 3, "to": 5, "why": "..."}.')
        if not (_number(s["from"]) and _number(s["to"])):
            return (f"A strike runs from {json.dumps(s['from'])} to {json.dumps(s['to'])}: from and to are word "
                    'numbers without quotes, like {"from": 3, "to": 5}.')
    if not isinstance(fillers, list) or not all(_number(i) for i in fillers):
        return 'In strikes.json, "fillers" should be a list of word numbers without quotes, like [4, 17].'
    spans = []
    for s in strikes:
        a, b = s["from"], s["to"]
        if a > b:
            return f"A strike runs from word {a} to word {b}, which ends before it starts. Fix it in strikes.json."
        if a < 0 or b >= count:
            return f"A strike points past the words: {a} to {b}, and there are only {count} words (0 to {count - 1})."
        spans.append((a, b))
    spans.sort()
    for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
        if a2 <= b1:
            return f"Two strikes overlap: words {a1} to {b1} and words {a2} to {b2}. Merge or trim them in strikes.json."
    for i in fillers:
        if not 0 <= i < count:
            return f"A filler points past the words: word {i}, and there are only {count} words."
    return None


def _job(ns, need_raw: bool = False) -> Job:
    return paths.job(ns.client, ns.video, need_raw)


def _args(p):
    p.add_argument("client")
    p.add_argument("video")


def keyterms(job: Job) -> list[str]:
    """The client's key terms from brand/kit.json, for the transcriber; none when there's no kit.
    Raises brand.BadKit, in plain words, when the kit is there but broken."""
    path = job.brand / "kit.json"
    if not path.exists():
        return []
    terms = brand.parse_kit(path, job.client).get("keyterms") or []
    if not isinstance(terms, list) or not all(isinstance(term, str) for term in terms):
        raise brand.BadKit(f'In clients/{job.client}/brand/kit.json, "keyterms" should be a list of words or short '
                           'phrases in quotes, like ["Northbank"].')
    return terms


@command("prepare", help="Transcribe a raw video and propose the retakes and fillers to cut.", args=_args)
def prepare_cmd(ns):
    job = _job(ns, need_raw=True)
    try:
        words = transcribe.prepare(job, keyterms(job))
    except (transcribe.NoSound, keys.MissingKey, brand.BadKit) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (transcribe.ScribeError, requests.RequestException) as exc:
        # The key never appears here: Scribe errors carry the status and a short reason only.
        print(f"The transcription couldn't run: {transcribe.why(exc)}. Run adeditor prepare {job.client} "
              f"\"{job.video}\" again to retry.", file=sys.stderr)
        return 1
    proposal = write_proposal(job, words)
    print(describe(words, proposal))
    return 0


@command("approve-cut", help="Approve work/strikes.json so the cut can use it.", args=_args)
def approve_cmd(ns):
    job = _job(ns)
    path, transcript = proposal_path(job), job.work / "transcript.json"
    if not path.exists() or not transcript.exists():
        print("There's no cut proposal for this video yet. Run adeditor prepare first.", file=sys.stderr)
        return 1
    try:
        proposal = read_proposal(path)
    except BadProposal as exc:
        print(str(exc), file=sys.stderr)
        return 1
    count = len(json.loads(transcript.read_text(encoding="utf-8"))["words"])
    problem = check_proposal(proposal, count)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    proposal["approved"] = True
    path.write_text(json.dumps(proposal, indent=1), encoding="utf-8")
    print(f"Approved {len(proposal['strikes'])} strikes and {len(proposal['fillers'])} fillers.")
    return 0
