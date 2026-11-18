import json
import os
import subprocess
import sys

import pytest
from conftest import cut_words, make_speech, put_in_raw

from adeditor import cli, media, paths, render

TEXT = ("If you sit at a desk all day, your back is already telling you something. "
        "Most people wait until it really hurts. You don't have to wait. "
        "Book a call and we'll show you how it works.")
PLAN = {
    "headings": [{"lines": ["You don't have to wait"], "from": "you don't have", "to": "book a call"}],
    "key_words": ["desk"],
    "broll": [{"clip": "pexels-1234567", "in": 0.5, "from": "most people", "to": "you don't have", "why": "people waiting"}],
}


def job_for(ad_job, **kw):
    words = cut_words(TEXT)
    return ad_job(words, round(words[-1]["end"] + 0.5, 3), plan=PLAN, clips=[("pexels-1234567", 10)], **kw)


def refusal(job) -> str:
    with pytest.raises(render.Refused) as err:
        render.render(job, ("1x1",))
    text = str(err.value)
    assert text.endswith(".") and ". " not in text  # one plain sentence
    return text


def test_render_refuses_an_unapproved_kit(ad_job):
    assert "adeditor client approve acme" in refusal(job_for(ad_job, kit={"approved": False}))


def test_render_refuses_strikes_not_yet_approved(ad_job):
    assert "adeditor approve-cut acme take1.mp4" in refusal(job_for(ad_job, strikes_approved=False))


def test_render_refuses_a_plan_that_does_not_check(ad_job):
    job = job_for(ad_job)
    plan = json.loads((job.work / "plan.json").read_text(encoding="utf-8"))
    plan["headings"][0]["from"] = "words nobody said"
    (job.work / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    assert "adeditor plan check acme take1.mp4" in refusal(job)


@pytest.mark.skipif(os.name == "nt", reason="niceness is a Mac and Linux idea")
def test_low_priority_lowers_the_child_and_not_us():
    before = os.nice(0)
    child = subprocess.run([sys.executable, "-c", "import os; print(os.nice(0))"], capture_output=True, text=True,
                           check=True, **render.low_priority())
    assert int(child.stdout) == min(19, before + 10)
    assert os.nice(0) == before


def test_render_runs_hyperframes_then_the_mix(ad_job, monkeypatch):
    job = job_for(ad_job)
    real_run = subprocess.run
    calls, mixed = [], []

    def fake_run(cmd, **kw):
        if any("hyperframes@" in str(c) for c in cmd):
            calls.append((cmd, kw))
            if "render" in cmd:
                (kw["cwd"] / "renders").mkdir(exist_ok=True)
                (kw["cwd"] / "renders" / "silent.mp4").write_bytes(b"silent")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kw)

    job.ads.mkdir(parents=True)
    (job.ads / "review.html").write_text("an old review", encoding="utf-8")

    def fake_mix(voice, bed, silent, out, duration, end_start, bpm, bed_offset_db=0, **kw):
        mixed.append(dict(voice=voice, bed=bed, silent=silent, out=out, duration=duration, end_start=end_start,
                          bpm=bpm, bed_offset_db=bed_offset_db, **kw))
        out.write_bytes(b"ad")
        return {"voice_stem": kw["stems_dir"] / "voice.wav", "bed_stem": None, "lufs": -14.0, "true_peak": -1.6,
                "bed_median_db": None}

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(render.mix, "mix", fake_mix)
    nice_before = os.nice(0) if os.name != "nt" else None
    done = render.render(job, ("1x1",))

    page_dir = job.work / "pages" / "1x1"
    assert [c[0][3:] for c in calls] == [
        ["lint"], ["validate"],
        ["render", "-o", "renders/silent.mp4", "--fps", "30", "--quality", "high", "--crf", "16", "--workers", "4", "--quiet"]]
    for cmd, kw in calls:
        assert cmd[1:3] == ["-y", "hyperframes@0.7.5"] and kw["cwd"] == page_dir
        assert ("creationflags" in kw) if os.name == "nt" else callable(kw.get("preexec_fn"))
    if nice_before is not None:
        assert os.nice(0) == nice_before

    out = job.ads / "take1 (1x1).mp4"
    assert done == {"1x1": out} and out.read_bytes() == b"ad"
    assert not (job.ads / "review.html").exists()  # the old sheet went when the render began
    [m] = mixed
    facts = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
    assert m["voice"] == job.work / "aroll.mp4" and m["bed"] is None and m["silent"] == page_dir / "renders" / "silent.mp4"
    assert (m["duration"], m["end_start"]) == (facts["duration"], facts["end"][0])
    assert m["stems_dir"] == job.work / "mix" and m["words"] == json.loads((job.work / "cut.json").read_text())["words"]

    report = json.loads((job.work / "render.json").read_text(encoding="utf-8"))
    entry = report["1x1"]
    assert entry["duration"] == facts["duration"]
    assert set(entry["window"]) >= {"scale", "x0", "y0", "mode", "panel", "src_w", "src_h"}
    assert entry["mode"] == facts["window"]["mode"] and entry["mode"] in ("fade", "panel")
    assert isinstance(entry["warnings"], list) and entry["file"] == "take1 (1x1).mp4"
    assert entry["inputs"] == render.inputs(job)


