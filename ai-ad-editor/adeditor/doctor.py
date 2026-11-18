"""`adeditor doctor`: checks everything the tool needs and says how to fix what is missing."""
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from adeditor import faces, keys, media
from adeditor.cli import command

HYPERFRAMES = "hyperframes@0.7.5"
FILTERS = ["loudnorm", "sidechaincompress", "atempo", "acrossfade", "amix", "ebur128"]
FFMPEG_FIX = "Mac: brew install ffmpeg. Windows: winget install Gyan.FFmpeg."
# The face model: Google's BlazeFace short-range file. The URL is the one MediaPipe's face detector
# guide links: https://ai.google.dev/edge/mediapipe/solutions/vision/face_detector/python
FACE_MODEL_PATH: Path = faces.MODEL_PATH
FACE_MODEL_URL: str = faces.MODEL_URL
MIN_MODEL_BYTES = 100_000


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    warn: bool = False  # passes, but the person should know something


def _run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([media.tool(args[0]), *args[1:]], capture_output=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def check_python(fix: bool = False) -> Check:
    v = sys.version_info
    ok = (3, 10) <= (v.major, v.minor) < (3, 13)
    return Check("python", ok, f"{v.major}.{v.minor}.{v.micro}",
                 "" if ok else "Use Python 3.10 to 3.12. Run it through uv (uv run adeditor doctor) and uv installs 3.12.")


def check_ffmpeg(fix: bool = False) -> Check:
    if not shutil.which("ffmpeg"):
        return Check("ffmpeg", False, "not found on PATH", FFMPEG_FIX)
    try:
        out = _run(["ffmpeg", "-hide_banner", "-filters"]).stdout
    except (OSError, subprocess.SubprocessError, media.ToolError) as exc:
        return Check("ffmpeg", False, f"could not run: {exc}", FFMPEG_FIX)
    available = set(re.findall(r"^\s*\S+\s+(\w+)\s", out, flags=re.M))
    missing = [f for f in FILTERS if f not in available]
    if missing:
        return Check("ffmpeg", False, "missing filters: " + ", ".join(missing),
                     "Install a full build of ffmpeg. " + FFMPEG_FIX)
    return Check("ffmpeg", True, "all mix filters present")


def check_ffprobe(fix: bool = False) -> Check:
    if shutil.which("ffprobe"):
        return Check("ffprobe", True, "found")
    return Check("ffprobe", False, "not found on PATH", FFMPEG_FIX)


def check_node(fix: bool = False) -> Check:
    fixmsg = "Install Node 22 or newer from https://nodejs.org (or: brew install node, winget install OpenJS.NodeJS)."
    if not shutil.which("node"):
        return Check("node", False, "not found on PATH", fixmsg)
    try:
        text = _run(["node", "--version"]).stdout.strip()
        major = int(text.lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError, media.ToolError):
        return Check("node", False, "could not read the version", fixmsg)
    return Check("node", major >= 22, text, "" if major >= 22 else fixmsg)


def check_hyperframes(fix: bool = False) -> Check:
    fixmsg = f"Check your internet connection, then run: npx -y {HYPERFRAMES} --version"
    try:
        r = _run(["npx", "-y", HYPERFRAMES, "--version"])
    except (OSError, subprocess.SubprocessError, media.ToolError) as exc:
        return Check("hyperframes", False, f"could not run: {exc}", fixmsg)
    version = r.stdout.strip()
    ok = r.returncode == 0 and version == HYPERFRAMES.split("@")[1]
    return Check("hyperframes", ok, version or "no output", "" if ok else fixmsg)


def check_hyperframes_browser(fix: bool = False) -> Check:
    # `hyperframes doctor` exits 0 even when a check fails, so read its Chrome line.
    fixmsg = f"Run: npx -y {HYPERFRAMES} browser ensure"
    cmd = ["npx", "-y", HYPERFRAMES, "browser", "ensure"] if fix else ["npx", "-y", HYPERFRAMES, "doctor"]
    try:
        r = _run(cmd, timeout=600)
        if fix:
            r = _run(["npx", "-y", HYPERFRAMES, "doctor"])
    except (OSError, subprocess.SubprocessError, media.ToolError) as exc:
        return Check("hyperframes browser", False, f"could not run: {exc}", fixmsg)
    line = next((ln.strip() for ln in (r.stdout + r.stderr).splitlines() if "Chrome" in ln), "")
    ok = line.startswith("✓")
    detail = "browser found" if ok else ("browser not found" if line else "no Chrome line in doctor output")
    return Check("hyperframes browser", ok, detail, "" if ok else fixmsg)


def _key_check(name: str, env_name: str) -> Check:
    try:
        keys.get(env_name)
        return Check(name, True, "present")
    except keys.MissingKey as exc:
        return Check(name, False, "missing", str(exc))


def check_elevenlabs_key(fix: bool = False) -> Check:
    return _key_check("elevenlabs key", keys.ELEVENLABS)


def _accepted(name: str, env_name: str, url: str, header: str) -> Check:
    try:
        key = keys.get(env_name)
    except keys.MissingKey:
        return Check(name, False, "no key to test", f"Add {env_name}= to ai-ad-editor/.env.")
    try:
        r = requests.get(url, headers={header: key}, timeout=20)
    except requests.RequestException as exc:
        return Check(name, False, f"could not reach the service ({type(exc).__name__})",
                     "Check your internet connection and run doctor again.")
    if r.status_code == 200:
        return Check(name, True, "accepted")
    return Check(name, False, f"rejected (HTTP {r.status_code})",
                 f"Check {env_name} in ai-ad-editor/.env is the full key with no spaces.")


def check_elevenlabs_accepts(fix: bool = False) -> Check:
    # API reference: https://elevenlabs.io/docs/api-reference/models/list
    # Auth header: https://elevenlabs.io/docs/api-reference/authentication
    return _accepted("elevenlabs access", keys.ELEVENLABS, "https://api.elevenlabs.io/v1/models", "xi-api-key")


def _pixabay_accepts() -> Check:
    # API reference: https://pixabay.com/api/docs/ (the key goes in the query string, so it is passed
    # as a parameter and a request error is never shown with its URL)
    key = keys.get(keys.PIXABAY)
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={"key": key, "q": "office", "per_page": 3}, timeout=20)
    except requests.RequestException as exc:
        return Check("pixabay", False, f"could not reach the service ({type(exc).__name__})",
                     "Check your internet connection and run doctor again.")
    if r.status_code == 200:
        return Check("pixabay", True, "accepted")
    if r.status_code == 429:
        return Check("pixabay", False, "too many requests (HTTP 429)", "Wait a minute and run doctor again.")
    return Check("pixabay", False, f"rejected (HTTP {r.status_code})",
                 f"Check {keys.PIXABAY} in ai-ad-editor/.env is the full key with no spaces.")


