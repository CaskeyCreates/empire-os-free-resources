import json

import pytest
from test_media import late_audio

from adeditor import aroll, cutplan, envelope, media


def test_filter_graph_trims_fades_and_joins_every_piece():
    segs = [{"src": [0.5, 1.5], "out": [0.0, 1.0]}, {"src": [2.0, 2.5], "out": [1.0, 1.5]}]
    graph = aroll.filter_graph(segs, 30.0)
    assert graph.count("[0:v]trim=") == 2
    assert graph.count("[0:a]aresample=async=1:first_pts=0,atrim=") == 2  # on the file's clock first
    assert "atrim=start=0.500000:end=1.500000,asetpts=PTS-STARTPTS" in graph
    assert "afade=t=in:d=0.008,afade=t=out:st=0.992000:d=0.008[a0]" in graph
    assert "afade=t=out:st=0.492000:d=0.008[a1]" in graph
    assert graph.endswith("[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")


def streams(path):
    out = media.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)]).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def test_aroll_length_matches_plan(take, video, tmp_path):
    pattern = [("s", 0.5), ("w", 0.5), ("s", 0.6), ("w", 0.4), ("s", 0.7), ("w", 0.3), ("s", 0.65),
               ("w", 0.5), ("s", 0.5)]
    wav, words, env = take(pattern)
    src = video(5, 320, 240, wav)
    plan = cutplan.plan(words, env, {"strikes": [{"from": 2, "to": 2, "why": "test"}], "fillers": []},
                        media.probe(src).fps)
    assert len(plan["segments"]) == 3
    out = aroll.build(src, plan, tmp_path / "work" / "aroll.mp4")
    frame = 1 / 30
    assert abs(media.probe(out).duration - plan["duration"]) <= frame
    s = streams(out)
    assert abs(float(s["video"]["duration"]) - plan["duration"]) <= frame
    assert abs(float(s["audio"]["duration"]) - plan["duration"]) <= frame
    assert s["video"]["codec_name"] == "h264" and s["video"]["pix_fmt"] == "yuv420p"
    assert s["video"]["avg_frame_rate"] == "30/1"
    assert (s["audio"]["codec_name"], s["audio"]["sample_rate"], s["audio"]["channels"]) == ("aac", "48000", 2)
    assert (tmp_path / "work" / "aroll.fc").read_text().endswith("concat=n=3:v=1:a=1[v][a]")


def heard_in(env, inset=0.08):
    """Word times as a transcriber would give them for this audio: one per stretch of sound."""
    _, line = envelope.floor_and_line(env)
    words, start = [], None
    for i, v in enumerate(env + [-100.0]):
        if v > line and start is None:
            start = i
        elif v <= line and start is not None:
            words.append({"word": f"w{len(words)}", "start": start / 100 + inset, "end": i / 100 - inset})
            start = None
    return words


def test_aroll_cuts_on_the_container_clock_when_audio_starts_late(speech, tmp_path):
    pattern = [("s", 0.5), ("w", 0.5), ("s", 0.6), ("w", 0.4), ("s", 0.6), ("w", 0.5), ("s", 0.5)]
    src = late_audio(speech(pattern), tmp_path / "late.mp4", seconds=4.5)
    env = envelope.envelope_db(media.extract_audio(src, tmp_path / "src.wav"))
    plan = cutplan.plan(heard_in(env), env, {"strikes": [], "fillers": []}, media.probe(src).fps)
    assert len(plan["segments"]) == 3
    out = aroll.build(src, plan, tmp_path / "aroll.mp4")
    cut_env = envelope.envelope_db(media.extract_audio(out, tmp_path / "aroll.wav"))
    _, line = envelope.floor_and_line(env)
    for w in plan["words"]:  # the A-roll holds each kept word's sound where the plan says it is
        assert all(v > line for v in cut_env[int(w["start"] * 100) + 2:int(w["end"] * 100) - 2])


def test_the_first_piece_stays_in_sync_when_it_starts_before_the_audio(speech, tmp_path):
    # Speech 50 ms into audio that starts 0.5 s after the picture: the lead-in reaches back before
    # the audio's first sample, and the sound must still land where the plan puts it.
    pattern = [("s", 0.05), ("w", 0.5), ("s", 0.6), ("w", 0.4), ("s", 0.6)]
    src = late_audio(speech(pattern), tmp_path / "late.mp4", seconds=2.7)
    env = envelope.envelope_db(media.extract_audio(src, tmp_path / "src.wav"))
    plan = cutplan.plan(heard_in(env), env, {"strikes": [], "fillers": []}, media.probe(src).fps)
    out = aroll.build(src, plan, tmp_path / "aroll.mp4")
    cut_env = envelope.envelope_db(media.extract_audio(out, tmp_path / "aroll.wav"))
    _, line = envelope.floor_and_line(env)
    sound_src = next(i for i, v in enumerate(env) if v > line) / 100
    planned = sound_src - plan["segments"][0]["src"][0]
    heard = next(i for i, v in enumerate(cut_env) if v > line) / 100
    assert abs(heard - planned) <= 0.02


def test_old_ffmpeg_gets_the_old_script_option(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if "-/filter_complex" in args:
            raise media.ToolError("ffmpeg failed (exit 8):\nUnrecognized option '/filter_complex'.\n"
                                  "Error splitting the argument list: Option not found")

    monkeypatch.setattr(aroll.media, "run", fake_run)
    monkeypatch.setattr(aroll.media, "probe", lambda p: media.Probe(320, 240, 30.0, 2.0, True, 0))
    cut = {"segments": [{"src": [0.0, 1.0], "out": [0.0, 1.0]}]}
    aroll.build(tmp_path / "src.mp4", cut, tmp_path / "aroll.mp4")
    assert "-/filter_complex" in calls[0] and "-filter_complex_script" in calls[1] and len(calls) == 2


def test_other_ffmpeg_failures_are_not_retried(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        raise media.ToolError("ffmpeg failed (exit 1):\nInvalid data found when processing input")

    monkeypatch.setattr(aroll.media, "run", fake_run)
    monkeypatch.setattr(aroll.media, "probe", lambda p: media.Probe(320, 240, 30.0, 2.0, True, 0))
    with pytest.raises(media.ToolError, match="Invalid data"):
        aroll.build(tmp_path / "src.mp4", {"segments": [{"src": [0.0, 1.0], "out": [0.0, 1.0]}]}, tmp_path / "o.mp4")
    assert len(calls) == 1