def test_a_render_failure_shows_what_hyperframes_said(ad_job, monkeypatch):
    job = job_for(ad_job)
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if any("hyperframes@" in str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, 1, "✗ missing_local_asset: assets/x.mp4", "")
        return real_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(render.RenderError) as err:
        render.render(job, ("1x1",))
    assert "missing_local_asset" in str(err.value) and "lint" in str(err.value)
    assert not job.ads.exists()


TIMEOUT = "◆  Validating 1x1 in headless Chrome\n\n✗ Navigation timeout of 10000 ms exceeded"


def fake_hyperframes(monkeypatch, fail):
    """subprocess.run where HyperFrames steps answer from `fail(step, attempt)` (None for success)."""
    real_run, calls = subprocess.run, []

    def fake_run(cmd, **kw):
        if any("hyperframes@" in str(c) for c in cmd):
            step = cmd[3]
            calls.append(step)
            said = fail(step, calls.count(step))
            if said:
                return subprocess.CompletedProcess(cmd, 1, said, "")
            if step == "render":
                (kw["cwd"] / "renders").mkdir(exist_ok=True)
                (kw["cwd"] / "renders" / "silent.mp4").write_bytes(b"silent")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def fake_mix_ok(voice, bed, silent, out, duration, end_start, bpm, bed_offset_db=0, **kw):
    out.write_bytes(b"ad")
    return {"voice_stem": None, "bed_stem": None, "lufs": -14.0, "true_peak": -1.6, "bed_median_db": None}


def test_a_hyperframes_timeout_is_tried_once_more(ad_job, monkeypatch, capsys):
    job = job_for(ad_job)
    calls = fake_hyperframes(monkeypatch, lambda step, n: TIMEOUT if step == "validate" and n == 1 else None)
    monkeypatch.setattr(render.mix, "mix", fake_mix_ok)
    assert render.render(job, ("1x1",)) == {"1x1": job.ads / "take1 (1x1).mp4"}
    assert calls == ["lint", "validate", "validate", "render"]
    assert "HyperFrames timed out, trying once more" in capsys.readouterr().out


def test_a_second_timeout_stops_the_render(ad_job, monkeypatch):
    job = job_for(ad_job)
    calls = fake_hyperframes(monkeypatch, lambda step, n: TIMEOUT if step == "render" else None)
    monkeypatch.setattr(render.mix, "mix", fake_mix_ok)
    with pytest.raises(render.RenderError) as err:
        render.render(job, ("1x1",))
    assert calls == ["lint", "validate", "render", "render"] and "Navigation timeout" in str(err.value)


def test_any_other_hyperframes_failure_is_not_tried_again(ad_job, monkeypatch, capsys):
    job = job_for(ad_job)
    calls = fake_hyperframes(monkeypatch, lambda step, n: "✗ missing_local_asset: assets/x.mp4" if step == "lint" else None)
    with pytest.raises(render.RenderError):
        render.render(job, ("1x1",))
    assert calls == ["lint"] and "trying once more" not in capsys.readouterr().out


