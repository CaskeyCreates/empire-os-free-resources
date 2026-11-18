import os

import pytest

from adeditor import paths
from adeditor.paths import ROOT, Job, new_videos


def test_job_folders():
    job = Job("acme", ROOT / "clients/acme/raw/Take 1.MOV")
    assert job.work == ROOT / "clients/acme/work/Take 1"
    assert job.ads == ROOT / "clients/acme/ads/Take 1"
    assert job.broll_cache == ROOT / "clients/acme/broll"
    assert job.raw == ROOT / "clients/acme/raw"
    assert job.brand == ROOT / "clients/acme/brand"


def finish(tmp_path, stem, ratios=("9x16", "4x5", "1x1"), review=True):
    ads = tmp_path / "clients/acme/ads" / stem
    ads.mkdir(parents=True, exist_ok=True)
    for r in ratios:
        (ads / f"{stem} ({r}).mp4").write_bytes(b"ad")
    if review:
        (ads / "review.html").write_text("checked")


def test_new_videos_skips_done_and_non_video(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    raw = tmp_path / "clients/acme/raw"
    raw.mkdir(parents=True)
    for name in ["a.mp4", "b.MOV", "notes.txt"]:
        (raw / name).write_bytes(b"x")
    finish(tmp_path, "a")
    assert new_videos("acme") == [raw / "b.MOV"]


@pytest.mark.parametrize("ratios,review", [
    (("9x16",), False),  # the render stopped on the second ratio
    (("9x16", "4x5"), True),  # an old review sheet, a ratio missing
    (("9x16", "4x5", "1x1"), False),  # rendered but never checked
])
def test_a_video_is_done_only_with_all_three_ads_and_the_review_sheet(tmp_path, monkeypatch, ratios, review):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    raw = tmp_path / "clients/acme/raw"
    raw.mkdir(parents=True)
    (raw / "a.mp4").write_bytes(b"x")
    finish(tmp_path, "a", ratios, review)
    assert new_videos("acme") == [raw / "a.mp4"]
    assert paths.RATIOS == ("9x16", "4x5", "1x1")


def test_new_videos_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    raw = tmp_path / "clients/acme/raw"
    raw.mkdir(parents=True)
    for name, t in [("new.mp4", 2000), ("old.m4v", 1000)]:
        (raw / name).write_bytes(b"x")
        os.utime(raw / name, (t, t))
    assert new_videos("acme") == [raw / "old.m4v", raw / "new.mp4"]


def test_new_videos_without_raw_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    assert new_videos("nobody") == []


def raw_folder(tmp_path, monkeypatch, *names):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    raw = tmp_path / "clients/acme/raw"
    raw.mkdir(parents=True)
    for name in names:
        (raw / name).write_bytes(b"x")
    return raw


def test_a_bare_file_name_is_found_in_raw(tmp_path, monkeypatch):
    raw = raw_folder(tmp_path, monkeypatch, "take 1.mov")
    assert paths.find_video("acme", "take 1.mov") == raw / "take 1.mov"
    job = paths.job("acme", "take 1.mov")
    assert job.video == raw / "take 1.mov" and job.work == tmp_path / "clients/acme/work/take 1"


def test_a_path_that_is_there_is_used_as_it_is(tmp_path, monkeypatch):
    raw_folder(tmp_path, monkeypatch, "take.mov")
    elsewhere = tmp_path / "Downloads" / "take.mov"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"x")
    assert paths.find_video("acme", str(elsewhere)) == elsewhere
    # a path that isn't there falls back to its name in raw/
    assert paths.find_video("acme", tmp_path / "gone" / "take.mov") == tmp_path / "clients/acme/raw/take.mov"


def test_a_video_in_neither_place_is_said_plainly_with_what_raw_holds(tmp_path, monkeypatch):
    raw_folder(tmp_path, monkeypatch, "take-landscape.mov", "take-vertical.mp4", ".DS_Store")
    with pytest.raises(paths.VideoNotFound) as err:
        paths.find_video("acme", "take-landscap.mov")
    said = str(err.value)
    assert "take-landscap.mov" in said and "clients/acme/raw/" in said
    assert "take-landscape.mov, take-vertical.mp4" in said and ".DS_Store" not in said


def test_an_empty_or_missing_raw_folder_is_said_plainly(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    with pytest.raises(paths.VideoNotFound) as err:
        paths.find_video("acme", "take.mov")
    assert "clients/acme/raw/ has nothing in it yet" in str(err.value)


def test_a_bad_client_name_is_refused_before_any_folder_is_looked_in(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="lower-case letters"):
        paths.job("../acme", "take.mov")