def check_broll_key(fix: bool = False) -> Check:
    """B-roll needs a Pixabay or a Pexels key that is accepted. With neither set it only warns, since
    a client's own clips need no key."""
    have = [n for n in (keys.PIXABAY, keys.PEXELS) if keys.present(n)]
    if not have:
        return Check("b-roll key", True, "no stock key set, so only your client's own clips will work",
                     f"For stock B-roll add {keys.PIXABAY}=your-key to ai-ad-editor/.env (a free key is on your Pixabay account page).",
                     warn=True)
    results = []
    if keys.PIXABAY in have:
        results.append(_pixabay_accepts())
    if keys.PEXELS in have:
        # API reference: https://www.pexels.com/api/documentation/ (GET /v1/videos/popular)
        results.append(_accepted("pexels", keys.PEXELS, "https://api.pexels.com/v1/videos/popular?per_page=1", "Authorization"))
    good = [r for r in results if r.ok]
    if good:
        if results[0].name == "pixabay" and not results[0].ok:  # Pixabay is the default, so it would fail at run time
            return Check("b-roll key", True, f"pexels accepted, but pixabay {results[0].detail}", results[0].fix, warn=True)
        return Check("b-roll key", True, f"{good[0].name} accepted")
    bad = results[0]
    return Check("b-roll key", False, f"{bad.name} {bad.detail}", bad.fix)


def check_face_model(fix: bool = False) -> Check:
    if FACE_MODEL_PATH.exists():
        return Check("face model", True, FACE_MODEL_PATH.name)
    if not fix:
        return Check("face model", False, "file missing", "Run: adeditor doctor --fix")
    tmp = FACE_MODEL_PATH.with_name(FACE_MODEL_PATH.name + ".part")
    try:
        r = requests.get(FACE_MODEL_URL, timeout=120)
        r.raise_for_status()
        if len(r.content) < MIN_MODEL_BYTES:
            return Check("face model", False, f"download too small ({len(r.content)} bytes)",
                         "Check your internet connection and run adeditor doctor --fix again.")
        FACE_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(r.content)
        tmp.replace(FACE_MODEL_PATH)
        return Check("face model", True, "downloaded")
    except (requests.RequestException, OSError) as exc:
        return Check("face model", False, f"download failed ({type(exc).__name__})",
                     "Check your internet connection and run adeditor doctor --fix again.")
    finally:
        tmp.unlink(missing_ok=True)


# Later tasks append their own functions (each takes fix and returns a Check).
CHECKS = [
    check_python,
    check_ffmpeg,
    check_ffprobe,
    check_node,
    check_hyperframes,
    check_hyperframes_browser,
    check_elevenlabs_key,
    check_elevenlabs_accepts,
    check_broll_key,
    check_face_model,
]


def run(fix: bool = False) -> list[Check]:
    return [fn(fix) for fn in CHECKS]


def format_table(results: list[Check]) -> str:
    width = max(len(c.name) for c in results)
    lines = []
    for c in results:
        lines.append(f"{'FAIL' if not c.ok else 'warn' if c.warn else 'ok  '}  {c.name.ljust(width)}  {c.detail}")
        if (not c.ok or c.warn) and c.fix:
            lines.append(f"      {' ' * width}  Fix: {c.fix}")
    return "\n".join(lines)


def _args(parser):
    parser.add_argument("--fix", action="store_true", help="download or install what we can")


@command("doctor", help="Check that everything the editor needs is installed and working.", args=_args)
def doctor_command(args) -> int:
    results = run(fix=args.fix)
    print(format_table(results))
    return 0 if all(c.ok for c in results) else 1