def test_the_render_command_takes_ratios(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    put_in_raw()
    seen = []
    monkeypatch.setattr(render, "render", lambda job, ratios: seen.append((job.client, job.video.name, ratios)) or {})
    assert cli.main(["render", "acme", "take1.mp4", "--ratio", "4x5"]) == 0
    assert cli.main(["render", "acme", "take1.mp4"]) == 0
    assert seen == [("acme", "take1.mp4", ("4x5",)), ("acme", "take1.mp4", ("9x16", "4x5", "1x1"))]

    def refuse(job, ratios):
        raise render.Refused("Nope, run adeditor something.")

    monkeypatch.setattr(render, "render", refuse)
    assert cli.main(["render", "acme", "take1.mp4"]) == 1
    assert "Nope, run adeditor something." in capsys.readouterr().err


def last_frame_luma(path) -> float:
    raw = subprocess.run([media.tool("ffmpeg"), "-v", "error", "-sseof", "-0.2", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return sum(raw) / len(raw)


@pytest.mark.render
def test_render_one_ratio(ad_job, tmp_path, video):
    pattern = [("s", 0.4)] + [("w", 0.6), ("s", 0.3)] * 6 + [("s", 0.2)]
    speech = round(sum(d for _, d in pattern), 3)  # 6 s
    take = video(speech, 1080, 1920, audio=make_speech(tmp_path, pattern))
    texts = "You don't have to wait now".split()
    words, t = [], 0.0
    for kind, dur in pattern:
        if kind == "w":
            words.append({"text": texts[len(words)], "start": round(t, 3), "end": round(t + dur, 3), "src": round(t, 3)})
        t += dur
    plan = {"broll": [{"clip": "pexels-1234567", "in": 0.5, "from": "to", "to": "wait", "why": "a test pattern"}],
            "headings": [{"lines": ["Wait *now*"], "from": "wait", "to": "now"}],
            "key_words": ["wait"]}
    job = ad_job(words, speech, plan=plan, aroll=take, kit={"music": "music/bed.mp3", "music_bpm": 72},
                 clips=[("pexels-1234567", 8)])
    (job.brand / "music").mkdir()
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               "anoisesrc=color=pink:amplitude=0.3:duration=20:sample_rate=48000:seed=1", "-ac", "2",
               str(job.brand / "music" / "bed.mp3")])

    done = render.render(job, ("1x1",))
    out = done["1x1"]
    facts = json.loads((job.work / "pages" / "1x1" / "page.json").read_text(encoding="utf-8"))
    info = json.loads(media.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=width,height,avg_frame_rate,duration", "-of", "json", str(out)]).stdout)
    stream = info["streams"][0]
    assert (stream["width"], stream["height"]) == (1080, 1080)
    assert stream["avg_frame_rate"] == "30/1"
    assert abs(float(stream["duration"]) - facts["duration"]) <= 1 / 30 + 1e-3
    assert last_frame_luma(out) > 16
    report = json.loads((job.work / "render.json").read_text(encoding="utf-8"))
    assert report["1x1"]["lufs"] == pytest.approx(-14, abs=1)


@pytest.mark.render
def test_render_the_panel_layout(ad_job, tmp_path, video):
    from PIL import Image

    pattern = [("s", 0.4)] + [("w", 0.6), ("s", 0.3)] * 4 + [("s", 0.2)]
    speech = round(sum(d for _, d in pattern), 3)
    take = video(speech, 2560, 1440, audio=make_speech(tmp_path, pattern))
    texts = "Book a call today".split()
    words, t = [], 0.0
    for kind, dur in pattern:
        if kind == "w":
            words.append({"text": texts[len(words)], "start": round(t, 3), "end": round(t + dur, 3), "src": round(t, 3)})
        t += dur
    job = ad_job(words, speech, plan={}, aroll=take, face={"x": 0.43, "y": 0.205, "w": 0.14, "h": 0.25})
    out = render.render(job, ("1x1",))["1x1"]
    facts = json.loads((job.work / "pages" / "1x1" / "page.json").read_text(encoding="utf-8"))
    assert facts["window"]["mode"] == "panel"
    assert json.loads((job.work / "render.json").read_text(encoding="utf-8"))["1x1"]["mode"] == "panel"
    frame = tmp_path / "first.png"
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-frames:v", "1", str(frame)])
    image = Image.open(frame).convert("RGB")
    paper = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))["colours"]["paper"]
    want = tuple(int(paper[i:i + 2], 16) for i in (1, 3, 5))
    corner = image.getpixel((12, 12))  # the panel, where no text is
    assert all(abs(a - b) <= 6 for a, b in zip(corner, want)), corner
    below = image.getpixel((12, int(facts["window"]["panel"]) + 120))  # the take, under the blend
    assert any(abs(a - b) > 30 for a, b in zip(below, want)), below


