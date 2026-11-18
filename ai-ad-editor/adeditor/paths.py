"""Where everything lives. One client is one folder under clients/."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
RATIOS = ("9x16", "4x5", "1x1")


class Job:
    """One raw video for one client, and the folders its outputs go in."""

    def __init__(self, client: str, video: Path):
        self.client = client
        self.video = Path(video)

    @property
    def client_dir(self) -> Path:
        return ROOT / "clients" / self.client

    @property
    def raw(self) -> Path:
        return self.client_dir / "raw"

    @property
    def brand(self) -> Path:
        return self.client_dir / "brand"

    @property
    def work(self) -> Path:
        return self.client_dir / "work" / self.video.stem

    @property
    def ads(self) -> Path:
        return self.client_dir / "ads" / self.video.stem

    @property
    def broll_cache(self) -> Path:
        return self.client_dir / "broll"


class VideoNotFound(Exception):
    """The video a command was given isn't where it says, nor in the client's raw/ folder."""


def find_video(client: str, video, need_raw: bool = True) -> Path:
    """The video a command was given: the path as typed when it's there, else that file name in
    clients/<client>/raw/, since people type just the name. A command that only reads the video's
    work folder (need_raw False) also goes on from the name or bare stem when clients/<client>/work/
    <stem>/ is there, since people delete big raw takes once they're cut. Otherwise VideoNotFound says
    so and lists what raw/ holds."""
    given = Path(video)
    if given.is_file():
        return given
    raw = ROOT / "clients" / client / "raw"
    if (raw / given.name).is_file():
        return raw / given.name
    if not need_raw and (ROOT / "clients" / client / "work" / given.stem).is_dir():
        return raw / given.name
    there = sorted(p.name for p in raw.iterdir() if p.is_file() and not p.name.startswith(".")) if raw.is_dir() else []
    where = f"clients/{client}/raw/"
    if not there:
        raise VideoNotFound(f"We can't find the video {video}, and {where} has nothing in it yet. Put the client's "
                            "video there, then run the step again.")
    raise VideoNotFound(f"We can't find the video {video}, and it isn't in {where} either. What's there: "
                        f"{', '.join(there)}.")


def job(client: str, video, need_raw: bool = False) -> Job:
    """The job every command works on: the client's name checked, then its video found (find_video).
    prepare and cut read the raw take itself, so they pass need_raw."""
    from adeditor import brand  # brand imports this module, so only when a command runs

    return Job(brand.check_slug(client), find_video(client, video, need_raw))


def done(client: str, video: Path) -> bool:
    """A video is done when all three ads and the review sheet are in ads/<stem>/. A render that
    stopped part way, or one never checked, isn't done (render removes the old sheet as it starts)."""
    stem = Path(video).stem
    ads = ROOT / "clients" / client / "ads" / stem
    return all((ads / f"{stem} ({r}).mp4").is_file() for r in RATIOS) and (ads / "review.html").is_file()


def new_videos(client: str) -> list[Path]:
    """Videos in raw/ that aren't done yet, oldest first."""
    raw = ROOT / "clients" / client / "raw"
    if not raw.is_dir():
        return []
    found = [p for p in raw.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES and not done(client, p)]
    return sorted(found, key=lambda p: (p.stat().st_mtime, p.name))
