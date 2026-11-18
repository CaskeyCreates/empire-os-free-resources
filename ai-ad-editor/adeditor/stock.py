"""What every B-roll source shares: clip ids, the cache, the client's own clips, downloads and credits.

A clip id carries its source: pixabay-<digits>, pexels-<digits> or own-<slug>. Only an id that matches
one of those forms ever becomes a file name. Stock clips are cached as <id>.mp4 in
clients/<client>/broll/ with who shot them in credits.json; a person's own clips are dropped in
clients/<client>/broll/own/ and are never copied or renamed.

Pixabay's rules, which this module keeps (https://pixabay.com/api/docs/ and
https://pixabay.com/service/license-summary/): show where videos come from, no systematic mass
downloads (only the picked clip is downloaded in full), credit is welcome though not required."""
import json
import re
from pathlib import Path

import requests

STOCK_ID = re.compile(r"(pixabay|pexels)-[0-9]+")
OWN_ID = re.compile(r"own-[a-z0-9]+(?:-[a-z0-9]+)*")
OWN_SUFFIXES = (".mp4", ".mov", ".m4v")
SOURCES = {
    "pixabay": ("Pixabay", "https://pixabay.com"),
    "pexels": ("Pexels", "https://www.pexels.com"),
}
OWN_CREDIT = "The client's own footage"
ID_HELP = 'like "pixabay-1234567", "pexels-1234567" or "own-my-clip"'


class StockError(Exception):
    pass


def valid_id(clip_id) -> bool:
    text = str(clip_id)
    return bool(STOCK_ID.fullmatch(text) or OWN_ID.fullmatch(text))


def source_of(clip_id: str) -> str:
    """"pixabay", "pexels" or "own" for a valid id."""
    return str(clip_id).split("-", 1)[0]


def slug(stem: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "clip"


def own_dir(cache: Path) -> Path:
    return Path(cache) / "own"


def own_clips(cache: Path) -> dict[str, Path]:
    """The client's own clips by id: own-<slug of the file's stem>, in file name order, with -2, -3 on a repeat."""
    folder = own_dir(cache)
    found: dict[str, Path] = {}
    if not folder.is_dir():
        return found
    for f in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not f.is_file() or f.suffix.lower() not in OWN_SUFFIXES:
            continue
        base = "own-" + slug(f.stem)
        clip_id, n = base, 1
        while clip_id in found:
            n += 1
            clip_id = f"{base}-{n}"
        found[clip_id] = f
    return found


def file_for(clip_id: str, cache: Path) -> Path:
    """Where a clip's file is (it may not exist yet for a stock clip). The id must be valid."""
    if not valid_id(clip_id):
        raise StockError(f"A clip id looks {ID_HELP}, and {clip_id!r} doesn't.")
    if source_of(clip_id) == "own":
        found = own_clips(cache).get(clip_id)
        if found is None:
            raise StockError(f"There's no file for {clip_id} in {own_dir(cache)}.")
        return found
    return Path(cache) / f"{clip_id}.mp4"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def remember_candidates(clips: list[dict], cache: Path) -> None:
    """Keep every candidate's details so --get can find one without a second search."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "candidates.json"
    known = read_json(path)
    known.update({c["id"]: c for c in clips})
    path.write_text(json.dumps(known, indent=1) + "\n", encoding="utf-8")


def candidate(clip_id: str, cache: Path) -> dict | None:
    return read_json(Path(cache) / "candidates.json").get(clip_id)


def fetch(clips: list[dict], cache: Path) -> list[Path]:
    """Download each picked clip once into `cache` as <id>.mp4 and record who shot it in credits.json."""
    bad = [str(c.get("id")) for c in clips if not STOCK_ID.fullmatch(str(c.get("id", "")))]
    if bad:
        raise StockError(f"A stock clip id looks {ID_HELP}, and {bad[0]!r} doesn't.")
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    credits_path = cache / "credits.json"
    credits = read_json(credits_path)
    out = []
    for clip in clips:
        dest = cache / f"{clip['id']}.mp4"
        if not dest.exists():
            download(clip["file"]["link"], dest, clip["id"])
        credits[clip["id"]] = {"source": source_of(clip["id"]), "user": clip["user"],
                               "user_url": clip["user_url"], "url": clip["url"]}
        out.append(dest)
    credits_path.write_text(json.dumps(credits, indent=2) + "\n", encoding="utf-8")
    return out


def download(link: str, dest: Path, label: str) -> None:
    """One file from a CDN link (no key is sent) to `dest`, through a .part file."""
    part = dest.with_suffix(".part")
    try:
        r = requests.get(link, stream=True, timeout=120)
        r.raise_for_status()
        with part.open("wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    except requests.RequestException as exc:
        part.unlink(missing_ok=True)
        raise StockError(f"Downloading clip {label} failed ({type(exc).__name__}). Try again.") from None
    part.replace(dest)