@pytest.mark.render
def test_render_the_card_layout(ad_job, tmp_path, video):
    from PIL import Image

    pattern = [("s", 0.4)] + [("w", 0.6), ("s", 0.3)] * 4 + [("s", 0.2)]
    speech = round(sum(d for _, d in pattern), 3)
    take = video(speech, 1080, 1920, audio=make_speech(tmp_path, pattern))
    texts = "Book a call today".split()
    words, t = [], 0.0
    for kind, dur in pattern:
        if kind == "w":
            words.append({"text": texts[len(words)], "start": round(t, 3), "end": round(t + dur, 3), "src": round(t, 3)})
        t += dur
    # a vertical close-up: in 1:1 it sits in a card
    job = ad_job(words, speech, plan={}, aroll=take, face={"x": 0.028, "y": 0.224, "w": 0.516, "h": 0.29})
    out = render.render(job, ("1x1",))["1x1"]
    facts = json.loads((job.work / "pages" / "1x1" / "page.json").read_text(encoding="utf-8"))
    assert facts["window"]["mode"] == "card"
    left, top, width, height = facts["window"]["frame"]
    frame = tmp_path / "first.png"
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-frames:v", "1", str(frame)])
    image = Image.open(frame).convert("RGB")
    paper = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))["colours"]["paper"]
    want = tuple(int(paper[i:i + 2], 16) for i in (1, 3, 5))
    beside = image.getpixel((6, int(top + height / 2)))  # the paper beside the card, past its shadow
    assert all(abs(a - b) <= 6 for a, b in zip(beside, want)), beside
    shadow = image.getpixel((int(left - 12), int(top + height / 2)))  # the soft shadow at the card's edge
    assert sum(shadow) < sum(want) - 6, shadow
    inside = image.getpixel((int(left + 40), int(top + height / 2)))  # the take, inside the card
    assert any(abs(a - b) > 30 for a, b in zip(inside, want)), inside


