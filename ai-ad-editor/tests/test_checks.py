import shutil
import json
import re

import pytest
from conftest import cut_words, make_speech

from adeditor import checks, cli, media, render, transcribe


def resolved(headings, broll):
    return {"headings": [{"lines": ["x"], "start": a, "end": b} for a, b in headings],
            "broll": [{"clip": f"c{i}", "start": a, "end": b} for i, (a, b) in enumerate(broll)]}


def test_stills_cover_every_moment():
    plan = resolved([(2.0, 4.0), (6.0, 8.0)], [(1.0, 1.8), (4.5, 5.5), (9.0, 10.0)])
    # a face that sits still, then jumps once, then drifts
    xs = [0.40, 0.40, 0.40, 0.70, 0.70, 0.72, 0.73, 0.73, 0.74, 0.74]
    faces = [{"t": i * 0.5, "x": x, "y": 0.3, "w": 0.2, "h": 0.2} for i, x in enumerate(xs)]
    got = checks.still_times(plan, faces, 1.0, end_start=10.5, duration=13.7)
    assert len(got) == 1 + 2 + 3 + 1 + 3
    labels = [label for label, _ in got]
    assert labels[0] == "First frame" and labels.count("End card") == 1
    times = dict(got)
    assert times["First frame"] == 0.0
    assert times["Heading 1"] == 3.0 and times["Heading 2"] == 7.0
    assert times["B-roll c0"] == pytest.approx(1.2) and times["End card"] == pytest.approx(12.1)
    assert any(label.startswith("Face move") and t == pytest.approx(1.25) for label, t in got)  # the jump


def test_face_times_are_divided_by_the_speed():
    faces = [{"t": 0.0, "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.2}, {"t": 1.5, "x": 0.7, "y": 0.3, "w": 0.2, "h": 0.2}]
    got = checks.still_times(resolved([], []), faces, 1.5, end_start=5.0, duration=8.0)
    assert dict(got)["Face move 1"] == pytest.approx(0.5)


PATTERN = [("w", 12.0)]


def build_ads(job, tmp_path, video, *, bed=None, join_words=None):
    audio = make_speech(tmp_path, PATTERN)
    job.ads.mkdir(parents=True, exist_ok=True)
    ad = job.ads / "take1 (9x16).mp4"
    ad.write_bytes(video(12, 1080, 1920, audio).read_bytes())
    report = {"9x16": {"window": {}, "warnings": ["the 9:16 crop is blown up 1.7x and will look soft"],
                       "duration": 12.0, "end_start": 8.8, "file": ad.name, "lufs": -14.0, "true_peak": -1.6,
                       "bed_median_db": bed, "inputs": render.inputs(job)}}
    (job.work / "render.json").write_text(json.dumps(report), encoding="utf-8")
    return ad


TEXT = "we help local clinics fill empty slots with patients who really need care today"


def test_a_near_word_diff_fails_the_report(ad_job, tmp_path, video, monkeypatch):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0)
    build_ads(job, tmp_path, video)
    # joins sit at 3, 6 and 9 s; "patients" is word 9 at 3.64 s, "who" at 3.28 s, "slots" near 2.92 s
    heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words if w["text"] != "slots"]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    report = checks.run(job)
    assert not report.ok
    sentence = next(s for s in report.failures if "'slots'" in s)
    assert "in the line" in sentence and "fill empty slots" in sentence
    assert "Every kept word was heard" not in report.summary and "slots" in report.summary
    assert (job.ads / "review.html").is_file()


def test_the_words_pass_and_far_noise_is_listed_not_failed(ad_job, tmp_path, video, monkeypatch):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0)
    build_ads(job, tmp_path, video)
    heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words]
    heard.insert(len(heard), {"word": "uh", "start": 11.5, "end": 11.7})  # an extra far from every join... 
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    report = checks.run(job)
    word_check = next(c for c in report.checks if c.name == "Words")
    assert word_check.ok and report.noise
    assert "Every kept word was heard" in word_check.detail


def credited(job, entries):
    """entries: clip id -> (source, page url). Pixabay and Pexels clips get a credit on record like `adeditor broll --get` writes."""
    job.broll_cache.mkdir(parents=True, exist_ok=True)
    homes = {"pexels": "https://www.pexels.com", "pixabay": "https://pixabay.com"}
    credits = {i: {"source": src, "user": f"Maker {n}", "user_url": f"{homes[src]}/@maker{n}", "url": page}
               for n, (i, (src, page)) in enumerate(entries.items(), 1)}
    (job.broll_cache / "credits.json").write_text(json.dumps(credits), encoding="utf-8")


