"""`adeditor broll`: find B-roll, look at it on a contact sheet, download only the clip you pick.

Sources: Pixabay (the default when PIXABAY_API_KEY is set), Pexels (--source pexels, or the default
when only PEXELS_API_KEY is set) and the client's own clips (--own, MP4, MOV or M4V files in
clients/<client>/broll/own/). Clip ids carry their source: pixabay-<n>, pexels-<n>, own-<slug>."""
import io
import re
import sys

from PIL import Image

from adeditor import contactsheet, keys, media, paths, pexels, pixabay, stock
from adeditor.cli import command

SOURCE_LINES = {"pixabay": (pixabay.CREDIT, "Videos from Pixabay (https://pixabay.com)."),
                "pexels": (pexels.CREDIT, "Videos from Pexels (https://www.pexels.com)."),
                "own": (stock.OWN_CREDIT, stock.OWN_CREDIT + ".")}
KEYS = {"pixabay": keys.PIXABAY, "pexels": keys.PEXELS}


def choose_source(explicit: str | None) -> str:
    """The --source when given (its key must be set), else Pixabay, else Pexels, else a plain stop."""
    if explicit:
        if not keys.present(KEYS[explicit]):
            raise stock.StockError(f"Missing {KEYS[explicit]}. Add {KEYS[explicit]}=your-key to ai-ad-editor/.env, "
                                   "or use --source with the other one, or --own for your client's own clips.")
        return explicit
    for name in ("pixabay", "pexels"):
        if keys.present(KEYS[name]):
            return name
    raise stock.StockError("No stock video key is set. Add PIXABAY_API_KEY=your-key to ai-ad-editor/.env (a free key is on your "
                           "Pixabay account page). You can still use your client's own clips: put MP4, MOV or M4V files in "
                           "clients/<client>/broll/own/ and run adeditor broll <client> <video> --own.")


def stock_rows(source: str, clips: list[dict], cache) -> list[tuple[str, list]]:
    rows = []
    for clip in clips:
        if source == "pixabay" and not clip.get("preview_file"):
            continue  # no small rendition to look at; --get can still fetch it by id
        if source == "pexels":
            frames = []
            for url in contactsheet.spread(clip.get("previews", [])):
                with Image.open(io.BytesIO(pexels.still(url))) as im:
                    frames.append(im.convert("RGB"))
        else:
            frames = contactsheet.video_frames(pixabay.tiny_file(clip, cache), clip.get("duration") or None)
        rows.append((f"{clip['id']}  {clip['duration']:.1f} s  {clip['user']}", frames))
    return rows


def own_rows(clips: dict) -> tuple[list, list]:
    rows, infos = [], []
    for clip_id, f in clips.items():
        p = media.probe(f)
        rows.append((f"{clip_id}  {p.duration:.1f} s  {f.name}", contactsheet.video_frames(f, p.duration)))
        infos.append((clip_id, p, f))
    return rows, infos


def _args(p) -> None:
    p.add_argument("client")
    p.add_argument("video")
    p.add_argument("query", nargs="?", help="what to search for")
    p.add_argument("--orientation", choices=["portrait", "landscape", "square"], default="portrait")
    p.add_argument("--source", choices=["pixabay", "pexels"], help="where to search (default: Pixabay, else Pexels)")
    p.add_argument("--own", action="store_true", help="use your client's own clips from clients/<client>/broll/own/")
    p.add_argument("--get", metavar="ID", help="download the one clip you picked, by its id, like pixabay-1234567")


def _get(ns, job) -> int:
    clip_id = ns.get.strip()
    if not stock.valid_id(clip_id):
        raise stock.StockError(f"A clip id looks {stock.ID_HELP}, and {clip_id!r} doesn't. Copy it from the contact sheet.")
    source = stock.source_of(clip_id)
    if source == "own":
        print(f"{clip_id} is your client's own clip, so it's already local: {stock.file_for(clip_id, job.broll_cache)}")
        return 0
    clip = stock.candidate(clip_id, job.broll_cache)
    if clip is None:
        clip = pexels.lookup(clip_id) if source == "pexels" else pixabay.lookup(clip_id, job.broll_cache)
    [dest] = stock.fetch([clip], job.broll_cache)
    print(f"Saved clip {clip_id} to {dest}")
    print(f"Its credit is in clients/{ns.client}/broll/credits.json. {SOURCE_LINES[source][1]}")
    return 0


def _sheet(ns, job) -> int:
    slug = stock.slug(ns.query) if ns.query else "own"
    out = job.work / "broll" / f"{slug}.jpg"
    if ns.own:
        clips = stock.own_clips(job.broll_cache)
        if not clips:
            raise stock.StockError(f"There are no clips in {stock.own_dir(job.broll_cache)}. Put your client's MP4, MOV or M4V files there.")
        rows, infos = own_rows(clips)
        path = contactsheet.sheet(rows, stock.OWN_CREDIT, out, infos[0][1].height >= infos[0][1].width)
        for clip_id, p, f in infos:
            print(f"{clip_id}  {p.duration:.1f} s  {p.width}x{p.height}  {f.name}")
        source = "own"
    else:
        source = choose_source(ns.source)
        name = source.capitalize()
        found = pexels.search(ns.query, None, ns.orientation) if source == "pexels" else pixabay.search(ns.query, job.broll_cache, ns.orientation)
        if not found:
            print(f"{name} found no clips for '{ns.query}'. Try plainer words.", file=sys.stderr)
            return 1
        stock.remember_candidates(found, job.broll_cache)
        f = found[0]["file"]
        path = contactsheet.sheet(stock_rows(source, found, job.broll_cache), SOURCE_LINES[source][0], out, f["height"] >= f["width"])
        for clip in found:
            f = clip["file"]
            print(f"{clip['id']}  {clip['duration']:.1f} s  {f['width']}x{f['height']}  by {clip['user']} ({clip['user_url']})")
    print(f"Contact sheet: {path}")
    print(f"Pick by eye, then run: adeditor broll {ns.client} {ns.video} --get <id>" if source != "own"
          else "Pick by eye, then use the clip's id in plan.json. Own clips are already local.")
    print(SOURCE_LINES[source][1])
    if source == "pexels" and "remaining" in pexels.LIMIT:
        print(f"Pexels requests left this month: {pexels.LIMIT['remaining']}.")
    return 0


@command("broll", help="Search for B-roll and make a contact sheet (Pixabay, Pexels or --own), or download the pick with --get.", args=_args)
def broll_cmd(ns):
    if sum(bool(x) for x in (ns.query, ns.get, ns.own)) != 1:
        print('Give either a search ("tired desk worker"), --own for your client\'s own clips, or --get <id> for the clip you picked.', file=sys.stderr)
        return 1
    job = paths.job(ns.client, ns.video)
    try:
        return _get(ns, job) if ns.get else _sheet(ns, job)
    except (stock.StockError, keys.MissingKey, media.ToolError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