def test_a_failed_mix_leaves_no_ads_folder_behind(ad_job, monkeypatch):
    job = job_for(ad_job)
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if any("hyperframes@" in str(c) for c in cmd):
            if "render" in cmd:
                (kw["cwd"] / "renders").mkdir(exist_ok=True)
                (kw["cwd"] / "renders" / "silent.mp4").write_bytes(b"silent")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kw)

    def broken_mix(voice, bed, silent, out, *a, **kw):
        out.write_bytes(b"half")
        raise media.ToolError("ffmpeg failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(render.mix, "mix", broken_mix)
    with pytest.raises(media.ToolError):
        render.render(job, ("1x1",))
    assert not job.ads.exists()


def test_render_says_a_broken_kit_is_broken_and_never_to_start_again(ad_job):
    job = job_for(ad_job)
    (job.brand / "kit.json").write_text('{"name": "Acme", "approved": true,,}')
    text = refusal(job)
    assert "kit.json isn't valid JSON" in text and "line 1" in text and "client new" not in text


def test_render_refuses_a_cut_the_word_check_never_saw(ad_job):
    job = job_for(ad_job)
    saved = (job.work / "verify.json").read_text(encoding="utf-8")
    (job.work / "verify.json").unlink()
    assert "adeditor cut acme take1.mp4" in refusal(job)
    (job.work / "verify.json").write_text(saved, encoding="utf-8")
    cut = json.loads((job.work / "cut.json").read_text(encoding="utf-8"))
    cut["segments"][0]["src"][1] += 0.1  # cut again since the check ran
    (job.work / "cut.json").write_text(json.dumps(cut), encoding="utf-8")
    assert "adeditor cut acme take1.mp4" in refusal(job)


def test_render_refuses_a_word_nobody_listened_to(ad_job):
    job = job_for(ad_job)
    saved = json.loads((job.work / "verify.json").read_text(encoding="utf-8"))
    diff = {"kind": "changed", "kept": "the", "heard": "a", "t": 3.2, "to_join": 0.1}
    saved.update(near=[diff], accepted=[])
    (job.work / "verify.json").write_text(json.dumps(saved), encoding="utf-8")
    text = refusal(job)
    assert "0:03" in text and "adeditor accept-words acme take1.mp4" in text
    saved["accepted"] = [diff]
    (job.work / "verify.json").write_text(json.dumps(saved), encoding="utf-8")
    render.gate(job)  # listened to and accepted: no refusal


def test_render_refuses_a_face_map_from_an_older_cut(ad_job):
    job = job_for(ad_job)
    _, samples = render.faces.load(job.work / "faces.json")
    (job.work / "faces.json").write_text(json.dumps({"cut": "an-older-cut", "samples": samples}), encoding="utf-8")
    assert "adeditor cut acme take1.mp4" in refusal(job)
    (job.work / "faces.json").write_text(json.dumps(samples), encoding="utf-8")  # a map from before cuts were named
    assert "adeditor cut acme take1.mp4" in refusal(job)


def test_what_an_ad_is_made_from_changes_with_the_cut_plan_kit_music_and_speed(ad_job):
    job = job_for(ad_job)
    first = render.inputs(job)
    assert render.inputs(job) == first
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    (job.brand / "kit.json").write_text(json.dumps({**kit, "approved": False}), encoding="utf-8")
    assert render.inputs(job) == first  # approving isn't a change to the ad
    changes = [
        (job.work / "plan.json", lambda d: {**d, "bed_offset_db": 3}),
        (job.brand / "kit.json", lambda d: {**d, "colours": {**d["colours"], "primary": "#123456"}}),
        (job.work / "cut.json", lambda d: {**d, "duration": d["duration"] + 0.1}),
    ]
    seen = {first}
    for path, change in changes:
        path.write_text(json.dumps(change(json.loads(path.read_text(encoding="utf-8")))), encoding="utf-8")
        seen.add(render.inputs(job))
    (job.work / "speed.json").write_text(json.dumps({"speed": 1.25}), encoding="utf-8")
    seen.add(render.inputs(job))
    (job.brand / "music").mkdir()
    (job.brand / "music" / "bed.mp3").write_bytes(b"one")
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    (job.brand / "kit.json").write_text(json.dumps({**kit, "music": "music/bed.mp3"}), encoding="utf-8")
    seen.add(render.inputs(job))
    (job.brand / "music" / "bed.mp3").write_bytes(b"two")  # the same name, new music
    seen.add(render.inputs(job))
    assert len(seen) == 7


def run_render(job, monkeypatch, mixed):
    fake_hyperframes(monkeypatch, lambda step, n: None)

    def fake_mix(voice, bed, silent, out, duration, end_start, bpm, bed_offset_db=0, **kw):
        mixed.append(kw.get("effects"))
        out.write_bytes(b"ad")
        eff = kw.get("effects")
        cues = [{"role": "cut", "event": "x", "at": 1.0, "start": 1.0, "gain_db": 6.0, "rate": 1.0, "clamped": True}] if eff else []
        return {"voice_stem": None, "bed_stem": None, "lufs": -14.0, "true_peak": -1.6, "bed_median_db": None,
                "effects": bool(eff), "cues": cues, "clamped": len(cues)}

    monkeypatch.setattr(render.mix, "mix", fake_mix)
    return render.render(job, ("1x1",))


def test_render_without_sounds_still_renders(ad_job, monkeypatch, capsys):
    from adeditor import sounds

    job = job_for(ad_job)
    plan_path = job.work / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["graphics"] = [{"kind": "checklist", "from": "all day", "to": "already telling", "to_end": True,
                         "items": ["your back", "already telling"]}]
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setenv(sounds.SWITCH, "1")
    mixed = []
    done = run_render(job, monkeypatch, mixed)
    assert done == {"1x1": job.ads / "take1 (1x1).mp4"} and mixed == [None]
    assert "No sound effects: ADEDITOR_NO_SOUNDS=1 is set." in capsys.readouterr().out
    entry = json.loads((job.work / "render.json").read_text(encoding="utf-8"))["1x1"]
    assert entry["sounds"] == {"on": False, "reason": "ADEDITOR_NO_SOUNDS=1 is set", "cues": [], "clamped": 0, "stem": None}


def test_render_places_sounds_and_records_them(ad_job, monkeypatch, capsys, tmp_path):
    from adeditor import sounds

    job = job_for(ad_job)
    monkeypatch.setattr(sounds, "available", lambda: ({"cut": {}}, None))
    plan_path = job.work / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["sfx_offset_db"] = 6
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    mixed = []
    run_render(job, monkeypatch, mixed)
    [eff] = mixed
    facts = json.loads((job.work / "pages" / "1x1" / "page.json").read_text(encoding="utf-8"))
    assert eff["events"] == facts["events"] and eff["offset_db"] == 6 and eff["manifest"] == {"cut": {}}
    entry = json.loads((job.work / "render.json").read_text(encoding="utf-8"))["1x1"]
    assert entry["sounds"]["on"] is True and entry["sounds"]["reason"] is None and entry["sounds"]["clamped"] == 1
    assert entry["sounds"]["stem"] == "effects-1x1.wav" and eff["name"] == "effects-1x1"
    assert entry["sounds"]["cues"][0]["clamped"] is True
    assert "No sound effects" not in capsys.readouterr().out


def test_a_plan_that_turns_sounds_off_says_so(ad_job, monkeypatch, capsys):
    job = job_for(ad_job)
    plan_path = job.work / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["sounds"] = False
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    mixed = []
    run_render(job, monkeypatch, mixed)
    assert mixed == [None] and "No sound effects: the plan turns them off" in capsys.readouterr().out


def test_an_ad_made_without_sounds_is_out_of_date_once_they_exist(ad_job, monkeypatch, tmp_path):
    from adeditor import sounds

    job = job_for(ad_job)
    monkeypatch.setenv(sounds.SWITCH, "1")
    off = render.inputs(job)
    monkeypatch.delenv(sounds.SWITCH)
    assert render.inputs(job) != off or sounds.signature() == "off"
    before = render.inputs(job)
    sounds.folder().mkdir(parents=True, exist_ok=True)
    (sounds.folder() / "cut.wav").write_bytes(b"x")
    (sounds.folder() / "manifest.json").write_text(json.dumps({"cut": {"file": "cut.wav", "made": "2026-11-18"}}))
    try:
        assert render.inputs(job) != before
    finally:
        (sounds.folder() / "cut.wav").unlink()
        (sounds.folder() / "manifest.json").unlink()


def test_the_render_line_says_the_peak_and_the_effects():
    r = {"duration": 26.0, "lufs": -14.3, "true_peak": -1.6, "bed_median_db": 15.0,
         "sounds": {"on": True, "cues": [{}] * 8, "offset_db": 0.0}}
    base = "take (9x16).mp4: 0:26, -14.3 LUFS, peak -1.6 dBTP, the music 15.0 dB under the voice"
    assert render.render_line("take (9x16).mp4", r) == base + ", 8 sound effects"
    quiet = {**r, "sounds": {**r["sounds"], "offset_db": -3.0}}
    assert render.render_line("take (9x16).mp4", quiet) == base + ", 8 sound effects 3 dB quieter"
    loud = {**r, "sounds": {**r["sounds"], "offset_db": 2.0}}
    assert render.render_line("take (9x16).mp4", loud) == base + ", 8 sound effects 2 dB louder"
    none = {**r, "sounds": {"on": False, "cues": [], "reason": "x"}}
    assert render.render_line("take (9x16).mp4", none) == base
    one = {**r, "sounds": {**r["sounds"], "cues": [{}]}}
    assert render.render_line("take (9x16).mp4", one) == base + ", 1 sound effect"
    half = {**r, "sounds": {**r["sounds"], "offset_db": -2.5}}
    assert render.render_line("take (9x16).mp4", half) == base + ", 8 sound effects 2.5 dB quieter"
