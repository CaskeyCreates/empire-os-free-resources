"""The Pexels B-roll source (optional, behind --source pexels): search and look up one clip.

API reference: https://www.pexels.com/api/documentation/ (video search is GET /v1/videos/search,
the key goes raw in the Authorization header, and the response has videos[] with id, url, duration,
user {name, url} and video_files[] with width, height, file_type and link).
The default limits are 200 requests an hour and 20,000 a month. The X-Ratelimit-Remaining and
X-Ratelimit-Reset headers come only on a 2xx response, so the last ones seen are kept here.
Each video has `image` and `video_pictures[]` (preview stills): the contact sheet is built from those,
so only the clip that gets picked is downloaded (GET /v1/videos/videos/:id fetches one by id).
Ids are prefixed here: pexels-<digits>.
Pexels asks every use to link back to Pexels and credit the videographer: credits.json carries
the videographer for each clip in the cache, and `adeditor broll` prints the link.
Pexels has paused new API keys (17 November 2026), so it is the optional source: --source pexels."""
import re
import time

import requests

from adeditor import keys
from adeditor.stock import StockError

SEARCH_URL = "https://api.pexels.com/v1/videos/search"
MIN_SHORT_SIDE = 1080
CREDIT = "Videos from Pexels, pexels.com"  # the API terms ask for a prominent link to Pexels
ID = re.compile(r"[0-9]+")  # a Pexels id is digits; only then does it become part of a file name
LIMIT: dict = {}  # last X-Ratelimit-Remaining and X-Ratelimit-Reset seen


class PexelsError(StockError):
    pass


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _pick_file(files: list[dict]) -> dict | None:
    """The smallest MP4 whose short side is at least 1080, else the largest MP4."""
    mp4 = [f for f in files if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height")]
    if not mp4:
        return None
    short = lambda f: min(f["width"], f["height"])
    big = [f for f in mp4 if short(f) >= MIN_SHORT_SIDE]
    if big:
        return min(big, key=lambda f: (short(f), f["width"] * f["height"]))
    return max(mp4, key=lambda f: (short(f), f["width"] * f["height"]))


def _remember(headers) -> None:
    for name, key in (("X-Ratelimit-Remaining", "remaining"), ("X-Ratelimit-Reset", "reset")):
        try:
            LIMIT[key] = int(headers.get(name))
        except (TypeError, ValueError):
            pass


def search(query: str, cache=None, orientation: str = "portrait", per_page: int = 12) -> list[dict]:
    """Clips for `query` (`cache` is unused; Pexels asks for none): {"id": "pexels-<n>", "url", "user", "user_url", "duration", "file"} where file is the
    chosen MP4 ({"link", "width", "height"})."""
    key = keys.get(keys.PEXELS)
    try:
        r = requests.get(SEARCH_URL, headers={"Authorization": key},
                         params={"query": query, "orientation": orientation, "per_page": per_page}, timeout=30)
    except requests.RequestException as exc:
        raise PexelsError(f"We couldn't reach Pexels ({type(exc).__name__}). Check your internet connection and try again.") from None
    if r.status_code == 429:
        # Pexels sends no rate-limit headers on a 429, and the one we kept from an earlier 2xx is the
        # monthly reset, so it is never used here: without the 429's own header, try again in an hour.
        try:
            ts = float(r.headers.get("X-Ratelimit-Reset"))
        except (TypeError, ValueError):
            ts = time.time() + 3600
        raise PexelsError(f"Pexels says we've hit the hourly limit; try again after {_when(ts)}")
    if r.status_code in (401, 403):
        raise PexelsError(f"Pexels didn't accept {keys.PEXELS}. Check it in ai-ad-editor/.env is the full key with no spaces.")
    if r.status_code != 200:
        raise PexelsError(f"Pexels answered with HTTP {r.status_code} for that search. Try again in a minute.")
    _remember(r.headers)
    out = []
    for v in r.json().get("videos", []):
        clip = _clip(v)
        if clip:
            out.append(clip)
    return out


def _clip(v: dict) -> dict | None:
    """A search result as a candidate, or None when it has no usable MP4 or its id isn't digits."""
    if not ID.fullmatch(str(v.get("id", ""))):
        return None
    f = _pick_file(v.get("video_files", []))
    if f is None:
        return None
    user = v.get("user") or {}
    stills = [p["picture"] for p in sorted(v.get("video_pictures") or [], key=lambda p: p.get("nr", 0)) if p.get("picture")]
    if not stills and v.get("image"):
        stills = [v["image"]]
    return {"id": f"pexels-{v['id']}", "url": v.get("url", ""), "user": user.get("name", ""),
            "user_url": user.get("url", ""), "duration": v.get("duration", 0),
            "file": {"link": f["link"], "width": f["width"], "height": f["height"]}, "previews": stills}


def lookup(clip_id: str) -> dict:
    """One clip by its prefixed id (for --get when no search remembers it)."""
    number = clip_id.split("-", 1)[1]
    key = keys.get(keys.PEXELS)
    try:
        r = requests.get(f"https://api.pexels.com/v1/videos/videos/{number}", headers={"Authorization": key}, timeout=30)
    except requests.RequestException as exc:
        raise PexelsError(f"We couldn't reach Pexels ({type(exc).__name__}). Check your internet connection and try again.") from None
    if r.status_code == 404:
        raise PexelsError(f"Pexels has no video {clip_id}. Check the id on the contact sheet.")
    if r.status_code == 429:
        raise PexelsError("Pexels says we've hit the hourly limit; try again in an hour")
    if r.status_code != 200:
        raise PexelsError(f"Pexels answered with HTTP {r.status_code} for video {clip_id}. Try again in a minute.")
    clip = _clip(r.json())
    if clip is None:
        raise PexelsError(f"Pexels video {clip_id} has no MP4 file we can use.")
    return clip


def still(url: str) -> bytes:
    try:
        r = requests.get(url, timeout=30)  # a CDN picture: the key is not sent
        r.raise_for_status()
        return r.content
    except requests.RequestException as exc:
        raise PexelsError(f"Downloading a Pexels preview failed ({type(exc).__name__}). Try again.") from None
