import json
import os
import subprocess

import pytest
from conftest import cut_words

from adeditor import cli, preview, render

TEXT = ("Hello and welcome to the clinic today. First we listen, then we test, next we plan, "
        "finally we move freely again. Most people wait until it really hurts. "
        "Book a call and we'll show you how it works.")
STEPS = {"kind": "steps", "from": "first we listen", "to": "finally we move", "to_end": True,
         "items": ["first we listen", "then we test", "next we plan", "finally we move"]}


def job_with(ad_job, plan):
    words = cut_words(TEXT)
    return ad_job(words, round(words[-1]["end"] + 0.5, 3), plan=plan)


def fake_hyperframes(monkeypatch, calls, stale=()):
    """Stand in for npx hyperframes: a snapshot call writes one still per --at time, as 0.7.5 does."""
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if not any("hyperframes@" in str(c) for c in cmd):
            return real_run(cmd, **kw)
        calls.append((cmd, kw))
        folder = kw["cwd"]
        snaps = folder / "snapshots"
        snaps.mkdir(exist_ok=True)
        times = cmd[cmd.index("--at") + 1].split(",")
        for i, t in enumerate(times):
            (snaps / f"frame-{i:02d}-at-{float(t):.1f}s.png").write_bytes(f"{folder.name}:{t}".encode())
        (snaps / "contact-sheet.jpg").write_bytes(b"sheet")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(render.subprocess, "run", fake_run)


def run(argv):
    return cli.main(argv)


def test_it_snapshots_each_graphics_full_at_time_and_never_describes(ad_job, monkeypatch, capsys):
    job = job_with(ad_job, {"graphics": [STEPS]})
    calls = []
    fake_hyperframes(monkeypatch, calls)
    assert run(["preview", "acme", "take1.mp4", "--ratio", "9x16"]) == 0
    (cmd, kw), = calls
    facts = json.loads((job.work / "pages" / "9x16" / "page.json").read_text(encoding="utf-8"))
    assert "snapshot" in cmd and "hyperframes@0.7.5" in " ".join(cmd)
    assert cmd[cmd.index("--at") + 1] == f"{facts['graphics'][0]['full_at']:g}"
    assert cmd[cmd.index("--describe") + 1] == "false"
    assert kw["cwd"] == job.work / "pages" / "9x16"
    dest = job.work / "preview" / "9x16-graphic-1.png"
    assert dest.is_file() and str(dest) in capsys.readouterr().out


def test_describe_false_is_always_in_the_arguments():
    for times in ([1.0], [1.0, 5.5, 9.25]):
        args = preview.snapshot_args(times)
        assert args[args.index("--describe") + 1] == "false"
        assert args[args.index("--at") + 1] == ",".join(f"{t:g}" for t in times)


def test_all_three_ratios_by_default_and_names_follow_the_pattern(ad_job, monkeypatch, capsys):
    job = job_with(ad_job, {"graphics": [STEPS]})
    calls = []
    fake_hyperframes(monkeypatch, calls)
    assert run(["preview", "acme", "take1.mp4"]) == 0
    assert len(calls) == 3
    names = sorted(p.name for p in (job.work / "preview").iterdir())
    assert names == ["1x1-graphic-1.png", "4x5-graphic-1.png", "9x16-graphic-1.png"]
    # each copy is that ratio's own still
    assert b"9x16" in (job.work / "preview" / "9x16-graphic-1.png").read_bytes()
    assert b"1x1" in (job.work / "preview" / "1x1-graphic-1.png").read_bytes()


def test_a_stale_still_is_never_copied(ad_job, monkeypatch):
    job = job_with(ad_job, {"graphics": [STEPS]})
    snaps = job.work / "pages" / "9x16" / "snapshots"
    snaps.mkdir(parents=True)
    (snaps / "frame-00-at-1.0s.png").write_bytes(b"stale")
    (snaps / "frame-07-at-2.0s.png").write_bytes(b"stale")
    calls = []
    fake_hyperframes(monkeypatch, calls)
    assert run(["preview", "acme", "take1.mp4", "--ratio", "9x16"]) == 0
    assert not (snaps / "frame-07-at-2.0s.png").exists()
    assert b"stale" not in (job.work / "preview" / "9x16-graphic-1.png").read_bytes()


def test_no_graphics_prints_a_plain_line_and_exits_0(ad_job, monkeypatch, capsys):
    job_with(ad_job, {})
    calls = []
    fake_hyperframes(monkeypatch, calls)
    assert run(["preview", "acme", "take1.mp4"]) == 0
    assert "no graphics" in capsys.readouterr().out and not calls


def test_a_gate_refusal_exits_1_with_the_gate_sentence(ad_job, monkeypatch, capsys):
    job_with(ad_job, {"graphics": [STEPS]})
    calls = []
    fake_hyperframes(monkeypatch, calls)
    (ad_job.__self__ if False else None)
    kit = json.loads((preview.paths.ROOT / "clients/acme/brand/kit.json").read_text(encoding="utf-8"))
    kit["approved"] = False
    (preview.paths.ROOT / "clients/acme/brand/kit.json").write_text(json.dumps(kit), encoding="utf-8")
    assert run(["preview", "acme", "take1.mp4"]) == 1
    assert "adeditor client approve acme" in capsys.readouterr().err and not calls


def test_a_snapshot_that_writes_the_wrong_number_of_stills_is_an_error(ad_job, monkeypatch, capsys):
    job_with(ad_job, {"graphics": [STEPS]})

    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if not any("hyperframes@" in str(c) for c in cmd):
            return real_run(cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    assert run(["preview", "acme", "take1.mp4", "--ratio", "9x16"]) == 1
    assert "0 stills" in capsys.readouterr().err


def test_the_snapshot_child_never_sees_the_vision_keys(ad_job, monkeypatch):
    job = job_with(ad_job, {"graphics": [STEPS]})
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret")
    monkeypatch.setenv("GOOGLE_API_KEY", "g-secret-2")
    monkeypatch.setenv("KEEP_ME", "1")
    calls = []
    fake_hyperframes(monkeypatch, calls)
    assert run(["preview", "acme", "take1.mp4", "--ratio", "9x16"]) == 0
    (_, kw), = calls
    assert "GEMINI_API_KEY" not in kw["env"] and "GOOGLE_API_KEY" not in kw["env"] and kw["env"]["KEEP_ME"] == "1"
    assert job and "GEMINI_API_KEY" in __import__("os").environ  # the parent's own environment is untouched