def review_for(ad_job, tmp_path, video, monkeypatch, clips, credits, own=()):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0, clips=[(i, 4) for i in clips],
                 plan={"broll": [{"clip": i, "from": f, "to": t, "why": "x"}
                                 for i, (f, t) in zip([*clips, *own], [("slots", "patients"), ("who", "need"), ("care", "today")])]})
    for name in own:
        folder = job.broll_cache / "own"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy(video(4, 360, 640), folder / (name.removeprefix("own-").replace("-", " ").title() + ".MOV"))
    credited(job, credits)
    build_ads(job, tmp_path, video)
    heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    report = checks.run(job)
    return report, (job.ads / "review.html").read_text(encoding="utf-8")


def test_review_lists_pexels_credits_and_links_pexels(ad_job, tmp_path, video, monkeypatch):
    report, html = review_for(ad_job, tmp_path, video, monkeypatch, ["pexels-111", "pexels-222"],
                              {"pexels-111": ("pexels", "https://www.pexels.com/video/111/"),
                               "pexels-222": ("pexels", "https://www.pexels.com/video/222/")})
    assert "Video by <a href=\"https://www.pexels.com/@maker1\">Maker 1</a> on <a href=\"https://www.pexels.com/video/111/\">Pexels</a>" in html
    assert "Maker 2" in html and "https://www.pexels.com/@maker2" in html
    assert 'href="https://www.pexels.com"' in html and "Pixabay" not in html
    assert report.credits == ["pexels-111", "pexels-222"]


def test_review_credits_each_clip_by_its_source(ad_job, tmp_path, video, monkeypatch):
    report, html = review_for(ad_job, tmp_path, video, monkeypatch, ["pixabay-333", "pexels-111"],
                              {"pixabay-333": ("pixabay", "https://pixabay.com/videos/office-333/"),
                               "pexels-111": ("pexels", "https://www.pexels.com/video/111/")}, own=["own-my-clip"])
    assert 'Video by <a href="https://pixabay.com/@maker1">Maker 1</a> on <a href="https://pixabay.com/videos/office-333/">Pixabay</a>' in html
    assert 'on <a href="https://www.pexels.com/video/111/">Pexels</a>' in html
    assert "The client&#x27;s own footage (own-my-clip)" in html
    assert 'Videos from <a href="https://pixabay.com">Pixabay</a>, <a href="https://www.pexels.com">Pexels</a>' in html
    assert [c for c in report.checks if c.name == "Credits"][0].ok


def test_a_missing_credit_names_its_source(ad_job, tmp_path, video, monkeypatch):
    report, _ = review_for(ad_job, tmp_path, video, monkeypatch, ["pixabay-333"], {})
    credit = [c for c in report.checks if c.name == "Credits"][0]
    assert not credit.ok and "Pixabay credit" in credit.problems[0]


def test_review_has_no_dashes(ad_job, tmp_path, video, monkeypatch):
    words = cut_words(TEXT)
    words[3]["text"] = "clinics—fill"  # a transcriber's dash must not reach the sheet
    job = ad_job(words, 12.0)
    (job.work / "cut.json").write_text(json.dumps({
        "segments": [{"src": [0, 6], "out": [0, 6]}, {"src": [6, 12], "out": [6, 12]}], "words": words,
        "duration": 12.0, "tight_joins": [6.0],
        "tight_words": [{"t": 6.0, "before": {"text": "slots–now", "t": 5.8}, "after": {"text": "with", "t": 6.1}}],
        "struck": []}), encoding="utf-8")
    build_ads(job, tmp_path, video)
    heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    checks.run(job)
    text = (job.ads / "review.html").read_text(encoding="utf-8")
    assert not re.search("—|–|--", text)
    assert "Listen here" in text and "6.0 s" in text and "slots" in text and "with" in text
    assert (job.ads / "stills").is_dir() and list((job.ads / "stills").glob("9x16_*.jpg"))
    assert 'src="stills/' in text and 'name="viewport"' in text


def test_the_check_command_exits_1_on_a_failure(ad_job, tmp_path, video, monkeypatch, capsys):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0)
    build_ads(job, tmp_path, video)
    heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words if w["text"] != "slots"]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    assert cli.main(["check", "acme", "take1.mp4"]) == 1
    assert "slots" in capsys.readouterr().out


