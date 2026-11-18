"""Pixabay video search, the default B-roll source.

API reference: https://pixabay.com/api/docs/ (GET https://pixabay.com/api/videos/ with key, q up to
100 characters, per_page 3 to 200, min_width and min_height; hits carry id, pageURL, duration, user and
user_id; videos has four renditions, large, medium, small and tiny, each with url, width, height,
size and thumbnail; a large rendition that isn't available has an empty url). Its terms, which bind
this module:
- Responses are cached for 24 hours: each search is saved by its query and parameters in the
  client's broll folder and reused while it is younger than that.
- 100 requests per 60 seconds per key; a 429 means wait a minute.
- Show where results come from: the contact sheet and review sheet say "Videos from Pixabay" with a
  link to https://pixabay.com, and each used clip is credited "Video by <user> on Pixabay".
- No systematic mass downloads: a search downloads nothing but each candidate's tiny rendition
  for the contact sheet; only the picked clip is downloaded in full.
Licence summary: https://pixabay.com/service/license-summary/ (credit is welcome, not required; no
clip with a recognisable trademark, logo or brand; nothing misleading).

The key travels in the query string, so it must never reach a message, a log or a file: requests are
built with params (never a URL string we print), a request error is replaced by a plain message, and the
cache file name is a hash that leaves the key out."""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

from adeditor import keys
from adeditor.stock import StockError, download

URL = "https://pixabay.com/api/videos/"
MIN_SHORT_SIDE = 1080
CACHE_SECONDS = 24 * 3600  # Pixabay asks for responses to be cached for 24 hours
CREDIT = "Videos from Pixabay, pixabay.com"
QUERY_MAX = 100
PER_PAGE = 12


class PixabayError(StockError):
    pass


def _short(r: dict) -> int:
    return min(r["width"], r["height"])


def _pick_file(renditions: dict) -> dict | None:
    """The smallest rendition whose short side is at least 1080, else the largest. Renditions with
    no url (an unavailable large one) don't count."""
    have = [r for r in (renditions or {}).values() if isinstance(r, dict) and r.get("url") and r.get("width") and r.get("height")]
    if not have:
        return None
    big = [r for r in have if _short(r) >= MIN_SHORT_SIDE]
    if big:
        return min(big, key=lambda r: (_short(r), r["width"] * r["height"]))
    return max(have, key=lambda r: (_short(r), r["width"] * r["height"]))


def _clip(hit: dict) -> dict | None:
    """A search hit as a candidate, or None without a usable id or file."""
    try:
        number = int(hit["id"])
    except (KeyError, TypeError, ValueError):
        return None
    if number < 0:
        return None
    videos = hit.get("videos") or {}
    f = _pick_file(videos)
    if f is None:
        return None
    user = str(hit.get("user") or "")
    user_url = ""
    uid = str(hit.get("user_id") or "")
    if user and re.fullmatch(r"[0-9]+", uid):  # anything else: no profile link, the credit links the page
        user_url = f"https://pixabay.com/users/{quote(user)}-{uid}/"
    # The sheet's preview is only ever a small rendition (tiny, else small, else medium): never the
    # large or 1080p file, so a search can't download full-size clips. No such rendition: no preview.
    preview = next((r["url"] for n in ("tiny", "small", "medium") if isinstance(r := videos.get(n), dict) and r.get("url")), "")
    return {"id": f"pixabay-{number}", "url": hit.get("pageURL", ""), "user": user, "user_url": user_url,
            "duration": hit.get("duration", 0),
            "file": {"link": f["url"], "width": f["width"], "height": f["height"]},
            "preview_file": preview}


def _request(params: dict) -> dict:
    """One API call. The key is in `params` only; nothing here prints or keeps it."""
    try:
        r = requests.get(URL, params=params, timeout=30)
    except requests.RequestException as exc:
        raise PixabayError(f"We couldn't reach Pixabay ({type(exc).__name__}). Check your internet connection and try again.") from None
    if r.status_code == 429:
        raise PixabayError("Pixabay says we've made too many requests (100 a minute). Wait a minute, then try again.")
    if r.status_code in (401, 403):
        raise PixabayError(f"Pixabay didn't accept {keys.PIXABAY}. Check it in ai-ad-editor/.env is the full key with no spaces.")
    if r.status_code != 200:
        raise PixabayError(f"Pixabay answered with HTTP {r.status_code} for that search. Check the key in "
                           f"{keys.PIXABAY} and the search words, then try again in a minute.")
    try:
        body = r.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise PixabayError("Pixabay sent an answer we couldn't read. Try again in a minute.")
    return body


def _cached(params: dict, cache: Path) -> dict:
    """The response for these parameters (the key left out), from the 24 hour cache when it's fresh."""
    shown = {k: v for k, v in params.items() if k != "key"}
    name = "search-" + hashlib.sha1(json.dumps(shown, sort_keys=True).encode("utf-8")).hexdigest()[:16] + ".json"
    path = Path(cache) / name
    if path.is_file() and time.time() - path.stat().st_mtime < CACHE_SECONDS:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    body = _request(params)
    Path(cache).mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return body


def search(query: str, cache: Path, orientation: str = "portrait", per_page: int = PER_PAGE) -> list[dict]:
    """Clips for `query`: {"id": "pixabay-<n>", "url", "user", "user_url", "duration", "file", "preview_file"}.
    Asks for at least 1080 high and, when that finds nothing, asks again without. Clips that match
    the orientation come first (Pixabay has no orientation filter for video)."""
    key = keys.get(keys.PIXABAY)
    q = query.strip()[:QUERY_MAX]
    base = {"q": q, "per_page": min(max(per_page, 3), 200)}
    body = _cached({"key": key, **base, "min_height": MIN_SHORT_SIDE}, cache)
    if not body.get("hits"):
        body = _cached({"key": key, **base}, cache)
    clips = [c for c in (_clip(h) for h in body.get("hits", [])) if c]
    want_tall = orientation == "portrait"
    wide = orientation == "landscape"
    fits = lambda c: (c["file"]["height"] >= c["file"]["width"]) if want_tall else (c["file"]["width"] >= c["file"]["height"] if wide else True)
    return sorted(clips, key=lambda c: not fits(c))


def lookup(clip_id: str, cache: Path) -> dict:
    """One clip by its id (for --get when no search remembers it)."""
    key = keys.get(keys.PIXABAY)
    number = clip_id.split("-", 1)[1]
    clip = next((c for c in (_clip(h) for h in _cached({"key": key, "id": number}, cache).get("hits", [])) if c), None)
    if clip is None:
        raise PixabayError(f"Pixabay has no usable video {clip_id}. Check the id on the contact sheet.")
    return clip


def tiny_file(clip: dict, cache: Path) -> Path:
    """The candidate's small rendition, cached, for the contact sheet's frames."""
    if not clip.get("preview_file"):
        raise PixabayError(f"{clip['id']} has no small preview, so it isn't on the contact sheet.")
    dest = Path(cache) / f"{clip['id']}-tiny.mp4"
    if not dest.exists():
        download(clip["preview_file"], dest, clip["id"])
    return dest