def test_a_black_last_frame_fails(tmp_path):
    black = tmp_path / "black.mp4"
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=360x640:r=30:d=2", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", str(black)])
    assert checks.last_frame_luma(black) < 2


def test_the_bed_check_passes_in_band_fails_out_of_band_and_notes_a_silent_bed(ad_job, monkeypatch):
    job = ad_job(cut_words(TEXT), 12.0)
    cut = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))
    (job.work / "mix").mkdir()
    (job.work / "mix" / "voice.wav").write_bytes(b"x")
    (job.work / "mix" / "bed.wav").write_bytes(b"x")
    for median, ok in ((15.0, True), (6.0, False), (24.0, False), (None, False)):
        monkeypatch.setattr(checks.mix, "bed_under_voice", lambda v, b, w, m=median: {"median_db": m, "by_30s": []})
        got = checks._bed(job, cut, {})
        assert got.ok is ok
    assert "Not measurable" in got.detail
    assert got.problems == ["The music can't be heard under the voice. Check that the music file has sound in it. "
                            "If bed_offset_db in plan.json is negative, set it back toward 0."]


def test_no_bed_at_all_passes_the_bed_check(ad_job):
    job = ad_job(cut_words(TEXT), 12.0)
    cut = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))
    got = checks._bed(job, cut, {"9x16": {"bed_median_db": None}})
    assert got.ok and got.detail == "No music bed."


def test_check_after_plan_json_was_broken_says_so_plainly(ad_job, tmp_path, video, monkeypatch, capsys):
    job = ad_job(cut_words(TEXT), 12.0)
    build_ads(job, tmp_path, video)
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: pytest.fail("no word check"))
    (job.work / "plan.json").write_text(json.dumps({"lockup": "kit", "headings": [{"lines": ["x"], "from": "nobody said this"}]}))
    assert cli.main(["check", "acme", "take1.mp4"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "adeditor plan check acme take1.mp4" in err


def test_check_with_a_broken_kit_says_so_plainly(ad_job, tmp_path, video, monkeypatch, capsys):
    job = ad_job(cut_words(TEXT), 12.0)
    build_ads(job, tmp_path, video)
    (job.brand / "kit.json").write_text('{"approved": true')
    assert cli.main(["check", "acme", "take1.mp4"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "kit.json isn't valid JSON" in err and "client new" not in err


def test_a_difference_the_person_accepted_is_listed_not_failed(ad_job, tmp_path, video, monkeypatch, capsys):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0)
    build_ads(job, tmp_path, video)
    saved = json.loads((job.work / "verify.json").read_text(encoding="utf-8"))
    # "slots" (2.92 s) was heard as "lots" in the cut take, and the person listened and said it's there
    saved.update(near=[{"kind": "changed", "kept": "slots", "heard": "lots", "t": 2.92, "to_join": 0.08}])
    saved["accepted"] = list(saved["near"])
    (job.work / "verify.json").write_text(json.dumps(saved), encoding="utf-8")
    heard = [{"word": "lots" if w["text"] == "slots" else w["text"], "start": w["start"], "end": w["end"]} for w in words]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    report = checks.run(job)
    word_check = next(c for c in report.checks if c.name == "Words")
    assert word_check.ok and report.accepted and "slots" in report.accepted[0]
    assert "accepted" in (job.ads / "review.html").read_text(encoding="utf-8").lower()
    # a different difference at the same spot still fails
    heard = [{"word": "spots" if w["text"] == "slots" else w["text"], "start": w["start"], "end": w["end"]} for w in words]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    report = checks.run(job)
    assert not next(c for c in report.checks if c.name == "Words").ok


def test_stills_find_a_face_that_drifts_off_centre_slowly():
    from adeditor import layout

    # The speaker sits in the middle, then drifts left a little at a time from 18 s to 28 s and stays
    # there: no single big move.
    def centre(t):
        return 0.5 - 0.3 * min(max((t - 18.0) / 10.0, 0.0), 1.0)

    faces = [{"t": i * 0.5, "x": centre(i * 0.5) - 0.06, "y": 0.3, "w": 0.12, "h": 0.2} for i in range(60)]
    win = layout.window(1920, 1080, faces, "9x16")
    window = {"scale": win.scale, "x0": win.x0, "y0": win.y0, "src_w": win.src_w, "src_h": win.src_h}
    got = checks.still_times(resolved([], []), faces, 1.0, end_start=29.0, duration=32.0, window=window, ratio="9x16")
    moves = [t for label, t in got if label.startswith("Face move")]
    far = [t for label, t in got if label.startswith("Face off centre")]
    assert moves and all(18.0 <= t < 28.0 for t in moves)  # the "biggest" moves are the drift's small, equal steps
    assert len(far) == checks.FACE_FAR and all(t >= 24.0 for t in far)
    assert all(abs(a - b) >= 2.0 for a in far for b in far if a != b)
    assert not [label for label, _ in checks.still_times(resolved([], []), faces, 1.0, end_start=29.0, duration=32.0)
                if label.startswith("Face off centre")]  # no window, no such stills


def test_the_off_centre_stills_see_the_face_where_the_panel_put_it():
    # The take starts 400 px down a 9:16 canvas, under the panel. For the first 5 s the face sits
    # mid-canvas (560 + 400 = 960); after that it sits 400 px below the middle (960 + 400).
    def at(cy):
        return {"x": 0.4, "y": cy / 1920 - 0.05, "w": 0.2, "h": 0.1}

    faces = [{"t": i * 0.5, **(at(560) if i < 10 else at(960))} for i in range(20)]
    window = {"scale": 1.0, "x0": 0.0, "y0": 0.0, "mode": "panel", "panel": 400.0, "src_w": 1080.0, "src_h": 1920.0}
    got = checks.still_times(resolved([], []), faces, 1.0, end_start=9.0, duration=10.0, window=window, ratio="9x16")
    far = [t for label, t in got if label.startswith("Face off centre")]
    assert far and all(t >= 5.0 for t in far)


def test_the_review_sheet_says_when_the_text_sits_in_a_band(ad_job, tmp_path, video, monkeypatch):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0)
    build_ads(job, tmp_path, video)
    report = json.loads((job.work / "render.json").read_text(encoding="utf-8"))
    report["9x16"]["mode"] = "panel"
    (job.work / "render.json").write_text(json.dumps(report), encoding="utf-8")
    heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    checks.run(job)
    sheet = (job.ads / "review.html").read_text(encoding="utf-8")
    assert "9:16: The take had no room above the head, so the text sits in a band at the top." in sheet
    report["9x16"]["mode"] = "card"
    (job.work / "render.json").write_text(json.dumps(report), encoding="utf-8")
    checks.run(job)
    sheet = (job.ads / "review.html").read_text(encoding="utf-8")
    assert "9:16: The take was framed tightly, so the picture sits as a framed card under the text band." in sheet
    report["9x16"]["mode"] = "fade"
    (job.work / "render.json").write_text(json.dumps(report), encoding="utf-8")
    checks.run(job)
    sheet = (job.ads / "review.html").read_text(encoding="utf-8")
    assert "no room above the head" not in sheet and "framed card" not in sheet


def test_the_off_centre_stills_see_the_face_in_its_card():
    # A card from x 162 and y 560: for the first 5 s the face sits mid-canvas (162 + 378 = 540 across,
    # 560 + 400 = 960 down); after that 120 px right of the middle.
    def at(cx):
        return {"x": cx / 756 - 0.1, "y": 400 / 1920 - 0.05, "w": 0.2, "h": 0.1}

    faces = [{"t": i * 0.5, **(at(378) if i < 10 else at(498))} for i in range(20)]
    window = {"scale": 0.7, "x0": 0.0, "y0": 0.0, "mode": "card", "panel": 560.0, "frame": [162.0, 560.0, 756.0, 1324.0],
              "src_w": 756.0, "src_h": 1920.0}
    got = checks.still_times(resolved([], []), faces, 1.0, end_start=9.0, duration=10.0, window=window, ratio="9x16")
    far = [t for label, t in got if label.startswith("Face off centre")]
    assert far and all(t >= 5.0 for t in far)


def test_captions_over_the_mouth_fail_the_check_and_say_why(ad_job, tmp_path, video, monkeypatch):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0)
    build_ads(job, tmp_path, video)
    report = json.loads((job.work / "render.json").read_text(encoding="utf-8"))
    report["9x16"].update(mode="card", mouth_covered=True)
    report["9x16"]["warnings"].append("In 9:16 some captions run past the bottom of the picture.")
    (job.work / "render.json").write_text(json.dumps(report), encoding="utf-8")
    heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    got = checks.run(job)
    mouth = next(c for c in got.checks if c.name == "Captions clear of the mouth")
    assert not mouth.ok and not got.ok
    assert mouth.problems == ["9:16: This take is too close for the captions to clear the mouth. "
                              "Ask the client for a wider take."]
    # a caption past the card's bottom is a warning, not a failure
    # (the fixture builds one quiet synthetic 9:16 file, so "Up to date" and "Loudness 9:16" fail on their own;
    # nothing else may, least of all the file check the warning belongs to)
    assert {c.name for c in got.checks if not c.ok} == {"Captions clear of the mouth", "Up to date", "Loudness 9:16"}
    assert "run past the bottom of the picture" in (job.ads / "review.html").read_text(encoding="utf-8")
    report["9x16"]["mouth_covered"] = False
    (job.work / "render.json").write_text(json.dumps(report), encoding="utf-8")
    assert next(c for c in checks.run(job).checks if c.name == "Captions clear of the mouth").ok


def up_to_date(job) -> checks.Check:
    return next(c for c in checks.run(job).checks if c.name == "Up to date")


def test_an_ad_made_before_the_last_change_fails_naming_it(ad_job, tmp_path, video, monkeypatch):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0)
    build_ads(job, tmp_path, video)
    heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    got = up_to_date(job)
    said = " ".join(got.problems)
    assert not got.ok and "The 4:5 ad hasn't been made" in said and "The 1:1 ad hasn't been made" in said
    assert "9:16" not in said  # made from what's there now
    plan_json = json.loads((job.work / "plan.json").read_text(encoding="utf-8"))
    (job.work / "plan.json").write_text(json.dumps({**plan_json, "bed_offset_db": 2}), encoding="utf-8")
    said = " ".join(up_to_date(job).problems)
    assert "The 9:16 ad was made before the last change" in said and "adeditor render acme take1.mp4" in said
    assert "--ratio" not in said  # all three, never one


def test_every_ratio_made_from_what_is_there_now_passes(ad_job, tmp_path, video, monkeypatch):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0)
    build_ads(job, tmp_path, video)
    entries = json.loads((job.work / "render.json").read_text(encoding="utf-8"))
    entries["4x5"] = entries["1x1"] = entries["9x16"]  # one file stands in for all three
    (job.work / "render.json").write_text(json.dumps(entries), encoding="utf-8")
    heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words]
    monkeypatch.setattr(transcribe, "transcribe", lambda audio, keyterms, cache_dir: heard)
    assert up_to_date(job).ok


def test_the_word_check_listens_to_an_ad_that_is_up_to_date(ad_job, tmp_path, video, monkeypatch):
    words = cut_words(TEXT)
    job = ad_job(words, 12.0)
    build_ads(job, tmp_path, video)
    entries = json.loads((job.work / "render.json").read_text(encoding="utf-8"))
    entries["1x1"] = {**entries["9x16"], "file": "take1 (1x1).mp4"}
    entries["9x16"]["inputs"] = "made-before-a-change"
    (job.ads / "take1 (1x1).mp4").write_bytes((job.ads / "take1 (9x16).mp4").read_bytes())
    (job.work / "render.json").write_text(json.dumps(entries), encoding="utf-8")
    listened = []

    def hear(audio, keyterms, cache_dir):
        listened.append(audio)
        return [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words]

    monkeypatch.setattr(transcribe, "transcribe", hear)
    real = checks.media.extract_audio
    took = []
    monkeypatch.setattr(checks.media, "extract_audio", lambda src, out, *a, **k: took.append(src) or real(src, out, *a, **k))
    checks.run(job)
    assert took[0].name == "take1 (1x1).mp4"  # the stale 9:16 isn't the one the words are checked on


def test_only_web_links_reach_the_review_sheet():
    line = checks._credit_line("111", {"user": "Maker", "user_url": "javascript:alert(1)", "url": "data:text/html,x"})
    assert "javascript" not in line and "data:" not in line
    assert "Maker" in line and 'href="https://www.pexels.com"' in line
    line = checks._credit_line("pixabay-1", {"user": "Maker", "user_url": "javascript:x", "url": "data:x"})
    assert "javascript" not in line and 'href="https://pixabay.com"' in line
    line = checks._credit_line("111", {"user": "Maker", "user_url": "https://www.pexels.com/@maker", "url": "http://www.pexels.com/video/111/"})
    assert 'href="https://www.pexels.com/@maker"' in line and 'href="http://www.pexels.com/video/111/"' in line
